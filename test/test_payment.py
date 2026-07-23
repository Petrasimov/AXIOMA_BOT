"""
test_payment.py — Оплата подписки

Здесь деньги, поэтому проверяются в первую очередь опасные случаи:
двойное продление, частичная оплата, потеря платежа при рестарте.
"""

import unittest

import config
import db as db_module
from bot import api, router
from handlers import payment
from utils import nowpayments as np
from tests.helpers import FakeAPI, FakeDB, make_callback


class FakeNowPayments:
    """Подменяет модуль utils.nowpayments."""

    def __init__(self, np_module, payment_module):
        self.np = np_module
        self.payment = payment_module
        self.status = 'waiting'
        self.extra = {}
        self.created = []
        self.listed = []
        self._original = {}

    def install(self):
        for name in ('create_payment', 'get_payment', 'list_recent_payments'):
            self._original[name] = getattr(self.np, name)

        async def create_payment(user_id, pay_currency):
            order_id = self.np.make_order_id(user_id)
            self.created.append((user_id, pay_currency, order_id))
            return {
                'payment_id': f'PAY{user_id}',
                'order_id': order_id,
                'pay_address': '0xABCDEF0123456789',
                'pay_amount': 5.02,
                'pay_currency': pay_currency,
                'expiration': None,
            }

        async def get_payment(payment_id):
            return {'payment_id': payment_id,
                    'payment_status': self.status,
                    'pay_amount': 5.02,
                    **self.extra}

        async def list_recent_payments(limit=100):
            return list(self.listed)

        self.np.create_payment = create_payment
        self.np.get_payment = get_payment
        self.np.list_recent_payments = list_recent_payments
        return self

    def uninstall(self):
        for name, fn in self._original.items():
            setattr(self.np, name, fn)


