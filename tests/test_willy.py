"""
Automated unit verification for Willy tools, registry schemas, and wake-word parsing.
"""

import sys
import os
import json

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from willy.tools.system_tools import (
    execute_powershell,
    launch_application,
    open_url,
    get_system_status,
    file_operations,
    interact_ui,
)
from willy.tools.registry import TOOL_DEFINITIONS, execute_tool
from willy.audio.wake_word import WakeWordDetector


def test_powershell():
    print("[*] Testing execute_powershell...")
    res = execute_powershell("Write-Output 'Willy Online'")
    assert res["success"] is True, f"PowerShell execution failed: {res}"
    assert "Willy Online" in res["stdout"], f"Unexpected stdout: {res['stdout']}"
    print("    -> PASS: PowerShell executed successfully.")


def test_system_status():
    print("[*] Testing get_system_status...")
    res = get_system_status()
    assert res["success"] is True, f"System status failed: {res}"
    print(f"    -> PASS: System status retrieved: {list(res['status'].keys())}")


def test_file_operations():
    print("[*] Testing file_operations...")
    test_file = "test_willy_temp.txt"
    # Write
    write_res = file_operations("write", test_file, content="Hello Willy!")
    assert write_res["success"] is True, f"File write failed: {write_res}"
    # Read
    read_res = file_operations("read", test_file)
    assert read_res["success"] is True and read_res["content"] == "Hello Willy!", f"File read mismatch: {read_res}"
    # Cleanup
    if os.path.exists(test_file):
        os.remove(test_file)
    print("    -> PASS: File read/write operations verified.")


def test_tool_registry():
    print("[*] Testing TOOL_DEFINITIONS and dispatcher...")
    assert len(TOOL_DEFINITIONS) >= 5, "Missing required tool definitions."
    for tool in TOOL_DEFINITIONS:
        assert "type" in tool and tool["type"] == "function"
        fn = tool["function"]
        assert "name" in fn and "description" in fn and "parameters" in fn
    
    # Test dispatcher
    dispatch_res = execute_tool("execute_powershell", {"command": "echo 'Testing Registry'"})
    assert dispatch_res["success"] is True
    print(f"    -> PASS: All {len(TOOL_DEFINITIONS)} tools validated in schema.")


def test_wake_word_detector():
    print("[*] Testing WakeWordDetector...")
    detector = WakeWordDetector(timeout_seconds=5.0)

    # Test wake word match
    triggered, cmd = detector.process_transcript("Hey Willy open notepad please")
    assert triggered is True, "Wake word 'Hey Willy' was not detected"
    assert "open notepad" in cmd, f"Command extraction unexpected: '{cmd}'"

    # Test active conversation session follow-up (no wake word needed)
    triggered2, cmd2 = detector.process_transcript("now search for cat pictures")
    assert triggered2 is True, "Follow-up in active session should be triggered without wake word"
    assert "search for cat pictures" in cmd2

    # Test dormant non-wake phrase
    detector.end_session()
    triggered3, _ = detector.process_transcript("good morning to the weather")
    assert triggered3 is False, "Random phrase without wake word should NOT trigger Willy"

    # Test "willy pause" standby mode
    triggered4, cmd4 = detector.process_transcript("willy pause")
    assert triggered4 is True and cmd4 == "__PAUSE__", "Willy pause must trigger __PAUSE__"
    assert detector.is_paused is True, "Detector must be paused"

    # Test casual speech while paused is ignored
    triggered5, _ = detector.process_transcript("can you open spotify")
    assert triggered5 is False, "Casual speech while paused must be completely ignored"

    # Test wake from pause with wake word
    triggered6, cmd6 = detector.process_transcript("Hey Willy")
    assert triggered6 is True and cmd6 == "__RESUME__", "Hey Willy must wake up from pause"
    assert detector.is_paused is False, "Detector must no longer be paused"

    print("    -> PASS: Wake-word detection, pause/standby mode, & conversational sessions verified.")


def test_autostart_manager():
    print("[*] Testing Windows Auto-Start Manager...")
    from willy.tools.autostart_tools import enable_autostart, get_autostart_status, manage_autostart
    # Test status
    status = get_autostart_status()
    assert "enabled" in status, "Status must report enabled boolean"

    # Test enable
    enable_res = enable_autostart(mode="ui")
    assert enable_res["success"] is True, f"Failed to enable autostart: {enable_res}"
    assert enable_res["enabled"] is True
    assert "main.py" in enable_res["command"]

    # Test unified dispatcher
    tool_status = manage_autostart(action="status")
    assert tool_status["enabled"] is True
    print("    -> PASS: Windows Auto-Start registry key management verified.")


def test_tts_stop_and_interruption():
    print("[*] Testing TTS Stop & Speech Barge-In Support...")
    from willy.audio.tts import TextToSpeech
    tts = TextToSpeech()
    assert hasattr(tts, "stop")
    assert hasattr(tts, "interrupt_event")
    tts.stop()
    assert tts.interrupt_event.is_set() is True
    assert tts.is_speaking is False
    print("    -> PASS: TTS stop and interruption mechanics verified.")


if __name__ == "__main__":
    print("=" * 60)
    print("RUNNING WILLY VERIFICATION SUITE")
    print("=" * 60)
    test_powershell()
    test_system_status()
    test_file_operations()
    test_tool_registry()
    test_wake_word_detector()
    test_autostart_manager()
    test_tts_stop_and_interruption()
    print("=" * 60)
    print("ALL TESTS PASSED SUCCESSFULLY! Willy is ready to roll.")
    print("=" * 60)

