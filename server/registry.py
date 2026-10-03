"""
The master registry: who signed in through the master's Google login and which hubs check in.

Master side (WILLY_MASTER=1): Registry stores sign-ins and daily check-ins.
Client side: build_ping()/send_ping() report this hub to the master once a day, unless the owner turned it
off (WILLY_TELEMETRY=off). What a check-in contains is exactly build_ping() below: hub address, version,
platform, owner email, how many accounts and PC/phone/server devices, the AI provider's name, and per-day
counts of commands and of tool names used. It never contains commands, replies, files, keys or
conversations.
"""

import json
import os
import platform
import re
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import func, insert, select, update

from server.accounts import master_hubs, master_usage, master_users
from server.broker import clean_origin
from server.config import DATA_DIR

HUB_ID_FILE = DATA_DIR / "hub_id.txt"
PING_EVERY_SEC = 24 * 3600
_HUB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def telemetry_enabled() -> bool:
    from server.broker import is_master, master_url

    return (os.getenv("WILLY_TELEMETRY", "on").strip().lower() not in ("0", "off", "false", "no")
            and not is_master() and bool(master_url()))


def hub_id() -> str:
    try:
        value = HUB_ID_FILE.read_text(encoding="utf-8").strip()
        if _HUB_ID_RE.match(value):
            return value
    except OSError:
        pass
    value = uuid.uuid4().hex
    try:
        HUB_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
        HUB_ID_FILE.write_text(value, encoding="utf-8")
    except OSError:
        pass
    return value


def build_ping(version: str, origin: str, owner_email: Optional[str], accounts: int, devices: Dict[str, int],
               ai_provider: str, usage_days: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "hub_id": hub_id(), "origin": origin, "version": version,
        "platform": f"{platform.system()} {platform.release()}"[:80],
        "owner_email": owner_email, "accounts": accounts,
        "devices": {"pc": devices.get("pc", 0), "mobile": devices.get("mobile", 0), "server": devices.get("server", 0)},
        "ai_provider": ai_provider, "usage": usage_days,
    }


