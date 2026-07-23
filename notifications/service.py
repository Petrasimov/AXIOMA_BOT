"""
service.py — Цикл рассылки уведомлений

Цикл каждые CHECK_INTERVAL секунд:
  1. Читает актуальные возможности из AnalysisResults (без дублей)
  2. Получает пользователей с включёнными уведомлениями
  3. Для каждого:
       - применяет его фильтры
       - помечает пропавшие связки как "спред падал"
       - обновляет LastSpread у активных
       - отправляет то, что прошло cooldown
       - не больше MAX_PER_CYCLE уведомлений за цикл

Порядок шагов важен: сначала отмечаем падения, потом решаем что
отправлять. Иначе связка, вернувшаяся после падения, будет
проверена по устаревшему флагу.
"""

import asyncio
import logging
from datetime import datetime, timezone

import config
import db
from bot import api
from notifications import cooldown as cooldown_module
from notifications import sender
from notifications.filters import (
    apply_user_filters,
    find_new_opportunities,
    make_opp_id,
)

logger = logging.getLogger(__name__)

# Хранит id возможностей из предыдущего цикла для каждого пользователя
# { user_id: set(opp_id) }
previous_opps: dict[int, set[str]] = {}

_running = True


def stop():
    """Просит цикл завершиться."""
    global _running
    _running = False


# ─── Один цикл ───────────────────────────────────────────────────────────────

async def check_and_notify():
    """Один цикл проверки и отправки уведомлений."""
    cycle_start = datetime.now()
    logger.info('═══ Цикл проверки уведомлений ═══')

    # 1. Текущие возможности
    try:
        current_opps = await db.get_current_opportunities()
    except Exception as e:
        logger.error(f'Ошибка получения возможностей из БД: {e}')
        return

    # 2. Пользователи с включёнными уведомлениями
    try:
        users = await db.get_active_notification_users()
    except Exception as e:
        logger.error(f'Ошибка получения пользователей из БД: {e}')
        return

    if not users:
        logger.info('Нет пользователей с включёнными уведомлениями')
        return

    if not current_opps:
        logger.info('Нет актуальных возможностей в AnalysisResults')
        # Все связки пропали — это падение спреда для каждого пользователя.
        # Отметить нужно обязательно, иначе вернувшиеся возможности
        # не пройдут проверку и уведомление не уйдёт.
        for user in users:
            try:
                await db.mark_dropped(user['user_id'], [])
            except Exception as e:
                logger.error(f'Ошибка отметки падений для {user["user_id"]}: {e}')
        previous_opps.clear()
        return

    logger.info(
        f'Возможностей: {len(current_opps)} | '
        f'пользователей: {len(users)}'
    )

    # 3. Обрабатываем каждого
    total_sent = 0

    for user in users:
        user_id      = user['user_id']
        trade_amount = float(user.get('trade_amount') or 100)

        try:
            sent = await process_user(user_id, user, current_opps, trade_amount)
            total_sent += sent
        except Exception as e:
            logger.error(f'Ошибка обработки пользователя {user_id}: {e}')

    elapsed = (datetime.now() - cycle_start).total_seconds()
    logger.info(f'Цикл завершён за {elapsed:.1f}с | отправлено уведомлений: {total_sent}')


