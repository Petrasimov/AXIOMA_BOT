"""
vacancies.py — Раздел "👥 Хочешь в команду?"

Вакансии статичные, прописаны в коде. Обновить — поменять текст здесь.

Резюме принимаем ОДНИМ сообщением по шаблону. Пошагового опроса нет
намеренно: он потребовал бы хранить, на каком шаге находится каждый
пользователь, а мы договорились ничего не хранить.

Контекст определяется через ForceReply — бот присылает шаблон,
пользователь отвечает реплаем, и в апдейте приходит текст шаблона.
Хранилищем работает Telegram.

⚠️ Текст пользователя обязательно экранируется перед отправкой
в админ-чат: parse_mode=HTML сломается на любом < или &,
и сообщение просто не уйдёт.
"""

import html
import logging
from datetime import datetime, timedelta, timezone

import config
from bot import api, keyboards

logger = logging.getLogger(__name__)


# Маркер по которому узнаём ответ на шаблон резюме.
# Должен быть уникальным среди всех форм бота.
RESUME_MARKER = 'ОТПРАВКА РЕЗЮМЕ'

# Московское время — команда русская, сервер в Польше
MSK = timezone(timedelta(hours=3))

# Telegram режет сообщения на 4096 символах. Оставляем запас под шапку.
MAX_RESUME_LEN = 3500


VACANCIES_TEXT = """🚀 <b>AXIOMA SCAN — МЫ ИЩЕМ В КОМАНДУ</b>

💻 <b>Frontend разработчик</b>
• React 19, CSS, JavaScript
• Опыт от 1 года
• Удалённо

📊 <b>Трейдер-фармщик</b>
• Опыт в крипто-арбитраже
• Минимальный депозит от $40
• Доход: 80% от прибыли

📣 <b>Комьюнити-менеджер</b>
• Ведение Telegram канала
• Общение с пользователями

💡 <b>Есть своя идея для проекта?</b>
Мы всегда открыты к интересным предложениям!"""


RESUME_TEMPLATE = f"""📝 <b>{RESUME_MARKER}</b>

Ответьте на это сообщение одним текстом, укажите:

1️⃣ ФИО
2️⃣ Возраст
3️⃣ Город
4️⃣ На какую позицию претендуете
5️⃣ Опыт работы
6️⃣ О себе
7️⃣ Контакт для связи

Можно просто списком — главное, чтобы всё было в одном сообщении."""


def _vacancies_keyboard() -> dict:
    return {
        'inline_keyboard': [
            [{'text': '📝 Отправить резюме', 'callback_data': 'jobs:resume'}],
            [{'text': '◀️ В меню', 'callback_data': 'menu:main'}],
        ]
    }


def _user_label(user: dict) -> str:
    """Подпись пользователя для карточки в админ-чате."""
    username = user.get('username')
    if username:
        return f'@{html.escape(username)}'

    name = ' '.join(filter(None, [
        user.get('first_name', ''),
        user.get('last_name', ''),
    ])).strip()
    return html.escape(name) if name else 'без имени'


async def show(chat_id: int, message_id: int | None = None):
    """Показывает список вакансий."""
    markup = _vacancies_keyboard()

    if message_id:
        try:
            await api.edit_message_text(
                chat_id, message_id, VACANCIES_TEXT, reply_markup=markup,
            )
            return
        except api.TelegramError as e:
            logger.debug(f'[JOBS] Не удалось отредактировать: {e}')

    await api.send_message(chat_id, VACANCIES_TEXT, reply_markup=markup)


async def ask_resume(chat_id: int):
    """Присылает шаблон резюме с ForceReply."""
    await api.send_message(
        chat_id,
        RESUME_TEMPLATE,
        reply_markup=keyboards.force_reply('Ваше резюме одним сообщением...'),
    )


async def handle_resume(message: dict):
    """Принимает резюме и пересылает в чат администраторов."""
    chat_id = message['chat']['id']
    user    = message.get('from', {})
    text    = (message.get('text') or '').strip()

    if not text:
        await api.send_message(
            chat_id,
            '⚠️ Резюме нужно прислать текстом. Попробуйте ещё раз.',
            reply_markup=keyboards.back_to_menu(),
        )
        return

    if not config.ADMIN_CHAT_ID:
        logger.error('[JOBS] ADMIN_CHAT_ID не задан — резюме некуда отправить')
        await api.send_message(
            chat_id,
            '⚠️ Не удалось отправить резюме. Попробуйте позже.',
            reply_markup=keyboards.back_to_menu(),
        )
        return

    if len(text) > MAX_RESUME_LEN:
        text = text[:MAX_RESUME_LEN] + '\n\n[...текст обрезан]'

    now = datetime.now(MSK).strftime('%H:%M %d.%m.%Y')

    card = (
        f'📋 <b>НОВОЕ РЕЗЮМЕ</b>\n\n'
        f'👤 {_user_label(user)} (ID: <code>{chat_id}</code>)\n'
        f'⏰ {now} МСК\n\n'
        f'{html.escape(text)}'
    )

    try:
        await api.send_message(config.ADMIN_CHAT_ID, card)
    except api.TelegramError as e:
        logger.error(f'[JOBS] Не удалось отправить резюме в админ-чат: {e}')
        await api.send_message(
            chat_id,
            '⚠️ Не удалось отправить резюме. Попробуйте позже.',
            reply_markup=keyboards.back_to_menu(),
        )
        return

    logger.info(f'[JOBS] Резюме от {chat_id} доставлено в админ-чат')

    await api.send_message(
        chat_id,
        '✅ <b>Резюме отправлено!</b>\n\n'
        'Мы свяжемся с вами в ближайшее время.',
        reply_markup=keyboards.back_to_menu(),
    )