# django-payu

Django payment service for [PayU](https://docs.payu.in/docs/prebuilt-checkout-page-integration) hosted checkout. It creates transactions, redirects customers to PayU, verifies callback hashes, and can confirm or refund payments through PayU merchant APIs.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill `.env` with your PayU merchant key and salt. Keep `PAYU_MODE=test` until you go live.

```
PAYU_MERCHANT_KEY=your_merchant_key
PAYU_MERCHANT_SALT=your_merchant_salt
PAYU_MODE=test
SITE_URL=http://127.0.0.1:8000
ALLOWED_HOSTS=localhost,127.0.0.1
```

`SITE_URL` is used to build PayU success, failure, and cancel URLs. For a real test transaction, that host must be reachable after PayU redirects the browser.

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

- Demo checkout: http://127.0.0.1:8000/
- API docs: http://127.0.0.1:8000/api/docs/
- Admin: http://127.0.0.1:8000/admin/

## Payment flow

1. Create a payment with `POST /api/payments/` and an `Idempotency-Key` header, or use the demo form.
2. The service stores the transaction, generates a SHA-512 hash, and returns PayU checkout fields.
3. The customer pays on PayU hosted checkout.
4. PayU POSTs back to `/payments/callback/success/` or `/payments/callback/failure/`.
5. The service verifies the reverse hash, merchant key, and amount before updating status. A later failure cannot overwrite a successful payment.

Retrying `POST /api/payments/` with the same `Idempotency-Key` and body returns the original transaction (`200`) instead of creating a second PayU payment. Reusing the key with a different amount or customer payload returns `409`.

## API

| Action | Method | Path |
| --- | --- | --- |
| Create / list payments | `POST` / `GET` | `/api/payments/` |
| Get payment | `GET` | `/api/payments/<txnid>/` |
| Verify with PayU | `POST` | `/api/payments/<txnid>/verify/` |
| Refund | `POST` | `/api/payments/<txnid>/refund/` |
| Auto-submit checkout | `GET` | `/payments/<txnid>/checkout/` |

Create a payment:

```bash
curl -X POST http://127.0.0.1:8000/api/payments/ \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: order-1001" \
  -d '{
    "amount": "10.00",
    "productinfo": "iPhone",
    "firstname": "Ashish",
    "email": "ashish@example.com",
    "phone": "9999999999",
    "reference_id": "ORD-1001"
  }'
```

The response includes `payu_url`, hashed `fields` for the PayU form, and `checkout_url`.

## Tests

```bash
python manage.py test payments
```
