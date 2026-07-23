"""
db.py — Все запросы к PostgreSQL

Таблицы которые читаем (C# создаёт и владеет схемой):
  "AnalysisResults"  — текущие арбитражные возможности
  "Users"            — пользователи и их доступы
  "UserSettings"     — настройки пользователей (фильтры, ActiveNotifications)

Таблица которой владеем сами:
  "NotificationCooldowns" — антиспам, история отправок.
  Создаётся ботом при старте, C# миграции её не трогают,
  колонки в неё добавлять можно свободно.
"""

import asyncpg
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Глобальный пул соединений
_pool: asyncpg.Pool | None = None

# Есть ли в Users колонка с датой окончания подписки.
# Определяется при старте: колонку добавляет C#, и пока её нет,
# бот выставляет только IsCexCexPaid без срока действия.
_has_paid_until: bool = False


async def init(database_url: str):
    """Инициализирует пул соединений и готовит таблицу cooldowns."""
    global _pool
    _pool = await asyncpg.create_pool(
        database_url,
        min_size=2,
        max_size=5,
        command_timeout=30,
    )
    await _create_cooldowns_table()
    await _detect_paid_until()
    logger.info('БД подключена, таблица NotificationCooldowns готова')


async def close():
    """Закрывает пул соединений."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def _create_cooldowns_table():
    """Создаёт таблицу NotificationCooldowns если не существует.

    DroppedSince — падал ли спред по этой связке с момента последней
    отправки. Без этого флага повторные уведомления невозможны:
    факт падения происходит МЕЖДУ отправками, и в момент проверки
    его уже никак не определить по текущим данным.

    ALTER отдельно — на проде таблица уже существует без этой колонки.
    """
    async with _pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS "NotificationCooldowns" (
                "Id"           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                "UserId"       BIGINT NOT NULL,
                "Symbol"       VARCHAR NOT NULL,
                "BidEx"        VARCHAR NOT NULL,
                "AskEx"        VARCHAR NOT NULL,
                "Strategy"     VARCHAR NOT NULL,
                "SentAt"       TIMESTAMP NOT NULL,
                "LastSpread"   DECIMAL NOT NULL,
                "DroppedSince" BOOLEAN NOT NULL DEFAULT false,
                UNIQUE ("UserId", "Symbol", "BidEx", "AskEx", "Strategy")
            )
        """)
        await conn.execute("""
            ALTER TABLE "NotificationCooldowns"
            ADD COLUMN IF NOT EXISTS "DroppedSince" BOOLEAN NOT NULL DEFAULT false
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS "IX_NotificationCooldowns_UserId"
            ON "NotificationCooldowns" ("UserId")
        """)


async def _detect_paid_until():
    """Проверяет, есть ли в Users колонка PaidUntil.

    Колонка нужна чтобы закрывать доступ по истечении месяца:
    булев флаг IsCexCexPaid не помнит, когда его поставили.
    Добавляет её C# своей миграцией — до тех пор бот работает
    без срока действия и честно об этом предупреждает.
    """
    global _has_paid_until
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'Users' AND column_name = 'PaidUntil'
        """)
    _has_paid_until = row is not None

    if _has_paid_until:
        logger.info('[DB] Колонка Users.PaidUntil найдена — срок подписки учитывается')
    else:
        logger.warning(
            '[DB] Колонки Users.PaidUntil нет — подписка будет выдаваться '
            'без срока действия. Закрывать доступ по истечении месяца нечем.'
        )


async def grant_access(user_id: int):
    """Открывает доступ к сканеру после оплаты.

    Если колонка PaidUntil есть — продлевает на месяц от большей из
    двух дат: текущей или уже оплаченной. Так продление не сжигает
    остаток действующей подписки.

    Возвращает дату окончания или None если колонки нет.
    """
    async with _pool.acquire() as conn:
        if _has_paid_until:
            row = await conn.fetchrow("""
                UPDATE "Users"
                SET "IsCexCexPaid" = true,
                    "PaidUntil" = GREATEST(
                        COALESCE("PaidUntil", NOW()), NOW()
                    ) + INTERVAL '1 month'
                WHERE "UserId" = $1
                RETURNING "PaidUntil"
            """, user_id)
            return row['PaidUntil'] if row else None

        await conn.execute("""
            UPDATE "Users"
            SET "IsCexCexPaid" = true
            WHERE "UserId" = $1
        """, user_id)
        return None


# ─── AnalysisResults ─────────────────────────────────────────────────────────

async def get_current_opportunities() -> list[dict]:
    """Возвращает актуальные арбитражные возможности из последних 2 минут.

    DISTINCT ON убирает дубли: если C# успел записать несколько
    снапшотов за окно, одна и та же связка вернулась бы несколько раз.
    Берём самый свежий снапшот по каждой связке.
    """
    async with _pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT DISTINCT ON (
                ar."Symbol", ar."SellExchange", ar."BuyExchange", ar."Strategy"
            )
                ar."Symbol"            AS symbol,
                ar."SellExchange"      AS bid_ex,
                ar."BuyExchange"       AS ask_ex,
                ar."Strategy"          AS strategy,
                ar."ProfitPercentage"  AS spread,
                ar."SnapshotAt"        AS snapshot_at
            FROM "AnalysisResults" ar
            WHERE ar."SnapshotAt" >= NOW() - INTERVAL '2 minutes'
            ORDER BY
                ar."Symbol", ar."SellExchange", ar."BuyExchange", ar."Strategy",
                ar."SnapshotAt" DESC
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


async def get_user_state(user_id: int) -> dict | None:
    """Состояние пользователя для меню бота.

    None — пользователь не зарегистрирован на сайте.
    notifications_on может быть None если строки в UserSettings нет.
    """
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT
                u."UserId"                AS user_id,
                u."IsCexCexPaid"          AS is_paid,
                u."IsActive"              AS is_active,
                us."ActiveNotifications"  AS notifications_on
            FROM "Users" u
            LEFT JOIN "UserSettings" us ON us."UserId" = u."UserId"
            WHERE u."UserId" = $1
        """, user_id)
        return dict(row) if row else None


