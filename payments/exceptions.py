class PaymentServiceError(Exception):
    """Base error for the payment service."""


class PayUConfigurationError(PaymentServiceError):
    """Raised when merchant key, salt, or mode is missing or invalid."""


class PayUAPIError(PaymentServiceError):
    """Raised when a PayU merchant API call fails."""


class InvalidCallbackError(PaymentServiceError):
    """Raised when a PayU callback cannot be trusted."""


class PaymentStateError(PaymentServiceError):
    """Raised when an action is not allowed in the current payment state."""


class IdempotencyConflictError(PaymentServiceError):
    """Raised when an idempotency key is reused with a different request."""
