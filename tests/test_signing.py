from kkpay import make_signature, verify_signature


def test_signature_matches_gateway_protocol():
    payload = {
        "merchant_id": "demo",
        "amount": 100.0,
        "notify_url": "https://example.com/notify",
        "redirect_url": "",
        "ignored": None,
    }
    assert make_signature(payload, "secret") == "3d5878c27df1c6abb7e7979975416d6f"


def test_verify_signature():
    payload = {"order_id": "ORDER-1", "status": 2}
    payload["signature"] = make_signature(payload, "secret")
    assert verify_signature(payload, "secret")
    assert not verify_signature({**payload, "status": 1}, "secret")
