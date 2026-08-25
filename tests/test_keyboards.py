"""
test_keyboards.py — Клавиатуры

Главное здесь — лимит Telegram на callback_data в 64 байта.
Превышение молча ломает кнопку, поэтому проверяем каждую.
"""

import unittest

import config
from bot import keyboards


def all_buttons(markup):
    return [b for row in markup.get('inline_keyboard', []) for b in row]


class TestMainMenu(unittest.TestCase):

    def setUp(self):
        self.menu = keyboards.main_menu()

    def test_has_five_sections(self):
        self.assertEqual(len(all_buttons(self.menu)), 5)

    def test_all_have_callback_data(self):
        for b in all_buttons(self.menu):
            self.assertIn('callback_data', b)
            self.assertTrue(b['callback_data'].startswith('menu:'))

    def test_callback_data_within_limit(self):
        for b in all_buttons(self.menu):
            self.assertLessEqual(len(b['callback_data'].encode()), 64)

    def test_expected_sections(self):
        actions = {b['callback_data'].split(':', 1)[1]
                   for b in all_buttons(self.menu)}
        self.assertEqual(
            actions,
            {'notify', 'pay', 'password', 'support', 'jobs'},
        )

    def test_every_button_has_text(self):
        for b in all_buttons(self.menu):
            self.assertTrue(b.get('text'))


class TestOtherKeyboards(unittest.TestCase):

    def test_back_to_menu(self):
        buttons = all_buttons(keyboards.back_to_menu())
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0]['callback_data'], 'menu:main')

    def test_open_scanner_is_url_button(self):
        buttons = all_buttons(keyboards.open_scanner())
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0]['url'], config.SCANNER_URL)
        self.assertNotIn('callback_data', buttons[0])


class TestForceReply(unittest.TestCase):
    """ForceReply — механизм, заменяющий хранение состояния диалога."""

    def test_sets_force_reply(self):
        markup = keyboards.force_reply()
        self.assertTrue(markup['force_reply'])
        self.assertTrue(markup['selective'])

    def test_placeholder_included(self):
        markup = keyboards.force_reply('Введите текст')
        self.assertEqual(markup['input_field_placeholder'], 'Введите текст')

    def test_placeholder_truncated_to_telegram_limit(self):
        markup = keyboards.force_reply('я' * 200)
        self.assertLessEqual(len(markup['input_field_placeholder']), 64)

    def test_no_placeholder_key_when_empty(self):
        markup = keyboards.force_reply('')
        self.assertNotIn('input_field_placeholder', markup)


if __name__ == '__main__':
    unittest.main()