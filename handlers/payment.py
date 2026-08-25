"""
payment.py — Раздел "💳 Купить подписку"

Оплата криптовалютой через NOWPayments. Своей истории платежей
не ведём — хранилищем работает сам NOWPayments, а связь платежа
с пользователем держится через order_id вида "axioma_{user_id}_{ts}".

Три уровня защиты от потери платежа при рестарте бота:

  1. Фоновый опрос статуса каждые 30с — пока процесс жив
  2. Кнопка "Проверить оплату" — живёт в самом сообщении,
     работает после любого рестарта
  3. Сверка на старте — просматриваем платежи за сутки и добиваем
     доступ тем, у кого он ещё не выдан (см. reconcile())

⚠️ Таймер обратного отсчёта намеренно не перерисовывается каждую
минуту: это 30 правок сообщения на каждый платёж. Вместо этого
показываем конкретное время дедлайна, а по истечении редактируем
сообщение один раз.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import config
import db
from bot import api, keyboards
from utils import nowpayments as np
from utils.qr import make_qr

logger = logging.getLogger(__name__)

MSK = timezone(timedelta(hours=3))

# Интервал опроса статуса платежа
POLL_INTERVAL = 30

# payment_id, по которым доступ уже выдан в этой сессии.
# Защита от двойного продления при повторном нажатии кнопки.
_granted: set[str] = set()


NOT_REGISTERED = (
    '🔒 <b>Вы не зарегистрированы</b>\n\n'
    'Чтобы оформить подписку, сначала войдите на сайте через Telegram.'
)

# Пока ключ NOWPayments не задан, платёж создать невозможно. Без этой
# проверки пользователь прошёл бы весь путь — экран монет, выбор сети,
# «создаю платёж...» — и упёрся в техническую ошибку. Честнее сказать
# сразу и дать живой канал связи.
PAYMENT_DISABLED = (
    '💳 <b>Оплата скоро откроется</b>\n\n'
    'Приём криптоплатежей ещё настраивается. Чтобы оформить подписку '
    'сейчас — напишите менеджеру через раздел «Поддержка» в меню.'
)


# ─── Экран выбора монеты ─────────────────────────────────────────────────────

def _currency_keyboard() -> dict:
    rows = [
        [{'text': 'USDT (BEP-20)', 'callback_data': 'pay:new:usdtbsc'}],
        [{'text': 'USDT (TRC-20)', 'callback_data': 'pay:new:usdttrc20'}],
        [{'text': 'USDT (ERC-20)', 'callback_data': 'pay:new:usdterc20'}],
        [
            {'text': 'BNB', 'callback_data': 'pay:new:bnbbsc'},
            {'text': 'ETH', 'callback_data': 'pay:new:eth'},
        ],
        [{'text': '◀️ В меню', 'callback_data': 'menu:main'}],
    ]
    return {'inline_keyboard': rows}


async def show(chat_id: int, message_id: int | None = None):
    """Показывает экран выбора монеты."""
    if not config.NOWPAYMENTS_API_KEY:
        logger.warning('[PAY] NOWPAYMENTS_API_KEY не задан — раздел оплаты закрыт')
        await api.send_message(
            chat_id, PAYMENT_DISABLED,
            reply_markup=keyboards.back_to_menu(),
        )
        return

    try:
        state = await db.get_user_state(chat_id)
    except Exception as e:
        logger.error(f'[PAY] Ошибка получения состояния {chat_id}: {e}')
        await api.send_message(
            chat_id, '⚠️ Не удалось проверить аккаунт. Попробуйте позже.',
            reply_markup=keyboards.back_to_menu(),
        )
        return

    if state is None:
        await api.send_message(
            chat_id, NOT_REGISTERED, reply_markup=keyboards.open_scanner(),
        )
        return

    price = config.SUBSCRIPTION_PRICE_USD
    text = (
        f'💳 <b>Подписка AXIOMA SCAN</b>\n\n'
        f'Стоимость: <b>${price:g}</b> за 1 месяц\n'
        f'Оплата криптовалютой, доступ открывается автоматически.\n\n'
        f'Выберите монету и сеть:'
    )

    if state.get('is_paid'):
        text += '\n\n✅ <i>Подписка уже активна — оплата продлит её.</i>'

    markup = _currency_keyboard()

    if message_id:
        try:
            await api.edit_message_text(chat_id, message_id, text, reply_markup=markup)
            return
        except api.TelegramError:
            pass

    await api.send_message(chat_id, text, reply_markup=markup)


# ─── Создание платежа ────────────────────────────────────────────────────────

async def create(callback: dict, currency: str):
    """Создаёт платёж и присылает реквизиты."""
    chat_id = callback['message']['chat']['id']

    if currency not in np.CURRENCIES:
        logger.warning(f'[PAY] Неизвестная монета: {currency}')
        return

    try:
        state = await db.get_user_state(chat_id)
    except Exception as e:
        logger.error(f'[PAY] Ошибка проверки аккаунта {chat_id}: {e}')
        state = None

    if state is None:
        await api.send_message(
            chat_id, NOT_REGISTERED, reply_markup=keyboards.open_scanner(),
        )
        return

    if not config.NOWPAYMENTS_API_KEY:
        await api.send_message(
            chat_id, PAYMENT_DISABLED,
            reply_markup=keyboards.back_to_menu(),
        )
        return

    await api.send_message(chat_id, '⏳ Создаю платёж...')

    try:
        payment = await np.create_payment(chat_id, currency)
    except np.NowPaymentsError as e:
        logger.error(f'[PAY] Не удалось создать платёж для {chat_id}: {e}')
        await api.send_message(
            chat_id,
            '⚠️ Не удалось создать платёж. Попробуйте позже '
            'или выберите другую монету.',
            reply_markup=keyboards.back_to_menu(),
        )
        return

    meta     = np.CURRENCIES[currency]
    deadline = datetime.now(MSK) + timedelta(minutes=config.PAYMENT_TIMEOUT_MINUTES)

    text = (
        f'💳 <b>ОПЛАТА ПОДПИСКИ AXIOMA SCAN</b>\n\n'
        f'Сумма: <b>{payment["pay_amount"]} {meta["label"]}</b>\n'
        f'Сеть: <b>{meta["network"]}</b>\n'
        f'⏰ Оплатите до <b>{deadline.strftime("%H:%M")} МСК</b>\n\n'
        f'Адрес для оплаты:\n'
        f'<code>{payment["pay_address"]}</code>\n'
        f'<i>(нажмите чтобы скопировать)</i>\n\n'
        f'⚠️ Отправляйте <b>точную сумму</b> именно в сети '
        f'<b>{meta["network"]}</b>. Перевод в другой сети будет потерян.'
    )

    markup = {
        'inline_keyboard': [
            [{'text': '🔄 Проверить оплату',
              'callback_data': f'pay:check:{payment["payment_id"]}'}],
            [{'text': '◀️ В меню', 'callback_data': 'menu:main'}],
        ]
    }

    # QR-код: если не получился — не беда, адрес есть текстом
    qr = await make_qr(payment['pay_address'])
    if qr:
        try:
            await api.send_photo(chat_id, qr, caption=text, reply_markup=markup)
        except api.TelegramError as e:
            logger.debug(f'[PAY] QR не отправился: {e}')
            await api.send_message(chat_id, text, reply_markup=markup)
    else:
        await api.send_message(chat_id, text, reply_markup=markup)

    # Фоновый опрос статуса
    asyncio.create_task(_watch(chat_id, payment['payment_id']))


# ─── Опрос статуса ───────────────────────────────────────────────────────────

async def _watch(chat_id: int, payment_id: str):
    """Опрашивает статус платежа до успеха или таймаута."""
    deadline = datetime.now(timezone.utc) + timedelta(
        minutes=config.PAYMENT_TIMEOUT_MINUTES
    )

    while datetime.now(timezone.utc) < deadline:
        await asyncio.sleep(POLL_INTERVAL)

        try:
            data = await np.get_payment(payment_id)
        except np.NowPaymentsError as e:
            logger.debug(f'[PAY] Опрос {payment_id}: {e}')
            continue

        status = (data.get('payment_status') or '').lower()

        if status in np.FINAL_OK:
            await _on_success(chat_id, payment_id)
            return

        if status == 'partially_paid':
            await _on_partial(chat_id, payment_id, data)
            return

        if status in np.FINAL_FAIL:
            logger.info(f'[PAY] Платёж {payment_id} завершился со статусом {status}')
            await api.send_message(
                chat_id,
                '⚠️ Платёж не прошёл. Попробуйте создать новый.',
                reply_markup=keyboards.back_to_menu(),
            )
            return

    logger.info(f'[PAY] Платёж {payment_id} — таймаут')
    try:
        await api.send_message(
            chat_id,
            '⌛ <b>Время на оплату истекло.</b>\n\n'
            'Если вы уже отправили перевод — нажмите «Проверить оплату» '
            'на сообщении с реквизитами, средства не потеряются.',
            reply_markup=keyboards.back_to_menu(),
        )
    except api.TelegramError:
        pass


async def check(callback: dict, payment_id: str):
    """Ручная проверка по кнопке «Проверить оплату»."""
    chat_id = callback['message']['chat']['id']

    try:
        data = await np.get_payment(payment_id)
    except np.NowPaymentsError as e:
        logger.error(f'[PAY] Ручная проверка {payment_id}: {e}')
        await api.send_message(
            chat_id, '⚠️ Не удалось проверить платёж. Попробуйте через минуту.',
        )
        return

    status = (data.get('payment_status') or '').lower()

    if status in np.FINAL_OK:
        await _on_success(chat_id, payment_id)
        return

    if status == 'partially_paid':
        await _on_partial(chat_id, payment_id, data)
        return

    if status in np.FINAL_FAIL:
        await api.send_message(
            chat_id, '⚠️ Платёж не прошёл. Создайте новый.',
            reply_markup=keyboards.back_to_menu(),
        )
        return

    await api.send_message(
        chat_id,
        '⏳ Платёж пока не подтверждён.\n\n'
        'Если вы только что отправили перевод — подтверждение сети '
        'может занять несколько минут.',
    )


# ─── Результаты ──────────────────────────────────────────────────────────────

async def _on_success(chat_id: int, payment_id: str):
    """Выдаёт доступ после подтверждённой оплаты."""
    if payment_id in _granted:
        logger.debug(f'[PAY] {payment_id} уже обработан')
        return
    _granted.add(payment_id)

    try:
        paid_until = await db.grant_access(chat_id)
    except Exception as e:
        logger.error(f'[PAY] Оплата прошла, но доступ не выдан {chat_id}: {e}')
        _granted.discard(payment_id)
        await api.send_message(
            chat_id,
            '⚠️ Оплата получена, но доступ не открылся автоматически. '
            'Напишите в поддержку — откроем вручную.',
            reply_markup=keyboards.back_to_menu(),
        )
        await _notify_admins(
            f'⚠️ Оплата прошла, доступ НЕ выдан\n'
            f'Пользователь: <code>{chat_id}</code>\n'
            f'Платёж: <code>{payment_id}</code>\n'
            f'Ошибка: {e}'
        )
        return

    logger.info(f'[PAY] Доступ выдан {chat_id} по платежу {payment_id}')

    text = (
        '✅ <b>Оплата получена!</b>\n\n'
        'Подписка активирована. Добро пожаловать в AXIOMA SCAN 🚀'
    )
    if paid_until:
        text += f'\n\nДоступ активен до <b>{paid_until.strftime("%d.%m.%Y")}</b>'

    await api.send_message(chat_id, text, reply_markup=keyboards.open_scanner())

    await _notify_admins(
        f'💰 Новая оплата\n'
        f'Пользователь: <code>{chat_id}</code>\n'
        f'Платёж: <code>{payment_id}</code>'
    )


async def _on_partial(chat_id: int, payment_id: str, data: dict):
    """Пришло меньше суммы. Доступ не выдаём, зовём админов."""
    actual   = data.get('actually_paid', '?')
    expected = data.get('pay_amount', '?')

    logger.warning(
        f'[PAY] Частичная оплата {payment_id}: {actual} из {expected}'
    )

    await api.send_message(
        chat_id,
        f'⚠️ <b>Получена неполная сумма</b>\n\n'
        f'Пришло: {actual}\nОжидалось: {expected}\n\n'
        f'Мы уже разбираемся — напишите в поддержку, решим вопрос.',
        reply_markup=keyboards.back_to_menu(),
    )

    await _notify_admins(
        f'⚠️ ЧАСТИЧНАЯ ОПЛАТА — требуется решение\n'
        f'Пользователь: <code>{chat_id}</code>\n'
        f'Платёж: <code>{payment_id}</code>\n'
        f'Пришло: {actual} из {expected}'
    )


async def _notify_admins(text: str):
    """Сообщение в админ-чат. Молча пропускаем если чат не настроен."""
    if not config.ADMIN_CHAT_ID:
        return
    try:
        await api.send_message(config.ADMIN_CHAT_ID, text)
    except api.TelegramError as e:
        logger.debug(f'[PAY] Не удалось уведомить админов: {e}')


# ─── Сверка после рестарта ───────────────────────────────────────────────────

async def reconcile():
    """Догоняет платежи, прошедшие пока бот лежал.

    Просматривает свежие платежи, вытаскивает user_id из order_id
    и выдаёт доступ тем, у кого его ещё нет. Проверка текущего
    состояния в БД защищает от повторного продления.
    """
    if not config.NOWPAYMENTS_API_KEY:
        logger.info('[PAY] NOWPAYMENTS_API_KEY не задан — сверка пропущена')
        return

    try:
        payments = await np.list_recent_payments(limit=100)
    except np.NowPaymentsError as e:
        logger.warning(f'[PAY] Сверка не удалась: {e}')
        return

    restored = 0

    for p in payments:
        status = (p.get('payment_status') or '').lower()
        if status not in np.FINAL_OK:
            continue

        user_id = np.parse_order_id(p.get('order_id', ''))
        if not user_id:
            continue

        payment_id = str(p.get('payment_id'))
        if payment_id in _granted:
            continue

        try:
            state = await db.get_user_state(user_id)
        except Exception as e:
            logger.error(f'[PAY] Сверка: не удалось проверить {user_id}: {e}')
            continue

        # Доступ уже есть — платёж был обработан до рестарта
        if state is None or state.get('is_paid'):
            _granted.add(payment_id)
            continue

        try:
            await db.grant_access(user_id)
            _granted.add(payment_id)
            restored += 1
            logger.info(f'[PAY] Сверка: доступ восстановлен для {user_id}')

            await api.send_message(
                user_id,
                '✅ <b>Оплата получена!</b>\n\nПодписка активирована 🚀',
                reply_markup=keyboards.open_scanner(),
            )
        except Exception as e:
            logger.error(f'[PAY] Сверка: не удалось выдать доступ {user_id}: {e}')

    if restored:
        logger.info(f'[PAY] Сверка завершена, восстановлено доступов: {restored}')
    else:
        logger.info('[PAY] Сверка завершена, необработанных платежей нет')