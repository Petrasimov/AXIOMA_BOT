"""
start.py — Команда /start и главное меню

Два сценария входа:

  /start          — обычный запуск, показываем меню
  /start hello    — переход с сайта по кнопке "Написать менеджеру"
                    из модалки авторизации. Показываем приветствие
                    для тех, кому доступ ещё не открыт.

Приветствие раньше отправлялось прямо с фронтенда через Bot API,
из-за чего токен бота попадал в собранный бандл. Теперь его шлёт
бот — и заодно это стало надёжнее: Bot API не может писать первым
тому, кто не открывал диалог, а по deep link пользователь
открывает диалог сам.
"""

import logging

from bot import api, keyboards

logger = logging.getLogger(__name__)


WELCOME_TEXT = """👋 Привет! Я официальный бот-менеджер <b>AXIOMA SCAN</b>.

🚀 Проект сейчас находится на стадии закрытого тестирования.

Возможны два варианта:

🔹 Если вы являетесь тестировщиком — ваш аккаунт уже в нашей системе. Пожалуйста, немного подождите — администраторы в ближайшее время откроют вам доступ к скринеру.

🔹 Если вы ещё не тестировщик — следите за обновлениями! Мы обязательно уведомим вас, когда AXIOMA SCAN выйдет в открытый доступ. Это произойдёт совсем скоро 🎯

Спасибо за интерес к AXIOMA и за ваше терпение! 🙏"""


MENU_TEXT = """Добро пожаловать в <b>AXIOMA SCAN</b>! 🚀

Криптовалютный арбитражный сканер нового поколения.

Выберите раздел:"""


# Разделы, которые появятся в следующих задачах
STUB_TEXT = {
    'password': '🔑 Восстановление пароля скоро появится.',
}


async def handle_start(message: dict, payload: str = ''):
    """Обрабатывает команду /start.

    payload — то что идёт после команды: /start hello → payload = 'hello'
    """
    chat_id = message['chat']['id']
    user = message.get('from', {})
    username = user.get('username') or user.get('first_name') or '?'

    logger.info(f'[START] user_id={chat_id} @{username} payload={payload!r}')

    # Переход с сайта — сначала приветствие
    if payload == 'hello':
        await api.send_message(chat_id, WELCOME_TEXT)

    await api.send_message(
        chat_id,
        MENU_TEXT,
        reply_markup=keyboards.main_menu(),
    )


async def show_menu(chat_id: int, message_id: int | None = None):
    """Показывает главное меню.

    Если передан message_id — редактирует существующее сообщение
    вместо отправки нового. Так меню не размножается по чату.
    """
    if message_id:
        try:
            await api.edit_message_text(
                chat_id,
                message_id,
                MENU_TEXT,
                reply_markup=keyboards.main_menu(),
            )
            return
        except api.TelegramError as e:
            # Например, "message is not modified" — не страшно
            logger.debug(f'[START] Не удалось отредактировать меню: {e}')

    await api.send_message(chat_id, MENU_TEXT, reply_markup=keyboards.main_menu())


async def handle_menu_callback(callback: dict, action: str):
    """Обрабатывает нажатия кнопок главного меню.

    В Задаче 1 живой только возврат в меню, остальные разделы —
    заглушки. Наполняются в Задачах 2-5.
    """
    chat_id = callback['message']['chat']['id']
    message_id = callback['message']['message_id']

    if action == 'main':
        await show_menu(chat_id, message_id)
        return

    text = STUB_TEXT.get(action)
    if text is None:
        logger.warning(f'[START] Неизвестное действие меню: {action}')
        return

    await api.send_message(
        chat_id,
        text,
        reply_markup=keyboards.back_to_menu(),
    )