"""
Tests for the realtime PC client: fast process snapshots, telemetry collection,
executor safety guards, and a live PCClientNode <-> hub round-trip over real WebSockets.
All actions used here are read-only (nothing is locked, muted, typed or killed).
"""

import os
import sys
import time
import socket
import asyncio
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from pc_client.tools.proc_snapshot import raw_snapshot, ProcessSampler
from pc_client.telemetry import TelemetryCollector, collect_static_specs
from pc_client.executor import LocalExecutor

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only APIs")


@windows_only
def test_nt_process_snapshot_matches_current_process():
    snap = raw_snapshot()
    assert snap and os.getpid() in snap
    name, created, cpu_time, working_set, threads = snap[os.getpid()]
    assert name.lower().startswith("python")
    assert working_set > 1024 * 1024 and threads >= 1


@windows_only
def test_process_sampler_is_fast_after_first_sample():
    sampler = ProcessSampler(os.cpu_count() or 1)
    assert sampler.sample()
    t0 = time.perf_counter()
    rows = sampler.sample()
    assert (time.perf_counter() - t0) < 1.0
    assert any(r["pid"] == os.getpid() for r in rows)
    assert all(0.0 <= r["cpu"] <= 100.0 for r in rows)


def test_collector_returns_full_heartbeat_quickly():
    collector = TelemetryCollector(background=False)
    collector.collect()
    t0 = time.perf_counter()
    data = collector.collect()
    assert time.perf_counter() - t0 < 2.0
    for key in ("cpu_pct", "ram_pct", "ram_total_gb", "disk_free_gb", "active_window",
                "net_up_kbps", "net_down_kbps", "tz_offset_min", "top_processes"):
        assert key in data, key


def test_static_specs_are_cached():
    first = collect_static_specs()
    t0 = time.perf_counter()
    second = collect_static_specs()
    assert time.perf_counter() - t0 < 0.05
    assert first == second and second.get("cpu_threads")


def test_executor_lists_processes_by_memory():
    res = LocalExecutor().execute_action("list_processes", {"sort_by": "memory", "limit": 5})
    assert res["success"] is True and len(res["processes"]) == 5
    mems = [p["mem_mb"] for p in res["processes"]]
    assert mems == sorted(mems, reverse=True)


def test_executor_refuses_to_kill_protected_or_own_process():
    ex = LocalExecutor(notifier=lambda *a: None)
    assert ex.execute_action("kill_process", {"pid": 4})["success"] is False
    assert ex.execute_action("kill_process", {"pid": os.getpid()})["success"] is False
    assert "No process" in ex.execute_action("kill_process", {"pid": 999999999})["error"]


