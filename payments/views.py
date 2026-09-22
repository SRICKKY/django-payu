import uuid

from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .exceptions import (
    IdempotencyConflictError,
    InvalidCallbackError,
    PaymentServiceError,
    PaymentStateError,
    PayUConfigurationError,
)
from .models import Payment, PaymentStatus
from .payu import PayUClient
from .serializers import (
    CheckoutSerializer,
    InitiatePaymentSerializer,
    PaymentSerializer,
    RefundRequestSerializer,
    RefundSerializer,
)
from .services import (
    handle_payu_callback,
    initiate_payment,
    normalize_idempotency_key,
    refund_payment,
    verify_with_payu,
)


IDEMPOTENCY_HEADER = OpenApiParameter(
    name="Idempotency-Key",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.HEADER,
    required=True,
    description="Required unique key. Retrying with the same key and body returns the original payment instead of charging twice.",
)


class IdempotencyConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "idempotency_conflict"
    default_detail = "Idempotency-Key was reused with a different payment request."


class HomeView(TemplateView):
    template_name = "payments/home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["idempotency_key"] = uuid.uuid4().hex
        return context


class PaymentListCreateView(APIView):
    @extend_schema(responses=PaymentSerializer(many=True))
    def get(self, request):
        payments = Payment.objects.all()[:100]
        return Response(PaymentSerializer(payments, many=True).data)

    @extend_schema(
        request=InitiatePaymentSerializer,
        responses={
            200: CheckoutSerializer,
            201: CheckoutSerializer,
        },
        parameters=[IDEMPOTENCY_HEADER],
    )
    def post(self, request):
        try:
            idempotency_key = normalize_idempotency_key(
                request.headers.get("Idempotency-Key")
            )
        except PaymentStateError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        if not idempotency_key:
            raise ValidationError(
                {"detail": "Idempotency-Key header is required to prevent duplicate payments."}
            )

        serializer = InitiatePaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            payment, checkout, created = initiate_payment(
                amount=data["amount"],
                productinfo=data["productinfo"],
                firstname=data["firstname"],
                lastname=data.get("lastname", ""),
                email=data["email"],
                phone=data["phone"],
                reference_id=data.get("reference_id", ""),
                udfs={f"udf{i}": data.get(f"udf{i}", "") for i in range(1, 6)},
                idempotency_key=idempotency_key,
            )
        except IdempotencyConflictError as exc:
            raise IdempotencyConflict(str(exc)) from exc
        except PayUConfigurationError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        payload = {**checkout, "payment": payment}
        return Response(
            CheckoutSerializer(payload).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class PaymentDetailView(APIView):
    @extend_schema(responses=PaymentSerializer)
    def get(self, request, txnid):
        payment = get_object_or_404(Payment, txnid=txnid)
        return Response(PaymentSerializer(payment).data)


class PaymentVerifyView(APIView):
    @extend_schema(request=None, responses=PaymentSerializer)
    def post(self, request, txnid):
        payment = get_object_or_404(Payment, txnid=txnid)
        try:
            payment = verify_with_payu(payment.txnid)
        except PaymentServiceError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(PaymentSerializer(payment).data)


class PaymentRefundView(APIView):
    @extend_schema(request=RefundRequestSerializer, responses=RefundSerializer)
    def post(self, request, txnid):
        payment = get_object_or_404(Payment, txnid=txnid)
        serializer = RefundRequestSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        try:
            refund = refund_payment(payment, amount=serializer.validated_data.get("amount"))
        except PaymentStateError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        except PaymentServiceError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(RefundSerializer(refund).data, status=status.HTTP_201_CREATED)


class CheckoutPageView(TemplateView):
    template_name = "payments/checkout.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        payment = get_object_or_404(Payment, txnid=kwargs["txnid"])
        if payment.status not in {PaymentStatus.CREATED, PaymentStatus.PENDING}:
            raise Http404("This payment is no longer awaiting checkout.")
        try:
            client = PayUClient()
        except PayUConfigurationError as exc:
            raise Http404(str(exc)) from exc
        context["payment"] = payment
        context["payu_url"] = client.config.payment_url
        context["fields"] = payment.request_payload
        return context


class DemoCheckoutView(View):
    def post(self, request):
        serializer = InitiatePaymentSerializer(data=request.POST)
        if not serializer.is_valid():
            return render(
                request,
                "payments/home.html",
                {"errors": serializer.errors, "form": request.POST},
                status=400,
            )
        data = serializer.validated_data
        try:
            payment, checkout, _created = initiate_payment(
                amount=data["amount"],
                productinfo=data["productinfo"],
                firstname=data["firstname"],
                lastname=data.get("lastname", ""),
                email=data["email"],
                phone=data["phone"],
                reference_id=data.get("reference_id", ""),
                idempotency_key=request.POST.get("idempotency_key") or uuid.uuid4().hex,
            )
        except IdempotencyConflictError as exc:
            return render(
                request,
                "payments/home.html",
                {
                    "errors": {"idempotency_key": [str(exc)]},
                    "form": request.POST,
                    "idempotency_key": request.POST.get("idempotency_key") or uuid.uuid4().hex,
                },
                status=409,
            )
        except PayUConfigurationError as exc:
            return render(
                request,
                "payments/home.html",
                {"errors": {"configuration": [str(exc)]}, "form": request.POST},
                status=400,
            )
        if payment.status not in {PaymentStatus.CREATED, PaymentStatus.PENDING}:
            return render(request, "payments/result.html", {"payment": payment})
        return render(
            request,
            "payments/checkout.html",
            {
                "payment": payment,
                "payu_url": checkout["payu_url"],
                "fields": checkout["fields"],
            },
        )


def _render_callback_result(request, *, payment=None, error=""):
    return render(
        request,
        "payments/result.html",
        {"payment": payment, "error": error},
        status=400 if error and payment is None else 200,
    )


@method_decorator(csrf_exempt, name="dispatch")
class PayUCallbackView(APIView):
    authentication_classes = []
    permission_classes = []

    @extend_schema(exclude=True)
    def post(self, request):
        payload = request.POST.dict()
        try:
            payment = handle_payu_callback(payload)
        except InvalidCallbackError as exc:
            return _render_callback_result(request, error=str(exc))
        except PayUConfigurationError as exc:
            return _render_callback_result(request, error=str(exc))
        return _render_callback_result(request, payment=payment)
