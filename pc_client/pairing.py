"""
Pairs this PC with a Willy account.

The PC asks the hub for a short code, opens the hub's /pair page in the browser (where the
person signs in with Google and approves), and polls until the hub hands over this PC's own
key. The key is used exactly like the old shared token (WILLY_REMOTE_TOKEN), so nothing else
in the client changes.
"""

import json
import time
import urllib.error
import urllib.request
import webbrowser
from typing import Any, Callable, Dict, Optional

POLL_EVERY_SEC = 2.0


def _post(base: str, path: str, body: Dict[str, Any], timeout: float = 15.0) -> Dict[str, Any]:
    req = urllib.request.Request(base + path, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "X-Willy-Client": "pc"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read().decode("utf-8"))
        except ValueError:
            data = {}
        data.setdefault("success", False)
        data.setdefault("error", data.get("detail") or f"The hub answered HTTP {e.code}.")
        data["_status"] = e.code
        return data
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"success": False, "error": f"Couldn't reach the hub ({e})."}


def pair_device(hub_base: str, device_id: str, device_type: str, name: str,
                on_code: Optional[Callable[[str, str], None]] = None,
                open_browser: bool = True, timeout_sec: float = 600.0,
                should_stop: Callable[[], bool] = lambda: False) -> Dict[str, Any]:
    """Blocks until the person approves (or it times out). Returns {"success": True,
    "device_key", "email"} or {"success": False, "error"}."""
    hub_base = hub_base.rstrip("/")
    start = _post(hub_base, "/api/v1/pair/start", {"device_id": device_id, "device_type": device_type, "name": name})
    if not start.get("success"):
        return {"success": False, "error": start.get("error") or "The hub refused to start pairing."}
    code, url = start["code"], start["verify_url"]
    if on_code:
        on_code(code, url)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    deadline = time.time() + min(timeout_sec, float(start.get("expires_in") or 600))
    while time.time() < deadline and not should_stop():
        time.sleep(POLL_EVERY_SEC)
        res = _post(hub_base, "/api/v1/pair/poll", {"code": code, "poll_secret": start["poll_secret"]})
        if res.get("status") == "approved":
            return {"success": True, "device_key": res["device_key"], "email": res.get("email"), "code": code}
        if not res.get("success") and res.get("_status") in (404, 410):
            return {"success": False, "error": res.get("error") or "The pairing code expired."}
        # pending, or a brief network hiccup: keep waiting
    return {"success": False, "error": "Pairing timed out. Start it again from the tray menu."}


def redeem_code(hub_base: str, code: str, device_id: str, device_type: str, name: str) -> Dict[str, Any]:
    """Trades the one-time code from the dashboard (Set up > Add your phone and PC) for this device's key."""
    res = _post(hub_base.rstrip("/"), "/api/v1/pair/redeem",
                {"code": code.strip(), "device_id": device_id, "device_type": device_type, "name": name})
    if res.get("device_key"):
        return {"success": True, "device_key": res["device_key"], "email": res.get("email")}
    return {"success": False, "error": res.get("error") or "The hub didn't accept that code."}