def test_executor_validates_inputs_without_side_effects():
    shown = []
    ex = LocalExecutor(notifier=lambda *a: shown.append(a))
    assert ex.execute_action("open_folder", {"path": r"Z:\definitely\missing"})["success"] is False
    assert ex.execute_action("notify", {"message": "  "})["success"] is False
    assert ex.execute_action("press_keys", {"keys": ""})["success"] is False
    assert ex.execute_action("no_such_action", {})["success"] is False
    assert shown == []  # nothing popped up on screen
    assert "duration_sec" in ex.execute_action("telemetry", {})


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_hub():
    """A real uvicorn hub on a free port: yields (port, token)."""
    import uvicorn
    import server.app as hub

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(hub.app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started
    yield port, hub.DEFAULT_TOKEN
    server.should_exit = True


def _start_node(port: int, token: str):
    from pc_client.client import PCClientNode

    node = PCClientNode(
        server_url=f"ws://127.0.0.1:{port}/ws/devices",
        token=token,
        on_log=lambda text: None,
        notifier=lambda *a: None,
    )
    thread = threading.Thread(target=lambda: asyncio.run(node.run()), daemon=True)
    thread.start()
    return node, thread


def _wait_for(predicate, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def test_pc_node_round_trip_with_live_hub(live_hub):
    import httpx

    port, token = live_hub
    node, node_thread = _start_node(port, token)
    try:
        headers = {"Authorization": f"Bearer {token}"}
        base = f"http://127.0.0.1:{port}"
        deadline = time.time() + 20
        pc = None
        while time.time() < deadline:
            devices = httpx.get(f"{base}/api/v1/devices", headers=headers).json()["devices"]
            pc = next((d for d in devices if d["device_id"] == node.device_id and d["online"]
                       and d["telemetry"].get("cpu_pct") is not None), None)
            if pc and node.latency_ms is not None:
                break
            time.sleep(0.2)
        assert pc is not None, "PC never reported telemetry"
        assert node.latency_ms is not None and node.latency_ms < 1000

        res = httpx.post(f"{base}/api/v1/devices/{node.device_id}/action", headers=headers,
                         json={"action": "list_processes", "payload": {"limit": 3}}, timeout=20).json()
        assert res["success"] is True and len(res["processes"]) == 3

        fut = node.submit(node.send_command("what time is it"))
        reply = fut.result(timeout=20)
        assert reply["success"] is True and reply["fast_path"] is True and reply["reply"].startswith("It's")
        assert node.devices.get(node.device_id), "node should mirror hub device state"
    finally:
        node.stop()
        node_thread.join(timeout=10)


def test_reconnect_opens_a_fresh_session_right_away(live_hub):
    port, token = live_hub
    node, node_thread = _start_node(port, token)
    try:
        assert _wait_for(lambda: node.connected), "never connected"
        first_session = node.connected_since
        started = time.time()
        assert node.reconnect() is True
        assert _wait_for(lambda: node.connected and node.connected_since != first_session, timeout=10)
        assert time.time() - started < 5, "a manual reconnect must skip the backoff wait"
        time.sleep(2.5)  # past the normal backoff: no extra reconnects afterwards
        assert node.connected and node.running
    finally:
        node.stop()
        node_thread.join(timeout=10)
    assert node.reconnect() is False, "a stopped node has to be started again instead"


def test_slow_or_failing_dns_dials_the_last_known_hub_address(tmp_path):
    from pc_client import client as client_mod
    from pc_client.client import PCClientNode

    node = PCClientNode(server_url="wss://hub.example.test/willy/ws/devices", token="t",
                        on_log=lambda text: None, notifier=lambda *a: None)
    node.address_cache = tmp_path / "hub_address.json"

    async def scenario():
        loop = asyncio.get_running_loop()
        node._loop = loop

        async def fast(host, port, **kw):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", port))]

        async def slow(host, port, **kw):
            await asyncio.sleep(30)

        async def failing(host, port, **kw):
            raise socket.gaierror(11001, "getaddrinfo failed")

        loop.getaddrinfo = fast
        assert await node._hub_address() == "10.1.2.3"
        loop.getaddrinfo = slow
        started = time.monotonic()
        assert await node._hub_address() == "10.1.2.3"
        assert time.monotonic() - started < client_mod.DNS_FAST_TIMEOUT_SEC + 1
        loop.getaddrinfo = failing
        assert await node._hub_address() == "10.1.2.3"

    asyncio.run(scenario())
    after_reboot = PCClientNode(server_url=node.server_url, token="t", on_log=lambda text: None)
    after_reboot.address_cache = node.address_cache
    assert after_reboot._cached_hub_ip("hub.example.test") == "10.1.2.3", "saved across restarts"
    assert after_reboot._cached_hub_ip("other.example.test") is None, "never used for another host"

    for url in ("ws://127.0.0.1:9/ws/devices", "ws://localhost:9/ws/devices"):
        local = PCClientNode(server_url=url, token="t", on_log=lambda text: None)

        async def literal():
            local._loop = asyncio.get_running_loop()
            return await local._hub_address()

        assert asyncio.run(literal()) is None, "addresses and localhost are dialled as they are"


def test_the_address_a_connection_reached_is_saved_for_next_time(tmp_path, monkeypatch):
    import urllib.request
    from types import SimpleNamespace
    from pc_client.client import PCClientNode

    node = PCClientNode(server_url="wss://hub.example.test/willy/ws/devices", token="t", on_log=lambda text: None)
    node.address_cache = tmp_path / "hub_address.json"
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {"https": "http://proxy.local:3128"})
    node._remember_peer(SimpleNamespace(remote_address=("192.0.2.10", 3128)))
    assert not node.address_cache.exists(), "behind a proxy the peer is the proxy, not the hub"
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})
    node._remember_peer(SimpleNamespace(remote_address=("203.0.113.7", 443)))
    fresh = PCClientNode(server_url=node.server_url, token="t", on_log=lambda text: None)
    fresh.address_cache = node.address_cache
    assert fresh._cached_hub_ip("hub.example.test") == "203.0.113.7"


