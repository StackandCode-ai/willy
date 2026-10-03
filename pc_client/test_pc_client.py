"""
Standalone Unit Tests for Willy PC Client.
Verifies PC client tools and execution run independently without server dependencies.
"""

import sys
from pathlib import Path

# Add pc_client directory to sys.path
PC_DIR = Path(__file__).resolve().parent
if str(PC_DIR) not in sys.path:
    sys.path.insert(0, str(PC_DIR))

import pytest
from pc_client.telemetry import collect_pc_telemetry
from pc_client.executor import LocalExecutor
from pc_client.tools.registry import dispatch_pc_command


def test_pc_telemetry():
    data = collect_pc_telemetry()
    assert isinstance(data, dict)
    assert "cpu_pct" in data
    assert "ram_pct" in data
    assert "active_window" in data


def test_pc_executor_telemetry():
    executor = LocalExecutor()
    res = executor.execute_action("telemetry", {})
    assert res["success"] is True
    assert "telemetry" in res


def test_pc_dispatch_status():
    res = dispatch_pc_command("What is the battery status?")
    assert isinstance(res, dict)
    assert res["success"] is True
    assert "battery" in res.get("reply", "").lower()


def test_pc_dispatch_mute():
    res = dispatch_pc_command("Mute volume")
    assert isinstance(res, dict)
    assert res["success"] is True
    assert "muted" in res.get("reply", "").lower()
