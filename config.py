"""
config.py — Настройки сервиса из .env файла
"""

import os
from dotenv import load_dotenv

load_dotenv()

# Telegram Bot
BOT_TOKEN: str = os.getenv('BOT_TOKEN', '')

# PostgreSQL
DATABASE_URL: str = os.getenv('DATABASE_URL', '')

# Интервал проверки в секундах (по умолчанию 60с)
CHECK_INTERVAL: int = int(os.getenv('CHECK_INTERVAL', '60'))

# Cooldown в часах — минимальный промежуток между уведомлениями по одной монете
COOLDOWN_HOURS: int = int(os.getenv('COOLDOWN_HOURS', '1'))

# Ссылка на сканер в кнопке сообщения
SCANNER_URL: str = 'https://axioma-scan.ru'


def validate():
    """Проверяет что все обязательные переменные заданы."""
    if not BOT_TOKEN:
        raise ValueError('BOT_TOKEN не задан в .env файле')
    if not DATABASE_URL:
        raise ValueError('DATABASE_URL не задан в .env файле')