"""
test_vacancies.py — Вакансии и резюме

Особое внимание — экранированию HTML и защите от того, чтобы
случайный текст пользователя не улетел в админ-чат как резюме.
"""

import unittest

import config
import db as db_module
from bot import api, router
from handlers import vacancies
from tests.helpers import FakeAPI, FakeDB, make_callback, make_message


class VacanciesTestCase(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.api = FakeAPI(api).install()
        self.db = FakeDB(db_module).install()
        self.admin = config.ADMIN_CHAT_ID

    async def asyncTearDown(self):
        self.api.uninstall()
        self.db.uninstall()

    def admin_cards(self):
        return self.api.to(self.admin)


class TestShowVacancies(VacanciesTestCase):

    async def test_shows_positions(self):
        await router.dispatch(make_callback(111, 'menu:jobs'))
        text = self.api.last(111).text
        self.assertIn('Frontend', text)
        self.assertIn('Трейдер', text)
        self.assertIn('Комьюнити', text)

    async def test_has_resume_button(self):
        await router.dispatch(make_callback(111, 'menu:jobs'))
        self.assertIn('jobs:resume', self.api.last(111).callbacks())


class TestResumeForm(VacanciesTestCase):

    async def test_template_uses_force_reply(self):
        """Без ForceReply бот не поймёт, что присланный
        текст — это резюме."""
        await router.dispatch(make_callback(111, 'jobs:resume'))
        markup = self.api.last(111).markup
        self.assertTrue(markup.get('force_reply'))

    async def test_marker_present_in_template(self):
        self.assertIn(vacancies.RESUME_MARKER, vacancies.RESUME_TEMPLATE)

    async def test_template_lists_all_seven_fields(self):
        template = vacancies.RESUME_TEMPLATE
        for digit in '1234567':
            self.assertIn(digit, template)


class TestResumeSubmission(VacanciesTestCase):

    async def submit(self, text, chat_id=111, **user):
        await router.dispatch(make_message(
            chat_id, text, reply_to_text=vacancies.RESUME_TEMPLATE, **user))

    async def test_reaches_admin_chat(self):
        await self.submit('Иван Иванов, 22, Киров, Frontend, 2 года, @ivan')
        cards = self.admin_cards()
        self.assertEqual(len(cards), 1)
        self.assertIn('НОВОЕ РЕЗЮМЕ', cards[0].text)

    async def test_card_contains_user_id(self):
        await self.submit('текст резюме', chat_id=555)
        self.assertIn('555', self.admin_cards()[0].text)

    async def test_card_contains_username(self):
        await self.submit('текст', username='petrasimov')
        self.assertIn('@petrasimov', self.admin_cards()[0].text)

    async def test_falls_back_to_name_without_username(self):
        await self.submit('текст', username=None,
                          first_name='Пётр', last_name='Симов')
        self.assertIn('Пётр Симов', self.admin_cards()[0].text)

    async def test_user_gets_confirmation(self):
        await self.submit('текст')
        self.assertTrue(self.api.any_text_contains('Резюме отправлено', 111))

    async def test_html_is_escaped(self):
        """Неэкранированный < или & ломает parse_mode=HTML,
        и сообщение просто не уходит."""
        await self.submit('<b>жирный</b> & <script>alert(1)</script>')
        card = self.admin_cards()[0].text
        self.assertNotIn('<script>', card)
        self.assertIn('&lt;script&gt;', card)

    async def test_long_text_truncated(self):
        """Лимит Telegram — 4096 символов."""
        await self.submit('А' * 10000)
        card = self.admin_cards()[0].text
        self.assertLess(len(card), 4096)
        self.assertIn('обрезан', card)

    async def test_empty_text_rejected(self):
        await router.dispatch(make_message(
            111, '', reply_to_text=vacancies.RESUME_TEMPLATE))
        self.assertEqual(self.admin_cards(), [])
        self.assertTrue(self.api.any_text_contains('текстом', 111))


class TestNoLeaks(VacanciesTestCase):
    """Проверки, что в админ-чат не попадает лишнее."""

    async def test_plain_message_not_sent_as_resume(self):
        await router.dispatch(make_message(111, 'просто пишу боту'))
        self.assertEqual(self.admin_cards(), [])

    async def test_reply_to_other_bot_message(self):
        await router.dispatch(make_message(
            111, 'ответ', reply_to_text='Добро пожаловать в AXIOMA SCAN!'))
        self.assertEqual(self.admin_cards(), [])


class TestWithoutAdminChat(VacanciesTestCase):
    """ADMIN_CHAT_ID может быть ещё не настроен."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.saved = config.ADMIN_CHAT_ID
        config.ADMIN_CHAT_ID = 0
        vacancies.config.ADMIN_CHAT_ID = 0

    async def asyncTearDown(self):
        config.ADMIN_CHAT_ID = self.saved
        vacancies.config.ADMIN_CHAT_ID = self.saved
        await super().asyncTearDown()

    async def test_user_told_about_failure(self):
        await router.dispatch(make_message(
            111, 'резюме', reply_to_text=vacancies.RESUME_TEMPLATE))
        self.assertTrue(self.api.any_text_contains('Не удалось', 111))


if __name__ == '__main__':
    unittest.main()