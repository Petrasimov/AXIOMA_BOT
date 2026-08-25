"""
test_cooldown.py — Логика антиспама

Отдельно проверяется регрессия бага Б1: до починки повторное
уведомление не приходило НИКОГДА, потому что условие падения
спреда было невыполнимо в принципе.
"""

import unittest
from datetime import datetime, timedelta, timezone

import config
from notifications.cooldown import can_send, make_key, record_sent
from tests.helpers import FakeDB, make_opp

import db as db_module


def record(hours_ago=0.0, dropped=False, last_spread=3.0):
    """Собирает cooldown-запись в том виде, в каком её отдаёт БД."""
    return {
        'sent_at': datetime.now(timezone.utc) - timedelta(hours=hours_ago),
        'last_spread': last_spread,
        'dropped_since': dropped,
    }


class TestMakeKey(unittest.TestCase):

    def test_normalizes_strategy(self):
        a = make_key(make_opp(strategy='futures_futures'))
        b = make_key(make_opp(strategy='ff'))
        self.assertEqual(a, b)
        self.assertEqual(a[3], 'ff')

    def test_key_shape(self):
        key = make_key(make_opp(symbol='X', bid_ex='a', ask_ex='b', strategy='sf'))
        self.assertEqual(key, ('X', 'a', 'b', 'sf'))

    def test_missing_fields(self):
        key = make_key({})
        self.assertEqual(len(key), 4)


class TestCanSend(unittest.TestCase):
    """Правило: нет записи → шлём. Иначе час прошёл И спред падал."""

    def test_no_record_sends(self):
        self.assertTrue(can_send(None))

    def test_hour_not_passed(self):
        self.assertFalse(can_send(record(hours_ago=0.5, dropped=True)))

    def test_hour_passed_but_no_drop(self):
        """Ключевой случай: связка висит с высоким спредом
        уже несколько часов — повторно слать не нужно."""
        self.assertFalse(can_send(record(hours_ago=5, dropped=False)))

    def test_hour_passed_and_dropped(self):
        self.assertTrue(can_send(record(hours_ago=2, dropped=True)))

    def test_exactly_at_threshold(self):
        just_over = config.COOLDOWN_HOURS + 0.01
        self.assertTrue(can_send(record(hours_ago=just_over, dropped=True)))

    def test_naive_datetime_handled(self):
        """В БД TIMESTAMP без таймзоны — падать на этом нельзя."""
        rec = {
            'sent_at': datetime.now() - timedelta(hours=3),  # без таймзоны
            'last_spread': 3.0,
            'dropped_since': True,
        }
        self.assertTrue(can_send(rec))

    def test_missing_dropped_field(self):
        rec = {'sent_at': datetime.now(timezone.utc) - timedelta(hours=5),
               'last_spread': 1.0}
        self.assertFalse(can_send(rec))


class TestRegressionB1(unittest.TestCase):
    """Регрессия бага Б1.

    Старое условие:
        spread_dropped = last_spread > 0.01 and current_spread < 0.01

    current_spread — это спред возможности, только что прошедшей
    фильтр min_spread. Он не может быть меньше 0.01, поэтому
    условие не выполнялось никогда, и каждая связка получала
    уведомление ровно один раз за всё время.
    """

    def test_old_condition_was_impossible(self):
        """Демонстрация: при любом реальном спреде старое условие ложно."""
        for last_spread in (0.5, 3.0, 12.0):
            for current_spread in (1.0, 3.5, 8.0):   # прошли фильтр
                old_result = last_spread > 0.01 and current_spread < 0.01
                self.assertFalse(old_result)

    def test_new_logic_allows_repeat(self):
        """Новая логика: связка пропадала → флаг взведён → шлём."""
        rec = record(hours_ago=2, dropped=True)
        self.assertTrue(can_send(rec))

    def test_new_logic_still_blocks_spam(self):
        """Но пока связка висит непрерывно — молчим."""
        rec = record(hours_ago=10, dropped=False)
        self.assertFalse(can_send(rec))


class TestRecordSent(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.db = FakeDB(db_module).install()

    async def asyncTearDown(self):
        self.db.uninstall()

    async def test_writes_normalized_strategy(self):
        """В таблицу должна попасть 'ff', а не 'futures_futures' —
        иначе последующие UPDATE не найдут строку (баг Б2)."""
        opp = make_opp(strategy='futures_futures')
        await record_sent(1, opp, 3.5)

        keys = list(self.db.cooldowns.keys())
        self.assertEqual(len(keys), 1)
        self.assertEqual(keys[0][4], 'ff')

    async def test_resets_dropped_flag(self):
        opp = make_opp()
        key = make_key(opp)
        self.db.add_cooldown(1, key, hours_ago=5, dropped=True)

        await record_sent(1, opp, 4.0)

        rec = self.db.cooldowns[(1, *key)]
        self.assertFalse(rec['dropped_since'])
        self.assertEqual(rec['last_spread'], 4.0)


if __name__ == '__main__':
    unittest.main()