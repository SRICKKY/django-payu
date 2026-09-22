from django.contrib import admin

from .json_display import pretty_json_html
from .models import Payment, Refund


class JSONAdminMixin:
    class Media:
        css = {"all": ["payments/admin_json.css"]}  # noqa: RUF012
        js = ["payments/admin_json.js"]  # noqa: RUF012

    def pretty_request_payload(self, obj):
        return pretty_json_html(getattr(obj, "request_payload", None))

    pretty_request_payload.short_description = "Request payload"

    def pretty_response_payload(self, obj):
        return pretty_json_html(getattr(obj, "response_payload", None))

    pretty_response_payload.short_description = "Response payload"


class RefundInline(admin.TabularInline):
    model = Refund
    extra = 0
    readonly_fields = (
        "token",
        "amount",
        "status",
        "payu_request_id",
        "message",
        "created_at",
        "updated_at",
    )


@admin.register(Payment)
class PaymentAdmin(JSONAdminMixin, admin.ModelAdmin):
    list_display = (
        "txnid",
        "amount",
        "status",
        "email",
        "payu_id",
        "created_at",
    )
    list_filter = ("status", "payment_mode")
    search_fields = ("txnid", "email", "phone", "payu_id", "reference_id", "idempotency_key")
    readonly_fields = (
        "txnid",
        "request_hash",
        "pretty_request_payload",
        "pretty_response_payload",
        "verified_at",
        "idempotency_key",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "txnid",
                    "status",
                    "amount",
                    "productinfo",
                    "reference_id",
                    "idempotency_key",
                )
            },
        ),
        (
            "Customer",
            {
                "fields": (
                    "firstname",
                    "lastname",
                    "email",
                    "phone",
                )
            },
        ),
        (
            "PayU",
            {
                "fields": (
                    "payu_id",
                    "bank_ref_num",
                    "payment_mode",
                    "gateway_status",
                    "error_code",
                    "error_message",
                    "request_hash",
                    "verified_at",
                )
            },
        ),
        (
            "User-defined fields",
            {
                "classes": ("collapse",),
                "fields": ("udf1", "udf2", "udf3", "udf4", "udf5"),
            },
        ),
        (
            "Gateway payloads",
            {
                "fields": (
                    "pretty_request_payload",
                    "pretty_response_payload",
                )
            },
        ),
        (
            "Timestamps",
            {
                "fields": ("created_at", "updated_at"),
            },
        ),
    )
    inlines = [RefundInline]


@admin.register(Refund)
class RefundAdmin(JSONAdminMixin, admin.ModelAdmin):
    list_display = ("token", "payment", "amount", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("token", "payment__txnid", "payu_request_id")
    readonly_fields = (
        "token",
        "pretty_response_payload",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "payment",
                    "token",
                    "amount",
                    "status",
                    "payu_request_id",
                    "message",
                )
            },
        ),
        ("Gateway payload", {"fields": ("pretty_response_payload",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )
