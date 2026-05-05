import base64
import datetime
import requests
from decimal import Decimal
from django.conf import settings
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework import status
from sales.models import Sale
from .models import Payment


def get_mpesa_access_token():
    url = 'https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials'
    if settings.MPESA_ENV == 'production':
        url = 'https://api.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials'
    response = requests.get(
        url,
        auth=(settings.MPESA_CONSUMER_KEY, settings.MPESA_CONSUMER_SECRET),
        timeout=30,
    )
    response.raise_for_status()
    return response.json()['access_token']


def _metadata_value(callback_metadata, name):
    for item in callback_metadata.get('Item', []):
        if item.get('Name') == name:
            return item.get('Value')
    return None


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def cash_payment(request):
    sale_id = request.data.get('sale_id')
    amount_paid = request.data.get('amount_paid')
    if not sale_id or amount_paid is None:
        return Response({'error': 'sale_id and amount_paid are required'}, status=400)

    try:
        sale = Sale.objects.get(id=sale_id)
    except Sale.DoesNotExist:
        return Response({'error': 'Sale not found'}, status=404)

    amount_paid = Decimal(str(amount_paid))
    total = Decimal(str(sale.total_amount))
    if amount_paid < total:
        return Response({'error': 'Amount paid is less than sale total'}, status=400)

    payment = Payment.objects.create(
        sale=sale,
        method='CASH',
        status='PAID',
        amount=total,
        amount_paid=amount_paid,
        change_due=amount_paid - total,
    )
    sale.status = 'PAID'
    sale.payment_method = 'CASH'
    sale.save()

    return Response({
        'message': 'Cash payment recorded',
        'payment_id': payment.id,
        'sale_id': sale.id,
        'status': payment.status,
        'amount': str(total),
        'amount_paid': str(amount_paid),
        'change_due': str(payment.change_due),
    }, status=201)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def stk_push(request):
    sale_id = request.data.get('sale_id')
    phone = request.data.get('phone')
    if not sale_id or not phone:
        return Response({'error': 'sale_id and phone are required'}, status=400)

    try:
        sale = Sale.objects.get(id=sale_id)
    except Sale.DoesNotExist:
        return Response({'error': 'Sale not found'}, status=404)

    if not settings.MPESA_CONSUMER_KEY or not settings.MPESA_CONSUMER_SECRET or not settings.MPESA_PASSKEY:
        return Response({'error': 'M-PESA credentials are not configured in .env'}, status=400)

    timestamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
    password = base64.b64encode((settings.MPESA_SHORTCODE + settings.MPESA_PASSKEY + timestamp).encode()).decode()
    token = get_mpesa_access_token()
    url = 'https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest'
    if settings.MPESA_ENV == 'production':
        url = 'https://api.safaricom.co.ke/mpesa/stkpush/v1/processrequest'

    payload = {
        'BusinessShortCode': settings.MPESA_SHORTCODE,
        'Password': password,
        'Timestamp': timestamp,
        'TransactionType': 'CustomerPayBillOnline',
        'Amount': int(sale.total_amount),
        'PartyA': phone,
        'PartyB': settings.MPESA_SHORTCODE,
        'PhoneNumber': phone,
        'CallBackURL': settings.MPESA_CALLBACK_URL,
        'AccountReference': f'QubitsSale{sale.id}',
        'TransactionDesc': 'Qubits Cyber Services POS payment',
    }

    res = requests.post(url, json=payload, headers={'Authorization': f'Bearer {token}'}, timeout=30)
    data = res.json()

    payment = Payment.objects.create(
        sale=sale,
        method='MPESA',
        status='PENDING',
        amount=sale.total_amount,
        phone_number=phone,
        checkout_request_id=data.get('CheckoutRequestID'),
        merchant_request_id=data.get('MerchantRequestID'),
    )
    sale.payment_method = 'MPESA'
    sale.customer_phone = phone
    sale.status = 'PENDING'
    sale.save()

    return Response({
        'message': 'STK Push initiated',
        'payment_id': payment.id,
        'sale_id': sale.id,
        'checkout_request_id': payment.checkout_request_id,
        'merchant_request_id': payment.merchant_request_id,
        'status': payment.status,
        'mpesa_response': data,
    })


@api_view(['POST'])
@permission_classes([AllowAny])
def mpesa_callback(request):
    body = request.data.get('Body', {})
    stk = body.get('stkCallback', {})
    checkout_id = stk.get('CheckoutRequestID')
    result_code = str(stk.get('ResultCode'))
    result_desc = stk.get('ResultDesc')

    if not checkout_id:
        return Response({'message': 'CheckoutRequestID missing'}, status=400)

    try:
        payment = Payment.objects.select_related('sale').get(checkout_request_id=checkout_id)
    except Payment.DoesNotExist:
        return Response({'message': 'Payment not found'}, status=404)

    payment.result_code = result_code
    payment.result_description = result_desc
    payment.raw_callback = request.data

    if result_code == '0':
        metadata = stk.get('CallbackMetadata', {})
        amount = _metadata_value(metadata, 'Amount')
        receipt = _metadata_value(metadata, 'MpesaReceiptNumber')
        transaction_date = _metadata_value(metadata, 'TransactionDate')
        phone = _metadata_value(metadata, 'PhoneNumber')

        payment.status = 'PAID'
        payment.amount_paid = Decimal(str(amount)) if amount is not None else payment.amount
        payment.change_due = Decimal('0')
        payment.mpesa_receipt_number = receipt
        payment.mpesa_transaction_date = str(transaction_date) if transaction_date else None
        if phone:
            payment.phone_number = str(phone)

        payment.sale.status = 'PAID'
        payment.sale.payment_method = 'MPESA'
        if phone:
            payment.sale.customer_phone = str(phone)
        payment.sale.save()
    else:
        payment.status = 'FAILED'
        payment.sale.status = 'FAILED'
        payment.sale.save()

    payment.save()
    return Response({'message': 'Callback processed'})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def payment_status(request, checkout_request_id):
    try:
        payment = Payment.objects.select_related('sale').get(checkout_request_id=checkout_request_id)
    except Payment.DoesNotExist:
        return Response({'error': 'Payment not found'}, status=404)

    return Response({
        'payment_id': payment.id,
        'sale_id': payment.sale_id,
        'sale_status': payment.sale.status,
        'method': payment.method,
        'status': payment.status,
        'amount': str(payment.amount),
        'amount_paid': str(payment.amount_paid),
        'phone_number': payment.phone_number,
        'checkout_request_id': payment.checkout_request_id,
        'merchant_request_id': payment.merchant_request_id,
        'mpesa_receipt_number': payment.mpesa_receipt_number,
        'result_code': payment.result_code,
        'result_description': payment.result_description,
        'mpesa_transaction_date': payment.mpesa_transaction_date,
        'updated_at': payment.updated_at,
    })
