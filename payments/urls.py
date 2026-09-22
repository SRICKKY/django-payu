from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("", views.HomeView.as_view(), name="home"),
    path("start/", views.DemoCheckoutView.as_view(), name="start"),
    path("api/payments/", views.PaymentListCreateView.as_view(), name="payment-list"),
    path(
        "api/payments/<str:txnid>/",
        views.PaymentDetailView.as_view(),
        name="payment-detail",
    ),
    path(
        "api/payments/<str:txnid>/verify/",
        views.PaymentVerifyView.as_view(),
        name="payment-verify",
    ),
    path(
        "api/payments/<str:txnid>/refund/",
        views.PaymentRefundView.as_view(),
        name="payment-refund",
    ),
    path(
        "payments/<str:txnid>/checkout/",
        views.CheckoutPageView.as_view(),
        name="checkout",
    ),
    path(
        "payments/callback/success/",
        views.PayUCallbackView.as_view(),
        name="callback-success",
    ),
    path(
        "payments/callback/failure/",
        views.PayUCallbackView.as_view(),
        name="callback-failure",
    ),
    path(
        "payments/callback/cancel/",
        views.PayUCallbackView.as_view(),
        name="callback-cancel",
    ),
]
