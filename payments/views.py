import base64
import datetime
import re
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings
from django.db import transaction
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework import status

from sales.models import Sale
from .models import Payment


def normalize_kenyan_phone(phone):
    """Return Safaricom STK format 2547XXXXXXXX or raise ValueError."""
    raw = re.sub(r"\D", "", str(phone or ""))
    if raw.startswith("0") and len(raw) == 10:
        raw = "254" + raw[1:]
    elif raw.startswith("7") and len(raw) == 9:
        raw = "254" + raw
    elif raw.startswith("1") and len(raw) == 9:
        raw = "254" + raw
    elif raw.startswith("254") and len(raw) == 12:
        pass
    else:
        raise ValueError("Use a valid Kenyan phone number, for example 2547XXXXXXXX or 07XXXXXXXX.")
    return raw


def mpesa_base_url():
    return "https://api.safaricom.co.ke" if getattr(settings, "MPESA_ENV", "sandbox") == "production" else "https://sandbox.safaricom.co.ke"


def get_mpesa_access_token():
    consumer_key = getattr(settings, "MPESA_CONSUMER_KEY", "")
    consumer_secret = getattr(settings, "MPESA_CONSUMER_SECRET", "")
    if not consumer_key or not consumer_secret:
        raise ValueError("M-PESA consumer key and consumer secret are not configured in .env.")

    response = requests.get(
        f"{mpesa_base_url()}/oauth/v1/generate?grant_type=client_credentials",
        auth=(consumer_key, consumer_secret),
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if "access_token" not in data:
        raise ValueError(f"M-PESA token response did not include access_token: {data}")
    return data["access_token"]


def _metadata_value(callback_metadata, name):
    for item in callback_metadata.get("Item", []):
        if item.get("Name") == name:
            return item.get("Value")
    return None


def _decimal(value, field_name):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise ValueError(f"{field_name} must be a valid number.")


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_payments(request):
    payments = Payment.objects.select_related("sale").order_by("-created_at")[:100]
    return Response([
        {
            "id": p.id,
            "sale_id": p.sale_id,
            "receipt_number": p.sale.receipt_number,
            "method": p.method,
            "status": p.status,
            "amount": str(p.amount),
            "amount_paid": str(p.amount_paid),
            "change_due": str(p.change_due),
            "phone_number": p.phone_number,
            "mpesa_receipt_number": p.mpesa_receipt_number,
            "created_at": p.created_at,
            "updated_at": p.updated_at,
        }
        for p in payments
    ])


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def cash_payment(request):
    sale_id = request.data.get("sale_id")
    amount_paid = request.data.get("amount_paid")
    if not sale_id or amount_paid in (None, ""):
        return Response({"error": "sale_id and amount_paid are required."}, status=400)

    try:
        amount_paid = _decimal(amount_paid, "amount_paid")
        sale = Sale.objects.get(id=sale_id)
    except Sale.DoesNotExist:
        return Response({"error": "Sale not found."}, status=404)
    except ValueError as exc:
        return Response({"error": str(exc)}, status=400)

    total = Decimal(str(sale.total_amount))
    if sale.status == "PAID":
        return Response({"error": "This sale has already been paid."}, status=400)
    if amount_paid < total:
        return Response({"error": "Amount paid is less than the sale total."}, status=400)

    with transaction.atomic():
        payment = Payment.objects.create(
            sale=sale,
            method="CASH",
            status="PAID",
            amount=total,
            amount_paid=amount_paid,
            change_due=amount_paid - total,
        )
        sale.status = "PAID"
        sale.payment_method = "CASH"
        sale.save(update_fields=["status", "payment_method"])

    return Response({
        "message": "Cash payment recorded successfully.",
        "payment_id": payment.id,
        "sale_id": sale.id,
        "receipt_number": sale.receipt_number,
        "status": payment.status,
        "amount": str(total),
        "amount_paid": str(amount_paid),
        "change_due": str(payment.change_due),
    }, status=201)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def stk_push(request):
    sale_id = request.data.get("sale_id")
    phone = request.data.get("phone")
    if not sale_id or not phone:
        return Response({"error": "sale_id and phone are required."}, status=400)

    try:
        phone = normalize_kenyan_phone(phone)
        sale = Sale.objects.get(id=sale_id)
    except ValueError as exc:
        return Response({"error": str(exc)}, status=400)
    except Sale.DoesNotExist:
        return Response({"error": "Sale not found."}, status=404)

    if sale.status == "PAID":
        return Response({"error": "This sale has already been paid."}, status=400)

    required = {
        "MPESA_CONSUMER_KEY": getattr(settings, "MPESA_CONSUMER_KEY", ""),
        "MPESA_CONSUMER_SECRET": getattr(settings, "MPESA_CONSUMER_SECRET", ""),
        "MPESA_SHORTCODE": getattr(settings, "MPESA_SHORTCODE", ""),
        "MPESA_PASSKEY": getattr(settings, "MPESA_PASSKEY", ""),
        "MPESA_CALLBACK_URL": getattr(settings, "MPESA_CALLBACK_URL", ""),
    }
    missing = [key for key, value in required.items() if not value or "example.com" in str(value)]
    if missing:
        return Response({
            "error": "M-PESA is not fully configured.",
            "missing": missing,
            "hint": "Set Safaricom sandbox credentials and use a public callback URL such as ngrok for MPESA_CALLBACK_URL."
        }, status=400)

    timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    password = base64.b64encode((settings.MPESA_SHORTCODE + settings.MPESA_PASSKEY + timestamp).encode()).decode()
    amount = max(1, int(Decimal(str(sale.total_amount))))

    payload = {
        "BusinessShortCode": settings.MPESA_SHORTCODE,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": amount,
        "PartyA": phone,
        "PartyB": settings.MPESA_SHORTCODE,
        "PhoneNumber": phone,
        "CallBackURL": settings.MPESA_CALLBACK_URL,
        "AccountReference": f"QubitsSale{sale.id}",
        "TransactionDesc": "Qubits Data Solutions POS payment",
    }

    try:
        token = get_mpesa_access_token()
        res = requests.post(
            f"{mpesa_base_url()}/mpesa/stkpush/v1/processrequest",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        data = res.json()
    except requests.RequestException as exc:
        return Response({"error": "M-PESA network request failed.", "detail": str(exc)}, status=502)
    except ValueError as exc:
        return Response({"error": str(exc)}, status=400)

    response_code = str(data.get("ResponseCode", ""))
    if response_code not in ("0", "") or data.get("errorCode"):
        return Response({"error": "M-PESA rejected the request.", "mpesa_response": data}, status=400)

    payment = Payment.objects.create(
        sale=sale,
        method="MPESA",
        status="PENDING",
        amount=sale.total_amount,
        phone_number=phone,
        checkout_request_id=data.get("CheckoutRequestID"),
        merchant_request_id=data.get("MerchantRequestID"),
    )
    sale.payment_method = "MPESA"
    sale.customer_phone = phone
    sale.status = "PENDING"
    sale.save(update_fields=["payment_method", "customer_phone", "status"])

    return Response({
        "message": "STK Push initiated successfully.",
        "payment_id": payment.id,
        "sale_id": sale.id,
        "receipt_number": sale.receipt_number,
        "checkout_request_id": payment.checkout_request_id,
        "merchant_request_id": payment.merchant_request_id,
        "status": payment.status,
        "mpesa_response": data,
    })


@api_view(["POST"])
@permission_classes([AllowAny])
def mpesa_callback(request):
    body = request.data.get("Body", {})
    stk = body.get("stkCallback", {})
    checkout_id = stk.get("CheckoutRequestID")
    result_code = str(stk.get("ResultCode"))
    result_desc = stk.get("ResultDesc", "")

    if not checkout_id:
        return Response({"message": "CheckoutRequestID missing."}, status=400)

    try:
        payment = Payment.objects.select_related("sale").get(checkout_request_id=checkout_id)
    except Payment.DoesNotExist:
        return Response({"message": "Payment not found."}, status=404)

    payment.result_code = result_code
    payment.result_description = result_desc
    payment.raw_callback = request.data

    if result_code == "0":
        metadata = stk.get("CallbackMetadata", {})
        amount = _metadata_value(metadata, "Amount")
        receipt = _metadata_value(metadata, "MpesaReceiptNumber")
        transaction_date = _metadata_value(metadata, "TransactionDate")
        phone = _metadata_value(metadata, "PhoneNumber")

        payment.status = "PAID"
        payment.amount_paid = Decimal(str(amount)) if amount is not None else payment.amount
        payment.change_due = Decimal("0")
        payment.mpesa_receipt_number = receipt
        payment.mpesa_transaction_date = str(transaction_date) if transaction_date else None
        if phone:
            payment.phone_number = str(phone)

        payment.sale.status = "PAID"
        payment.sale.payment_method = "MPESA"
        if phone:
            payment.sale.customer_phone = str(phone)
        payment.sale.save()
    else:
        payment.status = "FAILED"
        payment.sale.status = "FAILED"
        payment.sale.save(update_fields=["status"])

    payment.save()
    return Response({"message": "Callback processed."})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def payment_status(request, checkout_request_id):
    try:
        payment = Payment.objects.select_related("sale").get(checkout_request_id=checkout_request_id)
    except Payment.DoesNotExist:
        return Response({"error": "Payment not found."}, status=404)

    return Response({
        "payment_id": payment.id,
        "sale_id": payment.sale_id,
        "receipt_number": payment.sale.receipt_number,
        "sale_status": payment.sale.status,
        "method": payment.method,
        "status": payment.status,
        "amount": str(payment.amount),
        "amount_paid": str(payment.amount_paid),
        "change_due": str(payment.change_due),
        "phone_number": payment.phone_number,
        "checkout_request_id": payment.checkout_request_id,
        "merchant_request_id": payment.merchant_request_id,
        "mpesa_receipt_number": payment.mpesa_receipt_number,
        "result_code": payment.result_code,
        "result_description": payment.result_description,
        "mpesa_transaction_date": payment.mpesa_transaction_date,
        "updated_at": payment.updated_at,
    })
