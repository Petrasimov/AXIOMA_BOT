"""
notifications.py — Раздел "🔔 Уведомления"

Показывает текущий статус и переключает ActiveNotifications.

Проверки перед показом:
  - пользователь зарегистрирован на сайте (есть в Users)
  - есть строка в UserSettings (её создаёт C# при регистрации)
  - предупреждаем если нет подписки — уведомления включатся,
    но приходить не будут пока IsCexCexPaid = false
"""

import logging

import db
from bot import api, keyboards

logger = logging.getLogger(__name__)


NOT_REGISTERED = (
    '🔒 <b>Вы не зарегистрированы</b>\n\n'
    'Чтобы пользоваться уведомлениями, сначала войдите '
    'на сайте через Telegram.'
)

NO_SETTINGS = (
    '⚠️ <b>Настройки не найдены</b>\n\n'
    'Похоже, регистрация не завершилась. Зайдите на сайт '
    'и войдите через Telegram ещё раз.'
)


def _status_text(state: dict) -> str:
    """Собирает текст с текущим статусом."""
    enabled = bool(state.get('notifications_on'))

    lines = [
        '🔔 <b>Уведомления</b>',
        '',
        f'Статус: <b>{"включены ✅" if enabled else "выключены ❌"}</b>',
    ]

    if enabled:
        lines += [
            '',
            'Присылаю новые арбитражные возможности по вашим фильтрам '
            'из настроек сканера — не чаще раза в час по одной связке.',
        ]

    if not state.get('is_paid'):
        lines += [
            '',
            '⚠️ Подписка не активна — уведомления приходить не будут, '
            'даже если включены.',
        ]

    if not state.get('is_active'):
        lines += [
            '',
            '⚠️ Аккаунт неактивен — обратитесь в поддержку.',
        ]

    return '\n'.join(lines)


def _toggle_keyboard(enabled: bool) -> dict:
    """Кнопка переключения плюс возврат в меню."""
    label = '❌ Выключить' if enabled else '✅ Включить'
    action = 'notify:off' if enabled else 'notify:on'
    return {
        'inline_keyboard': [
            [{'text': label, 'callback_data': action}],
            [{'text': '◀️ В меню', 'callback_data': 'menu:main'}],
        ]
    }


async def show(chat_id: int, message_id: int | None = None):
    """Показывает текущий статус уведомлений."""
    try:
        state = await db.get_user_state(chat_id)
    except Exception as e:
        logger.error(f'[NOTIFY] Ошибка получения состояния {chat_id}: {e}')
        await api.send_message(
            chat_id,
            '⚠️ Не удалось получить настройки. Попробуйте позже.',
            reply_markup=keyboards.back_to_menu(),
        )
        return

    if state is None:
        await api.send_message(
            chat_id, NOT_REGISTERED,
            reply_markup=keyboards.open_scanner(),
        )
        return

    if state.get('notifications_on') is None:
        await api.send_message(
            chat_id, NO_SETTINGS,
            reply_markup=keyboards.open_scanner(),
        )
        return

    text = _status_text(state)
    markup = _toggle_keyboard(bool(state.get('notifications_on')))

    if message_id:
        try:
            await api.edit_message_text(chat_id, message_id, text, reply_markup=markup)
            return
        except api.TelegramError as e:
            logger.debug(f'[NOTIFY] Не удалось отредактировать: {e}')

    await api.send_message(chat_id, text, reply_markup=markup)


async def toggle(callback: dict, enable: bool):
    """Включает или выключает уведомления."""
    chat_id    = callback['message']['chat']['id']
    message_id = callback['message']['message_id']

    try:
        ok = await db.set_notifications(chat_id, enable)
    except Exception as e:
        logger.error(f'[NOTIFY] Ошибка переключения для {chat_id}: {e}')
        await api.send_message(
            chat_id,
            '⚠️ Не удалось изменить настройку. Попробуйте позже.',
            reply_markup=keyboards.back_to_menu(),
        )
        return

    if not ok:
        await api.send_message(
            chat_id, NO_SETTINGS,
            reply_markup=keyboards.open_scanner(),
        )
        return

    logger.info(f'[NOTIFY] {chat_id} → уведомления {"вкл" if enable else "выкл"}')

    # Перерисовываем тот же экран с новым статусом
    await show(chat_id, message_id)