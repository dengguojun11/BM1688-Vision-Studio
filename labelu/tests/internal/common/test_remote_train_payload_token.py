from labelu.internal.common.remote_train_payload_token import (
    create_remote_payload_token,
    verify_remote_payload_token,
)


def test_remote_payload_token_roundtrip():
    token = create_remote_payload_token(118)
    assert verify_remote_payload_token(token, 118) is True
    assert verify_remote_payload_token(token, 119) is False
    assert verify_remote_payload_token("bad", 118) is False
