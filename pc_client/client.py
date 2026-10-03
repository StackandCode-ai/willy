"""
Willy PC Client Daemon (Runs on Physical Laptop).
Connects outbound to central server, registers as a PC device,
streams live telemetry, and executes tasks received from the server or mobile app.

PCClientNode is shared by the headless CLI and the desktop app:
- actions run in a worker pool (up to MAX_PARALLEL_ACTIONS at once), so a slow action
  never delays heartbeats or other commands;
- heartbeats carry telemetry every HEARTBEAT_INTERVAL_SEC and measure real round-trip
  latency from the server's ack;
- the node mirrors the hub's live state (devices, activity) from pushed events and can
  send commands over the already-open socket (no HTTP/TLS handshake per command).
"""

import os
import sys
import time
import json
import uuid
import random
import asyncio
import socket
import ipaddress
import platform as py_plat
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import websockets
# Imported by name: websockets loads its submodules lazily, and an except clause naming
# websockets.exceptions.X before any connect() ran raises AttributeError instead of matching.
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus, WebSocketException

from pc_client.config import SERVER_URL, TOKEN, DEVICE_NAME, DEVICE_ID, HEARTBEAT_INTERVAL_SEC, SPEAK_REMINDERS
from pc_client.telemetry import get_collector, collect_static_specs
from pc_client.executor import LocalExecutor, Notifier, speak_text

MAX_PARALLEL_ACTIONS = 4
COMMAND_TIMEOUT_SEC = 60
MAX_ACTIVITY = 100
# How long a hub DNS lookup may take before the last known address is dialled instead.
DNS_FAST_TIMEOUT_SEC = 2.5
HUB_ADDRESS_CACHE = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "WillyPC" / "hub_address.json"


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False

StatusCallback = Callable[[str, str], None]          # (state, detail) state: connecting|online|offline
LogCallback = Callable[[str], None]
EventCallback = Callable[[Dict[str, Any]], None]
TelemetryCallback = Callable[[Dict[str, Any]], None]