def test_dns_failure_on_the_very_first_attempt_keeps_the_node_retrying(tmp_path):
    """Regression: in a fresh process websockets hasn't loaded its exceptions module yet, and a
    lookup failing before connect() used to crash the engine thread (exe stuck "Connecting")."""
    import subprocess

    script = f"""
import asyncio, socket, sys, threading, time
sys.path.insert(0, {str(PROJECT_ROOT)!r})
async def no_dns(self, *args, **kwargs):
    raise socket.gaierror(11001, "getaddrinfo failed")
asyncio.base_events.BaseEventLoop.getaddrinfo = no_dns
from pc_client.client import PCClientNode
logs = []
node = PCClientNode(server_url="wss://willy-hub.invalid/ws/devices", token="t", on_log=logs.append,
                    notifier=lambda *a: None)
node.address_cache = {str(tmp_path / 'none.json')!r}
thread = threading.Thread(target=lambda: asyncio.run(node.run()), daemon=True)
thread.start()
time.sleep(4)
print("ALIVE" if thread.is_alive() else "DEAD", sum("Could not resolve" in l for l in logs))
node.stop()
"""
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60,
                         env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert "Traceback" not in out.stderr, out.stderr
    state, failures = out.stdout.split()[-2:]
    assert state == "ALIVE" and int(failures) >= 2, out.stdout


def test_node_connects_through_saved_address_when_the_name_does_not_resolve(live_hub, tmp_path):
    from pc_client.client import PCClientNode

    port, token = live_hub
    cache = tmp_path / "hub_address.json"
    cache.write_text('{"host": "willy-hub.invalid", "ip": "127.0.0.1"}')
    node = PCClientNode(server_url=f"ws://willy-hub.invalid:{port}/ws/devices", token=token,
                        on_log=lambda text: None, notifier=lambda *a: None)
    node.address_cache = cache
    thread = threading.Thread(target=lambda: asyncio.run(node.run()), daemon=True)
    thread.start()
    try:
        assert _wait_for(lambda: node.connected, timeout=15), "never connected via the saved address"
    finally:
        node.stop()
        thread.join(timeout=10)


def test_second_client_for_same_pc_takes_over_without_ping_pong(live_hub):
    port, token = live_hub
    first, first_thread = _start_node(port, token)
    second = second_thread = None
    try:
        assert _wait_for(lambda: first.connected), "first client never connected"
        statuses = []
        first.on_status = lambda state, detail: statuses.append(detail)
        second, second_thread = _start_node(port, token)
        assert _wait_for(lambda: not first.running and not first_thread.is_alive()), "old client kept fighting"
        assert any("Replaced" in s for s in statuses)
        time.sleep(3)  # well past the reconnect backoff: the survivor must stay put
        assert second.connected and second.running
    finally:
        first.stop()
        if second:
            second.stop()
            second_thread.join(timeout=10)
        first_thread.join(timeout=10)
