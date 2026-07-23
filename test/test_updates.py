"""
test_updates.py — Приём апдейтов через long-polling

Два критичных момента:

  1. Сброс накопленного хвоста при старте. Telegram хранит
     недоставленные апдейты 24 часа — без сброса рестарт бота
     означал бы повторную обработку всего за сутки: дубли
     резюме и обращений в поддержку.

  2. Сдвиг offset ДО обработки. Если обработчик упадёт, апдейт
     не должен прийти повторно — иначе бот зациклится на одном
     «ядовитом» сообщении навсегда.
"""

import unittest

from bot import api, updates


class TestInitOffset(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self._original = api.get_updates

    async def asyncTearDown(self):
        api.get_updates = self._original

    async def test_discards_accumulated_tail(self):
        async def fake(offset, timeout):
            self.assertEqual(offset, -1, 'стартовый запрос должен быть offset=-1')
            return [{'update_id': 555}]
        api.get_updates = fake

        offset = await updates._init_offset()
        self.assertEqual(offset, 556)

    async def test_zero_when_no_updates(self):
        async def fake(offset, timeout):
            return []
        api.get_updates = fake

        self.assertEqual(await updates._init_offset(), 0)

    async def test_survives_api_error(self):
        async def fake(offset, timeout):
            raise api.TelegramError('сеть недоступна')
        api.get_updates = fake

        self.assertEqual(await updates._init_offset(), 0)

    async def test_conflict_propagates(self):
        """409 означает второй экземпляр бота — это не рядовая
        ошибка, её нельзя проглатывать."""
        async def fake(offset, timeout):
            raise api.TelegramConflict('terminated by other getUpdates')
        api.get_updates = fake

        with self.assertRaises(api.TelegramConflict):
            await updates._init_offset()


class TestRunLoop(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self._original = api.get_updates
        self._dispatch = updates.router.dispatch
        updates._running = True

    async def asyncTearDown(self):
        api.get_updates = self._original
        updates.router.dispatch = self._dispatch
        updates._running = True

    async def test_offsets_advance(self):
        """offset должен расти на update_id + 1."""
        seen_offsets = []
        batches = [
            [{'update_id': 10, 'message': {'chat': {'id': 1}, 'text': 'a'}}],
            [{'update_id': 11, 'message': {'chat': {'id': 1}, 'text': 'b'}}],
        ]

        async def fake_get(offset, timeout):
            if offset == -1:          # инициализация, хвост пуст
                return []
            seen_offsets.append(offset)
            if not batches:
                updates.stop()
                return []
            return batches.pop(0)

        async def fake_dispatch(update):
            pass

        api.get_updates = fake_get
        updates.router.dispatch = fake_dispatch

        await updates.run_loop()

        # offset растёт как update_id + 1
        self.assertIn(11, seen_offsets)
        self.assertIn(12, seen_offsets)

    async def test_failing_handler_does_not_stop_loop(self):
        """Падение обработчика не должно ронять long-polling."""
        processed = []
        batches = [
            [{'update_id': 1, 'message': {'chat': {'id': 1}, 'text': 'плохой'}},
             {'update_id': 2, 'message': {'chat': {'id': 1}, 'text': 'хороший'}}],
        ]

        async def fake_get(offset, timeout):
            if offset == -1:          # инициализация
                return []
            if not batches:
                updates.stop()
                return []
            return batches.pop(0)

        async def fake_dispatch(update):
            text = update['message']['text']
            if text == 'плохой':
                raise RuntimeError('обработчик упал')
            processed.append(text)

        api.get_updates = fake_get
        updates.router.dispatch = fake_dispatch

        await updates.run_loop()

        self.assertEqual(processed, ['хороший'],
                         'второй апдейт не обработан после падения первого')

    async def test_stop_breaks_loop(self):
        async def fake_get(offset, timeout):
            updates.stop()
            return []
        api.get_updates = fake_get

        await updates.run_loop()   # должен завершиться, а не зависнуть


if __name__ == '__main__':
    unittest.main()