def send_ping_sync(base_url: str, payload: Dict[str, Any], timeout: float = 15.0) -> bool:
    req = urllib.request.Request(base_url.rstrip("/") + "/api/v1/registry/ping", data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "X-Willy-Client": "hub"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def _int(value: Any, cap: int = 10_000_000) -> int:
    try:
        return max(0, min(int(value), cap))
    except (TypeError, ValueError):
        return 0


class Registry:
    def __init__(self, engine):
        self.engine = engine

    # ------------------------------------------------------------------ sign-ins (through the broker)

    def record_login(self, email: str, name: Optional[str], sub: str, hub_origin: str) -> None:
        now = time.time()
        with self.engine.begin() as c:
            row = c.execute(select(master_users.c.email).where(master_users.c.email == email)).first()
            if row is None:
                c.execute(insert(master_users).values(email=email, name=name, google_sub=sub, first_seen=now, last_seen=now,
                                                      logins=1, last_hub=hub_origin))
            else:
                c.execute(update(master_users).where(master_users.c.email == email).values(
                    name=name, google_sub=sub, last_seen=now, logins=master_users.c.logins + 1, last_hub=hub_origin))

    # ------------------------------------------------------------------ check-ins (from hubs)

    def record_ping(self, data: Dict[str, Any]) -> bool:
        hid = str(data.get("hub_id") or "")
        if not _HUB_ID_RE.match(hid):
            return False
        origin = clean_origin(str(data.get("origin") or "")) if data.get("origin") else None
        owner = str(data.get("owner_email") or "").strip().lower()[:320] or None
        if owner and (owner.endswith("@local.willy") or "@" not in owner):
            owner = None
        dev = data.get("devices") if isinstance(data.get("devices"), dict) else {}
        values = dict(origin=origin, version=str(data.get("version") or "")[:40], platform=str(data.get("platform") or "")[:80],
                      owner_email=owner, accounts=_int(data.get("accounts")), pcs=_int(dev.get("pc")), phones=_int(dev.get("mobile")),
                      servers=_int(dev.get("server")), ai_provider=str(data.get("ai_provider") or "")[:40])
        now = time.time()
        with self.engine.begin() as c:
            if c.execute(select(master_hubs.c.hub_id).where(master_hubs.c.hub_id == hid)).first() is None:
                c.execute(insert(master_hubs).values(hub_id=hid, first_seen=now, last_seen=now, **values))
            else:
                c.execute(update(master_hubs).where(master_hubs.c.hub_id == hid).values(last_seen=now, **values))
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            for day, d in list(usage.items())[:60]:
                if not _DAY_RE.match(str(day)) or not isinstance(d, dict):
                    continue
                tools = {str(k)[:60]: _int(v) for k, v in list((d.get("tools") or {}).items())[:120]} if isinstance(d.get("tools"), dict) else {}
                payload = json.dumps(tools, separators=(",", ":"))[:8000]
                existing = c.execute(select(master_usage.c.hub_id).where((master_usage.c.hub_id == hid) & (master_usage.c.day == day))).first()
                row = dict(commands=_int(d.get("commands")), failures=_int(d.get("failures")), tools_json=payload)
                if existing is None:
                    c.execute(insert(master_usage).values(hub_id=hid, day=day, **row))
                else:
                    c.execute(update(master_usage).where((master_usage.c.hub_id == hid) & (master_usage.c.day == day)).values(**row))
        return True

    # ------------------------------------------------------------------ views for the master's admin

    def summary(self) -> Dict[str, Any]:
        week = time.time() - 7 * 86400
        day_cut = time.strftime("%Y-%m-%d", time.gmtime(week))
        with self.engine.connect() as c:
            users = c.execute(select(func.count()).select_from(master_users)).scalar() or 0
            users_week = c.execute(select(func.count()).select_from(master_users).where(master_users.c.last_seen > week)).scalar() or 0
            hubs = c.execute(select(func.count()).select_from(master_hubs)).scalar() or 0
            hubs_week = c.execute(select(func.count()).select_from(master_hubs).where(master_hubs.c.last_seen > week)).scalar() or 0
            devices = c.execute(select(func.coalesce(func.sum(master_hubs.c.pcs), 0), func.coalesce(func.sum(master_hubs.c.phones), 0),
                                       func.coalesce(func.sum(master_hubs.c.servers), 0))).first()
            rows = c.execute(select(master_usage.c.commands, master_usage.c.failures, master_usage.c.tools_json)
                             .where(master_usage.c.day >= day_cut)).all()
            providers = c.execute(select(master_hubs.c.ai_provider, func.count()).group_by(master_hubs.c.ai_provider)).all()
        tools: Dict[str, int] = {}
        for _commands, _failures, tj in rows:
            try:
                for name, n in json.loads(tj or "{}").items():
                    tools[name] = tools.get(name, 0) + int(n)
            except ValueError:
                pass
        return {"users": users, "users_active_7d": users_week, "hubs": hubs, "hubs_active_7d": hubs_week,
                "devices": {"pc": int(devices[0]), "mobile": int(devices[1]), "server": int(devices[2])},
                "commands_7d": int(sum(r[0] for r in rows)), "failures_7d": int(sum(r[1] for r in rows)),
                "top_tools_7d": dict(sorted(tools.items(), key=lambda kv: -kv[1])[:15]),
                "ai_providers": {p or "unknown": n for p, n in providers}}

    def users(self, limit: int = 500) -> List[Dict[str, Any]]:
        with self.engine.connect() as c:
            rows = c.execute(select(master_users).order_by(master_users.c.last_seen.desc()).limit(limit))
            return [{k: v for k, v in r._mapping.items() if k != "google_sub"} for r in rows]

    def hubs(self, limit: int = 500) -> List[Dict[str, Any]]:
        with self.engine.connect() as c:
            rows = c.execute(select(master_hubs).order_by(master_hubs.c.last_seen.desc()).limit(limit))
            return [dict(r._mapping) for r in rows]
