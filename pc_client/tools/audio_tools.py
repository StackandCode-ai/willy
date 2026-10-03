"""
Windows master-volume control via the Core Audio API (pycaw).

Unlike media-key presses, this can set an exact level, read the current level, and
mute/unmute deterministically (a key press only toggles, so "unmute" could mute).
Falls back to media keys when pycaw/COM is unavailable.
"""

from typing import Any, Dict, Optional

VOLUME_STEP = 0.10


def _endpoint():
    """Returns the default speakers' IAudioEndpointVolume, or None."""
    try:
        import comtypes
        try:
            comtypes.CoInitialize()  # required once per worker thread; repeated calls are harmless
        except OSError:
            pass
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        speakers = AudioUtilities.GetSpeakers()
        endpoint = getattr(speakers, "EndpointVolume", None)
        if endpoint is not None:
            return endpoint
        # Older pycaw returns a raw IMMDevice.
        from ctypes import POINTER, cast
        from comtypes import CLSCTX_ALL
        interface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return cast(interface, POINTER(IAudioEndpointVolume))
    except Exception:
        return None


def get_volume() -> Dict[str, Any]:
    ep = _endpoint()
    if ep is None:
        return {"success": False, "error": "Audio endpoint unavailable."}
    try:
        return {
            "success": True,
            "level": round(ep.GetMasterVolumeLevelScalar() * 100),
            "muted": bool(ep.GetMute()),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def _press(key: str, times: int = 1) -> bool:
    try:
        import pyautogui
        for _ in range(times):
            pyautogui.press(key)
        return True
    except Exception:
        return False


def set_volume(action: str, level: Optional[int] = None) -> Dict[str, Any]:
    """action: set | up | down | mute | unmute | toggle_mute"""
    action = (action or "").lower().strip()
    ep = _endpoint()

    if ep is not None:
        try:
            current = ep.GetMasterVolumeLevelScalar()
            if action == "set":
                if level is None:
                    return {"success": False, "error": "A level from 0 to 100 is required."}
                target = max(0, min(100, int(level))) / 100.0
                ep.SetMasterVolumeLevelScalar(target, None)
                if target > 0 and ep.GetMute():
                    ep.SetMute(0, None)
            elif action == "up":
                ep.SetMasterVolumeLevelScalar(min(1.0, current + VOLUME_STEP), None)
                ep.SetMute(0, None)
            elif action == "down":
                ep.SetMasterVolumeLevelScalar(max(0.0, current - VOLUME_STEP), None)
            elif action == "mute":
                ep.SetMute(1, None)
            elif action == "unmute":
                ep.SetMute(0, None)
            elif action in ("toggle_mute", "toggle"):
                ep.SetMute(0 if ep.GetMute() else 1, None)
            else:
                return {"success": False, "error": f"Unknown volume action: {action}"}
            state = get_volume()
            return {
                "success": True,
                "action": action,
                "level": state.get("level"),
                "muted": state.get("muted"),
                "message": _describe(action, state),
            }
        except Exception as e:
            fallback_error = str(e)
    else:
        fallback_error = "Core Audio unavailable"

    # Media-key fallback (no exact levels possible).
    keys = {"up": ("volumeup", 5), "down": ("volumedown", 5),
            "mute": ("volumemute", 1), "unmute": ("volumemute", 1), "toggle_mute": ("volumemute", 1)}
    if action in keys and _press(*keys[action]):
        return {"success": True, "action": action, "method": "media_keys"}
    return {"success": False, "action": action, "error": fallback_error}


def _describe(action: str, state: Dict[str, Any]) -> str:
    level = state.get("level")
    if state.get("muted"):
        return "Audio muted."
    if action in ("unmute", "toggle_mute", "toggle"):
        return f"Audio unmuted at {level}%."
    return f"Volume is now {level}%."
