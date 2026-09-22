from __future__ import annotations

import hashlib
import json
import logging
import uuid
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from .exceptions import IdempotencyConflictError, InvalidCallbackError, PaymentStateError
from .models import Payment, PaymentStatus, Refund, RefundStatus
from .payu import PayUClient, format_amount

logger = logging.getLogger(__name__)

PAYU_STATUS_MAP = {
    "success": PaymentStatus.SUCCESS,
    "captured": PaymentStatus.SUCCESS,
    "failure": PaymentStatus.FAILED,
    "failed": PaymentStatus.FAILED,
    "pending": PaymentStatus.PENDING,
    "in progress": PaymentStatus.PENDING,
    "bounced": PaymentStatus.BOUNCED,
    "dropped": PaymentStatus.DROPPED,
    "usercancelled": PaymentStatus.CANCELLED,
    "userCancelled": PaymentStatus.CANCELLED,
    "cancelled": PaymentStatus.CANCELLED,
    "auto refund": PaymentStatus.REFUNDED,
    "refund": PaymentStatus.REFUNDED,
}

TERMINAL_SUCCESS = {PaymentStatus.SUCCESS, PaymentStatus.REFUNDED, PaymentStatus.PARTIALLY_REFUNDED}


def generate_txnid() -> str:
    return uuid.uuid4().hex[:20]


def generate_refund_token() -> str:
    return uuid.uuid4().hex[:20]


def normalize_idempotency_key(value: str | None) -> str | None:
    key = str(value or "").strip()
    if not key:
        return None
    if len(key) > 255:
        raise PaymentStateError("Idempotency-Key must be 255 characters or fewer.")
    return key


def initiate_fingerprint(data: dict) -> str:
    canonical = {
        "amount": format_amount(data["amount"]),
        "productinfo": str(data.get("productinfo") or ""),
        "firstname": str(data.get("firstname") or ""),
        "lastname": str(data.get("lastname") or ""),
        "email": str(data.get("email") or ""),
        "phone": str(data.get("phone") or ""),
        "reference_id": str(data.get("reference_id") or ""),
        "udf1": str(data.get("udf1") or ""),
        "udf2": str(data.get("udf2") or ""),
        "udf3": str(data.get("udf3") or ""),
        "udf4": str(data.get("udf4") or ""),
        "udf5": str(data.get("udf5") or ""),
    }
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _absolute_url(path: str) -> str:
    return f"{str(settings.SITE_URL).rstrip('/')}{path}"


def _udfs_from_payment(payment: Payment) -> dict[str, str]:
    return {
        "udf1": payment.udf1,
        "udf2": payment.udf2,
        "udf3": payment.udf3,
        "udf4": payment.udf4,
        "udf5": payment.udf5,
    }


def build_checkout(payment: Payment, client: PayUClient | None = None) -> dict:
    client = client or PayUClient()
    amount = format_amount(payment.amount)
    fields = client.build_checkout_fields(
        txnid=payment.txnid,
        amount=amount,
        productinfo=payment.productinfo,
        firstname=payment.firstname,
        email=payment.email,
        phone=payment.phone,
        lastname=payment.lastname,
        surl=_absolute_url(reverse("payments:callback-success")),
        furl=_absolute_url(reverse("payments:callback-failure")),
        curl=_absolute_url(reverse("payments:callback-cancel")),
        udfs=_udfs_from_payment(payment),
    )
    payment.request_hash = fields["hash"]
    payment.request_payload = fields
    payment.save(update_fields=["request_hash", "request_payload", "updated_at"])
    return checkout_from_payment(payment, client=client)


def checkout_from_payment(payment: Payment, client: PayUClient | None = None) -> dict:
    client = client or PayUClient()
    fields = payment.request_payload or {}
    return {
        "txnid": payment.txnid,
        "payu_url": client.config.payment_url,
        "fields": fields,
        "checkout_url": _absolute_url(
            reverse("payments:checkout", kwargs={"txnid": payment.txnid})
        ),
    }


