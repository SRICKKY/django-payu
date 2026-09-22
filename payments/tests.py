from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from payments.exceptions import IdempotencyConflictError
from payments.models import Payment, PaymentStatus, RefundStatus
from payments.payu import (
    PayUClient,
    PayUConfig,
    payment_request_hash,
    payment_response_hash,
)
from payments.services import (
    handle_payu_callback,
    initiate_payment,
    refund_payment,
    verify_with_payu,
)

TEST_SETTINGS = {
    "PAYU_MERCHANT_KEY": "testkey",
    "PAYU_MERCHANT_SALT": "testsalt",
    "PAYU_MODE": "test",
    "SITE_URL": "http://testserver",
}


def test_client() -> PayUClient:
    return PayUClient(
        PayUConfig(
            key="testkey",
            salt="testsalt",
            mode="test",
            payment_url="https://test.payu.in/_payment",
            postservice_url="https://test.payu.in/merchant/postservice.php?form=2",
        )
    )


def signed_callback(payment: Payment, status="success", **extra) -> dict:
    payload = {
        "key": "testkey",
        "txnid": payment.txnid,
        "amount": "10.00",
        "productinfo": payment.productinfo,
        "firstname": payment.firstname,
        "email": payment.email,
        "status": status,
        "mihpayid": "999888777",
        "mode": "CC",
        "bank_ref_num": "BANK123",
        "udf1": payment.udf1,
        "udf2": payment.udf2,
        "udf3": payment.udf3,
        "udf4": payment.udf4,
        "udf5": payment.udf5,
        "error": "",
        "error_Message": "",
        **extra,
    }
    payload["hash"] = payment_response_hash(payload, "testsalt")
    return payload


@override_settings(**TEST_SETTINGS)
class PayUHashTests(TestCase):
    def test_request_hash_uses_sha512_and_udf_slots(self):
        digest = payment_request_hash(
            key="testkey",
            txnid="abc123",
            amount="10.00",
            productinfo="iPhone",
            firstname="Ashish",
            email="ashish@example.com",
            salt="testsalt",
            udfs={"udf1": "order-1"},
        )
        self.assertEqual(len(digest), 128)
        self.assertEqual(
            digest,
            payment_request_hash(
                key="testkey",
                txnid="abc123",
                amount="10.00",
                productinfo="iPhone",
                firstname="Ashish",
                email="ashish@example.com",
                salt="testsalt",
                udfs={"udf1": "order-1"},
            ),
        )

    def test_response_hash_includes_additional_charges(self):
        payload = {
            "status": "success",
            "additional_charges": "1.00",
            "udf1": "",
            "udf2": "",
            "udf3": "",
            "udf4": "",
            "udf5": "",
            "email": "ashish@example.com",
            "firstname": "Ashish",
            "productinfo": "iPhone",
            "amount": "10.00",
            "txnid": "abc123",
            "key": "testkey",
        }
        digest = payment_response_hash(payload, "testsalt")
        self.assertEqual(len(digest), 128)
        self.assertNotEqual(digest, payment_response_hash({**payload, "additional_charges": ""}, "testsalt"))


