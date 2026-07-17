import pytest

from kkpay import KKPayQRCodeError, make_qr_png, payment_qr_payload, payment_qr_png


class PaymentLike:
    payment_url = "https://pay.example/checkout/T1"


def test_payment_qr_uses_the_expiring_checkout_url():
    payment = PaymentLike()
    assert payment_qr_payload(payment) == payment.payment_url
    image = payment_qr_png(payment)
    assert image.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(image) > 100


def test_qr_rejects_invalid_payloads():
    with pytest.raises(KKPayQRCodeError, match="must not be empty"):
        make_qr_png("")
    with pytest.raises(KKPayQRCodeError, match="absolute HTTP"):
        payment_qr_payload(type("BadPayment", (), {"payment_url": "TAddress"})())
