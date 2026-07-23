"""
main.py — AXIOMA BOT

Точка входа. Один процесс, две параллельные задачи:

  notifications.service.run_loop()  — рассылка уведомлений каждые 60с
  bot.updates.run_loop()            — приём входящих через long-polling

Обе используют общий пул asyncpg и общую HTTP сессию.

Запуск:
  python3 main.py

Systemd:
  sudo systemctl start axioma-bot
"""

import asyncio
import logging
import signal
import sys

import config
import db
from bot import api, updates
from handlers import payment
from notifications import sender, service

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

# aiohttp шумит на уровне DEBUG — приглушаем
logging.getLogger('aiohttp').setLevel(logging.WARNING)


# ─── Остановка ───────────────────────────────────────────────────────────────

def _shutdown(sig_name: str):
    """Просит обе задачи завершиться."""
    logger.info(f'Получен сигнал {sig_name}, завершаем работу...')
    service.stop()
    updates.stop()


def _install_signal_handlers(loop: asyncio.AbstractEventLoop):
    """Вешает обработчики SIGINT/SIGTERM.

    add_signal_handler есть не на всех платформах — под Windows
    молча откатываемся на поведение по умолчанию.
    """
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown, sig.name)
        except NotImplementedError:
            logger.debug(f'add_signal_handler недоступен для {sig.name}')


# ─── Запуск ──────────────────────────────────────────────────────────────────

async def run():
    logger.info('AXIOMA BOT запускается...')

    # 1. Конфиг
    try:
        config.validate()
    except ValueError as e:
        logger.critical(f'Ошибка конфигурации: {e}')
        sys.exit(1)

    # 2. HTTP сессия — нужна до любых обращений к Telegram
    await api.init()

    # 3. Проверяем токен
    if not await sender.check_bot():
        logger.critical('Telegram бот недоступен — проверьте BOT_TOKEN в .env')
        await api.close()
        sys.exit(1)

    # 4. БД (создаёт таблицу NotificationCooldowns)
    try:
        await db.init(config.DATABASE_URL)
    except Exception as e:
        logger.critical(f'Ошибка подключения к БД: {e}')
        await api.close()
        sys.exit(1)

    if not config.ADMIN_CHAT_ID:
        logger.warning(
            'ADMIN_CHAT_ID не задан — поддержка и резюме работать не будут '
            '(понадобится в Задачах 3-4)'
        )

    # Догоняем платежи, прошедшие пока бот лежал
    try:
        await payment.reconcile()
    except Exception as e:
        logger.error(f'Сверка платежей не удалась: {e}')

    _install_signal_handlers(asyncio.get_running_loop())

    # 5. Обе задачи параллельно
    try:
        await asyncio.gather(
            service.run_loop(),
            updates.run_loop(),
        )
    except asyncio.CancelledError:
        logger.info('Задачи отменены')
    finally:
        await api.close()
        await db.close()
        logger.info('AXIOMA BOT остановлен')


if __name__ == '__main__':
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass