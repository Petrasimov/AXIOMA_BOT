"""
test_filters.py — Фильтры пользователя

Каждая функция notifications/filters.py, включая граничные случаи:
пустые списки бирж, неизвестные стратегии, отсутствующие поля.
"""

import unittest

from notifications.filters import (
    apply_user_filters,
    find_new_opportunities,
    get_exchange_id,
    make_opp_id,
    normalize_strategy,
)
from tests.helpers import make_opp


class TestNormalizeStrategy(unittest.TestCase):

    def test_futures_futures(self):
        self.assertEqual(normalize_strategy('futures_futures'), 'ff')
        self.assertEqual(normalize_strategy('ff'), 'ff')

    def test_spot_futures(self):
        self.assertEqual(normalize_strategy('spot_futures'), 'sf')
        self.assertEqual(normalize_strategy('sf'), 'sf')

    def test_case_insensitive(self):
        self.assertEqual(normalize_strategy('FUTURES_FUTURES'), 'ff')
        self.assertEqual(normalize_strategy('Ff'), 'ff')

    def test_unknown_falls_back_to_sf(self):
        self.assertEqual(normalize_strategy('что-то'), 'sf')
        self.assertEqual(normalize_strategy(''), 'sf')

    def test_idempotent(self):
        """Повторная нормализация не должна менять результат —
        на этом держится совпадение ключей cooldown."""
        for s in ('futures_futures', 'ff', 'spot_futures', 'sf'):
            once = normalize_strategy(s)
            self.assertEqual(normalize_strategy(once), once)


class TestGetExchangeId(unittest.TestCase):

    def test_strips_market(self):
        self.assertEqual(get_exchange_id('binance_futures'), 'binance')
        self.assertEqual(get_exchange_id('gate_spot'), 'gate')

    def test_without_market(self):
        self.assertEqual(get_exchange_id('bybit'), 'bybit')

    def test_lowercases(self):
        self.assertEqual(get_exchange_id('KuCoin_Futures'), 'kucoin')


class TestApplyUserFilters(unittest.TestCase):

    def setUp(self):
        self.user = {'min_spread': 2.0, 'strategy_ff': True,
                     'strategy_sf': True, 'exchanges': []}

    def test_min_spread_cuts_below(self):
        opps = [make_opp(spread=1.0), make_opp(spread=2.0), make_opp(spread=5.0)]
        result = apply_user_filters(opps, self.user)
        self.assertEqual(len(result), 2)
        self.assertTrue(all(o['spread'] >= 2.0 for o in result))

    def test_strategy_ff_disabled(self):
        self.user['strategy_ff'] = False
        opps = [make_opp(strategy='futures_futures', spread=5),
                make_opp(strategy='spot_futures', spread=5)]
        result = apply_user_filters(opps, self.user)
        self.assertEqual(len(result), 1)
        self.assertEqual(normalize_strategy(result[0]['strategy']), 'sf')

    def test_strategy_sf_disabled(self):
        self.user['strategy_sf'] = False
        opps = [make_opp(strategy='futures_futures', spread=5),
                make_opp(strategy='spot_futures', spread=5)]
        result = apply_user_filters(opps, self.user)
        self.assertEqual(len(result), 1)
        self.assertEqual(normalize_strategy(result[0]['strategy']), 'ff')

    def test_both_strategies_disabled(self):
        self.user['strategy_ff'] = False
        self.user['strategy_sf'] = False
        result = apply_user_filters([make_opp(spread=9)], self.user)
        self.assertEqual(result, [])

    def test_exchange_whitelist_requires_both_sides(self):
        """Обе биржи должны быть разрешены, не одна."""
        self.user['exchanges'] = ['kucoin']
        opps = [make_opp(bid_ex='kucoin_futures', ask_ex='gate_futures', spread=5)]
        self.assertEqual(apply_user_filters(opps, self.user), [])

        self.user['exchanges'] = ['kucoin', 'gate']
        self.assertEqual(len(apply_user_filters(opps, self.user)), 1)

    def test_empty_exchanges_allows_all(self):
        """Пустой список бирж означает «все разрешены»."""
        self.user['exchanges'] = []
        opps = [make_opp(bid_ex='mexc_futures', ask_ex='bingx_futures', spread=5)]
        self.assertEqual(len(apply_user_filters(opps, self.user)), 1)

    def test_missing_fields_do_not_crash(self):
        result = apply_user_filters([{}], {'min_spread': 0})
        self.assertIsInstance(result, list)

    def test_none_min_spread(self):
        result = apply_user_filters([make_opp(spread=1)], {'min_spread': None})
        self.assertEqual(len(result), 1)


class TestMakeOppId(unittest.TestCase):

    def test_stable_across_strategy_spelling(self):
        """Разное написание стратегии должно давать одинаковый id."""
        a = make_opp_id(make_opp(strategy='futures_futures'))
        b = make_opp_id(make_opp(strategy='ff'))
        self.assertEqual(a, b)

    def test_different_pairs_differ(self):
        a = make_opp_id(make_opp(symbol='BTCUSDT'))
        b = make_opp_id(make_opp(symbol='ETHUSDT'))
        self.assertNotEqual(a, b)

    def test_direction_matters(self):
        """Смена сторон bid/ask — это другая возможность."""
        a = make_opp_id(make_opp(bid_ex='a_futures', ask_ex='b_futures'))
        b = make_opp_id(make_opp(bid_ex='b_futures', ask_ex='a_futures'))
        self.assertNotEqual(a, b)


class TestFindNewOpportunities(unittest.TestCase):

    def test_all_new_when_previous_empty(self):
        opps = [make_opp(symbol='A'), make_opp(symbol='B')]
        self.assertEqual(len(find_new_opportunities(opps, set())), 2)

    def test_filters_out_known(self):
        opps = [make_opp(symbol='A'), make_opp(symbol='B')]
        known = {make_opp_id(opps[0])}
        result = find_new_opportunities(opps, known)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['symbol'], 'B')

    def test_nothing_new(self):
        opps = [make_opp(symbol='A')]
        known = {make_opp_id(opps[0])}
        self.assertEqual(find_new_opportunities(opps, known), [])


if __name__ == '__main__':
    unittest.main()