import base64
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.config import settings
from app.services import jwt_auth


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@pytest.fixture()
def ed25519_jwt(monkeypatch):
    private = Ed25519PrivateKey.generate()
    public_x = _b64url(
        private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    )
    kid = "test-key"
    jwks = {"keys": [{"kty": "OKP", "crv": "Ed25519", "x": public_x, "kid": kid, "alg": "EdDSA"}]}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            ua = self.headers.get("User-Agent", "")
            if "Python-urllib" in ua:
                self.send_response(403)
                self.end_headers()
                self.wfile.write(b"Forbidden")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(jwks).encode())

        def log_message(self, format: str, *args: object) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    issuer = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setattr(settings, "better_auth_url", issuer)
    jwt_auth._jwks_client = None
    jwt_auth._jwks_url = None

    token = jwt.encode(
        {
            "sub": "auth-user-1",
            "email": "user@example.com",
            "iss": issuer,
            "aud": issuer,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        private,
        algorithm="EdDSA",
        headers={"kid": kid},
    )
    try:
        yield token
    finally:
        server.shutdown()
        jwt_auth._jwks_client = None
        jwt_auth._jwks_url = None


def test_verify_jwt_when_default_urllib_user_agent_is_blocked(ed25519_jwt):
    claims = jwt_auth.verify_better_auth_jwt(ed25519_jwt)
    assert claims.sub == "auth-user-1"
    assert claims.email == "user@example.com"
