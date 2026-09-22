from decimal import Decimal

from django.db import models


class PaymentStatus(models.TextChoices):
    CREATED = "created", "Created"
    PENDING = "pending", "Pending"
    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"
    BOUNCED = "bounced", "Bounced"
    DROPPED = "dropped", "Dropped"
    REFUNDED = "refunded", "Refunded"
    PARTIALLY_REFUNDED = "partially_refunded", "Partially refunded"


class RefundStatus(models.TextChoices):
    REQUESTED = "requested", "Requested"
    PENDING = "pending", "Pending"
    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed"


class Payment(models.Model):
    txnid = models.CharField(max_length=25, unique=True, db_index=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    productinfo = models.CharField(max_length=255)
    firstname = models.CharField(max_length=60)
    lastname = models.CharField(max_length=60, blank=True)
    email = models.EmailField()
    phone = models.CharField(max_length=15)
    reference_id = models.CharField(max_length=64, blank=True, db_index=True)
    idempotency_key = models.CharField(max_length=255, unique=True, null=True, blank=True)
    idempotency_fingerprint = models.CharField(max_length=64, blank=True)
    status = models.CharField(
        max_length=32,
        choices=PaymentStatus.choices,
        default=PaymentStatus.CREATED,
        db_index=True,
    )
    payu_id = models.CharField(max_length=64, blank=True)
    bank_ref_num = models.CharField(max_length=64, blank=True)
    payment_mode = models.CharField(max_length=32, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.CharField(max_length=255, blank=True)
    request_hash = models.CharField(max_length=128, blank=True)
    udf1 = models.CharField(max_length=255, blank=True)
    udf2 = models.CharField(max_length=255, blank=True)
    udf3 = models.CharField(max_length=255, blank=True)
    udf4 = models.CharField(max_length=255, blank=True)
    udf5 = models.CharField(max_length=255, blank=True)
    gateway_status = models.CharField(max_length=32, blank=True)
    request_payload = models.JSONField(default=dict, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.txnid} ({self.status})"

    @property
    def refunded_amount(self) -> Decimal:
        total = self.refunds.filter(status=RefundStatus.SUCCESS).aggregate(
            total=models.Sum("amount")
        )["total"]
        return total or Decimal("0.00")


class Refund(models.Model):
    payment = models.ForeignKey(
        Payment, related_name="refunds", on_delete=models.CASCADE
    )
    token = models.CharField(max_length=23, unique=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(
        max_length=16,
        choices=RefundStatus.choices,
        default=RefundStatus.REQUESTED,
    )
    payu_request_id = models.CharField(max_length=64, blank=True)
    message = models.CharField(max_length=255, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.token} ({self.status})"
