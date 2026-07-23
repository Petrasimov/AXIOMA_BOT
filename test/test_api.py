"""
test_api.py — Обёртка Telegram Bot API

Проверяется то, ради чего этот модуль и существует: соблюдение
лимитов Telegram и корректная реакция на коды ошибок.

Реальных запросов не делается — сессия подменяется.
"""

import asyncio
import unittest

from bot import api


class FakeResponse:
    def __init__(self, data):
        self._data = data

    async def json(self):
        return self._data

    @property
    def status(self):
        return self._data.get('error_code', 200)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeSession:
    """Отдаёт заранее заданные ответы по очереди."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = 0
        self.payloads = []

    def post(self, url, **kwargs):
        if 'json' in kwargs:
            self.payloads.append(kwargs['json'])
        idx = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return FakeResponse(self.responses[idx])

    async def close(self):
        pass


def ok(result=None):
    return {'ok': True, 'result': result if result is not None else {'message_id': 1}}


def err(code, description='error', **extra):
    return {'ok': False, 'error_code': code, 'description': description, **extra}


class TestThrottle(unittest.IsolatedAsyncioTestCase):
    """Лимиты Telegram: ~30 сообщений/сек глобально и 1/сек в чат."""

    async def asyncSetUp(self):
        await api.init()
        api._global_window.clear()
        api._chat_last.clear()

    async def asyncTearDown(self):
        await api.close()

    async def test_global_rate_limit(self):
        """Больше лимита за секунду отправить нельзя."""
        loop = asyncio.get_running_loop()
        start = loop.time()

        for i in range(api.config.RATE_GLOBAL_PER_SEC + 5):
            await api._throttle(chat_id=1000 + i)   # разные чаты

        elapsed = loop.time() - start
        self.assertGreater(elapsed, 0.9,
                           'глобальный лимит не притормозил отправку')

    async def test_per_chat_interval(self):
        """В один чат — не чаще одного сообщения в секунду."""
        loop = asyncio.get_running_loop()
        start = loop.time()

        for _ in range(3):
            await api._throttle(chat_id=777)

        elapsed = loop.time() - start
        expected = api.config.RATE_CHAT_INTERVAL * 2
        self.assertGreater(elapsed, expected - 0.2)

    async def test_chat_map_does_not_grow_forever(self):
        """Словарь последних отправок должен подчищаться."""
        for i in range(5200):
            api._chat_last[i] = 0.0        # заведомо старые записи
        await api._throttle(chat_id=99999)
        self.assertLess(len(api._chat_last), 5200)


class TestErrorHandling(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        await api.init()
        api._global_window.clear()
        api._chat_last.clear()

    async def asyncTearDown(self):
        api._session = None
        await api.close()

    async def test_success_returns_result(self):
        api._session = FakeSession([ok({'message_id': 42})])
        result = await api.send_message(1, 'hello')
        self.assertEqual(result['message_id'], 42)

    async def test_403_raises_bot_blocked(self):
        """Заблокировавший пользователь должен отличаться от прочих
        ошибок — по нему гасятся уведомления."""
        api._session = FakeSession([err(403, 'bot was blocked by the user')])
        with self.assertRaises(api.BotBlocked):
            await api.send_message(1, 'hi')

    async def test_409_raises_conflict(self):
        """409 означает второй экземпляр бота на том же токене."""
        api._session = FakeSession([err(409, 'terminated by other getUpdates')])
        with self.assertRaises(api.TelegramConflict):
            await api.get_updates(0, 0)

    async def test_429_waits_and_retries(self):
        session = FakeSession([
            err(429, 'Too Many Requests', parameters={'retry_after': 1}),
            ok({'message_id': 7}),
        ])
        api._session = session

        loop = asyncio.get_running_loop()
        start = loop.time()
        result = await api.send_message(1, 'hi')
        elapsed = loop.time() - start

        self.assertEqual(result['message_id'], 7)
        self.assertEqual(session.calls, 2, 'не было повтора после 429')
        self.assertGreater(elapsed, 1.0, 'retry_after не соблюдён')

    async def test_5xx_retries(self):
        session = FakeSession([err(502, 'Bad Gateway'), ok({'message_id': 8})])
        api._session = session
        result = await api.send_message(1, 'hi')
        self.assertEqual(result['message_id'], 8)
        self.assertEqual(session.calls, 2)

    async def test_4xx_does_not_retry(self):
        """На «чат не найден» повторять бессмысленно."""
        session = FakeSession([err(400, 'chat not found')])
        api._session = session
        with self.assertRaises(api.TelegramError):
            await api.send_message(1, 'hi')
        self.assertEqual(session.calls, 1, 'лишний повтор на 400')

    async def test_uninitialized_session_raises(self):
        api._session = None
        with self.assertRaises(api.TelegramError):
            await api.send_message(1, 'hi')


class TestPayloads(unittest.IsolatedAsyncioTestCase):
    """Проверяем что в Telegram уходит корректно собранный запрос."""

    async def asyncSetUp(self):
        await api.init()
        api._global_window.clear()
        api._chat_last.clear()
        self.session = FakeSession([ok()])
        api._session = self.session

    async def asyncTearDown(self):
        api._session = None
        await api.close()

    async def test_send_message_payload(self):
        await api.send_message(123, 'текст', reply_markup={'inline_keyboard': []})
        p = self.session.payloads[0]
        self.assertEqual(p['chat_id'], 123)
        self.assertEqual(p['text'], 'текст')
        self.assertEqual(p['parse_mode'], 'HTML')
        self.assertIn('reply_markup', p)

    async def test_get_updates_payload(self):
        await api.get_updates(offset=55, timeout=30)
        p = self.session.payloads[0]
        self.assertEqual(p['offset'], 55)
        self.assertEqual(p['timeout'], 30)
        self.assertEqual(p['allowed_updates'], ['message', 'callback_query'])

    async def test_answer_callback_payload(self):
        await api.answer_callback_query('abc', text='готово')
        p = self.session.payloads[0]
        self.assertEqual(p['callback_query_id'], 'abc')
        self.assertEqual(p['text'], 'готово')


if __name__ == '__main__':
    unittest.main()