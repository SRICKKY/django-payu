from decimal import Decimal

from rest_framework import serializers

from .models import Payment, Refund


class InitiatePaymentSerializer(serializers.Serializer):
    amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal("0.01")
    )
    productinfo = serializers.CharField(max_length=255)
    firstname = serializers.CharField(max_length=60)
    lastname = serializers.CharField(max_length=60, required=False, allow_blank=True, default="")
    email = serializers.EmailField()
    phone = serializers.RegexField(regex=r"^[0-9]{8,15}$")
    reference_id = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
    udf1 = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    udf2 = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    udf3 = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    udf4 = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    udf5 = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


class RefundRequestSerializer(serializers.Serializer):
    amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal("0.01"), required=False
    )


class RefundSerializer(serializers.ModelSerializer):
    class Meta:
        model = Refund
        fields = (
            "token",
            "amount",
            "status",
            "payu_request_id",
            "message",
            "created_at",
            "updated_at",
        )


class PaymentSerializer(serializers.ModelSerializer):
    refunded_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    refunds = RefundSerializer(many=True, read_only=True)

    class Meta:
        model = Payment
        fields = (
            "txnid",
            "amount",
            "productinfo",
            "firstname",
            "lastname",
            "email",
            "phone",
            "reference_id",
            "status",
            "payu_id",
            "bank_ref_num",
            "payment_mode",
            "error_code",
            "error_message",
            "gateway_status",
            "refunded_amount",
            "refunds",
            "verified_at",
            "created_at",
            "updated_at",
        )


class CheckoutSerializer(serializers.Serializer):
    txnid = serializers.CharField()
    payu_url = serializers.URLField()
    checkout_url = serializers.URLField()
    fields = serializers.DictField(child=serializers.CharField(allow_blank=True))
    payment = PaymentSerializer()
