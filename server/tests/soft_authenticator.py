"""A minimal software passkey authenticator for tests (ES256, "none" attestation).

Produces exactly what a browser hands the dashboard from navigator.credentials.create()/get(), so the
server's real WebAuthn verification runs: flags, origin, challenge, signature and sign counter.
"""

import base64
import hashlib
import json
import os
import struct

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

FLAG_UP = 0x01  # user present
FLAG_UV = 0x04  # user verified (biometric / PIN)
FLAG_AT = 0x40  # attested credential data included


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class SoftAuthenticator:
    def __init__(self, rp_id: str = "testserver", origin: str = "http://testserver"):
        self.rp_id = rp_id
        self.origin = origin
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(16)
        self.sign_count = 0
        self.user_handle: str | None = None

    def _rp_hash(self) -> bytes:
        return hashlib.sha256(self.rp_id.encode()).digest()

    def _client_data(self, kind: str, challenge: str, origin: str | None) -> bytes:
        return json.dumps({"type": kind, "challenge": challenge, "origin": origin or self.origin, "crossOrigin": False}).encode()

    def create(self, options: dict, *, user_verified: bool = True) -> dict:
        """Answer PublicKeyCredentialCreationOptionsJSON with a RegistrationResponseJSON."""
        self.user_handle = options["user"]["id"]
        client_data = self._client_data("webauthn.create", options["challenge"], None)
        nums = self.key.public_key().public_numbers()
        cose_key = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: nums.x.to_bytes(32, "big"), -3: nums.y.to_bytes(32, "big")})
        flags = FLAG_UP | FLAG_AT | (FLAG_UV if user_verified else 0)
        auth_data = (
            self._rp_hash()
            + bytes([flags])
            + struct.pack(">I", self.sign_count)
            + bytes(16)  # AAGUID
            + struct.pack(">H", len(self.credential_id))
            + self.credential_id
            + cose_key
        )
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": {"clientDataJSON": b64url(client_data), "attestationObject": b64url(attestation), "transports": ["internal"]},
            "clientExtensionResults": {},
            "authenticatorAttachment": "platform",
        }

    def get(self, options: dict, *, user_verified: bool = True, origin: str | None = None, counter_step: int = 1) -> dict:
        """Answer PublicKeyCredentialRequestOptionsJSON with an AuthenticationResponseJSON."""
        client_data = self._client_data("webauthn.get", options["challenge"], origin)
        self.sign_count += counter_step
        auth_data = self._rp_hash() + bytes([FLAG_UP | (FLAG_UV if user_verified else 0)]) + struct.pack(">I", self.sign_count)
        signature = self.key.sign(auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "authenticatorData": b64url(auth_data),
                "signature": b64url(signature),
                "userHandle": self.user_handle,
            },
            "clientExtensionResults": {},
            "authenticatorAttachment": "platform",
        }
