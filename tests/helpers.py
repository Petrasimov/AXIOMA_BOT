"""
helpers.py — Общие заглушки для тестов

FakeAPI перехватывает всё, что бот пытается отправить в Telegram,
и складывает в список. Это позволяет проверять не «не упало ли»,
а что именно ушло, кому и с какой клавиатурой.

FakeDB держит состояние пользователей в словаре — база не нужна.
"""

import re
from datetime import datetime, timedelta, timezone


def strip_html(text: str) -> str:
    """Убирает HTML-теги.

    Telegram отдаёт reply_to_message.text уже без разметки, поэтому
    в тестах реплаев карточку нужно прогонять через эту функцию —
    иначе тест проверял бы не тот текст, который придёт в проде.
    """
    return re.sub(r'<[^>]+>', '', text)


class Sent:
    """Одно перехваченное обращение к Telegram."""

    def __init__(self, kind, chat_id, text=None, markup=None, **extra):
        self.kind = kind          # send | edit | photo | markup | callback
        self.chat_id = chat_id
        self.text = text or ''
        self.markup = markup
        self.extra = extra

    def buttons(self):
        """Плоский список всех кнопок клавиатуры."""
        if not self.markup or 'inline_keyboard' not in self.markup:
            return []
        return [b for row in self.markup['inline_keyboard'] for b in row]

    def callbacks(self):
        """callback_data всех кнопок."""
        return [b.get('callback_data') for b in self.buttons()
                if 'callback_data' in b]

    def __repr__(self):
        return f'<Sent {self.kind} chat={self.chat_id} {self.text[:40]!r}>'


class FakeAPI:
    """Подменяет модуль bot.api.

    Патчим атрибуты самого модуля, а не отдельные импорты: во всех
    обработчиках используется `from bot import api`, то есть привязан
    модуль, и подмена его функций видна везде сразу.
    """

    def __init__(self, api_module):
        self.api = api_module
        self.sent: list[Sent] = []
        self._blocked: set[int] = set()
        self._fail: set[int] = set()
        self._original = {}

    # ─── Управление поведением ──────────────────────────────────────

    def block(self, chat_id: int):
        """Пользователь заблокировал бота — отправка кинет BotBlocked."""
        self._blocked.add(chat_id)

    def fail(self, chat_id: int):
        """Отправка в этот чат будет падать с TelegramError."""
        self._fail.add(chat_id)

    # ─── Выборки ────────────────────────────────────────────────────

    def to(self, chat_id: int) -> list[Sent]:
        return [s for s in self.sent if s.chat_id == chat_id]

    def texts(self, chat_id: int | None = None) -> list[str]:
        items = self.sent if chat_id is None else self.to(chat_id)
        return [s.text for s in items]

    def last(self, chat_id: int | None = None) -> Sent | None:
        items = self.sent if chat_id is None else self.to(chat_id)
        return items[-1] if items else None

    def clear(self):
        self.sent.clear()

    def any_text_contains(self, needle: str, chat_id: int | None = None) -> bool:
        return any(needle in t for t in self.texts(chat_id))

    # ─── Подмена ────────────────────────────────────────────────────

    def _check(self, chat_id):
        if chat_id in self._blocked:
            raise self.api.BotBlocked('bot was blocked by the user')
        if chat_id in self._fail:
            raise self.api.TelegramError('chat not found')

    def install(self):
        api = self.api
        for name in ('send_message', 'edit_message_text', 'send_photo',
                     'edit_message_reply_markup', 'answer_callback_query'):
            self._original[name] = getattr(api, name, None)

        async def send_message(chat_id, text, **kw):
            self._check(chat_id)
            self.sent.append(Sent('send', chat_id, text,
                                  kw.get('reply_markup'),
                                  reply_to=kw.get('reply_to_message_id')))
            return {'message_id': 1000 + len(self.sent)}

        async def edit_message_text(chat_id, message_id, text, **kw):
            self._check(chat_id)
            self.sent.append(Sent('edit', chat_id, text,
                                  kw.get('reply_markup'), message_id=message_id))
            return {'message_id': message_id}

        async def send_photo(chat_id, photo, **kw):
            self._check(chat_id)
            self.sent.append(Sent('photo', chat_id, kw.get('caption', ''),
                                  kw.get('reply_markup'), photo_size=len(photo)))
            return {'message_id': 2000 + len(self.sent)}

        async def edit_message_reply_markup(chat_id, message_id, reply_markup=None):
            self.sent.append(Sent('markup', chat_id, '', reply_markup,
                                  message_id=message_id))
            return {'message_id': message_id}

        async def answer_callback_query(callback_query_id, text=None, show_alert=False):
            self.sent.append(Sent('callback', 0, text or '',
                                  None, callback_id=callback_query_id))
            return True

        api.send_message = send_message
        api.edit_message_text = edit_message_text
        api.send_photo = send_photo
        api.edit_message_reply_markup = edit_message_reply_markup
        api.answer_callback_query = answer_callback_query
        return self

    def uninstall(self):
        for name, fn in self._original.items():
            if fn is not None:
                setattr(self.api, name, fn)


