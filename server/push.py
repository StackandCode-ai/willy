"""
Push notifications to the phone through Firebase Cloud Messaging (HTTP v1 API).

Used when the Willy app on the phone is not connected to the hub (ColorOS and other
battery savers kill background apps): reminders, alarms and watchdog alerts arrive as
system notifications, and a silent high-priority "wake" asks the app to reconnect.

Credentials: a Firebase service-account JSON (never in git), found at FIREBASE_CREDENTIALS
or <repo>/secrets/firebase-service-account.json or <repo>/secrets/*firebase-adminsdk*.json.
"""

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

REPO_DIR = Path(__file__).resolve().parent.parent
SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
CHANNEL_ID = "willy_alerts"


def _credentials_path() -> Optional[Path]:
    env = os.getenv("FIREBASE_CREDENTIALS")
    if env and Path(env).exists():
        return Path(env)
    secrets = REPO_DIR / "secrets"
    named = secrets / "firebase-service-account.json"
    if named.exists():
        return named
    found = sorted(secrets.glob("*firebase-adminsdk*.json")) if secrets.exists() else []
    return found[0] if found else None


class PushService:
    def __init__(self):
        self._creds = None
        self._project: Optional[str] = None
        self.last_error: Optional[str] = None

    @property
    def configured(self) -> bool:
        return _credentials_path() is not None

    def _access_token(self) -> str:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account

        if self._creds is None:
            path = _credentials_path()
            if path is None:
                raise RuntimeError("Firebase isn't set up on the hub (no service-account file).")
            self._creds = service_account.Credentials.from_service_account_file(str(path), scopes=[SCOPE])
            self._project = json.loads(path.read_text(encoding="utf-8")).get("project_id")
        if not self._creds.valid:
            self._creds.refresh(Request())
        return self._creds.token

    def _send_sync(self, message: Dict[str, Any]) -> Dict[str, Any]:
        import requests

        token = self._access_token()
        res = requests.post(f"https://fcm.googleapis.com/v1/projects/{self._project}/messages:send",
                            headers={"Authorization": f"Bearer {token}"}, json={"message": message}, timeout=10)
        if res.status_code == 200:
            return {"success": True, "id": res.json().get("name")}
        try:
            detail = res.json().get("error", {})
        except ValueError:
            detail = {}
        status = detail.get("status") or str(res.status_code)
        # The app was uninstalled / its token rotated: the caller forgets the token.
        gone = status in ("NOT_FOUND", "UNREGISTERED") or "UNREGISTERED" in json.dumps(detail)
        return {"success": False, "error": f"FCM {status}: {detail.get('message', '')[:160]}", "token_gone": gone}

    async def send(self, token: str, title: str = "", body: str = "", data: Optional[Dict[str, Any]] = None,
                   silent: bool = False) -> Dict[str, Any]:
        """A notification (title/body) or, with `silent`, a data-only high-priority message."""
        if not token:
            return {"success": False, "error": "This phone hasn't registered for push yet."}
        payload = {k: str(v) for k, v in (data or {}).items() if v is not None}
        message: Dict[str, Any] = {"token": token, "data": payload, "android": {"priority": "HIGH", "ttl": "600s"}}
        if not silent:
            message["notification"] = {"title": title[:120] or "Willy", "body": body[:500]}
            message["android"]["notification"] = {"channel_id": CHANNEL_ID, "sound": "default"}
        try:
            result = await asyncio.to_thread(self._send_sync, message)
        except Exception as e:  # noqa: BLE001 - network / credentials
            result = {"success": False, "error": str(e)[:200]}
        if not result.get("success"):
            self.last_error = result.get("error")
        return result


push_service = PushService()


def wake_data() -> Dict[str, Any]:
    return {"kind": "wake", "at": int(time.time())}
