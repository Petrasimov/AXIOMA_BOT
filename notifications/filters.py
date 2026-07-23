"""
filters.py — Применение фильтров пользователя к списку возможностей

Фильтры идентичны тем что использует фронтенд:
  - стратегия (FF / SF)
  - минимальный спред
  - разрешённые биржи
"""

import logging

logger = logging.getLogger(__name__)


def normalize_strategy(strategy: str) -> str:
    """Нормализует стратегию к 'ff' или 'sf'."""
    s = strategy.lower()
    if s in ('futures_futures', 'ff'):
        return 'ff'
    if s in ('spot_futures', 'sf'):
        return 'sf'
    return 'sf'  # fallback


def get_exchange_id(exchange_str: str) -> str:
    """Извлекает id биржи из строки вида 'binance_futures' → 'binance'."""
    return exchange_str.split('_')[0].lower()


def apply_user_filters(opportunities: list[dict], user: dict) -> list[dict]:
    """Фильтрует возможности по настройкам пользователя.

    Аргументы:
        opportunities — список возможностей из AnalysisResults
        user — строка из get_active_notification_users()

    Возвращает отфильтрованный список.
    """
    min_spread    = float(user.get('min_spread') or 0)
    strategy_ff   = bool(user.get('strategy_ff', True))
    strategy_sf   = bool(user.get('strategy_sf', True))
    allowed_ex    = set(user.get('exchanges') or [])

    result = []

    for opp in opportunities:
        spread   = float(opp.get('spread') or 0)
        strategy = normalize_strategy(opp.get('strategy', ''))
        bid_ex   = get_exchange_id(opp.get('bid_ex', ''))
        ask_ex   = get_exchange_id(opp.get('ask_ex', ''))

        # Фильтр стратегии
        if strategy == 'ff' and not strategy_ff:
            continue
        if strategy == 'sf' and not strategy_sf:
            continue

        # Фильтр минимального спреда
        if spread < min_spread:
            continue

        # Фильтр бирж — обе биржи должны быть в списке разрешённых
        if allowed_ex and (bid_ex not in allowed_ex or ask_ex not in allowed_ex):
            continue

        result.append(opp)

    return result


def make_opp_id(opp: dict) -> str:
    """Создаёт стабильный идентификатор возможности.
    Тот же формат что использует фронтенд: symbol_bidEx_askEx_strategy
    """
    symbol   = opp.get('symbol', '')
    bid_ex   = opp.get('bid_ex', '')
    ask_ex   = opp.get('ask_ex', '')
    strategy = normalize_strategy(opp.get('strategy', ''))
    return f"{symbol}_{bid_ex}_{ask_ex}_{strategy}"


def find_new_opportunities(
    current: list[dict],
    previous_ids: set[str],
) -> list[dict]:
    """Возвращает возможности которых не было в предыдущем цикле."""
    new = []
    for opp in current:
        opp_id = make_opp_id(opp)
        if opp_id not in previous_ids:
            new.append(opp)
    return new