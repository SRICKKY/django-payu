"""PayU India hosted checkout hashing and merchant APIs."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any, Mapping

from django.conf import settings

from .exceptions import PayUAPIError, PayUConfigurationError

logger = logging.getLogger(__name__)

PAYMENT_URLS = {
    "test": "https://test.payu.in/_payment",
    "production": "https://secure.payu.in/_payment",
}

POSTSERVICE_URLS = {
    "test": "https://test.payu.in/merchant/postservice.php?form=2",
    "production": "https://info.payu.in/merchant/postservice.php?form=2",
}


def format_amount(amount: Decimal | str | int | float) -> str:
    value = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    return f"{value:.2f}"


def sha512(value: str) -> str:
    return hashlib.sha512(value.encode("utf-8")).hexdigest().lower()


def _udf_values(udfs: Mapping[str, str] | None) -> tuple[str, str, str, str, str]:
    source = udfs or {}
    return tuple(str(source.get(f"udf{i}", "") or "") for i in range(1, 6))


def payment_request_hash(
    *,
    key: str,
    txnid: str,
    amount: str,
    productinfo: str,
    firstname: str,
    email: str,
    salt: str,
    udfs: Mapping[str, str] | None = None,
) -> str:
    udf1, udf2, udf3, udf4, udf5 = _udf_values(udfs)
    hash_string = (
        f"{key}|{txnid}|{amount}|{productinfo}|{firstname}|{email}|"
        f"{udf1}|{udf2}|{udf3}|{udf4}|{udf5}||||||{salt}"
    )
    return sha512(hash_string)


def payment_response_hash(payload: Mapping[str, Any], salt: str) -> str:
    additional_charges = str(
        payload.get("additional_charges") or payload.get("additionalCharges") or ""
    ).strip()
    split_info = str(payload.get("splitInfo") or payload.get("split_info") or "").strip()
    prefix = f"{additional_charges}|" if additional_charges else ""
    split_segment = f"{split_info}|" if split_info else ""
    hash_string = (
        f"{prefix}{salt}|{payload.get('status', '')}|{split_segment}|||||"
        f"{payload.get('udf5', '')}|{payload.get('udf4', '')}|"
        f"{payload.get('udf3', '')}|{payload.get('udf2', '')}|"
        f"{payload.get('udf1', '')}|{payload.get('email', '')}|"
        f"{payload.get('firstname', '')}|{payload.get('productinfo', '')}|"
        f"{payload.get('amount', '')}|{payload.get('txnid', '')}|"
        f"{payload.get('key', '')}"
    )
    return sha512(hash_string)


def hashes_match(expected: str, received: str) -> bool:
    if not expected or not received:
        return False
    return hmac.compare_digest(expected.lower(), received.lower())


def command_hash(*, key: str, command: str, var1: str, salt: str) -> str:
    return sha512(f"{key}|{command}|{var1}|{salt}")


@dataclass(frozen=True)
class PayUConfig:
    key: str
    salt: str
    mode: str
    payment_url: str
    postservice_url: str

    @classmethod
    def from_settings(cls) -> PayUConfig:
        key = str(getattr(settings, "PAYU_MERCHANT_KEY", "") or "").strip()
        salt = str(getattr(settings, "PAYU_MERCHANT_SALT", "") or "").strip()
        mode = str(getattr(settings, "PAYU_MODE", "test") or "test").strip().lower()
        if not key or not salt:
            raise PayUConfigurationError(
                "PAYU_MERCHANT_KEY and PAYU_MERCHANT_SALT must be configured."
            )
        if mode not in PAYMENT_URLS:
            raise PayUConfigurationError("PAYU_MODE must be 'test' or 'production'.")
        return cls(
            key=key,
            salt=salt,
            mode=mode,
            payment_url=PAYMENT_URLS[mode],
            postservice_url=POSTSERVICE_URLS[mode],
        )


class PayUClient:
    def __init__(self, config: PayUConfig | None = None):
        self.config = config or PayUConfig.from_settings()

    def build_checkout_fields(
        self,
        *,
        txnid: str,
        amount: str,
        productinfo: str,
        firstname: str,
        email: str,
        phone: str,
        surl: str,
        furl: str,
        curl: str,
        lastname: str = "",
        udfs: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        udf_map = {
            f"udf{i}": str((udfs or {}).get(f"udf{i}", "") or "") for i in range(1, 6)
        }
        fields = {
            "key": self.config.key,
            "txnid": txnid,
            "amount": amount,
            "productinfo": productinfo,
            "firstname": firstname,
            "lastname": lastname,
            "email": email,
            "phone": phone,
            "surl": surl,
            "furl": furl,
            "curl": curl,
            **udf_map,
        }
        fields["hash"] = payment_request_hash(
            key=self.config.key,
            txnid=txnid,
            amount=amount,
            productinfo=productinfo,
            firstname=firstname,
            email=email,
            salt=self.config.salt,
            udfs=udf_map,
        )
        return fields

    def verify_callback(self, payload: Mapping[str, Any]) -> bool:
        received = str(payload.get("hash") or "")
        expected = payment_response_hash(payload, self.config.salt)
        return hashes_match(expected, received)

    def verify_payment(self, txnid: str) -> dict[str, Any]:
        return self._post_command("verify_payment", txnid)

    def refund(self, mihpayid: str, token: str, amount: str) -> dict[str, Any]:
        return self._post_command(
            "cancel_refund_transaction",
            mihpayid,
            extra={"var2": token, "var3": amount},
        )

    def _post_command(
        self,
        command: str,
        var1: str,
        extra: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        body = {
            "key": self.config.key,
            "command": command,
            "var1": var1,
            "hash": command_hash(
                key=self.config.key,
                command=command,
                var1=var1,
                salt=self.config.salt,
            ),
        }
        if extra:
            body.update(extra)
        return _post_form(self.config.postservice_url, body)


def _post_form(url: str, data: Mapping[str, str], timeout: int = 20) -> dict[str, Any]:
    encoded = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        logger.exception("PayU merchant API request failed")
        raise PayUAPIError("Unable to reach PayU.") from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("PayU returned a non-JSON response: %s", raw[:500])
        raise PayUAPIError("PayU returned an unexpected response.") from exc

    if not isinstance(parsed, dict):
        raise PayUAPIError("PayU returned an unexpected response.")
    return parsed
