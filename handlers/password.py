"""
password.py — Раздел "🔑 Восстановление пароля"

Сценарий:

  1. Пользователь жмёт кнопку в меню
  2. Бот проверяет, что аккаунт есть в Users и у него задан логин
  3. ForceReply: "пришлите новый пароль"
  4. Бот передаёт пару логин+пароль бэкенду
  5. Сообщение пользователя с паролем УДАЛЯЕТСЯ
  6. Бот подтверждает смену и зовёт войти на сайте

═══ Почему бот не пишет пароль в БД напрямую ═══

Хеширование — зона ответственности бэкенда: он знает алгоритм, соль
и формат хранения. Если бот начнёт писать в "Users"."Password" сам,
любое изменение схемы на стороне C# молча сломает вход, а бот
получит доступ к самой чувствительной таблице проекта без нужды.

Поэтому бот дёргает POST /api/auth/register — тот же эндпоинт,
которым пользуется сайт. Бэкенд сам решает, как хранить.

⚠️ Пароль НИКОГДА не логируется и не попадает в текст ошибок.
Не добавлять его в logger даже на уровне debug.
"""

import logging

import aiohttp

import config
import db
from bot import api, keyboards

logger = logging.getLogger(__name__)


# Маркер формы — по нему роутер узнаёт ответ пользователя.
# Должен быть уникальным среди всех форм бота.
PASSWORD_MARKER = 'ВОССТАНОВЛЕНИЕ ПАРОЛЯ'

# Требования к паролю. Совпадают с проверкой на сайте: если бот
# разрешит короче, бэкенд всё равно откажет, а пользователь получит
# невнятную ошибку вместо понятной подсказки.
MIN_PASSWORD_LEN = 6
MAX_PASSWORD_LEN = 128


ASK_TEMPLATE = f"""🔑 <b>{PASSWORD_MARKER}</b>

Ответьте на это сообщение новым паролем — и он сразу заменит старый.

Требования: минимум {MIN_PASSWORD_LEN} символов.

🔒 Ваше сообщение с паролем будет удалено сразу после смены."""


NOT_REGISTERED = (
    '🔒 <b>Вы не зарегистрированы</b>\n\n'
    'Восстанавливать пока нечего. Войдите на сайте через Telegram — '
    'аккаунт создастся автоматически.'
)

NO_LOGIN = (
    '⚠️ <b>У вас ещё нет логина</b>\n\n'
    'Вы заходили только через Telegram, пароль для входа не задавался. '
    'Продолжайте входить через Telegram — это надёжнее и ничего '
    'запоминать не нужно.'
)

BACKEND_FAIL = (
    '⚠️ <b>Не удалось сменить пароль</b>\n\n'
    'Сервис временно недоступен. Попробуйте позже или напишите '
    'в поддержку через меню.'
)


async def ask(chat_id: int):
    """Проверяет аккаунт и присылает форму с ForceReply."""
    try:
        state = await db.get_user_state(chat_id)
    except Exception as e:
        logger.error(f'[PASSWORD] Ошибка получения состояния {chat_id}: {e}')
        await api.send_message(
            chat_id,
            '⚠️ Не удалось проверить аккаунт. Попробуйте позже.',
            reply_markup=keyboards.back_to_menu(),
        )
        return

    if state is None:
        await api.send_message(
            chat_id, NOT_REGISTERED,
            reply_markup=keyboards.open_scanner(),
        )
        return

    try:
        login = await db.get_user_login(chat_id)
    except Exception as e:
        logger.error(f'[PASSWORD] Ошибка получения логина {chat_id}: {e}')
        await api.send_message(
            chat_id,
            '⚠️ Не удалось проверить аккаунт. Попробуйте позже.',
            reply_markup=keyboards.back_to_menu(),
        )
        return

    if not login:
        await api.send_message(
            chat_id, NO_LOGIN,
            reply_markup=keyboards.open_scanner(),
        )
        return

    await api.send_message(
        chat_id,
        ASK_TEMPLATE,
        reply_markup=keyboards.force_reply('Новый пароль...'),
    )