class FakeDB:
    """Подменяет модуль db. Состояние живёт в словарях."""

    def __init__(self, db_module):
        self.db = db_module
        self.users: dict[int, dict] = {}
        self.cooldowns: dict[tuple, dict] = {}
        self.granted: list[int] = []
        self.notifications_set: list[tuple] = []
        self.opportunities: list[dict] = []
        self._original = {}

    # ─── Подготовка данных ──────────────────────────────────────────

    def add_user(self, user_id, is_paid=True, is_active=True,
                 notifications_on=True, **settings):
        self.users[user_id] = {
            'user_id': user_id,
            'is_paid': is_paid,
            'is_active': is_active,
            'notifications_on': notifications_on,
            'min_spread': settings.get('min_spread', 1.0),
            'strategy_ff': settings.get('strategy_ff', True),
            'strategy_sf': settings.get('strategy_sf', True),
            'exchanges': settings.get('exchanges', []),
            'trade_amount': settings.get('trade_amount', 100),
        }
        return self.users[user_id]

    def add_cooldown(self, user_id, key, hours_ago=0.0,
                     last_spread=3.0, dropped=False):
        self.cooldowns[(user_id, *key)] = {
            'sent_at': datetime.now(timezone.utc) - timedelta(hours=hours_ago),
            'last_spread': last_spread,
            'dropped_since': dropped,
        }

    # ─── Подмена ────────────────────────────────────────────────────

    def install(self):
        db = self.db
        names = ('get_user_state', 'set_notifications', 'disable_notifications',
                 'get_user_cooldowns', 'upsert_cooldown', 'mark_dropped',
                 'update_spreads', 'grant_access', 'get_current_opportunities',
                 'get_active_notification_users')
        for n in names:
            self._original[n] = getattr(db, n, None)

        async def get_user_state(user_id):
            u = self.users.get(user_id)
            if u is None:
                return None
            return {'user_id': user_id, 'is_paid': u['is_paid'],
                    'is_active': u['is_active'],
                    'notifications_on': u['notifications_on']}

        async def set_notifications(user_id, enabled):
            self.notifications_set.append((user_id, enabled))
            if user_id not in self.users:
                return False
            if self.users[user_id]['notifications_on'] is None:
                return False
            self.users[user_id]['notifications_on'] = enabled
            return True

        async def disable_notifications(user_id):
            await set_notifications(user_id, False)

        async def get_user_cooldowns(user_id):
            return {k[1:]: dict(v) for k, v in self.cooldowns.items()
                    if k[0] == user_id}

        async def upsert_cooldown(user_id, symbol, bid_ex, ask_ex, strategy, spread):
            self.cooldowns[(user_id, symbol, bid_ex, ask_ex, strategy)] = {
                'sent_at': datetime.now(timezone.utc),
                'last_spread': spread,
                'dropped_since': False,
            }

        async def mark_dropped(user_id, active_keys):
            active = set(active_keys)
            for k, v in self.cooldowns.items():
                if k[0] == user_id and k[1:] not in active:
                    v['dropped_since'] = True

        async def update_spreads(user_id, items):
            for sym, bid, ask, strat, spread in items:
                rec = self.cooldowns.get((user_id, sym, bid, ask, strat))
                if rec:
                    rec['last_spread'] = float(spread)

        async def grant_access(user_id):
            self.granted.append(user_id)
            if user_id in self.users:
                self.users[user_id]['is_paid'] = True
            return datetime.now() + timedelta(days=30)

        async def get_current_opportunities():
            return list(self.opportunities)

        async def get_active_notification_users():
            return [u for u in self.users.values()
                    if u['is_paid'] and u['is_active'] and u['notifications_on']]

        db.get_user_state = get_user_state
        db.set_notifications = set_notifications
        db.disable_notifications = disable_notifications
        db.get_user_cooldowns = get_user_cooldowns
        db.upsert_cooldown = upsert_cooldown
        db.mark_dropped = mark_dropped
        db.update_spreads = update_spreads
        db.grant_access = grant_access
        db.get_current_opportunities = get_current_opportunities
        db.get_active_notification_users = get_active_notification_users
        return self

    def uninstall(self):
        for name, fn in self._original.items():
            if fn is not None:
                setattr(self.db, name, fn)


# ─── Конструкторы апдейтов ──────────────────────────────────────────

def make_message(chat_id, text, user_id=None, username='tester',
                 reply_to_text=None, message_id=1, **user_extra):
    """Собирает апдейт с текстовым сообщением."""
    user = {'id': user_id or chat_id, 'username': username}
    user.update(user_extra)
    msg = {
        'message_id': message_id,
        'chat': {'id': chat_id},
        'from': user,
        'text': text,
    }
    if reply_to_text is not None:
        msg['reply_to_message'] = {'text': reply_to_text, 'message_id': 99}
    return {'update_id': message_id, 'message': msg}


def make_callback(user_id, data, chat_id=None, message_id=5):
    """Собирает апдейт с нажатием инлайн-кнопки."""
    return {
        'update_id': message_id,
        'callback_query': {
            'id': f'cb{message_id}',
            'data': data,
            'from': {'id': user_id, 'username': 'tester'},
            'message': {
                'message_id': message_id,
                'chat': {'id': chat_id if chat_id is not None else user_id},
            },
        },
    }


def make_opp(symbol='SIRENUSDT', spread=3.5, bid_ex='kucoin_futures',
             ask_ex='gate_futures', strategy='futures_futures'):
    """Арбитражная возможность в том виде, в каком её отдаёт БД."""
    return {
        'symbol': symbol,
        'bid_ex': bid_ex,
        'ask_ex': ask_ex,
        'strategy': strategy,
        'spread': spread,
    }