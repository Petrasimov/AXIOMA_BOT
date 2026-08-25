"""
api.py — Обёртка над Telegram Bot API

Единственная точка выхода в Telegram для всего процесса.

Что берёт на себя:
  - одна aiohttp.ClientSession на весь процесс (а не на каждое сообщение)
  - троттлинг: ~25 сообщений/сек глобально и 1/сек в один чат
  - обработка 429 Too Many Requests с уважением retry_after
  - обработка 403 (бот заблокирован) через отдельное исключение
  - повтор при сетевых ошибках и 5xx

Long-polling (get_updates) намеренно идёт мимо троттлинга и с собственным
таймаутом: соединение висит открытым 30 секунд, обычный таймаут его порвёт.
"""

import asyncio
import logging
from collections import deque

import aiohttp

import config

logger = logging.getLogger(__name__)

API_BASE = f'https://api.telegram.org/bot{config.BOT_TOKEN}'

# ─── Состояние модуля ────────────────────────────────────────────────────────

_session: aiohttp.ClientSession | None = None

# Метки времени отправок за последнюю секунду — для глобального лимита
_global_window: deque[float] = deque()

# Время последней отправки в каждый чат — для лимита 1/сек на чат
_chat_last: dict[int, float] = {}

# Троттлинг сериализован: держим блокировку в том числе во время сна.
# При наших объёмах это дешевле и предсказуемее, чем гонки за окно.
_throttle_lock: asyncio.Lock | None = None


# ─── Исключения ──────────────────────────────────────────────────────────────

class TelegramError(Exception):
    """Базовая ошибка Bot API."""


class BotBlocked(TelegramError):
    """403 — пользователь заблокировал бота или чат недоступен.

    Вызывающий код должен погасить уведомления для этого пользователя.
    """


class TelegramConflict(TelegramError):
    """409 — тем же токеном уже кто-то поллит getUpdates.

    Означает что рядом работает второй экземпляр бота либо остался
    включённым старый systemd юнит.
    """


# ─── Жизненный цикл ──────────────────────────────────────────────────────────

async def init():
    """Создаёт HTTP сессию. Вызывается один раз при старте."""
    global _session, _throttle_lock
    if _session is None:
        _session = aiohttp.ClientSession()
        _throttle_lock = asyncio.Lock()
        logger.info('[API] HTTP сессия создана')


async def close():
    """Закрывает HTTP сессию."""
    global _session
    if _session:
        await _session.close()
        _session = None
        logger.info('[API] HTTP сессия закрыта')


# ─── Троттлинг ───────────────────────────────────────────────────────────────

async def _throttle(chat_id: int | None):
    """Ждёт столько, сколько нужно чтобы не упереться в лимиты Telegram."""
    if _throttle_lock is None:
        return

    loop = asyncio.get_running_loop()

    async with _throttle_lock:
        now = loop.time()

        # Глобальный лимит — скользящее окно в одну секунду
        while _global_window and now - _global_window[0] > 1.0:
            _global_window.popleft()

        if len(_global_window) >= config.RATE_GLOBAL_PER_SEC:
            wait = 1.0 - (now - _global_window[0])
            if wait > 0:
                await asyncio.sleep(wait)
            now = loop.time()
            while _global_window and now - _global_window[0] > 1.0:
                _global_window.popleft()

        # Лимит на конкретный чат
        if chat_id is not None:
            last = _chat_last.get(chat_id)
            if last is not None:
                delta = now - last
                if delta < config.RATE_CHAT_INTERVAL:
                    await asyncio.sleep(config.RATE_CHAT_INTERVAL - delta)
                    now = loop.time()
            _chat_last[chat_id] = now

            # Не даём словарю расти бесконечно
            if len(_chat_last) > 5000:
                cutoff = now - 60
                for cid in [c for c, t in _chat_last.items() if t < cutoff]:
                    del _chat_last[cid]

        _global_window.append(now)


# ─── Базовый запрос ──────────────────────────────────────────────────────────

async def _request(
    method: str,
    payload: dict | None = None,
    *,
    timeout: float = 15.0,
    throttle_chat: int | None = None,
    retries: int = 2,
):
    """Выполняет запрос к Bot API и возвращает поле result.

    Бросает BotBlocked при 403, TelegramConflict при 409,
    TelegramError при остальных неустранимых ошибках.
    """
    if _session is None:
        raise TelegramError('HTTP сессия не инициализирована — вызовите api.init()')

    url = f'{API_BASE}/{method}'
    payload = payload or {}

    for attempt in range(retries + 1):
        if throttle_chat is not None:
            await _throttle(throttle_chat)

        try:
            async with _session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                data = await resp.json()

                if data.get('ok'):
                    return data.get('result')

                code = data.get('error_code', resp.status)
                desc = data.get('description', '')

                # 429 — превысили лимит, Telegram сам говорит сколько ждать
                if code == 429:
                    retry_after = data.get('parameters', {}).get('retry_after', 1)
                    logger.warning(
                        f'[API] 429 на {method}, ждём {retry_after}с '
                        f'(попытка {attempt + 1}/{retries + 1})'
                    )
                    if attempt < retries:
                        await asyncio.sleep(retry_after + 0.5)
                        continue
                    raise TelegramError(f'429 после всех попыток: {desc}')

                # 403 — заблокировали бота, повторять бессмысленно
                if code == 403:
                    raise BotBlocked(desc)

                # 409 — конфликт getUpdates
                if code == 409:
                    raise TelegramConflict(desc)

                # 5xx — временная проблема на стороне Telegram
                if code >= 500 and attempt < retries:
                    logger.warning(f'[API] {code} на {method}, повтор: {desc}')
                    await asyncio.sleep(1 + attempt)
                    continue

                raise TelegramError(f'{method} вернул {code}: {desc}')

        except (BotBlocked, TelegramConflict, TelegramError):
            raise
        except asyncio.TimeoutError:
            if attempt < retries:
                logger.warning(f'[API] Таймаут на {method}, повтор')
                await asyncio.sleep(1 + attempt)
                continue
            raise TelegramError(f'Таймаут на {method}')
        except aiohttp.ClientError as e:
            if attempt < retries:
                logger.warning(f'[API] Сетевая ошибка на {method}: {e}, повтор')
                await asyncio.sleep(1 + attempt)
                continue
            raise TelegramError(f'Сетевая ошибка на {method}: {e}')

    raise TelegramError(f'{method}: исчерпаны попытки')


