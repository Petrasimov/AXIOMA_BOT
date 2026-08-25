"""
test_password.py — Восстановление пароля (Задача 6)

Проверяем не «не упало ли», а что именно ушло пользователю, что
получил бэкенд и что сообщение с паролем удалено.

Отдельный акцент на безопасности: пароль не должен появиться ни в
одном ответе бота и должен исчезнуть из чата — на это есть отдельные
тесты, потому что цена ошибки здесь выше, чем у любого другого раздела.
"""

import unittest

import db as db_module
from bot import api, router
from handlers import password
from tests.helpers import FakeAPI, FakeDB, make_callback, make_message


class PasswordTestCase(unittest.IsolatedAsyncioTestCase):
    """Общая подготовка: перехват Telegram, БД и запросов к бэкенду."""

    async def asyncSetUp(self):
        self.api = FakeAPI(api).install()
        self.db = FakeDB(db_module).install()

        # deleteMessage появился вместе с Задачей 6 — FakeAPI о нём не знает
        self.deleted = []

        async def delete_message(chat_id, message_id):
            self.deleted.append((chat_id, message_id))
            return True

        self._orig_delete = getattr(api, 'delete_message', None)
        api.delete_message = delete_message

        # Логин пользователя — отдельный запрос, FakeDB его не подменяет
        self.login = 'petr'
        self._orig_get_login = getattr(db_module, 'get_user_login', None)

        async def get_user_login(user_id):
            return self.login

        db_module.get_user_login = get_user_login

        # Ответ бэкенда: (успех, причина)
        self.backend_result = (True, '')
        self.backend_calls = []
        self._orig_set = password._set_password

        async def fake_set(user_id, login, pw):
            self.backend_calls.append({'user_id': user_id, 'login': login, 'password': pw})
            return self.backend_result

        password._set_password = fake_set

    async def asyncTearDown(self):
        password._set_password = self._orig_set
        if self._orig_delete is not None:
            api.delete_message = self._orig_delete
        if self._orig_get_login is not None:
            db_module.get_user_login = self._orig_get_login
        self.api.uninstall()
        self.db.uninstall()


class TestAsk(PasswordTestCase):
    """Экран запроса нового пароля."""

    async def test_not_registered(self):
        """Аккаунта нет — восстанавливать нечего."""
        await password.ask(111)
        self.assertTrue(self.api.any_text_contains('не зарегистрированы', 111))

    async def test_no_login_suggests_telegram(self):
        """Заходил только через Telegram — пароля не существует."""
        self.db.add_user(111)
        self.login = ''
        await password.ask(111)
        self.assertTrue(self.api.any_text_contains('нет логина', 111))

    async def test_shows_form_with_force_reply(self):
        """Контекст диалога держит Telegram через ForceReply."""
        self.db.add_user(111)
        await password.ask(111)

        self.assertTrue(self.api.any_text_contains(password.PASSWORD_MARKER, 111))
        self.assertTrue(self.api.last(111).markup.get('force_reply'))

    async def test_form_warns_about_deletion(self):
        """Пользователь должен знать, что сообщение будет удалено."""
        self.db.add_user(111)
        await password.ask(111)
        self.assertIn('удалено', self.api.last(111).text)


