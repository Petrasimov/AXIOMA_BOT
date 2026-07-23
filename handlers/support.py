"""
support.py — Раздел "💬 Поддержка"

Маршрутизация без хранилища. Карточка в админ-чате сама и есть база:

  1. Пользователь пишет вопрос (ForceReply)
  2. Бот шлёт карточку в админ-чат, в тексте карточки — ID пользователя
  3. Админ отвечает РЕПЛАЕМ на карточку
  4. Бот достаёт ID из текста карточки и пересылает ответ пользователю
  5. Кнопка "Завершить диалог" несёт ID в callback_data

Ничего не хранится: ID живёт в тексте сообщения и в callback_data,
оба переживают любой рестарт бота.

⚠️ Бот пересылает ТОЛЬКО реплаи на карточки. Иначе обычная болтовня
админов между собой уехала бы случайному пользователю от имени
поддержки.

⚠️ Ответ админа анонимен — пользователь видит "Поддержка AXIOMA SCAN",
без имени конкретного человека.
"""

import html
import logging
import re
from datetime import datetime, timedelta, timezone

import config
from bot import api, keyboards

logger = logging.getLogger(__name__)


# Маркер формы обращения — по нему узнаём ответ пользователя
SUPPORT_MARKER = 'ОБРАЩЕНИЕ В ПОДДЕРЖКУ'

# Маркер карточки в админ-чате — по нему узнаём реплай админа
CARD_MARKER = 'Новый запрос в поддержку'

# Из карточки достаём ID пользователя.
# Telegram отдаёт reply_to_message.text уже без HTML-тегов, но на
# сырой текст с <code> регулярка тоже должна срабатывать.
ID_PATTERN = re.compile(r'ID:\s*(?:<code>)?\s*(\d+)')

MSK = timezone(timedelta(hours=3))

MAX_TEXT_LEN = 3500


ASK_TEMPLATE = f"""💬 <b>{SUPPORT_MARKER}</b>

Ответьте на это сообщение и опишите ваш вопрос — мы ответим в течение 3 часов.

Приложите как можно больше деталей: что делали, что ожидали увидеть, что произошло вместо этого."""


THANKS_TEXT = """Спасибо за обращение в поддержку AXIOMA SCAN!

Если у вас появятся ещё вопросы — мы всегда на связи 🙌"""


def _user_label(user: dict) -> str:
    """Подпись пользователя для карточки."""
    username = user.get('username')
    if username:
        return f'@{html.escape(username)}'

    name = ' '.join(filter(None, [
        user.get('first_name', ''),
        user.get('last_name', ''),
    ])).strip()
    return html.escape(name) if name else 'без имени'


def _card_keyboard(user_id: int) -> dict:
    """Кнопка завершения диалога. ID едет в callback_data.

    Лимит callback_data — 64 байта, Telegram ID влезает с запасом.
    """
    return {
        'inline_keyboard': [
            [{'text': '✅ Завершить диалог', 'callback_data': f'sup:close:{user_id}'}]
        ]
    }


async def ask(chat_id: int):
    """Присылает форму обращения с ForceReply."""
    await api.send_message(
        chat_id,
        ASK_TEMPLATE,
        reply_markup=keyboards.force_reply('Опишите ваш вопрос...'),
    )


