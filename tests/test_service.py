"""
test_service.py — Цикл рассылки уведомлений

Здесь проверяется поведение целиком, по циклам: появление связки,
её исчезновение, возврат. Именно так проявляется баг Б1, который
на уровне отдельных функций не виден.
"""

import unittest
from datetime import datetime, timedelta, timezone

import config
import db as db_module
from bot import api
from notifications import cooldown as cd
from notifications import sender, service
from tests.helpers import FakeAPI, FakeDB, make_opp


class ServiceTestCase(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.api = FakeAPI(api).install()
        self.db = FakeDB(db_module).install()
        self.user = self.db.add_user(1, min_spread=1.0)
        service.previous_opps.clear()

        self.sent_notifications = []
        self._original_send = sender.send_notification

        async def fake_send(user_id, opp, amount):
            self.sent_notifications.append((user_id, opp['symbol'],
                                            round(float(opp['spread']), 2)))
            return True

        sender.send_notification = fake_send

    async def asyncTearDown(self):
        sender.send_notification = self._original_send
        self.api.uninstall()
        self.db.uninstall()
        service.previous_opps.clear()

    async def cycle(self, opps):
        """Один проход по пользователю."""
        return await service.process_user(1, self.user, opps, 100.0)

    def age_cooldowns(self, hours):
        """Перематывает время последней отправки назад."""
        for rec in self.db.cooldowns.values():
            rec['sent_at'] = datetime.now(timezone.utc) - timedelta(hours=hours)


class TestBasicFlow(ServiceTestCase):

    async def test_new_opportunity_sends(self):
        await self.cycle([make_opp('SIRENUSDT', 3.5)])
        self.assertEqual(len(self.sent_notifications), 1)

    async def test_same_opportunity_not_resent(self):
        await self.cycle([make_opp('SIRENUSDT', 3.5)])
        await self.cycle([make_opp('SIRENUSDT', 3.4)])
        self.assertEqual(len(self.sent_notifications), 1)

    async def test_filtered_out_not_sent(self):
        self.user['min_spread'] = 5.0
        await self.cycle([make_opp('SIRENUSDT', 3.5)])
        self.assertEqual(self.sent_notifications, [])

    async def test_cooldown_written_after_send(self):
        await self.cycle([make_opp('SIRENUSDT', 3.5)])
        self.assertEqual(len(self.db.cooldowns), 1)


class TestRegressionB1(ServiceTestCase):
    """Полный сценарий бага Б1 по циклам.

    До починки шестой цикл молчал бы навсегда: повторное
    уведомление не приходило никогда.
    """

    async def test_repeat_after_drop_and_return(self):
        # 1. Связка появилась
        await self.cycle([make_opp('SIRENUSDT', 3.5)])
        self.assertEqual(len(self.sent_notifications), 1)

        # 2. Всё ещё здесь — молчим
        await self.cycle([make_opp('SIRENUSDT', 3.4)])
        self.assertEqual(len(self.sent_notifications), 1)

        # 3. Пропала — это и есть падение спреда
        await self.cycle([])
        rec = list(self.db.cooldowns.values())[0]
        self.assertTrue(rec['dropped_since'],
                        'исчезновение связки не отмечено как падение')

        # 4. Вернулась, но час ещё не прошёл
        await self.cycle([make_opp('SIRENUSDT', 4.0)])
        self.assertEqual(len(self.sent_notifications), 1)

        # ...прошло два часа
        self.age_cooldowns(2)

        # 5. Снова пропала
        await self.cycle([])

        # 6. Вернулась — час прошёл И спред падал
        await self.cycle([make_opp('SIRENUSDT', 5.2)])
        self.assertEqual(len(self.sent_notifications), 2,
                         'повторное уведомление не пришло — регрессия Б1')

    async def test_no_repeat_without_drop(self):
        """Связка висит непрерывно много часов — повторов быть не должно."""
        await self.cycle([make_opp('SIRENUSDT', 3.5)])
        self.age_cooldowns(10)

        for _ in range(5):
            await self.cycle([make_opp('SIRENUSDT', 3.5)])

        self.assertEqual(len(self.sent_notifications), 1)


class TestRateLimit(ServiceTestCase):
    """Б3: защита от лавины сообщений."""

    async def test_limit_per_cycle(self):
        opps = [make_opp(f'COIN{i}USDT', 3.0) for i in range(50)]
        sent = await self.cycle(opps)
        self.assertEqual(sent, config.MAX_NOTIFICATIONS_PER_CYCLE)

    async def test_rest_sent_in_next_cycles(self):
        """Остаток не теряется, а уходит в следующих циклах."""
        opps = [make_opp(f'COIN{i}USDT', 3.0) for i in range(12)]
        await self.cycle(opps)
        first = len(self.sent_notifications)

        service.previous_opps.clear()   # имитируем следующий цикл
        await self.cycle(opps)

        self.assertGreater(len(self.sent_notifications), first)


class TestBlockedUser(ServiceTestCase):
    """Б4: бот не должен долбиться в заблокировавшего его."""

    async def test_notifications_disabled_on_block(self):
        async def blocked(user_id, opp, amount):
            raise api.BotBlocked('bot was blocked by the user')
        sender.send_notification = blocked

        await self.cycle([make_opp('SIRENUSDT', 3.5)])

        self.assertIn((1, False), self.db.notifications_set)

    async def test_stops_sending_after_block(self):
        calls = []

        async def blocked(user_id, opp, amount):
            calls.append(opp['symbol'])
            raise api.BotBlocked('blocked')
        sender.send_notification = blocked

        opps = [make_opp(f'C{i}USDT', 3.0) for i in range(10)]
        await self.cycle(opps)

        self.assertEqual(len(calls), 1, 'после блокировки продолжил слать')


class TestDroppedMarking(ServiceTestCase):

    async def test_active_keys_not_marked(self):
        await self.cycle([make_opp('SIRENUSDT', 3.5)])
        await self.cycle([make_opp('SIRENUSDT', 3.5)])
        rec = list(self.db.cooldowns.values())[0]
        self.assertFalse(rec['dropped_since'])

    async def test_filtered_out_counts_as_drop(self):
        """Спред упал ниже порога пользователя — тоже падение."""
        await self.cycle([make_opp('SIRENUSDT', 3.5)])
        self.user['min_spread'] = 10.0
        await self.cycle([make_opp('SIRENUSDT', 3.5)])
        rec = list(self.db.cooldowns.values())[0]
        self.assertTrue(rec['dropped_since'])


class TestCheckAndNotify(ServiceTestCase):
    """Верхний уровень цикла."""

    async def test_no_users_no_crash(self):
        self.db.users.clear()
        await service.check_and_notify()

    async def test_no_opportunities_marks_drops(self):
        """Пустая выдача означает, что пропало всё —
        отметить это нужно обязательно, иначе после затишья
        ни одно уведомление не уйдёт."""
        self.db.add_cooldown(1, ('SIRENUSDT', 'kucoin_futures',
                                 'gate_futures', 'ff'), hours_ago=5)
        self.db.opportunities = []

        await service.check_and_notify()

        rec = list(self.db.cooldowns.values())[0]
        self.assertTrue(rec['dropped_since'])

    async def test_full_cycle_sends(self):
        self.db.opportunities = [make_opp('SIRENUSDT', 3.5)]
        await service.check_and_notify()
        self.assertEqual(len(self.sent_notifications), 1)

    async def test_db_error_does_not_crash(self):
        async def boom():
            raise RuntimeError('БД недоступна')
        db_module.get_current_opportunities = boom
        await service.check_and_notify()


class TestSenderFormatting(unittest.TestCase):
    """Формат текста уведомления."""

    def test_message_contains_key_fields(self):
        text = sender.build_message(make_opp('SIRENUSDT', 3.55), 100.0)
        self.assertIn('SIREN', text)
        self.assertIn('3.55', text)
        self.assertIn('KuCoin Futures', text)
        self.assertIn('Gate Futures', text)

    def test_profit_calculated(self):
        text = sender.build_message(make_opp('BTCUSDT', 2.0), 500.0)
        self.assertIn('10.0', text)      # 2% от 500

    def test_strategy_labels(self):
        ff = sender.build_message(make_opp(strategy='futures_futures'), 100)
        sf = sender.build_message(make_opp(strategy='spot_futures'), 100)
        self.assertIn('Futures-Futures', ff)
        self.assertIn('Spot-Futures', sf)

    def test_exchange_names_prettified(self):
        text = sender.build_message(
            make_opp(bid_ex='okx_futures', ask_ex='mexc_futures'), 100)
        self.assertIn('OKX', text)
        self.assertIn('MEXC', text)

    def test_missing_fields_do_not_crash(self):
        sender.build_message({}, 100.0)


if __name__ == '__main__':
    unittest.main()