"""
Unit tests to verify that common user desktop queries are routed 100% locally
with zero internet latency.
"""
import sys
import os
from unittest.mock import patch

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from willy.agent.local_router import route_local_intent

test_cases = [
    # (Input utterance from user audio notes & logs, expected tool_name)
    ("Scroll the screen.", "scroll_screen"),
    ("scroll down", "scroll_screen"),
    ("scroll up a bit", "scroll_screen"),
    ("scroll lock screen", "unlock_or_wake"),
    ("swipe up", "unlock_or_wake"),
    ("Eivilly type 4524.", "type_text"),
    ("type 4524", "type_text"),
    ("password secret123", "type_text"),
    ("pin 1234", "type_text"),
    ("Type now.", "type_text"),
    ("Willy wake up.", "unlock_or_wake"),
    ("Unlock the PC.", "unlock_or_wake"),
    ("unlock with 4524", "unlock_or_wake"),
    ("Hey Willy, lock the PC.", "lock_workstation"),
    ("calculator", "launch_windows_app"),
    ("open my Genshin Impact", "launch_windows_app"),
    ("Now open Genshin Impact.", "launch_windows_app"),
    ("Hey Willy, Swiss 2 light mode.", "set_windows_theme"),
    ("switch to dark mode", "set_windows_theme"),
    ("May you know which window I am using?", "get_active_window_info"),
    ("which window am i using", "get_active_window_info"),
    ("heavily locked laptop", "get_ram_diagnostics"),
    ("check ram", "get_ram_diagnostics"),
    ("boost computer", "boost_system_memory"),
    ("boost the window", "boost_system_memory"),
    ("clean ram", "boost_system_memory"),
    ("switch tab", "hotkey"),
    ("previous tab", "hotkey"),
    ("close tab", "hotkey"),
    ("new tab", "hotkey"),
    ("close window", "hotkey"),
    ("minimize window", "hotkey"),
    ("maximize window", "hotkey"),
    ("moving right", "press_key"),
    ("moving left", "press_key"),
    ("reboot wifi", "reboot_wifi"),
    ("restart wifi", "reboot_wifi"),
    ("disconnect wifi", "disconnect_wifi"),
    ("check wifi", "get_wifi_status"),
    ("bluetooth settings", "open_settings"),
    ("open camera", "launch_windows_app"),
    ("take screenshot", "hotkey"),
    ("open chrome profile Work", "open_chrome_profile"),
    ("switch to chrome Personal", "open_chrome_profile"),
    ("list chrome profiles", "get_chrome_profiles"),
    ("press space", "press_key"),
    ("hit enter", "press_key"),
    ("copy", "hotkey"),
    ("paste", "hotkey"),
    ("mute", "volume_control"),
    ("volume up", "volume_control"),
    ("How are you?", "instant_status"),
    ("Good morning.", "instant_greet"),
    ("what time is it", "get_local_time"),
]


def test_local_intent_router():
    """Verify that all historical queries correctly map to their designated local tools."""
    # Mock OS side-effects so unit tests NEVER lock user's screen or launch live apps/processes
    patches = [
        patch("willy.agent.local_router.lock_workstation", return_value={"success": True, "action": "lock"}),
        patch("willy.agent.local_router.unlock_or_wake", return_value={"success": True, "action": "wake"}),
        patch("willy.agent.local_router.launch_windows_app", return_value={"success": True, "target": "App"}),
        patch("willy.agent.local_router.shutdown_system", return_value={"success": True, "action": "shutdown"}),
        patch("willy.agent.local_router.restart_system", return_value={"success": True, "action": "restart"}),
        patch("willy.agent.local_router._boost_system_memory", return_value={"success": True, "reply": "Boosted!"}),
        patch("willy.agent.local_router._reboot_wifi", return_value={"success": True, "reply": "Rebooted WiFi."}),
        patch("willy.agent.local_router._get_wifi_status", return_value={"connected": True, "reply": "WiFi connected."}),
        patch("willy.agent.local_router._open_chrome_profile", return_value={"success": True, "reply": "Opening Chrome."}),
        patch("willy.agent.local_router._get_chrome_profiles", return_value={"profiles": [], "reply": "Profiles."}),
        patch("subprocess.Popen"),
        patch("subprocess.run"),
        # Never type, press keys or change settings on the real desktop (this test once typed
        # "4524" / "secret123" / "1234" + Enter into the user's active window).
        patch("willy.agent.local_router.pyautogui"),
        patch("willy.agent.local_router.volume_control", return_value={"success": True}),
        patch("willy.agent.local_router.scroll_screen", return_value={"success": True}),
        patch("willy.agent.local_router.set_windows_theme", return_value={"success": True}),
        patch("willy.agent.local_router.open_settings_page", return_value={"success": True}),
        patch("willy.agent.local_router.open_url", return_value={"success": True}),
    ]

    for p in patches:
        p.start()

    try:
        passed = 0
        failed = 0
        failures = []
        for query, expected_tool in test_cases:
            res = route_local_intent(query)
            tool = res.get("tool_name") if res else None
            if tool == expected_tool:
                passed += 1
            else:
                failures.append(f"'{query}' -> Got: {tool}, Expected: {expected_tool}")
                failed += 1

        assert failed == 0, f"Failures in local intent router:\n" + "\n".join(failures)
    finally:
        patch.stopall()


if __name__ == "__main__":
    test_local_intent_router()
    print("All local router intent tests passed successfully!")
