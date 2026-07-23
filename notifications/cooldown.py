"""
cooldown.py — Логика антиспама

Правило отправки:
  - записи нет                        → отправляем (первый раз)
  - есть, но час не прошёл            → молчим
  - час прошёл, но спред не падал     → молчим
  - час прошёл И спред падал          → отправляем

═══ Почему нужен флаг DroppedSince ═══

Раньше падение спреда пытались определить так:

    spread_dropped = last_spread > 0.01 and current_spread < 0.01

Условие невыполнимо в принципе. current_spread — это спред
возможности, которая ТОЛЬКО ЧТО прошла фильтр min_spread
пользователя. Она физически не может быть меньше 0.01.
В результате каждая связка получала уведомление ровно один раз
за всё время и больше никогда.

Настоящая причина глубже: падение спреда — это событие МЕЖДУ
отправками. В момент проверки его уже не видно ни по одному
текущему значению. Его нужно зафиксировать тогда, когда оно
происходит, и запомнить.

Что считается падением: связка пропала из отфильтрованного списка
пользователя. Либо спред опустился ниже его порога, либо пара
вообще исчезла из AnalysisResults. Оба случая означают одно —
возможности больше нет, и когда она вернётся, это будет уже
новая возможность, о которой стоит сообщить.

Отметку ставит db.mark_dropped() на каждом цикле, сбрасывает
db.upsert_cooldown() в момент отправки.
"""

import logging
from datetime import datetime, timezone

import db
from config import COOLDOWN_HOURS
from notifications.filters import normalize_strategy

logger = logging.getLogger(__name__)


def make_key(opp: dict) -> tuple:
    """Ключ связки для cooldown записи.

    Стратегия нормализуется — в таблице всегда лежит 'ff' или 'sf'.
    """
    return (
        opp.get('symbol', ''),
        opp.get('bid_ex', ''),
        opp.get('ask_ex', ''),
        normalize_strategy(opp.get('strategy', '')),
    )


def can_send(record: dict | None, user_id: int = 0, symbol: str = '') -> bool:
    """Решает, можно ли отправить уведомление.

    Чистая функция без походов в БД — запись передаётся готовой
    из предзагруженного словаря.

    record — из db.get_user_cooldowns(), либо None если связка новая.
    """
    if not record:
        logger.debug(f'[COOLDOWN] {user_id} {symbol} — новая связка, отправляем')
        return True

    sent_at = record['sent_at']
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=timezone.utc)

    hours_passed = (datetime.now(timezone.utc) - sent_at).total_seconds() / 3600

    if hours_passed < COOLDOWN_HOURS:
        logger.debug(
            f'[COOLDOWN] {user_id} {symbol} — час не прошёл '
            f'({hours_passed:.1f}ч < {COOLDOWN_HOURS}ч), пропускаем'
        )
        return False

    if not record.get('dropped_since'):
        logger.debug(
            f'[COOLDOWN] {user_id} {symbol} — час прошёл, '
            f'но спред с тех пор не падал, пропускаем'
        )
        return False

    logger.debug(
        f'[COOLDOWN] {user_id} {symbol} — час прошёл И спред падал, отправляем'
    )
    return True


async def record_sent(user_id: int, opp: dict, spread: float):
    """Записывает факт отправки. Сбрасывает DroppedSince."""
    symbol, bid_ex, ask_ex, strategy = make_key(opp)
    await db.upsert_cooldown(
        user_id=user_id,
        symbol=symbol,
        bid_ex=bid_ex,
        ask_ex=ask_ex,
        strategy=strategy,
        spread=spread,
    )