@override_settings(**TEST_SETTINGS)
class PaymentServiceTests(TestCase):
    def setUp(self):
        self.client_payu = test_client()

    def test_initiate_payment_stores_checkout_hash(self):
        payment, checkout, _ = initiate_payment(
            amount="10.00",
            productinfo="iPhone",
            firstname="Ashish",
            email="ashish@example.com",
            phone="9999999999",
            reference_id="ORD-1",
            client=self.client_payu,
        )
        self.assertEqual(payment.status, PaymentStatus.CREATED)
        self.assertEqual(payment.udf1, "ORD-1")
        self.assertEqual(checkout["fields"]["hash"], payment.request_hash)
        self.assertEqual(checkout["payu_url"], "https://test.payu.in/_payment")
        self.assertIn("surl", checkout["fields"])

    def test_same_idempotency_key_does_not_create_a_second_payment(self):
        kwargs = {
            "amount": "10.00",
            "productinfo": "iPhone",
            "firstname": "Ashish",
            "email": "ashish@example.com",
            "phone": "9999999999",
            "idempotency_key": "order-abc",
            "client": self.client_payu,
        }
        first, _, created_first = initiate_payment(**kwargs)
        second, _, created_second = initiate_payment(**kwargs)
        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.txnid, second.txnid)
        self.assertEqual(Payment.objects.count(), 1)

    def test_reused_idempotency_key_with_different_amount_conflicts(self):
        initiate_payment(
            amount="10.00",
            productinfo="iPhone",
            firstname="Ashish",
            email="ashish@example.com",
            phone="9999999999",
            idempotency_key="order-abc",
            client=self.client_payu,
        )
        with self.assertRaises(IdempotencyConflictError):
            initiate_payment(
                amount="20.00",
                productinfo="iPhone",
                firstname="Ashish",
                email="ashish@example.com",
                phone="9999999999",
                idempotency_key="order-abc",
                client=self.client_payu,
            )

    def test_valid_success_callback_marks_payment_success(self):
        payment, _, _ = initiate_payment(
            amount="10.00",
            productinfo="iPhone",
            firstname="Ashish",
            email="ashish@example.com",
            phone="9999999999",
            client=self.client_payu,
        )
        updated = handle_payu_callback(signed_callback(payment), client=self.client_payu)
        self.assertEqual(updated.status, PaymentStatus.SUCCESS)
        self.assertEqual(updated.payu_id, "999888777")

    def test_invalid_hash_is_rejected(self):
        payment, _, _ = initiate_payment(
            amount="10.00",
            productinfo="iPhone",
            firstname="Ashish",
            email="ashish@example.com",
            phone="9999999999",
            client=self.client_payu,
        )
        payload = signed_callback(payment)
        payload["hash"] = "0" * 128
        with self.assertRaisesMessage(Exception, "Invalid PayU response hash"):
            handle_payu_callback(payload, client=self.client_payu)
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.CREATED)

    def test_amount_tampering_is_rejected(self):
        payment, _, _ = initiate_payment(
            amount="10.00",
            productinfo="iPhone",
            firstname="Ashish",
            email="ashish@example.com",
            phone="9999999999",
            client=self.client_payu,
        )
        payload = signed_callback(payment, amount="1.00")
        with self.assertRaisesMessage(Exception, "amount"):
            handle_payu_callback(payload, client=self.client_payu)

    def test_successful_payment_is_not_downgraded(self):
        payment, _, _ = initiate_payment(
            amount="10.00",
            productinfo="iPhone",
            firstname="Ashish",
            email="ashish@example.com",
            phone="9999999999",
            client=self.client_payu,
        )
        handle_payu_callback(signed_callback(payment), client=self.client_payu)
        handle_payu_callback(signed_callback(payment, status="failure"), client=self.client_payu)
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.SUCCESS)

    def test_verify_with_payu_updates_status(self):
        payment, _, _ = initiate_payment(
            amount="10.00",
            productinfo="iPhone",
            firstname="Ashish",
            email="ashish@example.com",
            phone="9999999999",
            client=self.client_payu,
        )
        self.client_payu.verify_payment = lambda txnid: {
            "status": 1,
            "transaction_details": {
                payment.txnid: {
                    "status": "success",
                    "amt": "10.00",
                    "mihpayid": "payu-1",
                    "mode": "UPI",
                }
            },
        }
        updated = verify_with_payu(payment.txnid, client=self.client_payu)
        self.assertEqual(updated.status, PaymentStatus.SUCCESS)
        self.assertIsNotNone(updated.verified_at)

    def test_refund_marks_payment_refunded(self):
        payment, _, _ = initiate_payment(
            amount="10.00",
            productinfo="iPhone",
            firstname="Ashish",
            email="ashish@example.com",
            phone="9999999999",
            client=self.client_payu,
        )
        handle_payu_callback(signed_callback(payment), client=self.client_payu)
        payment.refresh_from_db()
        self.client_payu.refund = lambda *args, **kwargs: {
            "status": 1,
            "msg": "Refund Request Queued",
            "request_id": "RID-1",
        }
        refund = refund_payment(payment, amount=Decimal("10.00"), client=self.client_payu)
        self.assertEqual(refund.status, RefundStatus.SUCCESS)
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.REFUNDED)


