"""
Pairs this server with your Willy account (run once):

    python -m server_agent.pair --hub https://your-hub.example.com

It prints a link and a short code. Open the link on any device, sign in with Google and
approve; this server then receives its own key and saves it as WILLY_REMOTE_TOKEN (plus the
hub address) in the .env next to the project, which the agent reads on start.
"""

import argparse
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict

HOME = Path.home()


def _env_path() -> Path:
    here = Path(__file__).resolve().parent.parent / ".env"
    return here if here.parent.exists() else HOME / "win-assist" / ".env"


def _post(base: str, path: str, body: Dict) -> Dict:
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Willy-Client": "server"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read().decode())
        except ValueError:
            data = {}
        data.setdefault("error", data.get("detail") or f"HTTP {e.code}")
        data["_status"] = e.code
        return data
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"success": False, "error": str(e)}


def _save(env: Path, values: Dict[str, str]) -> None:
    lines = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
    keys = set(values)
    lines = [l for l in lines if l.split("=", 1)[0].strip() not in keys]
    lines += [f"{k}={v}" for k, v in values.items()]
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        env.chmod(0o600)
    except OSError:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Pair this server with your Willy account.")
    ap.add_argument("--hub", required=True, help="Hub address, e.g. https://truewilly.com/willy")
    ap.add_argument("--name", default=os.getenv("WILLY_SERVER_NAME", "Willy Server"))
    args = ap.parse_args()

    base = args.hub.rstrip("/")
    if not re.match(r"^https?://", base):
        base = "https://" + base
    device_id = "server_" + re.sub(r"[^a-z0-9]+", "_", socket.gethostname().lower()).strip("_")
    start = _post(base, "/api/v1/pair/start", {"device_id": device_id, "device_type": "server", "name": args.name})
    if not start.get("success"):
        print("Couldn't start pairing:", start.get("error"), file=sys.stderr)
        return 1
    print(f"\n  To connect this machine to your Willy account:")
    print(f"  1. Open in browser:  {start['verify_url']}")
    print(f"  2. Confirm the pairing code is:  {start['code']}")
    print(f"  3. Click Approve\n")
    print(f"Waiting for approval (code: {start['code']})", end="", flush=True)
    deadline = time.time() + float(start.get("expires_in") or 600)
    while time.time() < deadline:
        time.sleep(2)
        print(".", end="", flush=True)
        res = _post(base, "/api/v1/pair/poll", {"code": start["code"], "poll_secret": start["poll_secret"]})
        if res.get("status") == "approved":
            ws = base.replace("https://", "wss://").replace("http://", "ws://") + "/ws/devices"
            env = _env_path()
            _save(env, {"WILLY_REMOTE_TOKEN": res["device_key"], "WILLY_HUB_WS": ws, "WILLY_SERVER_NAME": args.name})
            print(f"\n\n[✓] Paired successfully with {res.get('email') or 'your account'}!")
            print(f"Configuration saved to {env}.\n")
            return 0
        if res.get("_status") in (404, 410):
            print(f"\n\nThe code expired: {res.get('error')}", file=sys.stderr)
            return 1
    print("\n\nTimed out waiting for approval.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