class PCClientNode:
    def __init__(
        self,
        on_status: Optional[StatusCallback] = None,
        on_log: Optional[LogCallback] = None,
        on_event: Optional[EventCallback] = None,
        on_telemetry: Optional[TelemetryCallback] = None,
        notifier: Optional[Notifier] = None,
        server_url: Optional[str] = None,
        token: Optional[str] = None,
    ):
        self.server_url = server_url or SERVER_URL
        self.token = token or TOKEN
        self.device_name = DEVICE_NAME
        self.device_id = DEVICE_ID
        self.executor = LocalExecutor(notifier=notifier)
        self.collector = get_collector()
        self.running = True

        self.on_status = on_status
        self.on_log = on_log
        self.on_event = on_event
        self.on_telemetry = on_telemetry

        self.connected = False
        self.state = "connecting"
        self.latency_ms: Optional[int] = None
        self.connected_since: Optional[float] = None
        self.last_telemetry: Dict[str, Any] = {}
        self.devices: Dict[str, Dict[str, Any]] = {}
        self.activity: List[Dict[str, Any]] = []
        self.server_info: Dict[str, Any] = {}
        self.actions_handled = 0

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ws = None
        self._send_lock: Optional[asyncio.Lock] = None
        self._slots: Optional[asyncio.Semaphore] = None
        self._pending: Dict[str, asyncio.Future] = {}
        self._specs: Optional[Dict[str, Any]] = None
        self._stop_event: Optional[asyncio.Event] = None  # ends a reconnect wait early (stop / reconnect)
        self._reconnect_now = False
        self.address_cache = HUB_ADDRESS_CACHE
        self._hub_ip: Optional[tuple] = None  # (host, last good IPv4 address)
        self._suspended = False  # the PC is going to sleep: no heartbeats until it wakes
        self._suspended_at = 0.0
        self._power = None       # Windows power/session listener (started by run())

    # ------------------------------------------------------------ properties

    @property
    def api_base_url(self) -> str:
        """HTTP(S) base URL of the hub derived from the WebSocket URL."""
        base = self.server_url.replace("wss://", "https://").replace("ws://", "http://")
        return base.split("/ws/")[0]

    # ------------------------------------------------------------- callbacks

    def _emit_status(self, state: str, detail: str = "") -> None:
        self.state = state
        if self.on_status:
            try:
                self.on_status(state, detail)
            except Exception:
                pass

    def log(self, text: str) -> None:
        if self.on_log:
            try:
                self.on_log(text)
                return
            except Exception:
                pass
        print(text)

    def _emit_event(self, event: Dict[str, Any]) -> None:
        if self.on_event:
            try:
                self.on_event(event)
            except Exception:
                pass

    # ------------------------------------------------------------ lifecycle

    def stop(self, reason: Optional[str] = "app_closed") -> None:
        """Stops the node (including an in-progress reconnect wait); safe from any thread.
        The hub is told why (default: the app was closed) before the socket closes."""
        self.running = False
        loop, ws = self._loop, self._ws
        power, self._power = self._power, None
        if power is not None:
            power.stop()
        if loop and loop.is_running():
            if self._stop_event is not None:
                loop.call_soon_threadsafe(self._stop_event.set)
            if ws is not None:
                async def goodbye() -> None:
                    if reason:
                        try:
                            await asyncio.wait_for(self._send({"type": "going_offline", "reason": reason}), 1.0)
                        except Exception:
                            pass
                    await ws.close()

                asyncio.run_coroutine_threadsafe(goodbye(), loop)

    def announce_offline(self, reason: str, wait: float = 1.5) -> bool:
        """Tells the hub this PC is about to go offline ('sleep', 'shutdown', 'logoff');
        safe from any thread, waits up to `wait` seconds for the message to go out."""
        loop = self._loop
        if loop is None or not loop.is_running() or self._ws is None:
            return False
        fut = asyncio.run_coroutine_threadsafe(self._send({"type": "going_offline", "reason": reason}), loop)
        try:
            return bool(fut.result(timeout=wait))
        except Exception:
            return False

    # Windows power / session events (listener thread) ------------------------------------

    def _on_suspend(self) -> None:
        self._suspended = True
        self._suspended_at = time.time()
        self.log("[*] The PC is going to sleep; telling the hub.")
        self.announce_offline("sleep")

    def _on_resume(self) -> None:
        if self._suspended:
            self._suspended = False
            self.log("[*] The PC woke up; reconnecting to the hub.")
            self.reconnect()

    def _on_session_end(self, kind: str) -> None:
        self.announce_offline(kind, wait=2.0)

    def _start_power_events(self) -> None:
        if self._power is not None or sys.platform != "win32":
            return
        try:
            from pc_client.power_events import PowerEvents
            power = PowerEvents(self._on_suspend, self._on_resume, self._on_session_end)
            if power.start():
                self._power = power
        except Exception as e:  # noqa: BLE001 - optional nicety
            self.log(f"[-] Power events unavailable: {e}")

    def reconnect(self) -> bool:
        """Connects to the hub again right away: drops a live connection, or cuts the retry
        wait short. Safe from any thread. False when the node isn't running (stopped, or
        replaced by another client); run() must then be started again."""
        loop = self._loop
        if not self.running or loop is None or not loop.is_running():
            return False

        def kick() -> None:
            self._reconnect_now = True
            if self._stop_event is not None:
                self._stop_event.set()
            if self._ws is not None:
                asyncio.ensure_future(self._ws.close())

        loop.call_soon_threadsafe(kick)
        return True

    def _ws_url(self) -> str:
        params = {
            "token": self.token,
            "device_id": self.device_id,
            "device_type": "pc",
            "name": self.device_name,
            "hostname": socket.gethostname(),
            "platform": self._platform_name(),
        }
        try:  # lets the hub tell a restart from a wake-up after an outage
            import psutil
            params["boot_time"] = str(int(psutil.boot_time()))
        except Exception:
            pass
        return f"{self.server_url}?{urllib.parse.urlencode(params)}"

    # ------------------------------------------------------- hub address

    async def _hub_address(self) -> Optional[str]:
        """IPv4 address to dial for the hub (None: dial the URL's host as it is).

        Some networks (phone hotspots) take 10+ s to answer, or drop, DNS queries for a name
        they resolved minutes earlier. The lookup gets DNS_FAST_TIMEOUT_SEC; after that the
        hub's last known address is dialled. TLS still checks the certificate for the hub's
        real name, so a stale address can't connect Willy anywhere else. A late answer still
        refreshes the saved address."""
        parsed = urllib.parse.urlparse(self.server_url)
        host = parsed.hostname or ""
        if not host or host == "localhost" or _is_ip(host):
            return None
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        lookup = asyncio.ensure_future(
            self._loop.getaddrinfo(host, port, family=socket.AF_INET, type=socket.SOCK_STREAM))

        def remember(fut: "asyncio.Future") -> None:
            if not fut.cancelled() and fut.exception() is None and fut.result():
                self._remember_hub_ip(host, fut.result()[0][4][0])

        lookup.add_done_callback(remember)
        try:
            infos = await asyncio.wait_for(asyncio.shield(lookup), DNS_FAST_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            cached = self._cached_hub_ip(host)
            if cached:
                self.log(f"[*] DNS for {host} is slow; dialling its last known address {cached}.")
                return cached
            infos = await asyncio.wait_for(lookup, 20)  # nothing saved yet: wait for the answer
        except (socket.gaierror, OSError) as e:
            cached = self._cached_hub_ip(host)
            if cached:
                self.log(f"[*] DNS lookup for {host} failed ({e}); dialling its last known address {cached}.")
                return cached
            raise
        return infos[0][4][0] if infos else None

    def _cached_hub_ip(self, host: str) -> Optional[str]:
        if self._hub_ip and self._hub_ip[0] == host:
            return self._hub_ip[1]
        try:
            data = json.loads(Path(self.address_cache).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        ip = data.get("ip") if isinstance(data, dict) and data.get("host") == host else None
        if isinstance(ip, str) and _is_ip(ip):
            self._hub_ip = (host, ip)
            return ip
        return None

    def _remember_peer(self, ws: Any) -> None:
        """Saves the address a live connection reached, whichever lookup found it (not when a
        proxy is in the way: then the peer is the proxy, not the hub)."""
        host = urllib.parse.urlparse(self.server_url).hostname or ""
        peer = getattr(ws, "remote_address", None)
        if not host or host == "localhost" or _is_ip(host) or not peer or not _is_ip(str(peer[0])):
            return
        try:
            if any(key in urllib.request.getproxies() for key in ("https", "wss", "all")):
                return
        except Exception:
            return
        self._remember_hub_ip(host, str(peer[0]))

    def _remember_hub_ip(self, host: str, ip: str) -> None:
        if self._hub_ip == (host, ip):
            return
        self._hub_ip = (host, ip)
        try:
            path = Path(self.address_cache)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"host": host, "ip": ip, "resolved_at": time.time()}), encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    def _platform_name() -> str:
        try:
            build = sys.getwindowsversion().build
            return f"Windows {'11' if build >= 22000 else py_plat.release()}"
        except Exception:
            return f"Windows {py_plat.release()}"

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._send_lock = asyncio.Lock()
        self._slots = asyncio.Semaphore(MAX_PARALLEL_ACTIONS)
        self._stop_event = asyncio.Event()
        self._reconnect_now = False
        self.collector.start()
        self._start_power_events()
        specs_task = asyncio.create_task(asyncio.to_thread(collect_static_specs))

        delay = 1.0
        # Resolve the hub over IPv4 first: on some networks (e.g. phone hotspots) the default
        # lookup also waits ~11 s for an IPv6 answer that never comes. After a failed lookup the
        # next attempt uses the default lookup once (the hub may be IPv6-only), then IPv4 again.
        prefer_ipv4 = True
        while self.running:
            self._emit_status("connecting", self.server_url)
            self.log(f"[*] Connecting to Willy Central Server: {self.server_url} ...")
            started = time.time()
            replaced = False
            try:
                dial = await self._hub_address() if prefer_ipv4 else None
                if not self.running:  # stopped while the lookup was running
                    break
                async with websockets.connect(
                    self._ws_url(),
                    ping_interval=20,
                    ping_timeout=20,
                    open_timeout=20,
                    max_size=8 * 1024 * 1024,
                    **({"host": dial} if dial else {"family": socket.AF_INET} if prefer_ipv4 else {}),
                ) as ws:
                    self._ws = ws
                    self.connected = True
                    self.connected_since = time.time()
                    self._remember_peer(ws)
                    self._emit_status("online", self.server_url)
                    self.log(f"[+] Connected to Server as '{self.device_name}' (ID: {self.device_id})")

                    if self._specs is None and specs_task.done():
                        self._specs = specs_task.result()
                    heartbeat = asyncio.create_task(self._heartbeat_loop(ws))
                    info = asyncio.create_task(self._send_device_info(specs_task))
                    try:
                        await self._receive_loop(ws)
                    finally:
                        heartbeat.cancel()
                        info.cancel()
            except (InvalidStatus, InvalidHandshake) as e:
                self.log(f"[-] Server rejected the connection ({e}). Check WILLY_REMOTE_TOKEN.")
            except ConnectionClosed as e:
                rcvd = getattr(e, "rcvd", None)
                if rcvd is not None and rcvd.code == 4000:
                    # The hub handed this PC's slot to a newer Willy client (e.g. the desktop app
                    # was opened while a background client ran). Step aside instead of fighting.
                    self.log("[-] Another Willy client for this PC connected; this one is stopping.")
                    self.running = False
                    replaced = True
                else:
                    self.log(f"[-] Server connection lost ({e}).")
            except socket.gaierror as e:
                prefer_ipv4 = not prefer_ipv4  # DNS hiccups are usually temporary: alternate lookups
                self.log(f"[-] Could not resolve the hub address ({e}).")
            except (OSError, asyncio.TimeoutError, WebSocketException) as e:
                self.log(f"[-] Server connection lost ({e}).")
            except Exception as e:
                self.log(f"[-] Unexpected client error: {e}")
            finally:
                was_connected = self.connected
                self.connected = False
                self._ws = None
                self.latency_ms = None
                self._fail_pending("Disconnected from hub.")
                if was_connected:
                    self._emit_status("offline", "Disconnected")

            if replaced:
                self._emit_status("offline", "Replaced by another Willy client on this PC")
            if not self.running:
                break
            if self._reconnect_now:  # reconnect() asked for a fresh connection: skip the wait
                self._reconnect_now = False
                self._stop_event.clear()
                delay = 1.0
                continue
            # Reset backoff after a healthy session; jitter avoids reconnect stampedes.
            delay = 1.0 if time.time() - started > 15 else min(20.0, delay * 2)
            wait = delay + random.uniform(0, 0.5)
            self._emit_status("offline", f"Reconnecting in {wait:.0f}s")
            self.log(f"[-] Reconnecting in {wait:.1f}s...")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=wait)
            except asyncio.TimeoutError:
                pass
            self._stop_event.clear()
            if self._reconnect_now:
                self._reconnect_now = False
                delay = 1.0

    # ------------------------------------------------------------- sending

    async def _send(self, data: Dict[str, Any]) -> bool:
        ws = self._ws
        if ws is None:
            return False
        try:
            async with self._send_lock:
                await ws.send(json.dumps(data, default=str))
            return True
        except Exception:
            return False

    async def _send_device_info(self, specs_task: "asyncio.Task") -> None:
        try:
            specs = self._specs or await specs_task
            self._specs = specs
            specs = dict(specs)
            specs["client_version"] = "3.1"
            specs["heartbeat_sec"] = HEARTBEAT_INTERVAL_SEC
            await self._send({"type": "device_info", "specs": specs})
        except Exception:
            pass

    async def _heartbeat_loop(self, ws) -> None:
        """Sends live telemetry every HEARTBEAT_INTERVAL_SEC (collection runs in a thread)."""
        while self.running:
            if self._suspended:  # announced sleep: a late beat would make the hub think it's awake
                if time.time() - self._suspended_at > 30:
                    # Still running 30 s later: the sleep was cancelled or the wake event missed.
                    self._on_resume()
                await asyncio.sleep(0.5)
                continue
            t0 = time.monotonic()
            try:
                telemetry = await asyncio.to_thread(self.collector.collect)
                telemetry["last_ping_ms"] = self.latency_ms
                self.last_telemetry = telemetry
                if self.on_telemetry:
                    try:
                        self.on_telemetry(telemetry)
                    except Exception:
                        pass
                if not await self._send({
                    "type": "heartbeat",
                    "device_id": self.device_id,
                    "telemetry": telemetry,
                    "ts": time.time(),
                }):
                    break
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.log(f"[-] Heartbeat error: {e}")
            await asyncio.sleep(max(0.2, HEARTBEAT_INTERVAL_SEC - (time.monotonic() - t0)))

    # ----------------------------------------------------------- receiving

    async def _receive_loop(self, ws) -> None:
        async for raw in ws:
            try:
                data = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(data, dict):
                continue

            req_id = data.get("req_id")
            action = data.get("action")
            if req_id and action:
                self.log(f"[PC] Received task: {action} (Req: {req_id})")
                asyncio.create_task(self._process_and_reply(req_id, action, data.get("payload") or {}))
                continue

            msg_type = data.get("type")
            if msg_type == "heartbeat_ack":
                sent = data.get("client_ts")
                if isinstance(sent, (int, float)):
                    self.latency_ms = max(0, round((time.time() - sent) * 1000))
                continue
            if msg_type == "token_update":
                self._save_new_token(str(data.get("token") or ""))
                continue
            if msg_type == "command_result":
                fut = self._pending.pop(data.get("request_id") or "", None)
                if fut and not fut.done():
                    fut.set_result(data.get("result") or {})
                continue
            self._apply_event(data)

    def _save_new_token(self, token: str, reason: str = "The hub issued a new access token") -> None:
        """The hub retired the token this PC used (or this PC just paired): keep the new one
        (also in the .env the app reads at start) and reconnect with it."""
        token = token.strip()
        if len(token) < 20 or token == self.token:
            return
        self.token = token
        os.environ["WILLY_REMOTE_TOKEN"] = token
        from pc_client import config

        config.TOKEN = token
        targets = [Path(sys.executable).resolve().parent / ".env"] if getattr(sys, "frozen", False) else []
        targets.append(Path(config.__file__).resolve().parent / ".env")
        if config.USER_ENV.exists():
            targets.append(config.USER_ENV)  # written by the first-run setup
        for env_path in targets:
            try:
                lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
                lines = [l for l in lines if not l.strip().startswith("WILLY_REMOTE_TOKEN=")] + [f"WILLY_REMOTE_TOKEN={token}"]
                env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            except OSError:
                pass
        self.log(f"[+] {reason}; saved it and reconnecting.")
        self.reconnect()

    def _apply_event(self, event: Dict[str, Any]) -> None:
        # devices / activity are replaced wholesale (never mutated in place), so a GUI thread
        # reading them always sees a consistent snapshot.
        msg_type = event.get("type")
        if msg_type == "snapshot":
            self.devices = {d["device_id"]: d for d in event.get("devices", []) if d.get("device_id")}
            self.activity = list(event.get("activity", []))[:MAX_ACTIVITY]
            self.server_info = event.get("server") or {}
        elif msg_type in ("device_discovered", "device_update", "device_offline"):
            dev = event.get("device")
            device_id = dev.get("device_id") if isinstance(dev, dict) else event.get("device_id")
            if isinstance(dev, dict) and device_id:
                self.devices = {**self.devices, device_id: dev}
            elif msg_type == "device_offline" and device_id in self.devices:
                self.devices = {**self.devices, device_id: {**self.devices[device_id], "online": False, "status": "offline"}}
        elif msg_type == "activity":
            entry = event.get("entry") or {}
            others = [e for e in self.activity if e.get("id") != entry.get("id")]
            self.activity = ([entry] + others)[:MAX_ACTIVITY]
        elif msg_type == "reminder_due":
            title = "⏰ " + (event.get("title") or "Reminder")
            message = event.get("message") or "Reminder"
            self.executor._notify_user(title=title, message=message)
            if SPEAK_REMINDERS:
                speak_text(f"{event.get('title') or 'Reminder'}: {message}")
            self.log(f"[Reminder] {message}")
        elif msg_type == "morning_call_due":
            self.executor._notify_user(title="☀️ Morning briefing", message=event.get("message") or "")
        self._emit_event(event)

    async def _process_and_reply(self, req_id: str, action: str, payload: dict) -> None:
        """Runs the action in a worker thread and replies back to the server."""
        async with self._slots:
            try:
                result = await asyncio.to_thread(self.executor.execute_action, action, payload)
            except Exception as e:
                result = {"success": False, "error": str(e)}
        self.actions_handled += 1
        ok = await self._send({
            "req_id": req_id,
            "result": result,
            "device_id": self.device_id,
            "timestamp": time.time(),
        })
        status = "ok" if isinstance(result, dict) and result.get("success", True) is not False else "failed"
        self.log(f"[PC] {action} {status} in {result.get('duration_sec', '?')}s" + ("" if ok else " (reply not delivered)"))

    # ----------------------------------------------------- outgoing commands

    def _fail_pending(self, reason: str) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_result({"success": False, "error": "DISCONNECTED", "reply": reason})
        self._pending.clear()

    async def send_command(self, query: str, return_audio: bool = False, session_id: str = "pc_app") -> Dict[str, Any]:
        """Runs a natural-language command through the hub over the open socket."""
        return await self._request({
            "type": "send_to_device",
            "target_device_id": self.device_id,
            "payload": {"query": query, "return_audio": return_audio, "session_id": session_id},
        })

    async def send_action(self, device_id: str, action: str, payload: Optional[Dict[str, Any]] = None,
                          timeout: Optional[float] = None) -> Dict[str, Any]:
        """Runs a direct action on another device (e.g. ring the phone) through the hub.
        `timeout`: how long to wait for the answer (slow server jobs such as installing updates)."""
        return await self._request({
            "type": "send_to_device",
            "target_device_id": device_id,
            "action": action,
            "payload": payload or {},
        }, timeout=timeout)

    async def _request(self, message: Dict[str, Any], timeout: Optional[float] = None) -> Dict[str, Any]:
        if not self.connected:
            return {"success": False, "error": "OFFLINE", "reply": "Not connected to the Willy hub."}
        request_id = uuid.uuid4().hex[:12]
        fut = asyncio.get_running_loop().create_future()
        self._pending[request_id] = fut
        message["request_id"] = request_id
        if not await self._send(message):
            self._pending.pop(request_id, None)
            return {"success": False, "error": "SEND_FAILED", "reply": "Could not reach the hub."}
        try:
            return await asyncio.wait_for(fut, timeout=timeout or COMMAND_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            return {"success": False, "error": "TIMEOUT", "reply": "The hub took too long to answer."}
        finally:
            self._pending.pop(request_id, None)

    def submit(self, coro) -> Optional["asyncio.Future"]:
        """Schedules a coroutine on the node's loop from another thread (e.g. the GUI)."""
        if not self._loop or not self._loop.is_running():
            coro.close()
            return None
        return asyncio.run_coroutine_threadsafe(coro, self._loop)


def main():
    print("=" * 65)
    print("[+] WILLY PC CLIENT NODE (Zero-Overhead Physical Worker)")
    print(f"   Device Name: {DEVICE_NAME}")
    print(f"   Device ID:   {DEVICE_ID}")
    print(f"   Server URL:  {SERVER_URL}")
    print(f"   Heartbeat:   every {HEARTBEAT_INTERVAL_SEC}s")
    print("=" * 65)

    node = PCClientNode()
    try:
        asyncio.run(node.run())
    except KeyboardInterrupt:
        print("\nExiting Willy PC Client.")


run_pc_client = main

if __name__ == "__main__":
    main()
