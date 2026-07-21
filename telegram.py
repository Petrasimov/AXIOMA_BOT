"""
telegram.py — Отправка сообщений через Telegram Bot API

Использует aiohttp для асинхронных HTTP запросов.
Не требует сторонних Telegram библиотек — только прямые запросы к Bot API.
"""

import logging
import aiohttp

from config import BOT_TOKEN, SCANNER_URL
from filters import normalize_strategy

logger = logging.getLogger(__name__)

TELEGRAM_API = f'https://api.telegram.org/bot{BOT_TOKEN}'


def _format_exchange(ex: str) -> str:
    """Форматирует название биржи для отображения.
    'kucoin_futures' → 'KuCoin Futures'
    'gate_spot'      → 'Gate Spot'
    """
    parts = ex.split('_')
    name   = parts[0].capitalize()
    market = parts[1].capitalize() if len(parts) > 1 else ''

    # Красивые имена для известных бирж
    pretty = {
        'Binance': 'Binance',
        'Bybit':   'Bybit',
        'Okx':     'OKX',
        'Gate':    'Gate',
        'Kucoin':  'KuCoin',
        'Mexc':    'MEXC',
        'Bitget':  'Bitget',
        'Bingx':   'BingX',
    }
    name = pretty.get(name, name)
    return f'{name} {market}' if market else name


def _format_strategy(strategy: str) -> str:
    s = normalize_strategy(strategy)
    return 'Futures-Futures' if s == 'ff' else 'Spot-Futures'


def build_message(opp: dict, trade_amount: float) -> str:
    """Формирует текст Telegram сообщения."""
    spread        = float(opp.get('spread') or 0)
    symbol        = opp.get('symbol', '').replace('USDT', '')
    bid_ex_label  = _format_exchange(opp.get('bid_ex', ''))
    ask_ex_label  = _format_exchange(opp.get('ask_ex', ''))
    strategy_label = _format_strategy(opp.get('strategy', ''))
    profit        = round(spread * trade_amount / 100, 2)

    text = (
        f'🔔 <b>Новая арбитражная возможность!</b>\n\n'
        f'💎 <b>{symbol}/USDT</b>\n'
        f'📊 Спред: <b>{spread:.2f}%</b> (+${profit} при ${int(trade_amount)})\n'
        f'⚖️ Стратегия: {strategy_label}\n'
        f'🏦 {bid_ex_label} → {ask_ex_label}\n'
    )

    return text


async def send_notification(user_id: int, opp: dict, trade_amount: float) -> bool:
    """Отправляет Telegram уведомление пользователю.

    Возвращает True если сообщение отправлено успешно.
    """
    text = build_message(opp, trade_amount)

    payload = {
        'chat_id':    user_id,
        'text':       text,
        'parse_mode': 'HTML',
        'reply_markup': {
            'inline_keyboard': [[
                {'text': '🚀 Открыть сканер', 'url': SCANNER_URL}
            ]]
        }
    }

    url = f'{TELEGRAM_API}/sendMessage'

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
                if resp.status == 200 and data.get('ok'):
                    logger.info(
                        f'[TG] ✅ Отправлено user_id={user_id} '
                        f'symbol={opp["symbol"]} spread={float(opp.get("spread", 0)):.2f}%'
                    )
                    return True
                else:
                    logger.warning(
                        f'[TG] ⚠️ Ошибка отправки user_id={user_id}: '
                        f'status={resp.status} response={data}'
                    )
                    return False
    except Exception as e:
        logger.error(f'[TG] ❌ Исключение при отправке user_id={user_id}: {e}')
        return False


async def check_bot() -> bool:
    """Проверяет что бот работает и токен валиден."""
    url = f'{TELEGRAM_API}/getMe'
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                if data.get('ok'):
                    bot_name = data['result'].get('username', '?')
                    logger.info(f'[TG] Бот подключён: @{bot_name}')
                    return True
                else:
                    logger.error(f'[TG] Бот не отвечает: {data}')
                    return False
    except Exception as e:
        logger.error(f'[TG] Ошибка подключения к боту: {e}')
        return False