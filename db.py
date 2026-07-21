"""
db.py — Все запросы к PostgreSQL

Таблицы которые читаем (C# создаёт):
  "AnalysisResults"  — текущие арбитражные возможности
  "Users"            — пользователи и их доступы
  "UserSettings"     — настройки пользователей (фильтры, ActiveNotifications)

Таблица которую создаём сами:
  "NotificationCooldowns" — антиспам, история отправок
"""

import asyncpg
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Глобальный пул соединений
_pool: asyncpg.Pool | None = None


async def init(database_url: str):
    """Инициализирует пул соединений и создаёт таблицу cooldowns."""
    global _pool
    _pool = await asyncpg.create_pool(
        database_url,
        min_size=2,
        max_size=5,
        command_timeout=30,
    )
    await _create_cooldowns_table()
    logger.info('БД подключена, таблица NotificationCooldowns готова')


async def close():
    """Закрывает пул соединений."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def _create_cooldowns_table():
    """Создаёт таблицу NotificationCooldowns если не существует.
    Python сервис управляет этой таблицей самостоятельно.
    C# миграции её не трогают.
    """
    async with _pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS "NotificationCooldowns" (
                "Id"          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                "UserId"      BIGINT NOT NULL,
                "Symbol"      VARCHAR NOT NULL,
                "BidEx"       VARCHAR NOT NULL,
                "AskEx"       VARCHAR NOT NULL,
                "Strategy"    VARCHAR NOT NULL,
                "SentAt"      TIMESTAMP NOT NULL,
                "LastSpread"  DECIMAL NOT NULL,
                UNIQUE ("UserId", "Symbol", "BidEx", "AskEx", "Strategy")
            )
        """)


# ─── AnalysisResults ─────────────────────────────────────────────────────────

async def get_current_opportunities() -> list[dict]:
    """Возвращает актуальные арбитражные возможности из последних 2 минут."""
    async with _pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT
                ar."Symbol"            AS symbol,
                ar."SellExchange"      AS bid_ex,
                ar."BuyExchange"       AS ask_ex,
                ar."Strategy"          AS strategy,
                ar."ProfitPercentage"  AS spread,
                ar."SnapshotAt"        AS snapshot_at
            FROM "AnalysisResults" ar
            WHERE ar."SnapshotAt" >= NOW() - INTERVAL '2 minutes'
            ORDER BY ar."ProfitPercentage" DESC
        """)
        return [dict(r) for r in rows]


# ─── Users + UserSettings ────────────────────────────────────────────────────

async def get_active_notification_users() -> list[dict]:
    """Возвращает пользователей у которых:
    - есть доступ к сканеру (IsCexCexPaid = true)
    - аккаунт активен (IsActive = true)
    - уведомления включены (ActiveNotifications = true)
    """
    async with _pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT
                u."UserId"          AS user_id,
                us."TradeAmount"    AS trade_amount,
                us."MinSpread"      AS min_spread,
                us."StrategyFf"     AS strategy_ff,
                us."StrategySf"     AS strategy_sf,
                us."Exchanges"      AS exchanges
            FROM "Users" u
            JOIN "UserSettings" us ON us."UserId" = u."UserId"
            WHERE u."IsCexCexPaid" = true
              AND u."IsActive" = true
              AND us."ActiveNotifications" = true
        """)
        result = []
        for r in rows:
            row = dict(r)
            # Exchanges хранится как JSON строка или массив — нормализуем
            exchanges = row.get('exchanges') or []
            if isinstance(exchanges, str):
                import json
                try:
                    exchanges = json.loads(exchanges)
                except Exception:
                    exchanges = []
            row['exchanges'] = [e.lower() for e in exchanges]
            result.append(row)
        return result


# ─── NotificationCooldowns ───────────────────────────────────────────────────

async def get_cooldown(user_id: int, symbol: str, bid_ex: str,
                        ask_ex: str, strategy: str) -> dict | None:
    """Возвращает запись cooldown или None если её нет."""
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT "Id", "SentAt", "LastSpread"
            FROM "NotificationCooldowns"
            WHERE "UserId"   = $1
              AND "Symbol"   = $2
              AND "BidEx"    = $3
              AND "AskEx"    = $4
              AND "Strategy" = $5
        """, user_id, symbol, bid_ex, ask_ex, strategy)
        return dict(row) if row else None


async def upsert_cooldown(user_id: int, symbol: str, bid_ex: str,
                           ask_ex: str, strategy: str, spread: float):
    """Создаёт или обновляет запись cooldown после отправки уведомления."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with _pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO "NotificationCooldowns"
                ("UserId", "Symbol", "BidEx", "AskEx", "Strategy", "SentAt", "LastSpread")
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT ("UserId", "Symbol", "BidEx", "AskEx", "Strategy")
            DO UPDATE SET
                "SentAt"     = EXCLUDED."SentAt",
                "LastSpread" = EXCLUDED."LastSpread"
        """, user_id, symbol, bid_ex, ask_ex, strategy, now, spread)


async def update_last_spread(user_id: int, symbol: str, bid_ex: str,
                              ask_ex: str, strategy: str, spread: float):
    """Обновляет LastSpread в cooldown записи для отслеживания падения спреда."""
    async with _pool.acquire() as conn:
        await conn.execute("""
            UPDATE "NotificationCooldowns"
            SET "LastSpread" = $6
            WHERE "UserId"   = $1
              AND "Symbol"   = $2
              AND "BidEx"    = $3
              AND "AskEx"    = $4
              AND "Strategy" = $5
        """, user_id, symbol, bid_ex, ask_ex, strategy, spread)