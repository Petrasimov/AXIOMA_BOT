"""
keyboards.py — Все клавиатуры бота

Используем только inline-клавиатуры: они привязаны к конкретному
сообщению и не занимают место внизу экрана.

callback_data ограничена 64 байтами — держим её короткой.
Формат: "раздел:действие" или "раздел:действие:параметр"
"""

import config


def main_menu() -> dict:
    """Главное меню бота."""
    return {
        'inline_keyboard': [
            [
                {'text': '🔔 Уведомления',    'callback_data': 'menu:notify'},
                {'text': '💳 Купить подписку', 'callback_data': 'menu:pay'},
            ],
            [
                {'text': '🔑 Восст. пароль', 'callback_data': 'menu:password'},
                {'text': '💬 Поддержка',      'callback_data': 'menu:support'},
            ],
            [
                {'text': '👥 Хочешь в команду?', 'callback_data': 'menu:jobs'},
            ],
        ]
    }


def back_to_menu() -> dict:
    """Одна кнопка возврата в главное меню."""
    return {
        'inline_keyboard': [
            [{'text': '◀️ В меню', 'callback_data': 'menu:main'}]
        ]
    }


def open_scanner() -> dict:
    """Кнопка-ссылка на сканер."""
    return {
        'inline_keyboard': [
            [{'text': '🚀 Открыть сканер', 'url': config.SCANNER_URL}]
        ]
    }


def force_reply(placeholder: str = '') -> dict:
    """ForceReply — открывает у пользователя поле ответа,
    привязанное к сообщению бота.

    Это наш способ хранить контекст диалога: в следующем апдейте
    придёт reply_to_message с текстом вопроса, по которому мы
    и поймём, что именно пользователь отвечает.
    """
    markup: dict = {
        'force_reply': True,
        'selective': True,
    }
    if placeholder:
        # Не длиннее 64 символов — ограничение Telegram
        markup['input_field_placeholder'] = placeholder[:64]
    return markup