async def handle_new_password(message: dict):
    """Принимает новый пароль и передаёт его бэкенду."""
    chat_id = message['chat']['id']
    message_id = message.get('message_id')
    password = message.get('text') or ''

    # Удаляем сообщение с паролем сразу, ещё до всех проверок:
    # даже неподошедший пароль не должен остаться в истории чата.
    if message_id:
        await api.delete_message(chat_id, message_id)

    # Пробелы по краям почти наверняка случайны, но внутри пароля
    # они осмысленны — strip только по краям.
    password = password.strip()

    if len(password) < MIN_PASSWORD_LEN:
        await api.send_message(
            chat_id,
            f'⚠️ Пароль слишком короткий — нужно минимум '
            f'{MIN_PASSWORD_LEN} символов.\n\nПопробуйте ещё раз.',
            reply_markup=keyboards.back_to_menu(),
        )
        return

    if len(password) > MAX_PASSWORD_LEN:
        await api.send_message(
            chat_id,
            f'⚠️ Пароль слишком длинный — не больше '
            f'{MAX_PASSWORD_LEN} символов.',
            reply_markup=keyboards.back_to_menu(),
        )
        return

    try:
        login = await db.get_user_login(chat_id)
    except Exception as e:
        logger.error(f'[PASSWORD] Ошибка получения логина {chat_id}: {e}')
        await api.send_message(
            chat_id, BACKEND_FAIL,
            reply_markup=keyboards.back_to_menu(),
        )
        return

    if not login:
        await api.send_message(
            chat_id, NO_LOGIN,
            reply_markup=keyboards.open_scanner(),
        )
        return

    ok, reason = await _set_password(chat_id, login, password)

    if not ok:
        text = {
            'conflict': (
                '⚠️ <b>Логин занят</b>\n\n'
                'Похоже, аккаунт изменился. Напишите в поддержку через меню.'
            ),
            'bad_request': (
                '⚠️ Бэкенд не принял пароль. Попробуйте другой — '
                'без пробелов по краям и не короче 6 символов.'
            ),
            'forbidden': (
                '⚠️ <b>Смена пароля недоступна</b>\n\n'
                'Обратитесь в поддержку через меню.'
            ),
        }.get(reason, BACKEND_FAIL)

        await api.send_message(chat_id, text, reply_markup=keyboards.back_to_menu())
        return

    logger.info(f'[PASSWORD] Пароль изменён для {chat_id}')

    await api.send_message(
        chat_id,
        '✅ <b>Пароль изменён</b>\n\n'
        f'Логин: <code>{login}</code>\n\n'
        'Теперь войдите на сайте с новым паролем.',
        reply_markup=keyboards.open_scanner(),
    )


async def _set_password(user_id: int, login: str, password: str) -> tuple[bool, str]:
    """Отправляет новый пароль бэкенду.

    Возвращает (успех, причина). Причина совпадает с тем, что
    различает фронтенд: bad_request | forbidden | conflict | error.

    userId передаём, чтобы бэкенд обновил пароль КОНКРЕТНОГО
    пользователя, а не завёл нового по совпадению логина.
    """
    url = f'{config.BACKEND_API_URL}/api/auth/register'
    payload = {
        'userId': user_id,
        'login': login,
        'password': password,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    return True, ''

                if resp.status == 400:
                    return False, 'bad_request'
                if resp.status == 403:
                    return False, 'forbidden'
                if resp.status == 409:
                    return False, 'conflict'

                # Тело ответа может содержать эхо запроса — не логируем его
                logger.error(f'[PASSWORD] Бэкенд вернул {resp.status}')
                return False, 'error'

    except Exception as e:
        # В текст исключения пароль попасть не может: aiohttp не
        # печатает тело запроса. Но на всякий случай логируем только тип.
        logger.error(f'[PASSWORD] Ошибка обращения к бэкенду: {type(e).__name__}')
        return False, 'error'