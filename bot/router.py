"""
router.py — Разбор входящих апдейтов и вызов нужного обработчика

Апдейт может быть трёх видов:

  1. Команда         — текст начинается с "/"
  2. Нажатие кнопки  — callback_query, разбираем callback_data
  3. Ответ на ForceReply — reply_to_message, контекст определяем
                           по тексту сообщения бота

Третий пункт — наш способ обходиться без хранения состояния.
Бот задаёт вопрос, пользователь отвечает реплаем, и в апдейте
приходит текст исходного вопроса. Хранилищем работает Telegram.
"""

import logging

import config
from bot import api
from handlers import notifications as notify_handler
from handlers import payment, start, support, vacancies

logger = logging.getLogger(__name__)


# Маркеры в тексте сообщений бота — по ним узнаём контекст ответа.
# Маркер должен быть уникальным среди всех форм бота.
REPLY_MARKERS = {
    vacancies.RESUME_MARKER: vacancies.handle_resume,
    support.SUPPORT_MARKER:  support.handle_question,
}


async def dispatch(update: dict):
    """Точка входа: разбирает апдейт и передаёт в обработчик."""
    if 'callback_query' in update:
        await _handle_callback(update['callback_query'])
        return

    if 'message' in update:
        await _handle_message(update['message'])
        return

    logger.debug(f'[ROUTER] Пропущен апдейт неизвестного типа: {list(update.keys())}')


# ─── Сообщения ───────────────────────────────────────────────────────────────

async def _handle_message(message: dict):
    """Обрабатывает входящее сообщение."""
    text = message.get('text', '')
    chat_id = message.get('chat', {}).get('id')

    if chat_id is None:
        return

    # Админ-чат обрабатывается отдельно: там бот реагирует только
    # на реплаи к карточкам поддержки и молчит на всё остальное,
    # иначе он засыпал бы админов меню на каждое их сообщение.
    if config.ADMIN_CHAT_ID and chat_id == config.ADMIN_CHAT_ID:
        if message.get('reply_to_message'):
            await support.handle_admin_reply(message)
        return

    # Команда
    if text.startswith('/'):
        await _handle_command(message, text)
        return

    # Ответ на ForceReply — определяем контекст по вопросу бота
    reply_to = message.get('reply_to_message')
    if reply_to:
        handled = await _handle_reply(message, reply_to)
        if handled:
            return

    # Свободный текст без контекста — подсказываем меню
    logger.debug(f'[ROUTER] Свободный текст от {chat_id}, показываем меню')
    await start.show_menu(chat_id)


async def _handle_command(message: dict, text: str):
    """Разбирает команду вида /start или /start hello."""
    parts = text.split(maxsplit=1)
    command = parts[0].lower()
    payload = parts[1].strip() if len(parts) > 1 else ''

    # Убираем упоминание бота: /start@axioma_manager_bot
    if '@' in command:
        command = command.split('@', 1)[0]

    if command == '/start':
        await start.handle_start(message, payload)
        return

    if command == '/menu':
        await start.show_menu(message['chat']['id'])
        return

    logger.debug(f'[ROUTER] Неизвестная команда: {command}')
    await start.show_menu(message['chat']['id'])


async def _handle_reply(message: dict, reply_to: dict) -> bool:
    """Обрабатывает ответ на ForceReply сообщение бота.

    Возвращает True если ответ распознан и обработан.
    """
    origin = reply_to.get('text', '')

    for marker, handler in REPLY_MARKERS.items():
        if marker in origin:
            logger.debug(f'[ROUTER] Ответ распознан: {marker}')
            await handler(message)
            return True

    return False


# ─── Callback кнопки ─────────────────────────────────────────────────────────

async def _handle_callback(callback: dict):
    """Обрабатывает нажатие инлайн-кнопки.

    callback_data имеет формат "раздел:действие" или
    "раздел:действие:параметр".
    """
    data = callback.get('data', '')
    callback_id = callback.get('id')

    # Гасим "часики" на кнопке сразу — Telegram ждёт этого ответа
    if callback_id:
        try:
            await api.answer_callback_query(callback_id)
        except api.TelegramError as e:
            logger.debug(f'[ROUTER] answerCallbackQuery: {e}')

    if not data or ':' not in data:
        logger.warning(f'[ROUTER] Пустая или некорректная callback_data: {data!r}')
        return

    section, action = data.split(':', 1)
    param = ''
    if ':' in action:
        action, param = action.split(':', 1)

    logger.debug(f'[ROUTER] Callback {section}:{action} param={param!r}')

    if section == 'menu':
        if action == 'notify':
            await notify_handler.show(
                callback['message']['chat']['id'],
                callback['message']['message_id'],
            )
            return
        if action == 'jobs':
            await vacancies.show(
                callback['message']['chat']['id'],
                callback['message']['message_id'],
            )
            return
        if action == 'support':
            await support.ask(callback['message']['chat']['id'])
            return
        if action == 'pay':
            await payment.show(
                callback['message']['chat']['id'],
                callback['message']['message_id'],
            )
            return
        await start.handle_menu_callback(callback, action)
        return

    if section == 'notify':
        await notify_handler.toggle(callback, enable=(action == 'on'))
        return

    if section == 'jobs':
        if action == 'resume':
            await vacancies.ask_resume(callback['message']['chat']['id'])
        return

    if section == 'sup':
        if action == 'close' and param.isdigit():
            await support.close_dialog(callback, int(param))
        return

    if section == 'pay':
        if action == 'new' and param:
            await payment.create(callback, param)
        elif action == 'check' and param:
            await payment.check(callback, param)
        return

    logger.warning(f'[ROUTER] Неизвестный раздел callback: {section}')