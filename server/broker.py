"""
Google sign-in for hubs that have no Firebase of their own.

The master hub (the one at truewilly.com, WILLY_MASTER=1) has the Google/Firebase setup. A client hub
sends the person there: master page -> Google -> the master signs a short-lived token that names the
person AND the hub it is for (aud) -> the browser hands it to that hub, which verifies the signature
against the master's public key (/api/v1/broker/jwks) and signs the person in.

Because the token is bound to one hub address and expires in 5 minutes, a copy that reaches any other
hub is useless there.  Tokens are Ed25519-signed JWTs (alg EdDSA).
"""

import base64
import json
import os
import re
import secrets
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from server.accounts import AuthError
from server.config import DATA_DIR

TOKEN_TTL_SEC = 300
DEFAULT_MASTER_URL = "https://truewilly.com/willy"
_PRIVATE_HOST = re.compile(r"^(localhost|127\.\d+\.\d+\.\d+|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|[a-z0-9-]+\.local)$", re.I)


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64u_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def master_url() -> str:
    """Where this hub sends people to sign in with Google ('' = not used)."""
    return os.getenv("WILLY_BROKER_URL", DEFAULT_MASTER_URL).strip().rstrip("/")


def master_issuer(url: str) -> str:
    """The `iss` of tokens the master at `url` signs (its address; the same string on both sides)."""
    return (url or "").strip().rstrip("/")


def is_master() -> bool:
    return os.getenv("WILLY_MASTER", "").strip().lower() in ("1", "true", "yes", "on")


def clean_origin(value: str) -> Optional[str]:
    """'https://hub.example.com' (scheme + host [+ port] only). http is accepted for home-network and
    localhost hubs only. Anything else (paths, credentials, odd schemes) is refused."""
    try:
        u = urlparse((value or "").strip())
    except ValueError:
        return None
    if u.scheme not in ("https", "http") or not u.hostname or u.username or u.password or u.path not in ("", "/") \
            or u.query or u.fragment or len(value) > 200:
        return None
    if u.scheme == "http" and not _PRIVATE_HOST.match(u.hostname):
        return None
    return f"{u.scheme}://{u.netloc.lower()}"


class BrokerSigner:
    """The master's signing key (created on first use, kept in the data folder)."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path or (DATA_DIR / "broker_key.pem"))
        self._key: Optional[Ed25519PrivateKey] = None
        self._lock = threading.Lock()

    def _load(self) -> Ed25519PrivateKey:
        with self._lock:
            if self._key is None:
                if self.path.exists():
                    self._key = serialization.load_pem_private_key(self.path.read_bytes(), password=None)
                else:
                    self._key = Ed25519PrivateKey.generate()
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    self.path.write_bytes(self._key.private_bytes(
                        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
                    try:
                        self.path.chmod(0o600)
                    except OSError:
                        pass
            return self._key

    def _public_raw(self) -> bytes:
        return self._load().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)

    @property
    def kid(self) -> str:
        import hashlib

        return hashlib.sha256(self._public_raw()).hexdigest()[:16]

    def jwks(self) -> Dict[str, Any]:
        return {"keys": [{"kty": "OKP", "crv": "Ed25519", "use": "sig", "alg": "EdDSA", "kid": self.kid,
                          "x": b64u(self._public_raw())}]}

    def sign(self, claims: Dict[str, Any]) -> str:
        header = {"alg": "EdDSA", "typ": "JWT", "kid": self.kid}
        signing_input = b64u(json.dumps(header, separators=(",", ":")).encode()) + "." + \
            b64u(json.dumps(claims, separators=(",", ":")).encode())
        return signing_input + "." + b64u(self._load().sign(signing_input.encode("ascii")))

    def issue(self, issuer: str, hub_origin: str, claims: Dict[str, Any], state: str) -> str:
        now = int(time.time())
        return self.sign({"iss": issuer, "aud": hub_origin, "iat": now, "exp": now + TOKEN_TTL_SEC, "jti": secrets.token_hex(8),
                          "state": state, "sub": claims["sub"], "email": claims["email"], "email_verified": True,
                          "name": claims.get("name"), "picture": claims.get("picture")})


class JwksCache:
    """The master's public keys, fetched over HTTPS and cached for an hour."""

    def __init__(self, fetch: Optional[Callable[[str], Dict[str, Any]]] = None):
        self._fetch = fetch or self._http_fetch
        self._keys: Dict[str, Any] = {}
        self._until = 0.0
        self._lock = threading.Lock()

    @staticmethod
    def _http_fetch(url: str) -> Dict[str, Any]:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def keys(self, jwks_url: str, force: bool = False) -> Dict[str, Any]:
        with self._lock:
            if force or not self._keys or time.time() > self._until:
                data = self._fetch(jwks_url)
                self._keys = {k["kid"]: k for k in data.get("keys", []) if k.get("kty") == "OKP" and k.get("crv") == "Ed25519"}
                self._until = time.time() + 3600
            return self._keys


def verify_token(token: str, jwks_url: str, issuer: str, audience: str, cache: JwksCache) -> Dict[str, Any]:
    """Checks signature, issuer, audience (this hub) and expiry; returns the claims."""
    try:
        head_b64, body_b64, sig_b64 = token.split(".")
        header = json.loads(b64u_decode(head_b64))
        claims = json.loads(b64u_decode(body_b64))
        signature = b64u_decode(sig_b64)
    except (ValueError, TypeError):
        raise AuthError("That sign-in is malformed.") from None
    if header.get("alg") != "EdDSA":
        raise AuthError("That sign-in uses an unsupported signature.")
    try:
        keys = cache.keys(jwks_url)
        jwk = keys.get(header.get("kid")) or cache.keys(jwks_url, force=True).get(header.get("kid"))
    except Exception:
        raise AuthError("Couldn't reach the sign-in service to check your login. Try again, or sign in with email.", 503) from None
    if not jwk:
        raise AuthError("That sign-in wasn't issued by the sign-in service this hub trusts.")
    try:
        Ed25519PublicKey.from_public_bytes(b64u_decode(jwk["x"])).verify(signature, f"{head_b64}.{body_b64}".encode("ascii"))
    except Exception:
        raise AuthError("That sign-in's signature is invalid.") from None
    now = time.time()
    if claims.get("iss") != issuer:
        raise AuthError("That sign-in came from a different service.")
    if claims.get("aud") != audience:
        raise AuthError("That sign-in was made for a different hub.")
    if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] < now - 30 or claims.get("iat", 0) > now + 60:
        raise AuthError("That sign-in has expired. Start again.")
    if not claims.get("sub") or not claims.get("email") or not claims.get("email_verified"):
        raise AuthError("That sign-in has no verified email.")
    return claims
