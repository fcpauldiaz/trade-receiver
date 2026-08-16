import base64
import json
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Iterator

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

AUTH_USER_ID = "auth-user-1"
AUTH_EMAIL = "user@example.com"
JWKS_KID = "test-key"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@dataclass(frozen=True)
class JwksIssuer:
    issuer: str
    token: str


@contextmanager
def blocked_urllib_jwks_issuer(
    *,
    user_id: str = AUTH_USER_ID,
    email: str = AUTH_EMAIL,
    expires_in: timedelta = timedelta(minutes=5),
) -> Iterator[JwksIssuer]:
    private = Ed25519PrivateKey.generate()
    public_x = _b64url(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
    jwks = {
        "keys": [
            {"kty": "OKP", "crv": "Ed25519", "x": public_x, "kid": JWKS_KID, "alg": "EdDSA"}
        ]
    }

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
    token = jwt.encode(
        {
            "sub": user_id,
            "email": email,
            "iss": issuer,
            "aud": issuer,
            "exp": datetime.now(timezone.utc) + expires_in,
        },
        private,
        algorithm="EdDSA",
        headers={"kid": JWKS_KID},
    )
    try:
        yield JwksIssuer(issuer=issuer, token=token)
    finally:
        server.shutdown()
