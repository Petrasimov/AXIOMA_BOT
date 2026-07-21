"""
cooldown.py — Логика антиспама

Правила отправки уведомления:
  - Если запись в NotificationCooldowns не существует → отправляем
  - Если существует:
      * Прошёл ли час с SentAt?      → НЕТ = не отправляем
      * Падал ли спред до 0 с тех пор? → НЕТ = не отправляем
      * Оба условия выполнены          → отправляем

После каждого цикла обновляем LastSpread чтобы отслеживать падение спреда.
"""

import logging
from datetime import datetime, timezone

import db
from config import COOLDOWN_HOURS
from filters import normalize_strategy

logger = logging.getLogger(__name__)


async def can_send(user_id: int, opp: dict, current_spread: float) -> bool:
    """Проверяет можно ли отправить уведомление по данной возможности.

    Возвращает True если уведомление нужно отправить.
    Как побочный эффект — обновляет LastSpread в cooldown записи.
    """
    symbol   = opp['symbol']
    bid_ex   = opp['bid_ex']
    ask_ex   = opp['ask_ex']
    strategy = normalize_strategy(opp['strategy'])

    record = await db.get_cooldown(user_id, symbol, bid_ex, ask_ex, strategy)

    if not record:
        # Никогда не отправляли → можно
        logger.debug(f'[COOLDOWN] {user_id} {symbol} — новая запись, отправляем')
        return True

    # Считаем сколько часов прошло
    sent_at = record['SentAt']
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    hours_passed = (now - sent_at).total_seconds() / 3600

    last_spread = float(record['LastSpread'])

    # Обновляем LastSpread независимо от решения
    await db.update_last_spread(user_id, symbol, bid_ex, ask_ex, strategy, current_spread)

    if hours_passed < COOLDOWN_HOURS:
        logger.debug(
            f'[COOLDOWN] {user_id} {symbol} — '
            f'час не прошёл ({hours_passed:.1f}ч < {COOLDOWN_HOURS}ч), пропускаем'
        )
        return False

    # Час прошёл — проверяем падал ли спред
    spread_dropped = last_spread > 0.01 and current_spread < 0.01
    if not spread_dropped:
        logger.debug(
            f'[COOLDOWN] {user_id} {symbol} — '
            f'час прошёл но спред не падал (last={last_spread:.2f}% cur={current_spread:.2f}%), пропускаем'
        )
        return False

    logger.debug(
        f'[COOLDOWN] {user_id} {symbol} — '
        f'час прошёл И спред падал, отправляем'
    )
    return True


async def record_sent(user_id: int, opp: dict, spread: float):
    """Записывает факт отправки уведомления в NotificationCooldowns."""
    await db.upsert_cooldown(
        user_id=user_id,
        symbol=opp['symbol'],
        bid_ex=opp['bid_ex'],
        ask_ex=opp['ask_ex'],
        strategy=normalize_strategy(opp['strategy']),
        spread=spread,
    )