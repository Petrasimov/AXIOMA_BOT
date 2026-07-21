"""
main.py — AXIOMA Notification Service

Точка входа. Основной цикл каждые 60 секунд:
  1. Читает актуальные возможности из AnalysisResults
  2. Получает пользователей с включёнными уведомлениями
  3. Применяет фильтры каждого пользователя
  4. Определяет новые возможности (сравнение с предыдущим циклом)
  5. Проверяет cooldown (антиспам)
  6. Отправляет Telegram сообщение
  7. Записывает cooldown

Запуск:
  python3 main.py

Systemd:
  sudo systemctl start axioma-notifications
"""

import asyncio
import logging
import signal
import sys
from datetime import datetime

import config
import db
import cooldown as cooldown_module
import telegram
from filters import (
    apply_user_filters,
    find_new_opportunities,
    make_opp_id,
)

# ─── Логирование ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)

# ─── Состояние в памяти ───────────────────────────────────────────────────────

# Хранит id возможностей из предыдущего цикла для каждого пользователя
# { user_id: set(opp_id) }
previous_opps: dict[int, set[str]] = {}

# Флаг для graceful shutdown
_running = True


# ─── Основная логика цикла ───────────────────────────────────────────────────

async def check_and_notify():
    """Один цикл проверки и отправки уведомлений."""
    cycle_start = datetime.now()
    logger.info('═══ Цикл проверки уведомлений ═══')

    # 1. Получаем текущие возможности из AnalysisResults
    try:
        current_opps = await db.get_current_opportunities()
    except Exception as e:
        logger.error(f'Ошибка получения возможностей из БД: {e}')
        return

    if not current_opps:
        logger.info('Нет актуальных возможностей в AnalysisResults')
        # Обнуляем для всех пользователей — все монеты "пропали"
        previous_opps.clear()
        return

    logger.info(f'Возможностей в текущем цикле: {len(current_opps)}')

    # 2. Получаем пользователей с включёнными уведомлениями
    try:
        users = await db.get_active_notification_users()
    except Exception as e:
        logger.error(f'Ошибка получения пользователей из БД: {e}')
        return

    if not users:
        logger.info('Нет пользователей с включёнными уведомлениями')
        return

    logger.info(f'Пользователей с уведомлениями: {len(users)}')

    # 3. Обрабатываем каждого пользователя
    total_sent = 0

    for user in users:
        user_id     = user['user_id']
        trade_amount = float(user.get('trade_amount') or 100)

        try:
            sent = await process_user(user_id, user, current_opps, trade_amount)
            total_sent += sent
        except Exception as e:
            logger.error(f'Ошибка обработки пользователя {user_id}: {e}')

    # 4. Обновляем состояние предыдущего цикла
    # Сохраняем глобальный набор id всех возможностей (без учёта пользователя)
    # — используется как база для сравнения в следующем цикле
    all_current_ids = {make_opp_id(opp) for opp in current_opps}

    # Обновляем для каждого пользователя отдельно (после apply_filters)
    # Это уже сделано в process_user — previous_opps обновляется там

    elapsed = (datetime.now() - cycle_start).total_seconds()
    logger.info(f'Цикл завершён за {elapsed:.1f}с | отправлено уведомлений: {total_sent}')


async def process_user(
    user_id: int,
    user: dict,
    all_opps: list[dict],
    trade_amount: float,
) -> int:
    """Обрабатывает одного пользователя. Возвращает количество отправленных уведомлений."""
    sent_count = 0

    # Применяем фильтры пользователя
    filtered = apply_user_filters(all_opps, user)

    if not filtered:
        logger.debug(f'[{user_id}] После фильтрации — 0 возможностей')
        previous_opps[user_id] = set()
        return 0

    logger.debug(f'[{user_id}] После фильтрации: {len(filtered)} возможностей')

    # Определяем новые — те которых не было в прошлом цикле для этого пользователя
    prev_ids = previous_opps.get(user_id, set())
    new_opps = find_new_opportunities(filtered, prev_ids)

    logger.debug(f'[{user_id}] Новых возможностей: {len(new_opps)}')

    # Проверяем cooldown и отправляем
    for opp in new_opps:
        spread = float(opp.get('spread') or 0)

        try:
            can = await cooldown_module.can_send(user_id, opp, spread)
        except Exception as e:
            logger.error(f'[{user_id}] Ошибка проверки cooldown для {opp["symbol"]}: {e}')
            continue

        if not can:
            continue

        # Отправляем уведомление
        success = await telegram.send_notification(user_id, opp, trade_amount)

        if success:
            # Записываем cooldown
            try:
                await cooldown_module.record_sent(user_id, opp, spread)
            except Exception as e:
                logger.error(f'[{user_id}] Ошибка записи cooldown для {opp["symbol"]}: {e}')

            sent_count += 1

    # Обновляем предыдущие возможности для следующего цикла
    # Обновляем LastSpread для существующих cooldowns которые не попали в new_opps
    current_ids = {make_opp_id(opp) for opp in filtered}
    previous_opps[user_id] = current_ids

    # Обновляем LastSpread для возможностей которые были раньше но не отправлялись сейчас
    # (нужно для отслеживания падения спреда)
    for opp in filtered:
        opp_id = make_opp_id(opp)
        if opp_id in prev_ids:  # была в прошлом цикле
            spread = float(opp.get('spread') or 0)
            try:
                await db.update_last_spread(
                    user_id,
                    opp['symbol'],
                    opp['bid_ex'],
                    opp['ask_ex'],
                    opp['strategy'],
                    spread,
                )
            except Exception:
                pass  # не критично

    return sent_count


# ─── Запуск ──────────────────────────────────────────────────────────────────

async def run():
    """Основной цикл сервиса."""
    global _running

    logger.info('AXIOMA Notification Service запускается...')

    # Валидация конфига
    try:
        config.validate()
    except ValueError as e:
        logger.critical(f'Ошибка конфигурации: {e}')
        sys.exit(1)

    # Проверяем бота
    bot_ok = await telegram.check_bot()
    if not bot_ok:
        logger.critical('Telegram бот недоступен — проверьте BOT_TOKEN в .env')
        sys.exit(1)

    # Подключаемся к БД (создаёт таблицу NotificationCooldowns)
    try:
        await db.init(config.DATABASE_URL)
    except Exception as e:
        logger.critical(f'Ошибка подключения к БД: {e}')
        sys.exit(1)

    logger.info(
        f'Сервис запущен | '
        f'интервал={config.CHECK_INTERVAL}с | '
        f'cooldown={config.COOLDOWN_HOURS}ч'
    )

    # Graceful shutdown по SIGINT / SIGTERM
    loop = asyncio.get_running_loop()

    def _shutdown(sig):
        logger.info(f'Получен сигнал {sig.name}, завершаем работу...')
        global _running
        _running = False

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown, sig)

    # Основной цикл
    try:
        while _running:
            await check_and_notify()
            if _running:
                logger.info(f'Следующая проверка через {config.CHECK_INTERVAL}с')
                await asyncio.sleep(config.CHECK_INTERVAL)
    finally:
        await db.close()
        logger.info('Сервис остановлен')


if __name__ == '__main__':
    asyncio.run(run())