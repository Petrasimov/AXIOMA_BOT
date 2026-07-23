"""
Пакет тестов AXIOMA_BOT.

Запуск всех тестов из корня проекта:
    python3 -m unittest discover -s tests -v

Запуск одного файла:
    python3 -m unittest tests.test_filters -v

Запуск одного теста:
    python3 -m unittest tests.test_cooldown.TestCanSend.test_hour_not_passed -v

Тесты не требуют ни базы данных, ни сети, ни реального бота —
всё внешнее подменяется заглушками. Никаких дополнительных
библиотек ставить не нужно, используется стандартный unittest.
"""

import os
import sys

# Переменные окружения должны быть выставлены ДО импорта config,
# иначе config.validate() не пройдёт и модули не загрузятся.
os.environ.setdefault('BOT_TOKEN', 'test:token')
os.environ.setdefault('DATABASE_URL', 'postgresql://test@localhost/test')
os.environ.setdefault('ADMIN_CHAT_ID', '-1009999999999')
os.environ.setdefault('NOWPAYMENTS_API_KEY', 'test-key')
os.environ.setdefault('CHECK_INTERVAL', '60')
os.environ.setdefault('COOLDOWN_HOURS', '1')
os.environ.setdefault('MAX_NOTIFICATIONS_PER_CYCLE', '5')

# Корень проекта в путях импорта
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)