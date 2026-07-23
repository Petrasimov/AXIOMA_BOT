"""
updates.py — Приём входящих апдейтов через getUpdates long-polling

Почему long-polling, а не webhook: не нужен HTTPS эндпоинт и правка
конфига nginx боевого сайта.

⚠️ getUpdates эксклюзивен. Если тем же токеном поллит кто-то ещё —
Telegram вернёт 409 Conflict. Чаще всего это забытый включённым
старый systemd юнит.

⚠️ Telegram хранит недоставленные апдейты 24 часа. Если после
рестарта начать с offset=0, бот заново обработает всё за сутки:
дубли резюме, дубли обращений в поддержку. Поэтому на старте
сбрасываем накопленный хвост — см. _init_offset().
"""

import asyncio
import logging

import config
from bot import api, router

logger = logging.getLogger(__name__)

_running = True


def stop():
    """Просит цикл завершиться."""
    global _running
    _running = False


async def _init_offset() -> int:
    """Отбрасывает апдейты, накопившиеся пока бот лежал.

    getUpdates с offset=-1 возвращает только самый последний апдейт.
    Взяв его update_id + 1, мы говорим Telegram что всё предыдущее
    доставлено, и начинаем с чистого листа.
    """
    try:
        updates = await api.get_updates(offset=-1, timeout=0)
    except api.TelegramConflict:
        raise
    except api.TelegramError as e:
        logger.warning(f'[UPDATES] Не удалось получить стартовый offset: {e}')
        return 0

    if not updates:
        logger.info('[UPDATES] Накопленных апдейтов нет')
        return 0

    last_id = updates[-1]['update_id']
    logger.info(f'[UPDATES] Отброшен накопленный хвост, стартовый offset={last_id + 1}')
    return last_id + 1


async def run_loop():
    """Основной цикл приёма апдейтов."""
    global _running

    offset = await _init_offset()
    logger.info(f'[UPDATES] Long-polling запущен (timeout={config.LONG_POLL_TIMEOUT}с)')

    consecutive_errors = 0

    while _running:
        try:
            updates = await api.get_updates(
                offset=offset,
                timeout=config.LONG_POLL_TIMEOUT,
            )
            consecutive_errors = 0

        except api.TelegramConflict as e:
            # Критично: рядом работает второй экземпляр бота
            logger.critical(
                f'[UPDATES] 409 Conflict — тем же токеном поллит кто-то ещё. '
                f'Проверьте что старый юнит axioma-notifications выключен. {e}'
            )
            await asyncio.sleep(15)
            continue

        except api.TelegramError as e:
            consecutive_errors += 1
            # Наращиваем паузу, но не больше минуты
            delay = min(2 ** consecutive_errors, 60)
            logger.error(f'[UPDATES] Ошибка получения апдейтов: {e}, пауза {delay}с')
            await asyncio.sleep(delay)
            continue

        except asyncio.CancelledError:
            raise

        except Exception as e:
            consecutive_errors += 1
            delay = min(2 ** consecutive_errors, 60)
            logger.exception(f'[UPDATES] Неожиданная ошибка: {e}, пауза {delay}с')
            await asyncio.sleep(delay)
            continue

        for update in updates:
            # Сдвигаем offset ДО обработки: если обработчик упадёт,
            # апдейт не придёт повторно и не зациклит бота
            offset = update['update_id'] + 1

            try:
                await router.dispatch(update)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception(f'[UPDATES] Ошибка обработки апдейта: {e}')

    logger.info('[UPDATES] Long-polling остановлен')