async def set_notifications(user_id: int, enabled: bool) -> bool:
    """Включает или выключает уведомления.

    Возвращает False если строки в UserSettings нет — её создаёт C#
    при регистрации, бот такие строки не заводит.
    """
    async with _pool.acquire() as conn:
        result = await conn.execute("""
            UPDATE "UserSettings"
            SET "ActiveNotifications" = $2
            WHERE "UserId" = $1
        """, user_id, enabled)
        # asyncpg возвращает строку вида "UPDATE 1"
        return result.split()[-1] != '0'


async def disable_notifications(user_id: int):
    """Гасит уведомления — когда пользователь заблокировал бота."""
    await set_notifications(user_id, False)
    logger.info(f'[DB] Уведомления выключены для {user_id} (бот заблокирован)')


# ─── NotificationCooldowns ───────────────────────────────────────────────────

async def get_user_cooldowns(user_id: int) -> dict[tuple, dict]:
    """Все cooldown записи пользователя одним запросом.

    Ключ: (symbol, bid_ex, ask_ex, strategy) — стратегия нормализована,
    ровно как её пишет upsert_cooldown.
    """
    async with _pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT "Symbol", "BidEx", "AskEx", "Strategy",
                   "SentAt", "LastSpread", "DroppedSince"
            FROM "NotificationCooldowns"
            WHERE "UserId" = $1
        """, user_id)

    return {
        (r['Symbol'], r['BidEx'], r['AskEx'], r['Strategy']): {
            'sent_at':       r['SentAt'],
            'last_spread':   float(r['LastSpread']),
            'dropped_since': r['DroppedSince'],
        }
        for r in rows
    }


async def upsert_cooldown(user_id: int, symbol: str, bid_ex: str,
                          ask_ex: str, strategy: str, spread: float):
    """Записывает факт отправки уведомления.

    DroppedSince сбрасывается в false — отсчёт "падал ли спред"
    начинается заново от этого момента.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with _pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO "NotificationCooldowns"
                ("UserId", "Symbol", "BidEx", "AskEx", "Strategy",
                 "SentAt", "LastSpread", "DroppedSince")
            VALUES ($1, $2, $3, $4, $5, $6, $7, false)
            ON CONFLICT ("UserId", "Symbol", "BidEx", "AskEx", "Strategy")
            DO UPDATE SET
                "SentAt"       = EXCLUDED."SentAt",
                "LastSpread"   = EXCLUDED."LastSpread",
                "DroppedSince" = false
        """, user_id, symbol, bid_ex, ask_ex, strategy, now, spread)


async def mark_dropped(user_id: int, active_keys: list[tuple]):
    """Помечает DroppedSince = true для связок, которых сейчас нет в выдаче.

    Возможность пропала из отфильтрованного списка — значит спред
    либо упал ниже порога пользователя, либо пара вообще исчезла
    из AnalysisResults. И то и другое означает: спред падал.

    active_keys — связки которые сейчас активны, их не трогаем.
    """
    symbols    = [k[0] for k in active_keys]
    bid_exs    = [k[1] for k in active_keys]
    ask_exs    = [k[2] for k in active_keys]
    strategies = [k[3] for k in active_keys]

    async with _pool.acquire() as conn:
        await conn.execute("""
            UPDATE "NotificationCooldowns" nc
            SET "DroppedSince" = true
            WHERE nc."UserId" = $1
              AND nc."DroppedSince" = false
              AND NOT EXISTS (
                  SELECT 1
                  FROM unnest($2::text[], $3::text[], $4::text[], $5::text[])
                       AS t(sym, bid, ask, strat)
                  WHERE t.sym   = nc."Symbol"
                    AND t.bid   = nc."BidEx"
                    AND t.ask   = nc."AskEx"
                    AND t.strat = nc."Strategy"
              )
        """, user_id, symbols, bid_exs, ask_exs, strategies)


async def update_spreads(user_id: int, items: list[tuple]):
    """Обновляет LastSpread пачкой.

    items — список (symbol, bid_ex, ask_ex, strategy, spread),
    стратегия уже нормализована.
    """
    if not items:
        return

    symbols    = [i[0] for i in items]
    bid_exs    = [i[1] for i in items]
    ask_exs    = [i[2] for i in items]
    strategies = [i[3] for i in items]
    spreads    = [float(i[4]) for i in items]

    async with _pool.acquire() as conn:
        await conn.execute("""
            UPDATE "NotificationCooldowns" nc
            SET "LastSpread" = t.spread
            FROM unnest($2::text[], $3::text[], $4::text[], $5::text[], $6::numeric[])
                 AS t(sym, bid, ask, strat, spread)
            WHERE nc."UserId"   = $1
              AND nc."Symbol"   = t.sym
              AND nc."BidEx"    = t.bid
              AND nc."AskEx"    = t.ask
              AND nc."Strategy" = t.strat
        """, user_id, symbols, bid_exs, ask_exs, strategies, spreads)