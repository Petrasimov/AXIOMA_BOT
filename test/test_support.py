"""
test_support.py — Поддержка

Ключевое здесь — маршрутизация без хранилища: ID пользователя
живёт в тексте карточки и в callback_data.

Отдельный блок — защита от утечки: сообщение админов не должно
уходить случайному пользователю от имени поддержки.
"""

import unittest

import config
import db as db_module
from bot import api, router
from handlers import support
from tests.helpers import FakeAPI, FakeDB, make_callback, make_message, strip_html


class SupportTestCase(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.api = FakeAPI(api).install()
        self.db = FakeDB(db_module).install()
        self.admin = config.ADMIN_CHAT_ID

    async def asyncTearDown(self):
        self.api.uninstall()
        self.db.uninstall()

    async def make_card(self, user_id=111, text='не приходят уведомления'):
        """Создаёт обращение и возвращает текст карточки так,
        как его отдаст Telegram — без HTML-разметки."""
        await router.dispatch(make_message(
            user_id, text, reply_to_text=support.ASK_TEMPLATE))
        card = self.api.to(self.admin)[0].text
        self.api.clear()
        return strip_html(card)


class TestQuestionForm(SupportTestCase):

    async def test_form_uses_force_reply(self):
        await router.dispatch(make_callback(111, 'menu:support'))
        self.assertTrue(self.api.last(111).markup.get('force_reply'))

    async def test_marker_in_template(self):
        self.assertIn(support.SUPPORT_MARKER, support.ASK_TEMPLATE)


class TestQuestionSubmission(SupportTestCase):

    async def test_card_reaches_admin_chat(self):
        await router.dispatch(make_message(
            111, 'кнопка не работает', reply_to_text=support.ASK_TEMPLATE))
        cards = self.api.to(self.admin)
        self.assertEqual(len(cards), 1)
        self.assertIn(support.CARD_MARKER, cards[0].text)

    async def test_card_has_close_button_with_user_id(self):
        await router.dispatch(make_message(
            111, 'вопрос', reply_to_text=support.ASK_TEMPLATE))
        callbacks = self.api.to(self.admin)[0].callbacks()
        self.assertIn('sup:close:111', callbacks)

    async def test_close_callback_within_telegram_limit(self):
        """callback_data не может быть длиннее 64 байт."""
        await router.dispatch(make_message(
            9999999999, 'вопрос', reply_to_text=support.ASK_TEMPLATE))
        for cb in self.api.to(self.admin)[0].callbacks():
            self.assertLessEqual(len(cb.encode()), 64)

    async def test_card_explains_how_to_reply(self):
        await router.dispatch(make_message(
            111, 'вопрос', reply_to_text=support.ASK_TEMPLATE))
        self.assertIn('реплаем', self.api.to(self.admin)[0].text)

    async def test_user_gets_confirmation(self):
        await router.dispatch(make_message(
            111, 'вопрос', reply_to_text=support.ASK_TEMPLATE))
        self.assertTrue(self.api.any_text_contains('Обращение отправлено', 111))

    async def test_html_escaped(self):
        await router.dispatch(make_message(
            111, '<img src=x onerror=alert(1)>',
            reply_to_text=support.ASK_TEMPLATE))
        card = self.api.to(self.admin)[0].text
        self.assertNotIn('<img', card)
        self.assertIn('&lt;img', card)

    async def test_long_text_truncated(self):
        await router.dispatch(make_message(
            111, 'Я' * 9000, reply_to_text=support.ASK_TEMPLATE))
        self.assertLess(len(self.api.to(self.admin)[0].text), 4096)


class TestAdminReply(SupportTestCase):

    async def test_reply_reaches_user(self):
        card = await self.make_card(111)
        await router.dispatch(make_message(
            self.admin, 'Проверьте подписку', user_id=777,
            reply_to_text=card))
        self.assertTrue(self.api.any_text_contains('Ответ поддержки', 111))

    async def test_reply_is_anonymous(self):
        """Пользователь не должен видеть, кто из админов ответил."""
        card = await self.make_card(111)
        await router.dispatch(make_message(
            self.admin, 'ответ', user_id=777, username='admin_vasya',
            reply_to_text=card))
        for text in self.api.texts(111):
            self.assertNotIn('admin_vasya', text)

    async def test_admin_gets_delivery_confirmation(self):
        card = await self.make_card(111)
        await router.dispatch(make_message(
            self.admin, 'ответ', user_id=777, reply_to_text=card))
        self.assertTrue(self.api.any_text_contains('Доставлено', self.admin))

    async def test_id_parsed_from_raw_html_too(self):
        """На случай если Telegram отдаст текст с разметкой."""
        await router.dispatch(make_message(
            111, 'вопрос', reply_to_text=support.ASK_TEMPLATE))
        raw_card = self.api.to(self.admin)[0].text   # с <code>
        self.api.clear()

        await router.dispatch(make_message(
            self.admin, 'ответ', user_id=777, reply_to_text=raw_card))
        self.assertTrue(self.api.any_text_contains('Ответ поддержки', 111))

    async def test_blocked_user_reported_to_admin(self):
        card = await self.make_card(111)
        self.api.block(111)
        await router.dispatch(make_message(
            self.admin, 'ответ', user_id=777, reply_to_text=card))
        self.assertTrue(self.api.any_text_contains('заблокировал', self.admin))

    async def test_delivery_failure_reported(self):
        card = await self.make_card(111)
        self.api.fail(111)
        await router.dispatch(make_message(
            self.admin, 'ответ', user_id=777, reply_to_text=card))
        self.assertTrue(self.api.any_text_contains('Не удалось', self.admin))


class TestLeakProtection(SupportTestCase):
    """Самое опасное место: сообщение админов не должно
    уехать случайному пользователю."""

    async def test_plain_admin_chat_message_ignored(self):
        await router.dispatch(make_message(
            self.admin, 'когда релиз?', user_id=777))
        self.assertEqual(self.api.sent, [])

    async def test_reply_to_non_card_ignored(self):
        await router.dispatch(make_message(
            self.admin, 'согласен', user_id=777,
            reply_to_text='когда релиз?'))
        self.assertEqual(self.api.sent, [])

    async def test_card_without_id_ignored(self):
        fake_card = f'{support.CARD_MARKER}\n\nбез идентификатора'
        await router.dispatch(make_message(
            self.admin, 'ответ', user_id=777, reply_to_text=fake_card))
        self.assertEqual(self.api.sent, [])


class TestCloseDialog(SupportTestCase):

    async def test_user_gets_thanks(self):
        await router.dispatch(make_callback(777, 'sup:close:111',
                                            chat_id=self.admin))
        self.assertTrue(self.api.any_text_contains('Спасибо за обращение', 111))

    async def test_button_removed(self):
        await router.dispatch(make_callback(777, 'sup:close:111',
                                            chat_id=self.admin))
        self.assertTrue(any(s.kind == 'markup' for s in self.api.sent))

    async def test_admin_gets_status(self):
        await router.dispatch(make_callback(777, 'sup:close:111',
                                            chat_id=self.admin))
        self.assertTrue(self.api.any_text_contains('завершён', self.admin))

    async def test_blocked_user_does_not_break_close(self):
        self.api.block(111)
        await router.dispatch(make_callback(777, 'sup:close:111',
                                            chat_id=self.admin))
        self.assertTrue(self.api.any_text_contains('завершён', self.admin))

    async def test_non_numeric_param_ignored(self):
        await router.dispatch(make_callback(777, 'sup:close:абв',
                                            chat_id=self.admin))
        # Не должно упасть и никому ничего не отправить
        sends = [s for s in self.api.sent if s.kind == 'send']
        self.assertEqual(sends, [])


if __name__ == '__main__':
    unittest.main()