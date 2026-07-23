"""
nowpayments.py — Клиент NOWPayments API

Документация: https://documenter.getpostman.com/view/7907941/S1a32n38

Используем три метода:
  GET  /v1/status              — жив ли сервис
  POST /v1/payment             — создать платёж
  GET  /v1/payment/{id}        — статус платежа
  GET  /v1/payment/            — список платежей (сверка после рестарта)

Своей истории платежей мы не ведём — хранилищем работает
сам NOWPayments. Связь платежа с пользователем держится через
order_id вида "axioma_{user_id}_{timestamp}".
"""

import logging
import time

import aiohttp

import config

logger = logging.getLogger(__name__)

API_BASE = 'https://api.nowpayments.io/v1'

# Монеты которые предлагаем. Ключ — код NOWPayments.
CURRENCIES = {
    'usdtbsc':  {'label': 'USDT (BEP-20)', 'network': 'BEP-20 (BSC)'},
    'usdttrc20': {'label': 'USDT (TRC-20)', 'network': 'TRC-20 (Tron)'},
    'usdterc20': {'label': 'USDT (ERC-20)', 'network': 'ERC-20 (Ethereum)'},
    'bnbbsc':   {'label': 'BNB',           'network': 'BEP-20 (BSC)'},
    'eth':      {'label': 'ETH',           'network': 'Ethereum'},
}

# Статусы при которых деньги считаются полученными
FINAL_OK = {'finished', 'confirmed'}

# Статусы при которых ждать дальше бессмысленно
FINAL_FAIL = {'failed', 'refunded', 'expired'}


class NowPaymentsError(Exception):
    """Ошибка обращения к NOWPayments."""


def make_order_id(user_id: int) -> str:
    """Идентификатор заказа.

    В нём зашит user_id — это позволяет восстановить связь платежа
    с пользователем после рестарта бота, не храня ничего у себя.
    """
    return f'axioma_{user_id}_{int(time.time())}'


def parse_order_id(order_id: str) -> int | None:
    """Достаёт user_id из order_id. None если формат чужой."""
    if not order_id or not order_id.startswith('axioma_'):
        return None
    parts = order_id.split('_')
    if len(parts) < 3 or not parts[1].isdigit():
        return None
    return int(parts[1])


async def _request(method: str, path: str, **kwargs) -> dict:
    """Запрос к API с ключом из .env."""
    if not config.NOWPAYMENTS_API_KEY:
        raise NowPaymentsError('NOWPAYMENTS_API_KEY не задан в .env')

    headers = {
        'x-api-key': config.NOWPAYMENTS_API_KEY,
        'Content-Type': 'application/json',
    }

    url = f'{API_BASE}{path}'

    try:
        async with aiohttp.ClientSession() as session:
            async with session.request(
                method, url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=20),
                **kwargs,
            ) as resp:
                data = await resp.json()

                if resp.status >= 400:
                    msg = data.get('message') or data
                    raise NowPaymentsError(f'{resp.status}: {msg}')

                return data

    except NowPaymentsError:
        raise
    except Exception as e:
        raise NowPaymentsError(f'Сетевая ошибка: {e}')


async def check_status() -> bool:
    """Проверяет доступность сервиса."""
    try:
        data = await _request('GET', '/status')
        return data.get('message') == 'OK'
    except NowPaymentsError as e:
        logger.warning(f'[NP] Сервис недоступен: {e}')
        return False


async def create_payment(user_id: int, pay_currency: str) -> dict:
    """Создаёт платёж. Возвращает данные для оплаты."""
    order_id = make_order_id(user_id)

    payload = {
        'price_amount':   config.SUBSCRIPTION_PRICE_USD,
        'price_currency': 'usd',
        'pay_currency':   pay_currency,
        'order_id':       order_id,
        'order_description': 'AXIOMA SCAN — подписка на 1 месяц',
    }

    data = await _request('POST', '/payment', json=payload)

    logger.info(
        f'[NP] Платёж создан: user={user_id} '
        f'payment_id={data.get("payment_id")} '
        f'{data.get("pay_amount")} {pay_currency}'
    )

    return {
        'payment_id':  str(data.get('payment_id')),
        'order_id':    order_id,
        'pay_address': data.get('pay_address'),
        'pay_amount':  data.get('pay_amount'),
        'pay_currency': data.get('pay_currency', pay_currency),
        'expiration':  data.get('expiration_estimate_date'),
    }


async def get_payment(payment_id: str) -> dict:
    """Статус конкретного платежа."""
    return await _request('GET', f'/payment/{payment_id}')


async def list_recent_payments(limit: int = 100) -> list[dict]:
    """Последние платежи — для сверки после рестарта бота.

    Отдаём как есть, фильтрация на стороне вызывающего.
    """
    data = await _request('GET', f'/payment/?limit={limit}&page=0')
    return data.get('data', []) or []