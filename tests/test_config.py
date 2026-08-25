"""
test_config.py — Настройки из .env

Проверяем что значения читаются, приводятся к нужным типам
и что validate() ловит отсутствие обязательных переменных.
"""

import unittest

import config


class TestValues(unittest.TestCase):

    def test_types(self):
        """Числовые настройки должны быть числами, а не строками."""
        self.assertIsInstance(config.CHECK_INTERVAL, int)
        self.assertIsInstance(config.COOLDOWN_HOURS, int)
        self.assertIsInstance(config.MAX_NOTIFICATIONS_PER_CYCLE, int)
        self.assertIsInstance(config.ADMIN_CHAT_ID, int)
        self.assertIsInstance(config.SUBSCRIPTION_PRICE_USD, float)
        self.assertIsInstance(config.PAYMENT_TIMEOUT_MINUTES, int)
        self.assertIsInstance(config.LONG_POLL_TIMEOUT, int)

    def test_rate_limits_below_telegram_ceiling(self):
        """Лимиты должны быть ниже потолка Telegram (30/сек и 1/сек)."""
        self.assertLess(config.RATE_GLOBAL_PER_SEC, 30)
        self.assertGreaterEqual(config.RATE_CHAT_INTERVAL, 1.0)

    def test_long_poll_below_telegram_max(self):
        """Long-polling дольше 50с Telegram не разрешает."""
        self.assertLess(config.LONG_POLL_TIMEOUT, 50)
        self.assertGreater(config.LONG_POLL_TIMEOUT, 0)

    def test_scanner_url(self):
        self.assertTrue(config.SCANNER_URL.startswith('https://'))

    def test_notification_limit_sane(self):
        """Лимит должен быть положительным — иначе уведомления не пойдут."""
        self.assertGreater(config.MAX_NOTIFICATIONS_PER_CYCLE, 0)


class TestValidate(unittest.TestCase):

    def setUp(self):
        self.token = config.BOT_TOKEN
        self.dsn = config.DATABASE_URL

    def tearDown(self):
        config.BOT_TOKEN = self.token
        config.DATABASE_URL = self.dsn

    def test_passes_with_required(self):
        config.validate()  # не должно бросить

    def test_fails_without_token(self):
        config.BOT_TOKEN = ''
        with self.assertRaises(ValueError):
            config.validate()

    def test_fails_without_database_url(self):
        config.DATABASE_URL = ''
        with self.assertRaises(ValueError):
            config.validate()

    def test_admin_chat_not_required(self):
        """Бот должен подниматься без ADMIN_CHAT_ID —
        он нужен только поддержке и резюме."""
        saved = config.ADMIN_CHAT_ID
        config.ADMIN_CHAT_ID = 0
        try:
            config.validate()
        finally:
            config.ADMIN_CHAT_ID = saved


if __name__ == '__main__':
    unittest.main()