def _create_payment(*, amount, productinfo, firstname, email, phone, lastname, reference_id, udfs, idempotency_key, fingerprint):
    return Payment.objects.create(
        txnid=generate_txnid(),
        amount=Decimal(format_amount(amount)),
        productinfo=productinfo,
        firstname=firstname,
        lastname=lastname or "",
        email=email,
        phone=phone,
        reference_id=reference_id or "",
        status=PaymentStatus.CREATED,
        udf1=udfs.get("udf1") or reference_id or "",
        udf2=udfs.get("udf2", ""),
        udf3=udfs.get("udf3", ""),
        udf4=udfs.get("udf4", ""),
        udf5=udfs.get("udf5", ""),
        idempotency_key=idempotency_key,
        idempotency_fingerprint=fingerprint,
    )


def _replay_or_conflict(payment: Payment, fingerprint: str, client: PayUClient | None) -> tuple[Payment, dict, bool]:
    if payment.idempotency_fingerprint and payment.idempotency_fingerprint != fingerprint:
        raise IdempotencyConflictError(
            "Idempotency-Key was reused with a different payment request."
        )
    if not payment.request_payload:
        return payment, build_checkout(payment, client=client), False
    return payment, checkout_from_payment(payment, client=client), False


def initiate_payment(
    *,
    amount,
    productinfo,
    firstname,
    email,
    phone,
    lastname="",
    reference_id="",
    udfs=None,
    idempotency_key=None,
    client=None,
) -> tuple[Payment, dict, bool]:
    udfs = udfs or {}
    fingerprint = initiate_fingerprint(
        {
            "amount": amount,
            "productinfo": productinfo,
            "firstname": firstname,
            "lastname": lastname or "",
            "email": email,
            "phone": phone,
            "reference_id": reference_id or "",
            **{f"udf{i}": udfs.get(f"udf{i}", "") for i in range(1, 6)},
        }
    )
    key = normalize_idempotency_key(idempotency_key)

    if key:
        existing = Payment.objects.filter(idempotency_key=key).first()
        if existing:
            return _replay_or_conflict(existing, fingerprint, client)
        try:
            with transaction.atomic():
                payment = _create_payment(
                    amount=amount,
                    productinfo=productinfo,
                    firstname=firstname,
                    email=email,
                    phone=phone,
                    lastname=lastname,
                    reference_id=reference_id,
                    udfs=udfs,
                    idempotency_key=key,
                    fingerprint=fingerprint,
                )
                checkout = build_checkout(payment, client=client)
                return payment, checkout, True
        except IntegrityError:
            existing = Payment.objects.get(idempotency_key=key)
            return _replay_or_conflict(existing, fingerprint, client)

    payment = _create_payment(
        amount=amount,
        productinfo=productinfo,
        firstname=firstname,
        email=email,
        phone=phone,
        lastname=lastname,
        reference_id=reference_id,
        udfs=udfs,
        idempotency_key=None,
        fingerprint=fingerprint,
    )
    checkout = build_checkout(payment, client=client)
    return payment, checkout, True


def _map_payu_status(raw_status: str) -> str:
    return PAYU_STATUS_MAP.get((raw_status or "").strip().lower(), PaymentStatus.PENDING)


def _amounts_match(stored: Decimal, reported: str) -> bool:
    try:
        return format_amount(stored) == format_amount(reported)
    except Exception:
        return False


def _apply_gateway_result(payment: Payment, payload: dict, *, source: str) -> Payment:
    raw_status = str(payload.get("status") or payload.get("unmappedstatus") or "")
    new_status = _map_payu_status(raw_status)
    reported_amount = str(payload.get("amount") or payload.get("amt") or "")

    if reported_amount and not _amounts_match(payment.amount, reported_amount):
        logger.warning(
            "PayU amount mismatch for %s via %s: stored=%s reported=%s",
            payment.txnid,
            source,
            payment.amount,
            reported_amount,
        )
        raise InvalidCallbackError("Payment amount does not match the original transaction.")

    if payment.status in TERMINAL_SUCCESS and new_status != PaymentStatus.SUCCESS:
        logger.info(
            "Ignoring non-success PayU update for completed payment %s (%s -> %s)",
            payment.txnid,
            payment.status,
            new_status,
        )
        payment.response_payload = payload
        payment.save(update_fields=["response_payload", "updated_at"])
        return payment

    payment.status = new_status
    payment.gateway_status = raw_status
    payment.payu_id = str(payload.get("mihpayid") or payment.payu_id)
    payment.bank_ref_num = str(payload.get("bank_ref_num") or payment.bank_ref_num)
    payment.payment_mode = str(payload.get("mode") or payment.payment_mode)
    payment.error_code = str(payload.get("error") or payload.get("error_code") or "")
    payment.error_message = str(
        payload.get("error_Message") or payload.get("error_message") or payload.get("field9") or ""
    )
    payment.response_payload = payload
    payment.save()
    return payment