async def handle_question(message: dict):
    """Принимает вопрос пользователя и шлёт карточку в админ-чат."""
    chat_id = message['chat']['id']
    user    = message.get('from', {})
    text    = (message.get('text') or '').strip()

    if not text:
        await api.send_message(
            chat_id,
            '⚠️ Опишите вопрос текстом, пожалуйста.',
            reply_markup=keyboards.back_to_menu(),
        )
        return

    if not config.ADMIN_CHAT_ID:
        logger.error('[SUPPORT] ADMIN_CHAT_ID не задан — обращение некуда отправить')
        await api.send_message(
            chat_id,
            '⚠️ Не удалось отправить обращение. Попробуйте позже.',
            reply_markup=keyboards.back_to_menu(),
        )
        return

    if len(text) > MAX_TEXT_LEN:
        text = text[:MAX_TEXT_LEN] + '\n\n[...текст обрезан]'

    now = datetime.now(MSK).strftime('%H:%M %d.%m.%Y')

    card = (
        f'📩 <b>{CARD_MARKER}</b>\n\n'
        f'👤 {_user_label(user)} (ID: <code>{chat_id}</code>)\n'
        f'⏰ {now} МСК\n\n'
        f'{html.escape(text)}\n\n'
        f'━━━━━━━━━━━━━━━\n'
        f'↩️ <i>Ответьте реплаем на это сообщение — '
        f'бот перешлёт ваш ответ пользователю</i>'
    )

    try:
        await api.send_message(
            config.ADMIN_CHAT_ID,
            card,
            reply_markup=_card_keyboard(chat_id),
        )
    except api.TelegramError as e:
        logger.error(f'[SUPPORT] Не удалось отправить обращение в админ-чат: {e}')
        await api.send_message(
            chat_id,
            '⚠️ Не удалось отправить обращение. Попробуйте позже.',
            reply_markup=keyboards.back_to_menu(),
        )
        return

    logger.info(f'[SUPPORT] Обращение от {chat_id} доставлено в админ-чат')

    await api.send_message(
        chat_id,
        '✅ <b>Обращение отправлено!</b>\n\n'
        'Мы ответим в течение 3 часов прямо здесь, в этом чате.',
        reply_markup=keyboards.back_to_menu(),
    )


async def handle_admin_reply(message: dict) -> bool:
    """Пересылает ответ админа пользователю.

    Вызывается только для реплаев внутри админ-чата.
    Возвращает True если ответ переслан.
    """
    reply_to = message.get('reply_to_message') or {}
    origin   = reply_to.get('text', '') or reply_to.get('caption', '') or ''

    # Реплай не на карточку поддержки — не наше дело
    if CARD_MARKER not in origin:
        return False

    match = ID_PATTERN.search(origin)
    if not match:
        logger.warning('[SUPPORT] В карточке не найден ID пользователя')
        return False

    user_id = int(match.group(1))
    text    = (message.get('text') or '').strip()

    if not text:
        return False

    if len(text) > MAX_TEXT_LEN:
        text = text[:MAX_TEXT_LEN]

    answer = (
        f'💬 <b>Ответ поддержки AXIOMA SCAN</b>\n\n'
        f'{html.escape(text)}'
    )

    admin_chat = message['chat']['id']

    try:
        await api.send_message(user_id, answer, reply_markup=keyboards.back_to_menu())
    except api.BotBlocked:
        logger.info(f'[SUPPORT] Пользователь {user_id} заблокировал бота')
        await api.send_message(
            admin_chat,
            f'⚠️ Пользователь <code>{user_id}</code> заблокировал бота — '
            f'ответ не доставлен.',
            reply_to_message_id=message['message_id'],
        )
        return True
    except api.TelegramError as e:
        logger.error(f'[SUPPORT] Не удалось доставить ответ {user_id}: {e}')
        await api.send_message(
            admin_chat,
            f'⚠️ Не удалось доставить ответ пользователю <code>{user_id}</code>.',
            reply_to_message_id=message['message_id'],
        )
        return True

    logger.info(f'[SUPPORT] Ответ админа доставлен пользователю {user_id}')

    await api.send_message(
        admin_chat,
        f'✅ Доставлено пользователю <code>{user_id}</code>',
        reply_to_message_id=message['message_id'],
    )
    return True


async def close_dialog(callback: dict, user_id: int):
    """Завершает диалог: благодарит пользователя, гасит кнопку."""
    admin_chat = callback['message']['chat']['id']
    message_id = callback['message']['message_id']

    try:
        await api.send_message(user_id, THANKS_TEXT, reply_markup=keyboards.back_to_menu())
        delivered = True
    except api.TelegramError as e:
        logger.warning(f'[SUPPORT] Не удалось отправить благодарность {user_id}: {e}')
        delivered = False

    # Убираем кнопку — диалог закрыт, повторно нажать нельзя
    try:
        await api.edit_message_reply_markup(admin_chat, message_id, reply_markup=None)
    except api.TelegramError as e:
        logger.debug(f'[SUPPORT] Не удалось убрать кнопку: {e}')

    status = '✅ Диалог завершён' if delivered else '⚠️ Диалог завершён, но уведомление не доставлено'
    await api.send_message(
        admin_chat,
        f'{status} — <code>{user_id}</code>',
        reply_to_message_id=message_id,
    )

    logger.info(f'[SUPPORT] Диалог с {user_id} завершён')