# ─── Методы Bot API ──────────────────────────────────────────────────────────

async def get_me() -> dict:
    """Информация о боте. Заодно проверяет что токен валиден."""
    return await _request('getMe', timeout=10)


async def send_message(
    chat_id: int,
    text: str,
    *,
    reply_markup: dict | None = None,
    parse_mode: str | None = 'HTML',
    reply_to_message_id: int | None = None,
    disable_web_page_preview: bool = True,
) -> dict:
    """Отправляет текстовое сообщение. Возвращает объект Message."""
    payload: dict = {
        'chat_id': chat_id,
        'text': text,
        'disable_web_page_preview': disable_web_page_preview,
    }
    if parse_mode:
        payload['parse_mode'] = parse_mode
    if reply_markup:
        payload['reply_markup'] = reply_markup
    if reply_to_message_id:
        payload['reply_to_message_id'] = reply_to_message_id

    return await _request('sendMessage', payload, throttle_chat=chat_id)


async def edit_message_text(
    chat_id: int,
    message_id: int,
    text: str,
    *,
    reply_markup: dict | None = None,
    parse_mode: str | None = 'HTML',
) -> dict:
    """Редактирует текст ранее отправленного сообщения."""
    payload: dict = {
        'chat_id': chat_id,
        'message_id': message_id,
        'text': text,
    }
    if parse_mode:
        payload['parse_mode'] = parse_mode
    if reply_markup:
        payload['reply_markup'] = reply_markup

    return await _request('editMessageText', payload, throttle_chat=chat_id)


async def edit_message_reply_markup(
    chat_id: int,
    message_id: int,
    reply_markup: dict | None = None,
) -> dict:
    """Меняет только клавиатуру сообщения. None убирает её."""
    payload: dict = {
        'chat_id': chat_id,
        'message_id': message_id,
    }
    if reply_markup is not None:
        payload['reply_markup'] = reply_markup

    return await _request('editMessageReplyMarkup', payload, throttle_chat=chat_id)


async def answer_callback_query(
    callback_query_id: str,
    text: str | None = None,
    show_alert: bool = False,
) -> bool:
    """Гасит "часики" на нажатой инлайн-кнопке.

    Telegram ждёт этого ответа — без него кнопка висит в состоянии загрузки.
    """
    payload: dict = {'callback_query_id': callback_query_id}
    if text:
        payload['text'] = text
    if show_alert:
        payload['show_alert'] = True

    return await _request('answerCallbackQuery', payload, timeout=10)


async def delete_message(chat_id: int, message_id: int) -> bool:
    """Удаляет сообщение.

    Нужен Задаче 6: сообщение с новым паролем удаляется сразу после
    обработки, иначе пароль остаётся висеть в истории чата открытым
    текстом — и у пользователя, и на серверах Telegram.

    Ошибку не считаем фатальной: сообщение старше 48 часов Telegram
    удалить не даст, но это не повод ронять сценарий.
    """
    try:
        await _request(
            'deleteMessage',
            {'chat_id': chat_id, 'message_id': message_id},
            timeout=10,
        )
        return True
    except TelegramError as e:
        logger.debug(f'[API] Не удалось удалить сообщение {message_id}: {e}')
        return False


async def send_photo(
    chat_id: int,
    photo: bytes,
    *,
    caption: str | None = None,
    reply_markup: dict | None = None,
    parse_mode: str | None = 'HTML',
) -> dict:
    """Отправляет фото (QR-код) с подписью.

    Идёт multipart/form-data, а не JSON — поэтому мимо общего
    _request, но троттлинг соблюдаем вручную.
    """
    if _session is None:
        raise TelegramError('HTTP сессия не инициализирована')

    await _throttle(chat_id)

    form = aiohttp.FormData()
    form.add_field('chat_id', str(chat_id))
    form.add_field('photo', photo, filename='qr.png', content_type='image/png')
    if caption:
        form.add_field('caption', caption)
    if parse_mode:
        form.add_field('parse_mode', parse_mode)
    if reply_markup:
        import json as _json
        form.add_field('reply_markup', _json.dumps(reply_markup))

    try:
        async with _session.post(
            f'{API_BASE}/sendPhoto',
            data=form,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            data = await resp.json()
            if data.get('ok'):
                return data.get('result')

            code = data.get('error_code', resp.status)
            desc = data.get('description', '')
            if code == 403:
                raise BotBlocked(desc)
            raise TelegramError(f'sendPhoto вернул {code}: {desc}')

    except (BotBlocked, TelegramError):
        raise
    except Exception as e:
        raise TelegramError(f'sendPhoto: {e}')


async def get_updates(offset: int, timeout: int) -> list[dict]:
    """Long-polling за новыми апдейтами.

    Идёт мимо троттлинга и с расширенным таймаутом: соединение
    намеренно висит открытым до timeout секунд.
    """
    payload = {
        'offset': offset,
        'timeout': timeout,
        'allowed_updates': ['message', 'callback_query'],
    }
    result = await _request(
        'getUpdates',
        payload,
        timeout=timeout + 15,
        retries=0,
    )
    return result or []