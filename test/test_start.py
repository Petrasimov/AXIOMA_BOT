"""
test_start.py — Команда /start и главное меню
"""

import unittest

import db as db_module
from bot import api
from handlers import start
from tests.helpers import FakeAPI, FakeDB, make_message


class StartTestCase(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.api = FakeAPI(api).install()
        self.db = FakeDB(db_module).install()

    async def asyncTearDown(self):
        self.api.uninstall()
        self.db.uninstall()


class TestHandleStart(StartTestCase):

    async def test_plain_start(self):
        msg = make_message(111, '/start')['message']
        await start.handle_start(msg)
        self.assertEqual(len(self.api.to(111)), 1)
        self.assertIn('Добро пожаловать', self.api.last(111).text)

    async def test_hello_payload_adds_greeting(self):
        """Переход с сайта: сначала приветствие, потом меню."""
        msg = make_message(111, '/start hello')['message']
        await start.handle_start(msg, 'hello')

        texts = self.api.texts(111)
        self.assertEqual(len(texts), 2)
        self.assertIn('бот-менеджер', texts[0])
        self.assertIn('Добро пожаловать', texts[1])

    async def test_unknown_payload_ignored(self):
        msg = make_message(111, '/start что-то')['message']
        await start.handle_start(msg, 'что-то')
        self.assertEqual(len(self.api.to(111)), 1)

    async def test_menu_attached(self):
        msg = make_message(111, '/start')['message']
        await start.handle_start(msg)
        self.assertTrue(self.api.last(111).callbacks())


class TestShowMenu(StartTestCase):

    async def test_sends_when_no_message_id(self):
        await start.show_menu(111)
        self.assertEqual(self.api.last(111).kind, 'send')

    async def test_edits_when_message_id_given(self):
        """Возврат в меню редактирует сообщение,
        чтобы не плодить копии в чате."""
        await start.show_menu(111, message_id=42)
        self.assertEqual(self.api.last(111).kind, 'edit')

    async def test_falls_back_to_send_if_edit_fails(self):
        async def fail_edit(chat_id, message_id, text, **kw):
            raise api.TelegramError('message is not modified')
        api.edit_message_text = fail_edit

        await start.show_menu(111, message_id=42)
        self.assertEqual(self.api.last(111).kind, 'send')


class TestStubs(StartTestCase):
    """Заглушки нереализованных разделов."""

    async def test_only_password_left(self):
        """По мере выполнения задач заглушки должны исчезать."""
        self.assertEqual(list(start.STUB_TEXT.keys()), ['password'])

    async def test_stub_shows_message(self):
        callback = {
            'message': {'chat': {'id': 111}, 'message_id': 5},
            'from': {'id': 111},
        }
        await start.handle_menu_callback(callback, 'password')
        self.assertTrue(self.api.any_text_contains('скоро появится', 111))

    async def test_unknown_action_ignored(self):
        callback = {
            'message': {'chat': {'id': 111}, 'message_id': 5},
            'from': {'id': 111},
        }
        await start.handle_menu_callback(callback, 'несуществующий')
        self.assertEqual(self.api.to(111), [])


class TestWelcomeText(unittest.TestCase):
    """Приветствие переехало с фронтенда — проверяем что не потерялось."""

    def test_mentions_closed_testing(self):
        self.assertIn('закрыт', start.WELCOME_TEXT)

    def test_covers_both_cases(self):
        self.assertIn('тестировщик', start.WELCOME_TEXT)

    def test_valid_html(self):
        """Несбалансированные теги ломают parse_mode=HTML."""
        for tag in ('b', 'i', 'code'):
            self.assertEqual(
                start.WELCOME_TEXT.count(f'<{tag}>'),
                start.WELCOME_TEXT.count(f'</{tag}>'),
            )


if __name__ == '__main__':
    unittest.main()