async def process_user(
    user_id: int,
    user: dict,
    all_opps: list[dict],
    trade_amount: float,
) -> int:
    """Обрабатывает одного пользователя.

    Возвращает количество отправленных уведомлений.
    """
    # Фильтры пользователя
    filtered = apply_user_filters(all_opps, user)

    # Ключи связок которые сейчас активны
    active_keys = [cooldown_module.make_key(opp) for opp in filtered]

    # Помечаем пропавшие как "спред падал".
    # Делаем это ДО решения об отправке — иначе вернувшаяся связка
    # будет проверена по флагу, который ещё не выставлен.
    try:
        await db.mark_dropped(user_id, active_keys)
    except Exception as e:
        logger.error(f'[{user_id}] Ошибка отметки падений: {e}')

    if not filtered:
        logger.debug(f'[{user_id}] После фильтрации — 0 возможностей')
        previous_opps[user_id] = set()
        return 0

    logger.debug(f'[{user_id}] После фильтрации: {len(filtered)} возможностей')

    # Все cooldown записи пользователя одним запросом
    try:
        cooldowns = await db.get_user_cooldowns(user_id)
    except Exception as e:
        logger.error(f'[{user_id}] Ошибка получения cooldowns: {e}')
        return 0

    # Новые для этого пользователя
    prev_ids = previous_opps.get(user_id, set())
    new_opps = find_new_opportunities(filtered, prev_ids)

    logger.debug(f'[{user_id}] Новых возможностей: {len(new_opps)}')

    sent_count = 0
    blocked = False

    for opp in new_opps:
        # Лимит на цикл: у нового пользователя cooldown-таблица пуста,
        # и без лимита улетело бы столько сообщений, сколько связок
        # прошло фильтр — сотни. Telegram отрубит на ~30/сек.
        if sent_count >= config.MAX_NOTIFICATIONS_PER_CYCLE:
            logger.info(
                f'[{user_id}] Достигнут лимит {config.MAX_NOTIFICATIONS_PER_CYCLE} '
                f'уведомлений за цикл, остальные — в следующем'
            )
            break

        key    = cooldown_module.make_key(opp)
        spread = float(opp.get('spread') or 0)

        if not cooldown_module.can_send(cooldowns.get(key), user_id, key[0]):
            continue

        try:
            success = await sender.send_notification(user_id, opp, trade_amount)
        except api.BotBlocked:
            # Пользователь заблокировал бота — гасим уведомления,
            # иначе бот будет долбиться в него каждый цикл вечно
            logger.info(f'[{user_id}] Бот заблокирован, выключаем уведомления')
            try:
                await db.disable_notifications(user_id)
            except Exception as e:
                logger.error(f'[{user_id}] Не удалось выключить уведомления: {e}')
            blocked = True
            break
        except Exception as e:
            logger.warning(f'[{user_id}] Отправка не удалась: {e}')
            continue

        if success:
            try:
                await cooldown_module.record_sent(user_id, opp, spread)
                # Локальная копия — чтобы в этом же цикле связка
                # не прошла проверку повторно
                cooldowns[key] = {
                    'sent_at':       datetime.now(timezone.utc),
                    'last_spread':   spread,
                    'dropped_since': False,
                }
            except Exception as e:
                logger.error(f'[{user_id}] Ошибка записи cooldown для {key[0]}: {e}')

            sent_count += 1

    if blocked:
        previous_opps.pop(user_id, None)
        return sent_count

    # Обновляем LastSpread пачкой у связок, которые были и в прошлом цикле
    updates = [
        (*cooldown_module.make_key(opp), float(opp.get('spread') or 0))
        for opp in filtered
        if make_opp_id(opp) in prev_ids
    ]
    if updates:
        try:
            await db.update_spreads(user_id, updates)
        except Exception as e:
            logger.debug(f'[{user_id}] Ошибка обновления спредов: {e}')

    previous_opps[user_id] = {make_opp_id(opp) for opp in filtered}

    return sent_count


# ─── Цикл ────────────────────────────────────────────────────────────────────

async def run_loop():
    """Основной цикл сервиса уведомлений."""
    logger.info(
        f'[NOTIFY] Сервис уведомлений запущен | '
        f'интервал={config.CHECK_INTERVAL}с | '
        f'cooldown={config.COOLDOWN_HOURS}ч | '
        f'лимит={config.MAX_NOTIFICATIONS_PER_CYCLE}/цикл'
    )

    while _running:
        try:
            await check_and_notify()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f'[NOTIFY] Неожиданная ошибка в цикле: {e}')

        if not _running:
            break

        logger.info(f'[NOTIFY] Следующая проверка через {config.CHECK_INTERVAL}с')

        # Спим короткими отрезками, чтобы быстро реагировать на остановку
        slept = 0
        while slept < config.CHECK_INTERVAL and _running:
            await asyncio.sleep(1)
            slept += 1

    logger.info('[NOTIFY] Сервис уведомлений остановлен')