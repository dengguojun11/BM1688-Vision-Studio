import asyncio
import hashlib
import hmac
import time

import pytest
from starlette.requests import Request

from labelu.internal.common.config import settings
from labelu.internal.common.error_code import LabelUException
from labelu.internal.common.third_party_auth import verify_third_party_request
from labelu.internal.domain.models.annotation_taxonomy import ThirdPartyRequestReceipt


def _signed_request(
    body: bytes,
    *,
    secret: str,
    nonce: str,
    timestamp: int,
    path: str = "/api/v1/integrations/strategy-tasks",
    signature_override: str | None = None,
) -> Request:
    body_hash = hashlib.sha256(body).hexdigest()
    canonical = "\n".join(("POST", path, str(timestamp), nonce, body_hash)).encode()
    signature = hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest()
    headers = [
        (b"content-type", b"application/json"),
        (b"x-labelu-timestamp", str(timestamp).encode()),
        (b"x-labelu-nonce", nonce.encode()),
        (
            b"x-labelu-signature",
            (signature_override or f"sha256={signature}").encode(),
        ),
    ]
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": headers,
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 10000),
        },
        receive,
    )


def test_v2_hmac_uses_method_path_timestamp_nonce_and_raw_body_hash(
    db, monkeypatch
):
    secret = "test-third-party-secret"
    monkeypatch.setattr(settings, "STRATEGY_EVOLUTION_CALLBACK_HMAC_SECRET", secret)
    monkeypatch.setattr(settings, "THIRD_PARTY_RATE_LIMIT_PER_MINUTE", 100)
    now = int(time.time())
    body = b'{"schema_version":"v2","labels":["person"]}'
    request = _signed_request(
        body,
        secret=secret,
        nonce="nonce-canonical-1",
        timestamp=now,
    )

    asyncio.run(
        verify_third_party_request(
            request,
            required=True,
            db=db,
            source_system="Strategy_Evolution",
        )
    )

    receipt = db.query(ThirdPartyRequestReceipt).one()
    assert receipt.source_system == "strategy_evolution"
    assert receipt.request_sha256 == hashlib.sha256(body).hexdigest()


def test_v2_hmac_rejects_bad_signature_expired_timestamp_and_replay(
    db, monkeypatch
):
    secret = "test-third-party-secret"
    monkeypatch.setattr(settings, "STRATEGY_EVOLUTION_CALLBACK_HMAC_SECRET", secret)
    monkeypatch.setattr(settings, "THIRD_PARTY_RATE_LIMIT_PER_MINUTE", 100)
    now = int(time.time())
    body = b"{}"

    with pytest.raises(LabelUException) as bad_signature:
        asyncio.run(
            verify_third_party_request(
                _signed_request(
                    body,
                    secret=secret,
                    nonce="nonce-bad-signature",
                    timestamp=now,
                    signature_override="sha256=" + "0" * 64,
                ),
                required=True,
                db=db,
            )
        )
    assert bad_signature.value.status_code == 401

    with pytest.raises(LabelUException) as expired:
        asyncio.run(
            verify_third_party_request(
                _signed_request(
                    body,
                    secret=secret,
                    nonce="nonce-expired",
                    timestamp=now
                    - int(settings.STRATEGY_EVOLUTION_CALLBACK_HMAC_MAX_SKEW_SECONDS)
                    - 10,
                ),
                required=True,
                db=db,
            )
        )
    assert expired.value.status_code == 401

    first = _signed_request(
        body,
        secret=secret,
        nonce="nonce-replay",
        timestamp=now,
    )
    asyncio.run(verify_third_party_request(first, required=True, db=db))
    second = _signed_request(
        body,
        secret=secret,
        nonce="nonce-replay",
        timestamp=now,
    )
    with pytest.raises(LabelUException) as replay:
        asyncio.run(verify_third_party_request(second, required=True, db=db))
    assert replay.value.status_code == 409