class TestValidation(PasswordTestCase):
    """Проверки до обращения к бэкенду."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.db.add_user(111)

    async def test_short_password_rejected(self):
        msg = make_message(111, '123')['message']
        await password.handle_new_password(msg)

        self.assertTrue(self.api.any_text_contains('слишком короткий', 111))
        self.assertEqual(self.backend_calls, [], 'бэкенд не должен вызываться')

    async def test_long_password_rejected(self):
        msg = make_message(111, 'x' * 200)['message']
        await password.handle_new_password(msg)

        self.assertTrue(self.api.any_text_contains('слишком длинный', 111))
        self.assertEqual(self.backend_calls, [])

    async def test_short_password_still_deleted(self):
        """Неподошедший пароль тоже не должен остаться в чате."""
        msg = make_message(111, '123', message_id=7)['message']
        await password.handle_new_password(msg)
        self.assertIn((111, 7), self.deleted)

    async def test_edges_trimmed(self):
        """Пробелы по краям почти наверняка случайны."""
        msg = make_message(111, '  Secret123  ')['message']
        await password.handle_new_password(msg)
        self.assertEqual(self.backend_calls[0]['password'], 'Secret123')

    async def test_inner_spaces_kept(self):
        """А внутри пароля пробел осмыслен — его не трогаем."""
        msg = make_message(111, 'two words pass')['message']
        await password.handle_new_password(msg)
        self.assertEqual(self.backend_calls[0]['password'], 'two words pass')


class TestSuccess(PasswordTestCase):
    """Успешная смена пароля."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.db.add_user(111)

    async def test_backend_receives_user_id(self):
        """userId обязателен: иначе бэкенд может завести нового
        пользователя по совпадению логина вместо смены пароля."""
        msg = make_message(111, 'Secret123')['message']
        await password.handle_new_password(msg)

        call = self.backend_calls[0]
        self.assertEqual(call['user_id'], 111)
        self.assertEqual(call['login'], 'petr')

    async def test_confirmation_reminds_login(self):
        msg = make_message(111, 'Secret123')['message']
        await password.handle_new_password(msg)

        text = self.api.last(111).text
        self.assertIn('Пароль изменён', text)
        self.assertIn('petr', text)

    async def test_message_deleted(self):
        msg = make_message(111, 'Secret123', message_id=42)['message']
        await password.handle_new_password(msg)
        self.assertIn((111, 42), self.deleted)


class TestSecurity(PasswordTestCase):
    """Пароль не должен утечь в чат."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.db.add_user(111)

    async def test_password_never_echoed_on_success(self):
        secret = 'SuperSecret99'
        await password.handle_new_password(make_message(111, secret)['message'])

        for text in self.api.texts():
            self.assertNotIn(secret, text)

    async def test_password_never_echoed_on_error(self):
        """Даже в сообщении об ошибке."""
        self.backend_result = (False, 'error')
        secret = 'SuperSecret99'
        await password.handle_new_password(make_message(111, secret)['message'])

        for text in self.api.texts():
            self.assertNotIn(secret, text)

    async def test_password_never_echoed_when_too_short(self):
        secret = 'abc'
        await password.handle_new_password(make_message(111, secret)['message'])

        for text in self.api.texts():
            self.assertNotIn(secret, text)


class TestBackendErrors(PasswordTestCase):
    """Каждая причина отказа — со своим понятным текстом."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.db.add_user(111)

    async def _run(self, reason):
        self.backend_result = (False, reason)
        await password.handle_new_password(make_message(111, 'Secret123')['message'])
        return self.api.last(111).text

    async def test_conflict(self):
        self.assertIn('Логин занят', await self._run('conflict'))

    async def test_bad_request(self):
        self.assertIn('не принял', await self._run('bad_request'))

    async def test_forbidden(self):
        self.assertIn('недоступна', await self._run('forbidden'))

    async def test_error(self):
        self.assertIn('Сервис временно недоступен', await self._run('error'))

    async def test_no_success_message_on_failure(self):
        text = await self._run('error')
        self.assertNotIn('Пароль изменён', text)


class TestRouting(PasswordTestCase):
    """Связь с роутером."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.db.add_user(111)

    async def test_menu_button_opens_form(self):
        await router.dispatch(make_callback(111, 'menu:password'))
        self.assertTrue(self.api.any_text_contains(password.PASSWORD_MARKER, 111))

    async def test_reply_recognised_by_marker(self):
        """Ответ на форму узнаётся по маркеру в тексте вопроса."""
        update = make_message(
            111, 'NewPass123',
            reply_to_text=f'🔑 {password.PASSWORD_MARKER}\n\nОтветьте...',
        )
        await router.dispatch(update)

        self.assertEqual(len(self.backend_calls), 1)
        self.assertTrue(self.api.any_text_contains('Пароль изменён', 111))

    async def test_marker_registered(self):
        self.assertIn(password.PASSWORD_MARKER, router.REPLY_MARKERS)


if __name__ == '__main__':
    unittest.main()