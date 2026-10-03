"""
Standalone Unit Tests for Willy Central Server Hub.
Verifies server components run independently without external repo dependencies.
"""

import sys
from pathlib import Path

# Add server directory to sys.path
SERVER_DIR = Path(__file__).resolve().parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import pytest
import asyncio
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient

from server.config import settings
from server.device_manager import DeviceManager, DeviceInfo
from server.orchestrator import ServerOrchestrator
from server.voice_service import ServerVoiceService
from server.app import app

client = TestClient(app)


def test_server_health():
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "devices" not in data  # public: no device list without sign-in


def test_server_config():
    assert hasattr(settings, "HOST")
    assert hasattr(settings, "PORT")
    assert hasattr(settings, "WILLY_REMOTE_TOKEN")


def test_server_orchestrator():
    orchestrator = ServerOrchestrator()
    res = asyncio.run(orchestrator.process_query("Hello Willy"))
    assert isinstance(res, dict)
    assert isinstance(res.get("reply"), str)
    assert len(res.get("reply")) > 0


def test_server_device_manager():
    async def _run():
        mgr = DeviceManager()
        ws_mock = AsyncMock()
        dev = await mgr.register_device(
            websocket=ws_mock,
            device_id="pc_unit_test",
            device_type="pc",
            name="Test-Laptop",
        )
        assert dev.device_id == "pc_unit_test"
        assert dev.status == "online"

        # Update telemetry
        dev.update_telemetry({"battery_pct": 92, "cpu_pct": 10})
        assert dev.telemetry["battery_pct"] == 92

        devices = mgr.get_all_devices()
        assert len(devices) == 1
        assert devices[0]["telemetry"]["battery_pct"] == 92

        await mgr.unregister_device("pc_unit_test")
        assert dev.status == "offline"

    asyncio.run(_run())


def test_server_voice_service_english():
    service = ServerVoiceService()
    assert service.is_tamil_text("Hello") is False


def test_morning_briefing_service():
    from server.morning_briefing import morning_service

    # Update telemetry with unread mails, missed calls, whatsapp chats
    telemetry = morning_service.update_telemetry({
        "unread_emails_count": 4,
        "email_senders": ["Google", "Stripe"],
        "missed_calls_count": 2,
        "missed_calls": [{"name": "Dad", "time": "8:00 AM"}],
        "whatsapp_unread_count": 7,
        "whatsapp_senders": [{"name": "Alex"}, {"name": "Work Group"}],
        "total_notifications_count": 15,
    })
    assert telemetry["unread_emails_count"] == 4
    assert telemetry["missed_calls_count"] == 2
    assert telemetry["whatsapp_unread_count"] == 7

    # Generate briefing script
    briefing = asyncio.run(morning_service.generate_briefing(include_audio=False, pc_online=True))
    assert briefing["success"] is True
    assert "morning briefing" in briefing["script"].lower()
    assert "4 unread email" in briefing["script"]
    assert "2 missed call" in briefing["script"]
    assert "7 unread whatsapp" in briefing["script"].lower()

    # Reminders & alarms
    rem = morning_service.add_reminder("Test task from ChatGPT", "10:30 AM")
    assert rem["text"] == "Test task from ChatGPT"
    assert len(morning_service.list_reminders()) > 0

    alm = morning_service.add_alarm("07:00 AM", "Morning Call")
    assert alm["time"] == "07:00 AM"


def test_morning_briefing_api_endpoints():
    token = settings.WILLY_REMOTE_TOKEN
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Test GET /api/v1/morning/briefing
    res = client.get("/api/v1/morning/briefing?include_audio=false", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert "script" in data

    # 2. Test POST /api/v1/mobile/telemetry
    tel_res = client.post("/api/v1/mobile/telemetry", headers=headers, json={
        "unread_emails_count": 5,
        "email_senders": ["HR"],
        "missed_calls_count": 1,
        "missed_calls": [{"name": "Manager"}],
        "whatsapp_unread_count": 3,
        "whatsapp_senders": [{"name": "Team"}],
        "total_notifications_count": 9,
    })
    assert tel_res.status_code == 200
    assert tel_res.json()["telemetry"]["unread_emails_count"] == 5

    # 3. Test POST /api/v1/reminders
    rem_res = client.post("/api/v1/reminders", headers=headers, json={
        "text": "Review project deliverables",
        "time": "14:00",
    })
    assert rem_res.status_code == 200
    assert rem_res.json()["reminder"]["text"] == "Review project deliverables"


def test_device_tracking_and_actions():
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {settings.WILLY_REMOTE_TOKEN}"}

    # Test /api/v1/devices returns list
    dev_res = client.get("/api/v1/devices", headers=headers)
    assert dev_res.status_code == 200
    assert "devices" in dev_res.json()

    # Test ring endpoint on offline/non-existent device returns DEVICE_OFFLINE gracefully
    ring_res = client.post("/api/v1/devices/fake_device_id/ring", headers=headers, json={"message": "Test"})
    assert ring_res.status_code == 200
    data = ring_res.json()
    assert data["success"] is False
    assert data["error"] == "DEVICE_OFFLINE"

    # Test clipboard endpoint on offline device
    cb_res = client.post("/api/v1/devices/fake_device_id/clipboard", headers=headers, json={"text": "hello"})
    assert cb_res.status_code == 200
    assert cb_res.json()["success"] is False

    # Test quickdrop endpoint on offline device
    qd_res = client.post("/api/v1/devices/fake_device_id/quickdrop", headers=headers, json={"url": "https://example.com"})
    assert qd_res.status_code == 200
    assert qd_res.json()["success"] is False

