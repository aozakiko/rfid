from __future__ import annotations

import copy
from typing import Any

from cloud_edge_demo.pipeline import create_signed_envelope, verify_signed_envelope


def _verify_rejected(envelope: dict[str, Any], shared_secret: str) -> bool:
    try:
        verify_signed_envelope(envelope, shared_secret)
    except ValueError:
        return True
    return False


def run_security_check(shared_secret: str = "rfid-course-demo-secret") -> dict[str, Any]:
    """Exercise the HMAC-SHA256 message integrity design used in the demo."""
    payload = {
        "device_id": "reader-security-test",
        "gateway_id": "edge-security-test",
        "batch_index": 1,
        "temperature_c": 24.2,
        "humidity_pct": 52.0,
        "tags": [{"id": 1, "price": 99}, {"id": 2, "price": 99}],
    }

    envelope = create_signed_envelope(payload, shared_secret)
    valid_message_accepted = verify_signed_envelope(envelope, shared_secret) == payload

    tampered_payload = copy.deepcopy(envelope)
    tampered_payload["payload"]["temperature_c"] = 88.8
    tampered_payload_rejected = _verify_rejected(tampered_payload, shared_secret)

    wrong_secret_rejected = _verify_rejected(envelope, shared_secret + "-wrong")

    missing_signature = copy.deepcopy(envelope)
    missing_signature["signature"] = ""
    missing_signature_rejected = _verify_rejected(missing_signature, shared_secret)

    passed = all(
        [
            valid_message_accepted,
            tampered_payload_rejected,
            wrong_secret_rejected,
            missing_signature_rejected,
        ]
    )

    return {
        "algorithm": "HMAC-SHA256",
        "valid_message_accepted": valid_message_accepted,
        "tampered_payload_rejected": tampered_payload_rejected,
        "wrong_secret_rejected": wrong_secret_rejected,
        "missing_signature_rejected": missing_signature_rejected,
        "passed": passed,
    }

