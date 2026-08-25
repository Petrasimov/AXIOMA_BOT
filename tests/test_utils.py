"""
test_utils.py — Вспомогательные модули

QR-код и клиент NOWPayments. Реальных запросов не делается.
"""

import unittest

import config
from utils import nowpayments as np
from utils.qr import make_qr

try:
    import qrcode  # noqa: F401
    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False


class TestQR(unittest.IsolatedAsyncioTestCase):

    @unittest.skipUnless(HAS_QRCODE, 'библиотека qrcode не установлена')
    async def test_returns_png_bytes(self):
        data = await make_qr('0x1234567890abcdef')
        self.assertIsInstance(data, bytes)
        self.assertTrue(data.startswith(b'\x89PNG'))

    @unittest.skipUnless(HAS_QRCODE, 'библиотека qrcode не установлена')
    async def test_long_address_ok(self):
        data = await make_qr('T' * 100)
        self.assertIsInstance(data, bytes)

    async def test_returns_none_on_failure(self):
        """Оплата не должна ломаться из-за картинки."""
        import utils.qr as qr_module
        original = qr_module._render

        def boom(data):
            raise RuntimeError('нет библиотеки')
        qr_module._render = boom

        try:
            self.assertIsNone(await make_qr('0xabc'))
        finally:
            qr_module._render = original


class TestOrderId(unittest.TestCase):

    def test_roundtrip(self):
        for user_id in (1, 12345, 9999999999):
            oid = np.make_order_id(user_id)
            self.assertEqual(np.parse_order_id(oid), user_id)

    def test_rejects_garbage(self):
        for bad in ('', 'axioma', 'axioma_', 'axioma_abc_1',
                    'other_1_2', None):
            self.assertIsNone(np.parse_order_id(bad))


class TestCurrencies(unittest.TestCase):

    def test_five_currencies(self):
        self.assertEqual(len(np.CURRENCIES), 5)

    def test_networks_specified(self):
        """Сеть должна быть указана явно — перевод
        в неверной сети теряется безвозвратно."""
        for code, meta in np.CURRENCIES.items():
            self.assertTrue(meta['network'])
            self.assertTrue(meta['label'])

    def test_usdt_variants_have_distinct_networks(self):
        usdt = [m['network'] for c, m in np.CURRENCIES.items()
                if c.startswith('usdt')]
        self.assertEqual(len(usdt), len(set(usdt)))


class TestStatusSets(unittest.TestCase):

    def test_success_statuses(self):
        self.assertIn('finished', np.FINAL_OK)
        self.assertIn('confirmed', np.FINAL_OK)

    def test_failure_statuses(self):
        self.assertIn('failed', np.FINAL_FAIL)
        self.assertIn('expired', np.FINAL_FAIL)

    def test_partial_not_in_either(self):
        """partially_paid обрабатывается отдельно —
        деньги пришли, но доступ не выдаётся."""
        self.assertNotIn('partially_paid', np.FINAL_OK)
        self.assertNotIn('partially_paid', np.FINAL_FAIL)

    def test_waiting_not_final(self):
        self.assertNotIn('waiting', np.FINAL_OK)
        self.assertNotIn('waiting', np.FINAL_FAIL)


class TestApiKeyRequired(unittest.IsolatedAsyncioTestCase):

    async def test_raises_without_key(self):
        saved = config.NOWPAYMENTS_API_KEY
        config.NOWPAYMENTS_API_KEY = ''
        try:
            with self.assertRaises(np.NowPaymentsError):
                await np._request('GET', '/status')
        finally:
            config.NOWPAYMENTS_API_KEY = saved


if __name__ == '__main__':
    unittest.main()