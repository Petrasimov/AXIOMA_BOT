"""
sender.py — Формирование и отправка уведомлений

Бывший telegram.py. Отправка переведена на общий bot/api.py:
теперь используется одна HTTP сессия на весь процесс, работает
троттлинг и обработка 429/403.
"""

import logging

from bot import api, keyboards
from notifications.filters import normalize_strategy

logger = logging.getLogger(__name__)


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

    При 403 (пользователь заблокировал бота) пробрасывает BotBlocked —
    вызывающий код должен погасить уведомления этому пользователю.
    """
    text = build_message(opp, trade_amount)

    try:
        await api.send_message(
            user_id,
            text,
            reply_markup=keyboards.open_scanner(),
        )
        logger.info(
            f'[TG] ✅ Отправлено user_id={user_id} '
            f'symbol={opp.get("symbol")} spread={float(opp.get("spread") or 0):.2f}%'
        )
        return True

    except api.BotBlocked:
        # Пробрасываем — обработка в service.py (Задача 2)
        raise

    except api.TelegramError as e:
        logger.warning(f'[TG] ⚠️ Ошибка отправки user_id={user_id}: {e}')
        return False


async def check_bot() -> bool:
    """Проверяет что бот работает и токен валиден."""
    try:
        me = await api.get_me()
        logger.info(f'[TG] Бот подключён: @{me.get("username", "?")}')
        return True
    except api.TelegramError as e:
        logger.error(f'[TG] Ошибка подключения к боту: {e}')
        return False