class PaymentTestCase(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.api = FakeAPI(api).install()
        self.db = FakeDB(db_module).install()
        self.np = FakeNowPayments(np, payment).install()
        self.admin = config.ADMIN_CHAT_ID
        payment._granted.clear()

        # QR в тестах не рисуем — это отдельная зависимость
        self._qr = payment.make_qr

        async def no_qr(data):
            return None
        payment.make_qr = no_qr

    async def asyncTearDown(self):
        payment.make_qr = self._qr
        self.np.uninstall()
        self.api.uninstall()
        self.db.uninstall()
        payment._granted.clear()


class TestOrderId(unittest.TestCase):
    """order_id — единственная связь платежа с пользователем."""

    def test_contains_user_id(self):
        oid = np.make_order_id(12345)
        self.assertEqual(np.parse_order_id(oid), 12345)

    def test_prefix(self):
        self.assertTrue(np.make_order_id(1).startswith('axioma_'))

    def test_unique_per_call(self):
        import time
        a = np.make_order_id(1)
        time.sleep(1.01)
        b = np.make_order_id(1)
        self.assertNotEqual(a, b)

    def test_rejects_foreign_order_id(self):
        self.assertIsNone(np.parse_order_id('other_999_123'))
        self.assertIsNone(np.parse_order_id(''))
        self.assertIsNone(np.parse_order_id('axioma_abc_123'))
        self.assertIsNone(np.parse_order_id('axioma_1'))


class TestCurrencies(unittest.TestCase):

    def test_all_have_label_and_network(self):
        for code, meta in np.CURRENCIES.items():
            self.assertIn('label', meta)
            self.assertIn('network', meta)

    def test_status_sets_do_not_overlap(self):
        self.assertFalse(np.FINAL_OK & np.FINAL_FAIL)


class TestShowScreen(PaymentTestCase):

    async def test_unregistered_rejected(self):
        await router.dispatch(make_callback(999, 'menu:pay'))
        self.assertTrue(self.api.any_text_contains('не зарегистрированы', 999))

    async def test_shows_price_and_currencies(self):
        self.db.add_user(111, is_paid=False)
        await router.dispatch(make_callback(111, 'menu:pay'))
        last = self.api.last(111)
        self.assertIn('Подписка', last.text)
        callbacks = last.callbacks()
        for code in np.CURRENCIES:
            self.assertIn(f'pay:new:{code}', callbacks)

    async def test_mentions_active_subscription(self):
        self.db.add_user(111, is_paid=True)
        await router.dispatch(make_callback(111, 'menu:pay'))
        self.assertTrue(self.api.any_text_contains('уже активна', 111))


class TestCreatePayment(PaymentTestCase):

    async def test_invoice_contains_address_and_amount(self):
        self.db.add_user(111, is_paid=False)
        await router.dispatch(make_callback(111, 'pay:new:usdtbsc'))

        invoice = [s for s in self.api.to(111) if 'ОПЛАТА ПОДПИСКИ' in s.text]
        self.assertTrue(invoice, 'инвойс не отправлен')
        text = invoice[0].text
        self.assertIn('0xABCDEF0123456789', text)
        self.assertIn('5.02', text)

    async def test_invoice_warns_about_network(self):
        """Перевод в неверной сети теряется — предупреждение обязательно."""
        self.db.add_user(111, is_paid=False)
        await router.dispatch(make_callback(111, 'pay:new:usdtbsc'))
        invoice = [s for s in self.api.to(111) if 'ОПЛАТА ПОДПИСКИ' in s.text][0]
        self.assertIn('BEP-20', invoice.text)

    async def test_invoice_has_check_button(self):
        self.db.add_user(111, is_paid=False)
        await router.dispatch(make_callback(111, 'pay:new:usdtbsc'))
        invoice = [s for s in self.api.to(111) if 'ОПЛАТА ПОДПИСКИ' in s.text][0]
        self.assertTrue(any(c.startswith('pay:check:')
                            for c in invoice.callbacks()))

    async def test_works_without_qr(self):
        """Если QR не сгенерировался — оплата всё равно должна пройти."""
        self.db.add_user(111, is_paid=False)
        await router.dispatch(make_callback(111, 'pay:new:usdtbsc'))
        self.assertTrue(self.api.any_text_contains('ОПЛАТА ПОДПИСКИ', 111))

    async def test_unknown_currency_ignored(self):
        self.db.add_user(111, is_paid=False)
        await router.dispatch(make_callback(111, 'pay:new:dogecoin'))
        self.assertEqual(self.np.created, [])

    async def test_unregistered_cannot_create(self):
        await router.dispatch(make_callback(999, 'pay:new:usdtbsc'))
        self.assertEqual(self.np.created, [])


class TestCheckPayment(PaymentTestCase):

    async def test_waiting_asks_to_wait(self):
        self.db.add_user(111, is_paid=False)
        self.np.status = 'waiting'
        await router.dispatch(make_callback(111, 'pay:check:PAY111'))
        self.assertTrue(self.api.any_text_contains('не подтверждён', 111))
        self.assertEqual(self.db.granted, [])

    async def test_finished_grants_access(self):
        self.db.add_user(111, is_paid=False)
        self.np.status = 'finished'
        await router.dispatch(make_callback(111, 'pay:check:PAY111'))
        self.assertEqual(self.db.granted, [111])
        self.assertTrue(self.api.any_text_contains('Оплата получена', 111))

    async def test_confirmed_also_grants(self):
        self.db.add_user(111, is_paid=False)
        self.np.status = 'confirmed'
        await router.dispatch(make_callback(111, 'pay:check:PAY111'))
        self.assertEqual(self.db.granted, [111])

    async def test_admins_notified_about_payment(self):
        self.db.add_user(111, is_paid=False)
        self.np.status = 'finished'
        await router.dispatch(make_callback(111, 'pay:check:PAY111'))
        self.assertTrue(self.api.any_text_contains('Новая оплата', self.admin))

    async def test_no_double_grant(self):
        """Повторное нажатие «Проверить оплату» не должно
        продлевать подписку второй раз."""
        self.db.add_user(111, is_paid=False)
        self.np.status = 'finished'

        await router.dispatch(make_callback(111, 'pay:check:PAY111'))
        await router.dispatch(make_callback(111, 'pay:check:PAY111'))
        await router.dispatch(make_callback(111, 'pay:check:PAY111'))

        self.assertEqual(self.db.granted, [111])

    async def test_failed_payment(self):
        self.db.add_user(111, is_paid=False)
        self.np.status = 'failed'
        await router.dispatch(make_callback(111, 'pay:check:PAY111'))
        self.assertEqual(self.db.granted, [])
        self.assertTrue(self.api.any_text_contains('не прошёл', 111))

    async def test_expired_payment(self):
        self.db.add_user(111, is_paid=False)
        self.np.status = 'expired'
        await router.dispatch(make_callback(111, 'pay:check:PAY111'))
        self.assertEqual(self.db.granted, [])


class TestPartialPayment(PaymentTestCase):
    """Пришло меньше суммы — деньги у нас, доступа нет."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.db.add_user(111, is_paid=False)
        self.np.status = 'partially_paid'
        self.np.extra = {'actually_paid': 2.5}

    async def test_access_not_granted(self):
        await router.dispatch(make_callback(111, 'pay:check:PAY111'))
        self.assertEqual(self.db.granted, [])

    async def test_user_informed_with_amounts(self):
        await router.dispatch(make_callback(111, 'pay:check:PAY111'))
        text = ' '.join(self.api.texts(111))
        self.assertIn('неполная сумма', text)
        self.assertIn('2.5', text)

    async def test_admins_alerted(self):
        await router.dispatch(make_callback(111, 'pay:check:PAY111'))
        self.assertTrue(self.api.any_text_contains(
            'ЧАСТИЧНАЯ ОПЛАТА', self.admin))


class TestGrantFailure(PaymentTestCase):
    """Оплата прошла, а БД недоступна — худший случай."""

    async def test_user_and_admins_notified(self):
        self.db.add_user(111, is_paid=False)
        self.np.status = 'finished'

        async def boom(user_id):
            raise RuntimeError('БД недоступна')
        db_module.grant_access = boom

        await router.dispatch(make_callback(111, 'pay:check:PAY111'))

        self.assertTrue(self.api.any_text_contains('доступ не открылся', 111))
        self.assertTrue(self.api.any_text_contains('НЕ выдан', self.admin))

    async def test_retry_possible_after_failure(self):
        """После сбоя платёж не должен считаться обработанным,
        иначе повторная попытка ничего не даст."""
        self.db.add_user(111, is_paid=False)
        self.np.status = 'finished'

        async def boom(user_id):
            raise RuntimeError('БД недоступна')
        original = db_module.grant_access
        db_module.grant_access = boom

        await router.dispatch(make_callback(111, 'pay:check:PAY111'))
        self.assertNotIn('PAY111', payment._granted)

        db_module.grant_access = original
        await router.dispatch(make_callback(111, 'pay:check:PAY111'))
        self.assertEqual(self.db.granted, [111])


class TestReconcile(PaymentTestCase):
    """Сверка после рестарта — третий уровень защиты платежей."""

    async def test_restores_missed_payment(self):
        self.db.add_user(333, is_paid=False)
        self.np.listed = [
            {'payment_id': 'P333', 'order_id': 'axioma_333_170',
             'payment_status': 'finished'},
        ]
        await payment.reconcile()
        self.assertEqual(self.db.granted, [333])

    async def test_skips_already_paid(self):
        """Если доступ уже есть — платёж был обработан до рестарта."""
        self.db.add_user(444, is_paid=True)
        self.np.listed = [
            {'payment_id': 'P444', 'order_id': 'axioma_444_170',
             'payment_status': 'finished'},
        ]
        await payment.reconcile()
        self.assertEqual(self.db.granted, [])

    async def test_skips_foreign_order_id(self):
        self.np.listed = [
            {'payment_id': 'P555', 'order_id': 'shop_555_170',
             'payment_status': 'finished'},
        ]
        await payment.reconcile()
        self.assertEqual(self.db.granted, [])

    async def test_skips_unfinished(self):
        self.db.add_user(666, is_paid=False)
        self.np.listed = [
            {'payment_id': 'P666', 'order_id': 'axioma_666_170',
             'payment_status': 'waiting'},
        ]
        await payment.reconcile()
        self.assertEqual(self.db.granted, [])

    async def test_skips_unknown_user(self):
        self.np.listed = [
            {'payment_id': 'P777', 'order_id': 'axioma_777_170',
             'payment_status': 'finished'},
        ]
        await payment.reconcile()
        self.assertEqual(self.db.granted, [])

    async def test_mixed_batch(self):
        """Полный сценарий: из четырёх платежей нужен один."""
        self.db.add_user(333, is_paid=False)
        self.db.add_user(444, is_paid=True)
        self.np.listed = [
            {'payment_id': 'P333', 'order_id': 'axioma_333_1',
             'payment_status': 'finished'},
            {'payment_id': 'P444', 'order_id': 'axioma_444_1',
             'payment_status': 'finished'},
            {'payment_id': 'P555', 'order_id': 'other_555',
             'payment_status': 'finished'},
            {'payment_id': 'P666', 'order_id': 'axioma_666_1',
             'payment_status': 'waiting'},
        ]
        await payment.reconcile()
        self.assertEqual(self.db.granted, [333])

    async def test_notifies_restored_user(self):
        self.db.add_user(333, is_paid=False)
        self.np.listed = [
            {'payment_id': 'P333', 'order_id': 'axioma_333_1',
             'payment_status': 'finished'},
        ]
        await payment.reconcile()
        self.assertTrue(self.api.any_text_contains('Оплата получена', 333))

    async def test_survives_api_failure(self):
        async def boom(limit=100):
            raise np.NowPaymentsError('сервис недоступен')
        np.list_recent_payments = boom
        await payment.reconcile()   # не должно бросить


if __name__ == '__main__':
    unittest.main()