"""
Unit and Integration tests for the 3-tier Willy architecture:
- Server Hub (Device Discovery, Telemetry, Command Routing)
- PC Client (System Telemetry, Execution)
"""

import pytest
import asyncio
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock
from server.device_manager import DeviceManager, DeviceInfo
from pc_client.telemetry import collect_pc_telemetry
from pc_client.executor import LocalExecutor
from server.app import app, DEFAULT_TOKEN

client = TestClient(app)


def test_server_health():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "devices" not in data  # public: no device list without sign-in


def test_device_manager_registration():
    async def _test():
        mgr = DeviceManager()
        mock_ws = AsyncMock()

        dev = await mgr.register_device(
            websocket=mock_ws,
            device_id="pc-test-1",
            device_type="pc",
            name="Hari-PC",
            platform="Windows 11",
        )
        assert dev.device_id == "pc-test-1"
        assert dev.status == "online"

        devices = mgr.get_all_devices()
        assert len(devices) == 1
        assert devices[0]["device_id"] == "pc-test-1"

        # Update telemetry
        dev.update_telemetry({"battery_pct": 80, "cpu_pct": 25})
        assert dev.telemetry["battery_pct"] == 80
        assert dev.telemetry["cpu_pct"] == 25

        # Unregister
        await mgr.unregister_device("pc-test-1")
        assert dev.status == "offline"
        assert dev.to_dict()["online"] is False

    asyncio.run(_test())


def test_api_devices_unauthorized():
    response = client.get("/api/v1/devices")
    assert response.status_code == 401


def test_api_devices_authorized():
    response = client.get(
        "/api/v1/devices",
        headers={"Authorization": f"Bearer {DEFAULT_TOKEN}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "devices" in data


def test_pc_telemetry_gathering():
    telemetry = collect_pc_telemetry()
    assert isinstance(telemetry, dict)
    assert "cpu_pct" in telemetry
    assert "ram_pct" in telemetry
    assert "active_window" in telemetry


def test_pc_executor_telemetry_action():
    executor = LocalExecutor()
    result = executor.execute_action("telemetry", {})
    assert isinstance(result, dict)
    assert result["success"] is True
    assert "telemetry" in result

