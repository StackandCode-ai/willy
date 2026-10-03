"""
Reads the visible text of an app window through Windows UI Automation (what screen readers
use): chat messages in WhatsApp / Claude, an open document, a dialog. Read-only.
"""

import time
from typing import Any, Dict, List, Optional

MAX_ELEMENTS = 4000
TIME_BUDGET_SEC = 6.0
EDIT, DOCUMENT = 50004, 50030                 # UIA control type ids
TEXT_PATTERN, VALUE_PATTERN = 10014, 10002    # UIA pattern ids


def _pattern_text(element, text_iface, value_iface) -> str:
    for pattern_id, iface in ((TEXT_PATTERN, text_iface), (VALUE_PATTERN, value_iface)):
        try:
            pattern = element.GetCurrentPattern(pattern_id)
            if not pattern:
                continue
            pattern = pattern.QueryInterface(iface)
            text = pattern.DocumentRange.GetText(20000) if iface is text_iface else pattern.CurrentValue
            if text and text.strip():
                return text.strip()
        except Exception:
            continue
    return ""


def _find_window(target: Optional[str]) -> Optional[int]:
    import win32gui

    if not target:
        return win32gui.GetForegroundWindow() or None
    wanted = target.strip().lower()
    hits: List[tuple] = []

    def visit(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd) or ""
            if wanted in title.lower():
                hits.append((len(title), hwnd))
        return True

    win32gui.EnumWindows(visit, None)
    return min(hits)[1] if hits else None


def read_window_text(target: Optional[str] = None, max_chars: int = 4000) -> Dict[str, Any]:
    """Text of the window whose title contains `target` (default: the window in front)."""
    try:
        import win32gui
        import comtypes.client

        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen.UIAutomationClient import (
            CUIAutomation, IUIAutomation, IUIAutomationTextPattern, IUIAutomationValuePattern, TreeScope_Descendants,
        )
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"UI Automation is unavailable: {e}"}

    hwnd = _find_window(target)
    if not hwnd:
        return {"success": False, "error": f"No open window matches '{target}'." if target else "No window is in front."}
    title = win32gui.GetWindowText(hwnd)
    try:
        uia = comtypes.client.CreateObject(CUIAutomation, interface=IUIAutomation)
        root = uia.ElementFromHandle(hwnd)
        found = root.FindAll(TreeScope_Descendants, uia.CreateTrueCondition())
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"Couldn't read '{title}': {e}"}

    lines: List[str] = []
    seen = set()
    total = 0
    deadline = time.monotonic() + TIME_BUDGET_SEC
    for i in range(min(found.Length, MAX_ELEMENTS)):
        if time.monotonic() > deadline or total >= max_chars:
            break
        try:
            element = found.GetElement(i)
            items = [(element.CurrentName or "").strip()]
            if element.CurrentControlType in (EDIT, DOCUMENT):  # typed text lives in a pattern, not the name
                items.append(_pattern_text(element, IUIAutomationTextPattern, IUIAutomationValuePattern))
        except Exception:
            continue
        for name in items:
            if not name or len(name) < 2 or name in seen:
                continue
            seen.add(name)
            lines.append(name)
            total += len(name) + 1
    text = "\n".join(lines)[:max_chars]
    return {"success": True, "window": title, "text": text, "truncated": total >= max_chars,
            "message": f"Read {len(lines)} text items from '{title}'."}
