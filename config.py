"""
config.py — Настройки бота из .env файла
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ─── Telegram ────────────────────────────────────────────────────────────────

BOT_TOKEN: str = os.getenv('BOT_TOKEN', '')

# Чат администраторов — сюда падают обращения в поддержку и резюме.
# Для супергруппы ID отрицательный, вида -1001234567890
ADMIN_CHAT_ID: int = int(os.getenv('ADMIN_CHAT_ID', '0') or 0)

# ─── PostgreSQL ──────────────────────────────────────────────────────────────

DATABASE_URL: str = os.getenv('DATABASE_URL', '')

# ─── Уведомления ─────────────────────────────────────────────────────────────

# Интервал проверки новых возможностей, секунды
CHECK_INTERVAL: int = int(os.getenv('CHECK_INTERVAL', '60'))

# Минимальный промежуток между уведомлениями по одной связке, часы
COOLDOWN_HOURS: int = int(os.getenv('COOLDOWN_HOURS', '1'))

# Сколько уведомлений максимум отправляем одному пользователю за цикл.
# Защита от лавины: у нового пользователя cooldown-таблица пуста,
# и без лимита улетело бы столько сообщений, сколько связок прошло
# фильтр — сотни за раз.
MAX_NOTIFICATIONS_PER_CYCLE: int = int(os.getenv('MAX_NOTIFICATIONS_PER_CYCLE', '5'))

# ─── Ссылки ──────────────────────────────────────────────────────────────────

SCANNER_URL: str = os.getenv('SCANNER_URL', 'https://axioma-scan.ru')

# ─── Бэкенд C# (Задача 6) ────────────────────────────────────────────────────

# Смену пароля делает бэкенд, а не бот: только он знает, каким алгоритмом
# хешировать. Бот ходит на localhost — контейнер слушает 127.0.0.1:5000
# и наружу не смотрит, поэтому запрос не выходит за пределы сервера.
BACKEND_API_URL: str = os.getenv('BACKEND_API_URL', 'http://localhost:5000')

# ─── NOWPayments (Задача 5) ──────────────────────────────────────────────────

NOWPAYMENTS_API_KEY: str = os.getenv('NOWPAYMENTS_API_KEY', '')
SUBSCRIPTION_PRICE_USD: float = float(os.getenv('SUBSCRIPTION_PRICE_USD', '5'))
PAYMENT_TIMEOUT_MINUTES: int = int(os.getenv('PAYMENT_TIMEOUT_MINUTES', '30'))

# ─── Long-polling ────────────────────────────────────────────────────────────

# Сколько секунд Telegram держит соединение открытым, ожидая апдейты.
# Ставим меньше 50 — иначе упрёмся в лимиты самого Telegram.
LONG_POLL_TIMEOUT: int = 30

# ─── Лимиты отправки ─────────────────────────────────────────────────────────

# Telegram режет на ~30 сообщений/сек глобально и ~1/сек в один чат.
# Держимся ниже потолка с запасом.
RATE_GLOBAL_PER_SEC: int = 25
RATE_CHAT_INTERVAL: float = 1.05


def validate():
    """Проверяет что обязательные переменные заданы.

    ADMIN_CHAT_ID и NOWPAYMENTS_API_KEY не проверяем — они нужны
    только Задачам 4 и 5, бот должен подниматься и без них.
    """
    if not BOT_TOKEN:
        raise ValueError('BOT_TOKEN не задан в .env файле')
    if not DATABASE_URL:
        raise ValueError('DATABASE_URL не задан в .env файле')