@override_settings(**TEST_SETTINGS)
class PaymentAPITests(TestCase):
    def test_initiate_api_requires_idempotency_key(self):
        response = self.client.post(
            reverse("payments:payment-list"),
            {
                "amount": "10.00",
                "productinfo": "iPhone",
                "firstname": "Ashish",
                "email": "ashish@example.com",
                "phone": "9999999999",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Payment.objects.count(), 0)

    def test_initiate_api_returns_checkout_fields(self):
        response = self.client.post(
            reverse("payments:payment-list"),
            {
                "amount": "10.00",
                "productinfo": "iPhone",
                "firstname": "Ashish",
                "email": "ashish@example.com",
                "phone": "9999999999",
            },
            content_type="application/json",
            headers={"Idempotency-Key": "pay-1"},
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertIn("fields", body)
        self.assertEqual(body["fields"]["key"], "testkey")
        self.assertTrue(Payment.objects.filter(txnid=body["txnid"]).exists())

    def test_initiate_api_replays_the_same_idempotency_key(self):
        payload = {
            "amount": "10.00",
            "productinfo": "iPhone",
            "firstname": "Ashish",
            "email": "ashish@example.com",
            "phone": "9999999999",
        }
        first = self.client.post(
            reverse("payments:payment-list"),
            payload,
            content_type="application/json",
            headers={"Idempotency-Key": "pay-1"},
        )
        second = self.client.post(
            reverse("payments:payment-list"),
            payload,
            content_type="application/json",
            headers={"Idempotency-Key": "pay-1"},
        )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["txnid"], second.json()["txnid"])
        self.assertEqual(Payment.objects.count(), 1)

    def test_initiate_api_rejects_idempotency_key_reuse_with_different_body(self):
        self.client.post(
            reverse("payments:payment-list"),
            {
                "amount": "10.00",
                "productinfo": "iPhone",
                "firstname": "Ashish",
                "email": "ashish@example.com",
                "phone": "9999999999",
            },
            content_type="application/json",
            headers={"Idempotency-Key": "pay-1"},
        )
        response = self.client.post(
            reverse("payments:payment-list"),
            {
                "amount": "50.00",
                "productinfo": "iPhone",
                "firstname": "Ashish",
                "email": "ashish@example.com",
                "phone": "9999999999",
            },
            content_type="application/json",
            headers={"Idempotency-Key": "pay-1"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Payment.objects.count(), 1)

    def test_callback_endpoint_accepts_signed_payu_post(self):
        payment, _, _ = initiate_payment(
            amount="10.00",
            productinfo="iPhone",
            firstname="Ashish",
            email="ashish@example.com",
            phone="9999999999",
            client=test_client(),
        )
        response = self.client.post(
            reverse("payments:callback-success"),
            signed_callback(payment),
        )
        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.SUCCESS)

    @patch("payments.views.verify_with_payu")
    def test_verify_endpoint(self, mocked_verify):
        payment, _, _ = initiate_payment(
            amount="10.00",
            productinfo="iPhone",
            firstname="Ashish",
            email="ashish@example.com",
            phone="9999999999",
            client=test_client(),
        )
        payment.status = PaymentStatus.SUCCESS
        payment.save(update_fields=["status"])
        mocked_verify.return_value = payment
        response = self.client.post(reverse("payments:payment-verify", kwargs={"txnid": payment.txnid}))
        self.assertEqual(response.status_code, 200)
        mocked_verify.assert_called_once_with(payment.txnid)


@override_settings(**TEST_SETTINGS)
class PrettyJSONAdminTests(TestCase):
    def test_pretty_json_highlights_keys_and_values(self):
        from payments.json_display import pretty_json_html

        html = str(
            pretty_json_html(
                {"amount": "10.00", "status": True, "retries": 1, "note": None}
            )
        )
        self.assertIn("json-pretty", html)
        self.assertIn("json-key", html)
        self.assertIn("json-string", html)
        self.assertIn("json-bool", html)
        self.assertIn("json-number", html)
        self.assertIn("json-null", html)
        self.assertIn("Copy JSON", html)

    def test_payment_admin_renders_pretty_payloads(self):
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_superuser(
            "admin", "admin@example.com", "pass"
        )
        payment, _, _ = initiate_payment(
            amount="10.00",
            productinfo="iPhone",
            firstname="Ashish",
            email="ashish@example.com",
            phone="9999999999",
            client=test_client(),
        )
        self.client.force_login(user)
        response = self.client.get(
            reverse("admin:payments_payment_change", args=[payment.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "json-pretty")
        self.assertContains(response, "Gateway payloads")
        self.assertContains(response, payment.txnid)

