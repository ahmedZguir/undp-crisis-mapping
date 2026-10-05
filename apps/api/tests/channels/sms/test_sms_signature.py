"""SMS webhook HMAC verification: hex HMAC-SHA256 over body + X-Timestamp, ±5-min window."""

from __future__ import annotations

import hashlib
import hmac
import time

from api.channels.sms.routes import _signature_ok

SECRET = "s3cr3t-signing-key"  # gitleaks:allow  — dummy fixture, not a real key
BODY = b'{"event":"sms:received","payload":{"sender":"+97433885517","message":"hi"}}'


def _sign(body: bytes, timestamp: str, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body + timestamp.encode(), hashlib.sha256).hexdigest()


def test_valid_signature_passes() -> None:
    ts = str(int(time.time()))
    assert _signature_ok(BODY, _sign(BODY, ts), ts, SECRET) is True


def test_wrong_signature_fails() -> None:
    ts = str(int(time.time()))
    assert _signature_ok(BODY, _sign(BODY, ts) + "00", ts, SECRET) is False


def test_tampered_body_fails() -> None:
    ts = str(int(time.time()))
    sig = _sign(BODY, ts)
    assert _signature_ok(BODY + b" ", sig, ts, SECRET) is False


def test_wrong_secret_fails() -> None:
    ts = str(int(time.time()))
    assert _signature_ok(BODY, _sign(BODY, ts, "other"), ts, SECRET) is False


def test_missing_header_fails() -> None:
    ts = str(int(time.time()))
    assert _signature_ok(BODY, None, ts, SECRET) is False
    assert _signature_ok(BODY, _sign(BODY, ts), None, SECRET) is False


def test_non_numeric_timestamp_fails() -> None:
    assert _signature_ok(BODY, "deadbeef", "not-a-number", SECRET) is False


def test_stale_timestamp_fails() -> None:
    ts = str(int(time.time()) - 600)  # 10 min old, outside the 5-min window
    assert _signature_ok(BODY, _sign(BODY, ts), ts, SECRET) is False


# ---- dedup key (regression: content-derived messageId collisions) --------


def test_dedup_key_distinguishes_repeated_answers() -> None:
    from api.channels.sms.routes import InboundSms, _dedup_key

    # Same text answered twice => SMS Gate reuses messageId, but receivedAt
    # differs => the keys MUST differ so the second answer is processed.
    a = InboundSms(
        from_e164="+974",
        body="1",
        message_id="ce616001",
        received_at="2026-06-08T13:15:00+03:00",
        sim_number=1,
    )
    b = InboundSms(
        from_e164="+974",
        body="1",
        message_id="ce616001",
        received_at="2026-06-08T13:16:30+03:00",
        sim_number=1,
    )
    assert _dedup_key(a) != _dedup_key(b)


def test_dedup_key_collapses_redeliveries() -> None:
    from api.channels.sms.routes import InboundSms, _dedup_key

    # A retry of the SAME SMS repeats messageId AND receivedAt => same key.
    m = dict(
        from_e164="+974",
        body="1",
        message_id="ce616001",
        received_at="2026-06-08T13:15:00+03:00",
        sim_number=1,
    )
    assert _dedup_key(InboundSms(**m)) == _dedup_key(InboundSms(**m))