@transaction.atomic
def handle_payu_callback(payload: dict, client: PayUClient | None = None) -> Payment:
    client = client or PayUClient()
    txnid = str(payload.get("txnid") or "").strip()
    if not txnid:
        raise InvalidCallbackError("Callback is missing txnid.")

    try:
        payment = Payment.objects.select_for_update().get(txnid=txnid)
    except Payment.DoesNotExist as exc:
        raise InvalidCallbackError("Unknown transaction.") from exc

    if str(payload.get("key") or "") != client.config.key:
        raise InvalidCallbackError("Merchant key does not match.")

    if not client.verify_callback(payload):
        logger.warning("Invalid PayU response hash for txnid=%s", txnid)
        raise InvalidCallbackError("Invalid PayU response hash.")

    return _apply_gateway_result(payment, payload, source="callback")


def _transaction_details(verify_response: dict, txnid: str) -> dict:
    details = verify_response.get("transaction_details") or {}
    if isinstance(details, dict):
        record = details.get(txnid) or next(iter(details.values()), None)
        if isinstance(record, dict):
            return record
    return {}


@transaction.atomic
def verify_with_payu(txnid: str, client: PayUClient | None = None) -> Payment:
    client = client or PayUClient()
    payment = Payment.objects.select_for_update().get(txnid=txnid)
    response = client.verify_payment(payment.txnid)
    details = _transaction_details(response, payment.txnid)
    if not details:
        raise PaymentStateError("PayU did not return transaction details for this payment.")
    payment = _apply_gateway_result(payment, details, source="verify")
    payment.verified_at = timezone.now()
    payment.save(update_fields=["verified_at", "updated_at"])
    return payment


@transaction.atomic
def refund_payment(payment: Payment, amount=None, client: PayUClient | None = None) -> Refund:
    client = client or PayUClient()
    if payment.status not in {PaymentStatus.SUCCESS, PaymentStatus.PARTIALLY_REFUNDED}:
        raise PaymentStateError("Only successful payments can be refunded.")
    if not payment.payu_id:
        raise PaymentStateError("Payment is missing a PayU transaction id.")

    remaining = payment.amount - payment.refunded_amount
    refund_amount = Decimal(format_amount(amount if amount is not None else remaining))
    if refund_amount <= 0 or refund_amount > remaining:
        raise PaymentStateError("Refund amount is outside the remaining captured amount.")

    refund = Refund.objects.create(
        payment=payment,
        token=generate_refund_token(),
        amount=refund_amount,
        status=RefundStatus.REQUESTED,
    )
    response = client.refund(payment.payu_id, refund.token, format_amount(refund_amount))
    refund.response_payload = response
    refund.payu_request_id = str(
        response.get("request_id")
        or (response.get("details") or {}).get("request_id")
        or ""
    )
    refund.message = str(response.get("msg") or response.get("message") or "")

    status_code = str(response.get("status") or "")
    if status_code in {"1", "success"}:
        refund.status = RefundStatus.SUCCESS
    elif status_code in {"0", "failure", "failed"}:
        refund.status = RefundStatus.FAILED
    else:
        refund.status = RefundStatus.PENDING
    refund.save()

    if refund.status == RefundStatus.SUCCESS:
        if payment.refunded_amount >= payment.amount:
            payment.status = PaymentStatus.REFUNDED
        else:
            payment.status = PaymentStatus.PARTIALLY_REFUNDED
        payment.save(update_fields=["status", "updated_at"])

    return refund
