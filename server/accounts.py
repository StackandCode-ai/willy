"""
Willy accounts: Google sign-in, sessions, per-device keys and device pairing.

Storage is any SQLAlchemy URL in WILLY_DATABASE_URL (e.g. mysql+pymysql://user:pass@host/willy);
without one a SQLite file in the data folder is used, so a self-hosted hub needs no database
setup. Secrets (session tokens, device keys, pairing poll secrets) are stored only as SHA-256
hashes; the raw value is handed out once.

Who gets which space:
- The hub owner (the Google account in WILLY_OWNER_EMAIL) owns the "home" space: the devices,
  reminders and history that existed before accounts.
- Everyone else who signs in gets a private space of their own (WILLY_SIGNUP=open), or is
  refused (WILLY_SIGNUP=closed, the default for a self-hosted hub) unless their email is in
  WILLY_ALLOWED_EMAILS.
- The hub token (WILLY_REMOTE_TOKEN) still works and always means the owner.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from sqlalchemy import (Boolean, Column, Float, MetaData, String, Table, create_engine, delete, func, insert,
                        select, update)
from sqlalchemy.engine import Engine

from server.config import DATA_DIR

HOME_SPACE = "home"
LOCAL_OWNER_EMAIL = "owner@local.willy"  # placeholder owner of a hub that has no Google sign-in
SESSION_TTL_SEC = 60 * 60 * 24 * 30
PAIR_TTL_SEC = 10 * 60
SESSION_PREFIX = "wses_"
DEVICE_KEY_PREFIX = "wdev_"
# Pairing codes are read off one screen and typed or approved on another: no 0/O/1/I/L.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
FIREBASE_CERTS_URL = "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"
RESOLVE_CACHE_SEC = 60

metadata = MetaData()

users = Table(
    "willy_users", metadata,
    Column("id", String(40), primary_key=True),
    Column("google_sub", String(128), unique=True, nullable=True),
    Column("email", String(320), unique=True, nullable=False),
    Column("name", String(200), nullable=True),
    Column("picture", String(1000), nullable=True),
    Column("space_id", String(40), nullable=False),
    Column("role", String(20), nullable=False, default="user"),      # admin | user
    Column("status", String(20), nullable=False, default="active"),  # active | blocked
    Column("created_at", Float, nullable=False),
    Column("last_login_at", Float, nullable=True),
    Column("password_hash", String(300), nullable=True),            # email + password sign-in (scrypt)
    Column("email_verified", Boolean, nullable=False, default=False),  # True once Google (or the owner) vouched for it
)

sessions = Table(
    "willy_sessions", metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("user_id", String(40), nullable=False, index=True),
    Column("created_at", Float, nullable=False),
    Column("expires_at", Float, nullable=False),
    Column("client", String(200), nullable=True),
)

device_keys = Table(
    "willy_device_keys", metadata,
    Column("key_hash", String(64), primary_key=True),
    Column("user_id", String(40), nullable=False, index=True),
    Column("device_id", String(120), nullable=False),
    Column("device_type", String(20), nullable=False),
    Column("label", String(200), nullable=True),
    Column("created_at", Float, nullable=False),
    Column("last_used_at", Float, nullable=True),
    Column("revoked", Boolean, nullable=False, default=False),
)

pair_codes = Table(
    "willy_pair_codes", metadata,
    Column("code", String(16), primary_key=True),
    Column("poll_hash", String(64), nullable=True),     # device-initiated: the device's poll secret
    Column("device_id", String(120), nullable=True),
    Column("device_type", String(20), nullable=True),
    Column("name", String(200), nullable=True),
    Column("user_id", String(40), nullable=True),       # set when approved (or at creation for link codes)
    Column("created_at", Float, nullable=False),
    Column("expires_at", Float, nullable=False),
    Column("approved_at", Float, nullable=True),
    Column("redeemed_at", Float, nullable=True),
)


# --- Master registry (only filled on the hub that runs as the master, WILLY_MASTER=1) ---------------
# Who signed in through the master's Google login, which hubs checked in, and what features they use.
# Never commands, files or conversations.

master_users = Table(
    "willy_master_users", metadata,
    Column("email", String(320), primary_key=True),
    Column("name", String(200), nullable=True),
    Column("google_sub", String(128), nullable=True),
    Column("first_seen", Float, nullable=False),
    Column("last_seen", Float, nullable=False),
    Column("logins", Float, nullable=False, default=0),
    Column("last_hub", String(300), nullable=True),
)

master_hubs = Table(
    "willy_master_hubs", metadata,
    Column("hub_id", String(64), primary_key=True),
    Column("origin", String(300), nullable=True),
    Column("version", String(40), nullable=True),
    Column("platform", String(80), nullable=True),
    Column("owner_email", String(320), nullable=True),
    Column("accounts", Float, nullable=True),
    Column("pcs", Float, nullable=True),
    Column("phones", Float, nullable=True),
    Column("servers", Float, nullable=True),
    Column("ai_provider", String(40), nullable=True),
    Column("first_seen", Float, nullable=False),
    Column("last_seen", Float, nullable=False),
)

master_usage = Table(
    "willy_master_usage", metadata,
    Column("hub_id", String(64), primary_key=True),
    Column("day", String(10), primary_key=True),
    Column("commands", Float, nullable=False, default=0),
    Column("failures", Float, nullable=False, default=0),
    Column("tools_json", String(8000), nullable=True),
)


class AuthError(Exception):
    """Sign-in or pairing refused; the message is safe to show the user."""

    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Principal:
    """Who is calling: the account, its space, and how it proved it."""
    user_id: Optional[str]
    space_id: str
    role: str
    via: str                      # hub_token | session | device_key
    email: Optional[str] = None
    name: Optional[str] = None
    device_id: Optional[str] = None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_home(self) -> bool:
        return self.space_id == HOME_SPACE


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _csv_env(name: str) -> List[str]:
    return [x.strip().lower() for x in os.getenv(name, "").split(",") if x.strip()]


def _new_code() -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def normalize_code(code: str) -> str:
    raw = "".join(ch for ch in str(code or "").upper() if ch.isalnum())
    return f"{raw[:4]}-{raw[4:8]}" if len(raw) == 8 else ""


EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[A-Za-z]{2,}$")
MIN_PASSWORD = 10
_DUMMY_HASH = None


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: Optional[str]) -> bool:
    """Takes the same time for unknown accounts (no stored hash): it checks against a dummy one."""
    global _DUMMY_HASH
    known = bool(stored)
    if not known:
        _DUMMY_HASH = _DUMMY_HASH or hash_password("dummy-password-for-timing")
    try:
        _, n, r, p, salt, digest = (stored if known else _DUMMY_HASH).split("$")
        calc = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(calc, bytes.fromhex(digest)) and known
    except (ValueError, TypeError):
        return False


def check_password_strength(password: str) -> Optional[str]:
    if len(password or "") < MIN_PASSWORD:
        return f"Use a password of at least {MIN_PASSWORD} characters."
    if len(password) > 200:
        return "That password is too long."
    if password.lower() in ("password123", "1234567890", "qwertyuiop", "password12", "passwordpassword"):
        return "That password is too common. Pick another."
    return None


class FirebaseVerifier:
    """Checks Firebase Auth ID tokens (what Google sign-in on the web and in the apps returns)
    against Google's public certificates, cached for as long as Google says they're valid."""

    def __init__(self, project_id: str):
        self.project_id = project_id
        self._certs: Dict[str, str] = {}
        self._certs_until = 0.0
        self._lock = threading.Lock()

    def _certificates(self) -> Dict[str, str]:
        with self._lock:
            if self._certs and time.time() < self._certs_until:
                return self._certs
            with urllib.request.urlopen(FIREBASE_CERTS_URL, timeout=10) as resp:
                certs = json.loads(resp.read().decode("utf-8"))
                max_age = 3600
                for part in (resp.headers.get("Cache-Control") or "").split(","):
                    part = part.strip()
                    if part.startswith("max-age="):
                        try:
                            max_age = int(part.split("=", 1)[1])
                        except ValueError:
                            pass
            self._certs, self._certs_until = certs, time.time() + max(60, max_age - 60)
            return certs

    def verify(self, id_token: str) -> Dict[str, Any]:
        from google.auth import jwt

        if not self.project_id:
            raise AuthError("Google sign-in isn't set up on this hub (FIREBASE_PROJECT_ID).", 503)
        try:
            claims = jwt.decode(id_token, certs=self._certificates(), audience=self.project_id,
                                clock_skew_in_seconds=30)
        except Exception as e:  # wrong signature, expired, wrong audience...
            raise AuthError(f"That Google sign-in couldn't be verified ({type(e).__name__}).") from None
        if claims.get("iss") != f"https://securetoken.google.com/{self.project_id}":
            raise AuthError("That sign-in is for a different Firebase project.")
        if not claims.get("sub"):
            raise AuthError("That sign-in has no account id.")
        if not claims.get("email") or not claims.get("email_verified"):
            raise AuthError("Sign in with a Google account that has a verified email.")
        return claims


class Accounts:
    def __init__(self, url: Optional[str] = None, project_id: Optional[str] = None):
        url = url or os.getenv("WILLY_DATABASE_URL", "").strip() or f"sqlite:///{(DATA_DIR / 'willy.db').as_posix()}"
        if url.startswith("sqlite:///"):
            DATA_DIR.mkdir(parents=True, exist_ok=True)
        kwargs: Dict[str, Any] = {"future": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        else:
            kwargs.update(pool_pre_ping=True, pool_recycle=1800)
        self.engine: Engine = create_engine(url, **kwargs)
        metadata.create_all(self.engine)
        self.firebase = FirebaseVerifier(project_id if project_id is not None else os.getenv("FIREBASE_PROJECT_ID", "").strip())
        self._cache: Dict[str, Any] = {}   # token hash -> (expires, Principal)
        self._rate: Dict[str, List[float]] = {}

    @property
    def backend(self) -> str:
        return self.engine.dialect.name

    # ------------------------------------------------------------------ policy

    @staticmethod
    def owner_emails() -> List[str]:
        return _csv_env("WILLY_OWNER_EMAIL")

    @staticmethod
    def signup_mode() -> str:
        mode = os.getenv("WILLY_SIGNUP", "closed").strip().lower()
        return mode if mode in ("open", "closed") else "closed"

    def allow_rate(self, key: str, limit: int, per_sec: float) -> bool:
        """Simple sliding window: at most `limit` calls per `per_sec` for this key."""
        now = time.time()
        hits = [t for t in self._rate.get(key, []) if now - t < per_sec]
        if len(hits) >= limit:
            self._rate[key] = hits
            return False
        hits.append(now)
        self._rate[key] = hits
        if len(self._rate) > 5000:
            self._rate = {k: v for k, v in self._rate.items() if v and now - v[-1] < per_sec}
        return True

    # ------------------------------------------------------------------ users

    def _row_user(self, row) -> Optional[Dict[str, Any]]:
        return dict(row._mapping) if row is not None else None

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        with self.engine.connect() as c:
            return self._row_user(c.execute(select(users).where(users.c.id == user_id)).first())

    def home_owner(self) -> Optional[Dict[str, Any]]:
        with self.engine.connect() as c:
            return self._row_user(c.execute(select(users).where(users.c.space_id == HOME_SPACE)
                                            .order_by(users.c.created_at)).first())

    def list_users(self) -> List[Dict[str, Any]]:
        with self.engine.connect() as c:
            rows = [dict(r._mapping) for r in c.execute(select(users).order_by(users.c.created_at))]
            counts = dict(c.execute(select(device_keys.c.user_id, func.count()).where(device_keys.c.revoked.is_(False))
                                    .group_by(device_keys.c.user_id)).all())
        for r in rows:
            r["device_keys"] = int(counts.get(r["id"], 0))
            r.pop("google_sub", None)
        return rows

    def set_status(self, user_id: str, status: str) -> bool:
        if status not in ("active", "blocked"):
            raise ValueError(status)
        with self.engine.begin() as c:
            row = c.execute(select(users).where(users.c.id == user_id)).first()
            if row is None or (status == "blocked" and row._mapping["space_id"] == HOME_SPACE):
                return False  # the owner can't lock themselves out
            c.execute(update(users).where(users.c.id == user_id).values(status=status))
        self._cache.clear()
        return True

    def sign_in_google(self, id_token: str, client: str = "") -> Dict[str, Any]:
        """Verifies the Google sign-in, creates the account on first sign-in (if allowed) and
        returns {"token": session token, "user": {...}}."""
        claims = self.firebase.verify(id_token)
        return self.sign_in_claims(claims, client)

    def sign_in_claims(self, claims: Dict[str, Any], client: str = "") -> Dict[str, Any]:
        email = str(claims["email"]).strip().lower()
        sub = str(claims["sub"])
        now = time.time()
        with self.engine.begin() as c:
            row = c.execute(select(users).where((users.c.google_sub == sub) | (users.c.email == email))).first()
            if row is None:
                # Ownership is never first-come-first-served: only the configured owner email gets
                # the home space (a stranger reaching a fresh public hub first must not win it).
                home = c.execute(select(users).where(users.c.space_id == HOME_SPACE)).first()
                if home is not None and home._mapping["email"] == LOCAL_OWNER_EMAIL and email in self.owner_emails():
                    # The configured owner signs in with Google for the first time: they take over the
                    # placeholder owner, keeping the devices already paired to it.
                    c.execute(update(users).where(users.c.id == home._mapping["id"]).values(
                        google_sub=sub, email=email, name=claims.get("name"), picture=claims.get("picture"),
                        last_login_at=now, email_verified=True))
                    return self._new_session(c, home._mapping["id"], now, client)
                has_home = home is not None
                is_owner = email in self.owner_emails() and not has_home
                if not is_owner and self.signup_mode() != "open" and email not in _csv_env("WILLY_ALLOWED_EMAILS"):
                    raise AuthError("This Willy hub is private. Ask its owner to add your email, or set up your own Willy.", 403)
                uid = uuid.uuid4().hex
                c.execute(insert(users).values(
                    id=uid, google_sub=sub, email=email, name=claims.get("name"), picture=claims.get("picture"),
                    space_id=HOME_SPACE if is_owner else uid, role="admin" if is_owner else "user",
                    status="active", created_at=now, last_login_at=now, email_verified=True))
            else:
                uid = row._mapping["id"]
                if row._mapping["status"] != "active":
                    raise AuthError("This account is blocked on this Willy hub.", 403)
                if row._mapping["password_hash"] and not row._mapping["email_verified"]:
                    # Someone registered this address with a password before its real owner proved it with
                    # Google: that password, its sessions and its paired devices can't be trusted.
                    c.execute(update(users).where(users.c.id == uid).values(password_hash=None))
                    c.execute(delete(sessions).where(sessions.c.user_id == uid))
                    c.execute(delete(device_keys).where(device_keys.c.user_id == uid))
                    self._cache.clear()
                c.execute(update(users).where(users.c.id == uid).values(
                    email_verified=True, google_sub=sub, name=claims.get("name") or row._mapping["name"],
                    picture=claims.get("picture") or row._mapping["picture"], last_login_at=now))
            return self._new_session(c, uid, now, client)

    def _new_session(self, c, uid: str, now: float, client: str) -> Dict[str, Any]:
        raw = SESSION_PREFIX + secrets.token_urlsafe(32)
        c.execute(insert(sessions).values(token_hash=_hash(raw), user_id=uid, created_at=now,
                                          expires_at=now + SESSION_TTL_SEC, client=(client or "")[:200]))
        user = dict(c.execute(select(users).where(users.c.id == uid)).first()._mapping)
        return {"token": raw, "user": self.public_user(user), "expires_at": now + SESSION_TTL_SEC}

    @staticmethod
    def public_user(user: Dict[str, Any]) -> Dict[str, Any]:
        return {"id": user["id"], "email": user["email"], "name": user.get("name"), "picture": user.get("picture"),
                "role": user["role"], "owner": user["space_id"] == HOME_SPACE, "has_password": bool(user.get("password_hash"))}

    def _signup_allowed(self, email: str) -> bool:
        return self.signup_mode() == "open" or email in _csv_env("WILLY_ALLOWED_EMAILS")

    def register_email(self, email: str, password: str, name: str = "", client: str = "") -> Dict[str, Any]:
        """Email + password sign-up for people without Google. Never creates the owner (an unverified
        address can't claim the hub); the owner uses the hub token link or Google."""
        email = (email or "").strip().lower()
        if not EMAIL_RE.match(email):
            raise AuthError("Enter a valid email address.", 400)
        weak = check_password_strength(password)
        if weak:
            raise AuthError(weak, 400)
        if email in self.owner_emails():
            raise AuthError("That address belongs to this hub's owner: sign in with Google or the owner link.", 403)
        if not self._signup_allowed(email):
            raise AuthError("This Willy hub is private. Ask its owner to add your email.", 403)
        now = time.time()
        with self.engine.begin() as c:
            if c.execute(select(users.c.id).where(users.c.email == email)).first() is not None:
                raise AuthError("An account with this email already exists. Sign in instead.", 409)
            uid = uuid.uuid4().hex
            c.execute(insert(users).values(id=uid, google_sub=None, email=email, name=(name or email.split("@")[0])[:200],
                                           space_id=uid, role="user", status="active", created_at=now, last_login_at=now,
                                           password_hash=hash_password(password), email_verified=False))
            return self._new_session(c, uid, now, client)

    def login_email(self, email: str, password: str, client: str = "") -> Dict[str, Any]:
        email = (email or "").strip().lower()
        now = time.time()
        with self.engine.begin() as c:
            row = c.execute(select(users).where(users.c.email == email)).first()
            ok = verify_password(password or "", row._mapping["password_hash"] if row is not None else None)
            if not ok:
                raise AuthError("Email or password is wrong.", 401)
            if row._mapping["status"] != "active":
                raise AuthError("This account is blocked on this Willy hub.", 403)
            c.execute(update(users).where(users.c.id == row._mapping["id"]).values(last_login_at=now))
            return self._new_session(c, row._mapping["id"], now, client)

    def set_password(self, user_id: str, password: str, email: Optional[str] = None, vouched: bool = False,
                     keep_session: str = "") -> None:
        """Sets or changes an account's password. `vouched`: the caller already proved control of the hub
        (hub token), so a new email on the local owner account counts as verified."""
        weak = check_password_strength(password)
        if weak:
            raise AuthError(weak, 400)
        values: Dict[str, Any] = {"password_hash": hash_password(password)}
        with self.engine.begin() as c:
            row = c.execute(select(users).where(users.c.id == user_id)).first()
            if row is None:
                raise AuthError("Unknown account.", 404)
            if email:
                email = email.strip().lower()
                if not EMAIL_RE.match(email):
                    raise AuthError("Enter a valid email address.", 400)
                if row._mapping["email"] != email:
                    if row._mapping["email"] != LOCAL_OWNER_EMAIL:
                        raise AuthError("The email of an account can't be changed here.", 400)
                    if c.execute(select(users.c.id).where(users.c.email == email)).first() is not None:
                        raise AuthError("Another account already uses that email.", 409)
                    values.update(email=email, email_verified=bool(vouched))
            c.execute(update(users).where(users.c.id == user_id).values(**values))
            # Other sessions of this account end (the person just chose a new secret).
            stale = delete(sessions).where(sessions.c.user_id == user_id)
            if keep_session:
                stale = stale.where(sessions.c.token_hash != _hash(keep_session))
            c.execute(stale)
        self._cache.clear()

    def sign_out(self, raw: str) -> None:
        with self.engine.begin() as c:
            c.execute(delete(sessions).where(sessions.c.token_hash == _hash(raw)))
        self._cache.pop(_hash(raw), None)

    # ------------------------------------------------------------------ token -> principal

    def resolve(self, raw: Optional[str]) -> Optional[Principal]:
        """A session token or device key -> Principal (None if unknown, expired, revoked or blocked)."""
        raw = (raw or "").strip()
        if not raw.startswith((SESSION_PREFIX, DEVICE_KEY_PREFIX)):
            return None
        h = _hash(raw)
        hit = self._cache.get(h)
        if hit and hit[0] > time.time():
            return hit[1]
        now = time.time()
        principal = None
        with self.engine.begin() as c:
            if raw.startswith(SESSION_PREFIX):
                row = c.execute(select(sessions.c.user_id, sessions.c.expires_at).where(sessions.c.token_hash == h)).first()
                if row is not None and row.expires_at > now:
                    principal = self._principal(c, row.user_id, "session")
            else:
                row = c.execute(select(device_keys).where(device_keys.c.key_hash == h)).first()
                if row is not None and not row._mapping["revoked"]:
                    principal = self._principal(c, row._mapping["user_id"], "device_key", row._mapping["device_id"])
                    c.execute(update(device_keys).where(device_keys.c.key_hash == h).values(last_used_at=now))
        if principal is not None:
            self._cache[h] = (now + RESOLVE_CACHE_SEC, principal)
            if len(self._cache) > 5000:
                self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
        return principal

    def _principal(self, c, user_id: str, via: str, device_id: Optional[str] = None) -> Optional[Principal]:
        u = c.execute(select(users).where(users.c.id == user_id)).first()
        if u is None or u._mapping["status"] != "active":
            return None
        m = u._mapping
        return Principal(user_id=m["id"], space_id=m["space_id"], role=m["role"], via=via, email=m["email"],
                         name=m["name"], device_id=device_id)

    def owner_principal(self) -> Principal:
        """What the hub token stands for: the home space's owner. A hub without Google sign-in
        gets a local owner account, so devices can still be paired (and a Google owner can take
        it over later)."""
        owner = self.home_owner() or self.ensure_local_owner()
        return Principal(user_id=owner["id"], space_id=HOME_SPACE, role="admin", via="hub_token",
                         email=owner["email"], name=owner.get("name"))

    def ensure_local_owner(self) -> Dict[str, Any]:
        with self.engine.begin() as c:
            row = c.execute(select(users).where(users.c.space_id == HOME_SPACE)).first()
            if row is None:
                uid = uuid.uuid4().hex
                c.execute(insert(users).values(id=uid, google_sub=None, email=LOCAL_OWNER_EMAIL, name="Owner",
                                               space_id=HOME_SPACE, role="admin", status="active",
                                               created_at=time.time(), last_login_at=None, email_verified=True))
                row = c.execute(select(users).where(users.c.id == uid)).first()
            return dict(row._mapping)

    # ------------------------------------------------------------------ device keys

    def issue_device_key(self, user_id: str, device_id: str, device_type: str, label: str = "") -> str:
        """A new key for one device. An older key the same device had is revoked."""
        raw = DEVICE_KEY_PREFIX + secrets.token_urlsafe(32)
        now = time.time()
        with self.engine.begin() as c:
            c.execute(update(device_keys).where((device_keys.c.user_id == user_id) & (device_keys.c.device_id == device_id))
                      .values(revoked=True))
            c.execute(insert(device_keys).values(key_hash=_hash(raw), user_id=user_id, device_id=device_id,
                                                 device_type=device_type, label=(label or device_id)[:200],
                                                 created_at=now, last_used_at=None, revoked=False))
        self._cache.clear()
        return raw

    def list_device_keys(self, user_id: str) -> List[Dict[str, Any]]:
        with self.engine.connect() as c:
            rows = c.execute(select(device_keys.c.device_id, device_keys.c.device_type, device_keys.c.label,
                                    device_keys.c.created_at, device_keys.c.last_used_at)
                             .where((device_keys.c.user_id == user_id) & device_keys.c.revoked.is_(False))
                             .order_by(device_keys.c.created_at))
            return [dict(r._mapping) for r in rows]

    def revoke_device(self, user_id: str, device_id: str) -> int:
        with self.engine.begin() as c:
            n = c.execute(update(device_keys).where((device_keys.c.user_id == user_id) & (device_keys.c.device_id == device_id)
                                                    & device_keys.c.revoked.is_(False)).values(revoked=True)).rowcount
        self._cache.clear()
        return n

    # ------------------------------------------------------------------ pairing

    def _purge_codes(self, c) -> None:
        c.execute(delete(pair_codes).where(pair_codes.c.expires_at < time.time() - 3600))

    def start_pairing(self, device_id: str, device_type: str, name: str) -> Dict[str, Any]:
        """Device-initiated (PC app, server agent): the device shows the code, the user approves
        it while signed in, the device polls with its secret and receives its key once."""
        poll_secret = secrets.token_urlsafe(24)
        now = time.time()
        with self.engine.begin() as c:
            self._purge_codes(c)
            for _ in range(5):
                code = _new_code()
                if c.execute(select(pair_codes.c.code).where(pair_codes.c.code == code)).first() is None:
                    break
            c.execute(insert(pair_codes).values(code=code, poll_hash=_hash(poll_secret), device_id=device_id[:120],
                                                device_type=device_type[:20], name=(name or device_id)[:200],
                                                created_at=now, expires_at=now + PAIR_TTL_SEC))
        return {"code": code, "poll_secret": poll_secret, "expires_in": PAIR_TTL_SEC}

    def pairing_info(self, code: str) -> Optional[Dict[str, Any]]:
        code = normalize_code(code)
        with self.engine.connect() as c:
            row = c.execute(select(pair_codes).where(pair_codes.c.code == code)).first()
        if row is None or row._mapping["expires_at"] < time.time() or row._mapping["redeemed_at"]:
            return None
        m = row._mapping
        return {"code": code, "device_id": m["device_id"], "device_type": m["device_type"], "name": m["name"],
                "approved": bool(m["approved_at"]), "device_initiated": bool(m["poll_hash"])}

    def approve_pairing(self, code: str, user_id: str) -> Dict[str, Any]:
        code = normalize_code(code)
        now = time.time()
        with self.engine.begin() as c:
            row = c.execute(select(pair_codes).where(pair_codes.c.code == code)).first()
            if row is None or row._mapping["expires_at"] < now or row._mapping["redeemed_at"] or not row._mapping["poll_hash"]:
                raise AuthError("That code has expired or was already used. Start pairing again on the device.", 404)
            if row._mapping["approved_at"] and row._mapping["user_id"] != user_id:
                raise AuthError("That code was already approved by another account.", 409)
            c.execute(update(pair_codes).where(pair_codes.c.code == code).values(user_id=user_id, approved_at=now))
        return self.pairing_info(code) or {}

    def poll_pairing(self, code: str, poll_secret: str) -> Dict[str, Any]:
        code = normalize_code(code)
        now = time.time()
        with self.engine.begin() as c:
            row = c.execute(select(pair_codes).where(pair_codes.c.code == code)).first()
            if row is None or not row._mapping["poll_hash"] or not secrets.compare_digest(row._mapping["poll_hash"], _hash(poll_secret or "")):
                raise AuthError("Unknown pairing code.", 404)
            m = row._mapping
            if m["redeemed_at"]:
                raise AuthError("This pairing was already completed.", 410)
            if m["expires_at"] < now:
                raise AuthError("The pairing code expired. Start again.", 410)
            if not m["approved_at"]:
                return {"status": "pending"}
            c.execute(update(pair_codes).where(pair_codes.c.code == code).values(redeemed_at=now))
        key = self.issue_device_key(m["user_id"], m["device_id"], m["device_type"], m["name"])
        user = self.get_user(m["user_id"]) or {}
        return {"status": "approved", "device_key": key, "device_id": m["device_id"], "email": user.get("email")}

    def create_link_code(self, user_id: str) -> Dict[str, Any]:
        """User-initiated (QR on the dashboard): a one-time code a phone or PC redeems for its key."""
        now = time.time()
        with self.engine.begin() as c:
            self._purge_codes(c)
            code = _new_code()
            c.execute(insert(pair_codes).values(code=code, user_id=user_id, created_at=now, expires_at=now + PAIR_TTL_SEC,
                                                approved_at=now))
        return {"code": code, "expires_in": PAIR_TTL_SEC}

    def redeem_link_code(self, code: str, device_id: str, device_type: str, name: str) -> Dict[str, Any]:
        code = normalize_code(code)
        now = time.time()
        with self.engine.begin() as c:
            row = c.execute(select(pair_codes).where(pair_codes.c.code == code)).first()
            if row is None or row._mapping["poll_hash"] or row._mapping["redeemed_at"] or row._mapping["expires_at"] < now:
                raise AuthError("That code has expired or was already used. Make a new one on the dashboard.", 404)
            user_id = row._mapping["user_id"]
            c.execute(update(pair_codes).where(pair_codes.c.code == code)
                      .values(redeemed_at=now, device_id=device_id[:120], device_type=device_type[:20], name=name[:200]))
        key = self.issue_device_key(user_id, device_id, device_type, name)
        user = self.get_user(user_id) or {}
        return {"device_key": key, "device_id": device_id, "email": user.get("email")}

    def issue_key_for_session(self, principal: Principal, device_id: str, device_type: str, name: str) -> Dict[str, Any]:
        """A device that signed in with Google itself (the phone app) trades its session for a device key."""
        if principal.user_id is None:
            raise AuthError("Sign in with Google first.")
        key = self.issue_device_key(principal.user_id, device_id, device_type, name)
        return {"device_key": key, "device_id": device_id, "email": principal.email}
