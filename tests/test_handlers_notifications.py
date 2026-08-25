"""
test_handlers_notifications.py — Раздел «🔔 Уведомления»

Проверяются все состояния пользователя: незарегистрирован,
без подписки, без строки настроек, заблокированный аккаунт.
"""

import unittest

import db as db_module
from bot import api, router
from handlers import notifications as nh
from tests.helpers import FakeAPI, FakeDB, make_callback


class NotificationsTestCase(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.api = FakeAPI(api).install()
        self.db = FakeDB(db_module).install()

    async def asyncTearDown(self):
        self.api.uninstall()
        self.db.uninstall()


class TestShow(NotificationsTestCase):

    async def test_unregistered_user(self):
        await router.dispatch(make_callback(999, 'menu:notify'))
        self.assertTrue(self.api.any_text_contains('не зарегистрированы', 999))

    async def test_status_enabled(self):
        self.db.add_user(111, notifications_on=True)
        await router.dispatch(make_callback(111, 'menu:notify'))
        self.assertTrue(self.api.any_text_contains('включены', 111))

    async def test_status_disabled(self):
        self.db.add_user(111, notifications_on=False)
        await router.dispatch(make_callback(111, 'menu:notify'))
        self.assertTrue(self.api.any_text_contains('выключены', 111))

    async def test_warns_without_subscription(self):
        """Уведомления можно включить без подписки,
        но приходить они не будут — надо предупредить."""
        self.db.add_user(111, is_paid=False, notifications_on=True)
        await router.dispatch(make_callback(111, 'menu:notify'))
        self.assertTrue(self.api.any_text_contains('Подписка не активна', 111))

    async def test_warns_inactive_account(self):
        self.db.add_user(111, is_active=False)
        await router.dispatch(make_callback(111, 'menu:notify'))
        self.assertTrue(self.api.any_text_contains('Аккаунт неактивен', 111))

    async def test_missing_user_settings_row(self):
        """Строку в UserSettings создаёт C#. Если её нет —
        бот не должен молча делать вид, что всё хорошо."""
        self.db.add_user(111)
        self.db.users[111]['notifications_on'] = None
        await router.dispatch(make_callback(111, 'menu:notify'))
        self.assertTrue(self.api.any_text_contains('Настройки не найдены', 111))

    async def test_toggle_button_label_matches_state(self):
        self.db.add_user(111, notifications_on=False)
        await router.dispatch(make_callback(111, 'menu:notify'))
        callbacks = self.api.last(111).callbacks()
        self.assertIn('notify:on', callbacks)


class TestToggle(NotificationsTestCase):

    async def test_turn_on(self):
        self.db.add_user(111, notifications_on=False)
        await router.dispatch(make_callback(111, 'notify:on'))
        self.assertTrue(self.db.users[111]['notifications_on'])

    async def test_turn_off(self):
        self.db.add_user(111, notifications_on=True)
        await router.dispatch(make_callback(111, 'notify:off'))
        self.assertFalse(self.db.users[111]['notifications_on'])

    async def test_screen_redrawn_after_toggle(self):
        self.db.add_user(111, notifications_on=False)
        await router.dispatch(make_callback(111, 'notify:on'))
        self.assertTrue(self.api.any_text_contains('включены', 111))

    async def test_toggle_without_settings_row(self):
        self.db.add_user(111)
        self.db.users[111]['notifications_on'] = None
        await router.dispatch(make_callback(111, 'notify:on'))
        self.assertTrue(self.api.any_text_contains('Настройки не найдены', 111))


class TestDatabaseFailure(NotificationsTestCase):
    """Падение БД не должно оставлять пользователя без ответа."""

    async def test_show_handles_db_error(self):
        async def boom(user_id):
            raise RuntimeError('БД недоступна')
        db_module.get_user_state = boom

        await router.dispatch(make_callback(111, 'menu:notify'))
        self.assertTrue(self.api.any_text_contains('Попробуйте позже', 111))

    async def test_toggle_handles_db_error(self):
        async def boom(user_id, enabled):
            raise RuntimeError('БД недоступна')
        db_module.set_notifications = boom

        await router.dispatch(make_callback(111, 'notify:on'))
        self.assertTrue(self.api.any_text_contains('Попробуйте позже', 111))


if __name__ == '__main__':
    unittest.main()