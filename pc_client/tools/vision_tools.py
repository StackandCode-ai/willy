"""
Multimodal Vision & Multi-Monitor Workspace Awareness Tools for Willy.
Enables Willy to:
- Detect all connected monitors and their layout
- Track the exact cursor position and which screen the cursor is on
- Identify the active foreground application and which screen the user is working on
- Capture the active monitor, a specific monitor, or all monitors combined
- Visually answer questions and click elements across multiple screens
"""

import io
import os
import re
import json
import base64
import ctypes
from typing import Dict, Any, Optional, Tuple, Union

from PIL import Image, ImageGrab
import win32api
import win32gui
import win32process

try:
    import psutil
except ImportError:
    psutil = None

try:
    import pyautogui
    pyautogui.FAILSAFE = False
except ImportError:
    pyautogui = None

try:
    from pc_client.config import settings
except ImportError:
    settings = None


def _attach_thread_to_desktop():
    """Attaches current thread to the interactive desktop if needed."""
    try:
        user32 = ctypes.windll.user32
        hDesk = user32.OpenInputDesktop(0, False, 0x01FF)
        if hDesk:
            user32.SetThreadDesktop(hDesk)
            user32.CloseDesktop(hDesk)
    except Exception:
        pass


def get_workspace_focus_info() -> Dict[str, Any]:
    """
    Detects all connected monitors, real-time mouse cursor position,
    which screen contains the cursor, the active foreground window, and
    which monitor the user is currently working on.
    """
    _attach_thread_to_desktop()
    user32 = ctypes.windll.user32

    # 1. Cursor Position
    class POINT(ctypes.Structure):
        _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]

    pt = POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    cursor_x, cursor_y = pt.x, pt.y

    # 2. Enumerate All Monitors
    monitors_raw = win32api.EnumDisplayMonitors()
    monitors_list = []
    cursor_monitor_idx = 1
    cursor_monitor_name = "Monitor 1"

    for idx, (hmon, _, rect) in enumerate(monitors_raw, 1):
        info = win32api.GetMonitorInfo(hmon)
        is_primary = bool(info.get("Flags", 0) & 1)
        left, top, right, bottom = rect
        w = right - left
        h = bottom - top
        has_cur = (left <= cursor_x < right and top <= cursor_y < bottom)

        mon_data = {
            "index": idx,
            "name": f"Monitor {idx}" + (" (Primary)" if is_primary else ""),
            "device": info.get("Device", ""),
            "bounds": {"left": left, "top": top, "right": right, "bottom": bottom, "width": w, "height": h},
            "is_primary": is_primary,
            "has_cursor": has_cur,
        }
        if has_cur:
            cursor_monitor_idx = idx
            cursor_monitor_name = mon_data["name"]
        monitors_list.append(mon_data)

    # 3. Active Foreground Window
    hwnd = win32gui.GetForegroundWindow()
    active_title = win32gui.GetWindowText(hwnd).strip() if hwnd else ""
    active_rect = win32gui.GetWindowRect(hwnd) if hwnd else None
    active_proc = ""
    if hwnd:
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if psutil:
                active_proc = psutil.Process(pid).name()
        except Exception:
            pass

    # Determine which monitor contains the active window
    active_monitor_idx = cursor_monitor_idx
    active_monitor_name = cursor_monitor_name
    if active_rect:
        center_x = (active_rect[0] + active_rect[2]) // 2
        center_y = (active_rect[1] + active_rect[3]) // 2
        for m in monitors_list:
            b = m["bounds"]
            if b["left"] <= center_x < b["right"] and b["top"] <= center_y < b["bottom"]:
                active_monitor_idx = m["index"]
                active_monitor_name = m["name"]
                break

    total_screens = len(monitors_list)
    screen_phrase = "1 display" if total_screens == 1 else f"{total_screens} displays"
    app_phrase = f"in {active_proc}" if active_proc else "on your desktop"
    if active_title:
        app_phrase += f" ({active_title[:35]})"

    spoken_reply = (
        f"You have {screen_phrase}. "
        f"Your mouse cursor is on {cursor_monitor_name} at coordinates ({cursor_x}, {cursor_y}). "
        f"You are actively working on {active_monitor_name} {app_phrase}."
    )

    return {
        "success": True,
        "total_monitors": total_screens,
        "cursor": {
            "x": cursor_x,
            "y": cursor_y,
            "monitor_index": cursor_monitor_idx,
            "monitor_name": cursor_monitor_name,
        },
        "active_window": {
            "title": active_title,
            "process": active_proc,
            "monitor_index": active_monitor_idx,
            "monitor_name": active_monitor_name,
        },
        "monitors": monitors_list,
        "reply": spoken_reply,
    }


def capture_screen_image(
    target_monitor: Union[str, int] = "active",
    max_size: Tuple[int, int] = (1280, 720),
    quality: int = 80,
) -> Tuple[Optional[str], int, int, int, int, str]:
    """
    Captures either:
      - 'active': the monitor containing the cursor or focused window
      - 'all': the combined virtual desktop spanning all monitors
      - 1, 2, 3...: a specific monitor index
    Returns (base64_jpeg_string, width, height, offset_x, offset_y, monitor_label).
    """
    _attach_thread_to_desktop()
    monitors_raw = win32api.EnumDisplayMonitors()
    total_monitors = len(monitors_raw)

    selected_rect = None
    offset_x, offset_y = 0, 0
    label = "Monitor 1"

    # 1. Panoramic Multi-Monitor Grab
    if str(target_monitor).lower() in ("all", "both", "everything", "combined"):
        try:
            img = ImageGrab.grab(all_screens=True)
            label = f"All Screens ({total_monitors} monitors)"
            offset_x, offset_y = 0, 0
        except Exception:
            img = ImageGrab.grab()
    else:
        # 2. Determine target monitor index
        target_idx = 1
        t_str = str(target_monitor).lower().strip()

        # Check if user specified a number or "screen 2"
        digits = re.findall(r"\d+", t_str)
        if digits and 1 <= int(digits[0]) <= total_monitors:
            target_idx = int(digits[0])
        else:
            # "active": pick monitor containing cursor
            user32 = ctypes.windll.user32
            class POINT(ctypes.Structure):
                _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]
            pt = POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            for idx, (hmon, _, rect) in enumerate(monitors_raw, 1):
                if rect[0] <= pt.x < rect[2] and rect[1] <= pt.y < rect[3]:
                    target_idx = idx
                    break

        if 1 <= target_idx <= total_monitors:
            _, _, selected_rect = monitors_raw[target_idx - 1]
            offset_x, offset_y = selected_rect[0], selected_rect[1]
            label = f"Monitor {target_idx}"
            try:
                img = ImageGrab.grab(bbox=selected_rect, all_screens=True)
            except Exception:
                img = ImageGrab.grab()
        else:
            img = ImageGrab.grab()

    orig_w, orig_h = img.size

    # Resize to optimize token bandwidth
    img_resized = img.copy()
    img_resized.thumbnail(max_size, Image.Resampling.LANCZOS)

    # Convert to JPEG bytes
    buffer = io.BytesIO()
    if img_resized.mode in ("RGBA", "P"):
        img_resized = img_resized.convert("RGB")
    img_resized.save(buffer, format="JPEG", quality=quality, optimize=True)
    b64_str = base64.b64encode(buffer.getvalue()).decode("utf-8")

    return b64_str, orig_w, orig_h, offset_x, offset_y, label


def _get_vision_response(prompt: str, b64_img: str, system_prompt: str = "") -> str:
    """
    Queries OpenAI or Groq vision models with the captured screenshot.
    """
    openai_key = settings.OPENAI_API_KEY
    groq_key = settings.GROQ_API_KEY

    # 1. Use OpenAI if valid non-placeholder key is provided
    if openai_key and not openai_key.startswith("your_") and len(openai_key) > 20:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=openai_key)
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})

            messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{b64_img}",
                            "detail": "high",
                        },
                    },
                ],
            })

            resp = client.chat.completions.create(
                model=settings.OPENAI_MODEL or "gpt-4o",
                messages=messages,
                max_tokens=600,
                temperature=0.3,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            print(f"[!] OpenAI Vision failed: {e}. Falling back to Groq.")

    # 2. Use Groq Vision with Qwen 3.8 27B
    if groq_key:
        try:
            from groq import Groq
            client = Groq(api_key=groq_key)
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})

            messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{b64_img}",
                        },
                    },
                ],
            })

            vision_model = settings.GROQ_MODEL if "qwen" in settings.GROQ_MODEL.lower() else "qwen/qwen3.8-27b"
            resp = client.chat.completions.create(
                model=vision_model,
                messages=messages,
                max_tokens=600,
                temperature=0.3,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            return f"Vision inspection failed: {str(e)}"

    return "No vision-capable API key (OpenAI or Groq) is configured."


def describe_screen(question: Optional[str] = None, monitor: Union[str, int] = "active") -> Dict[str, Any]:
    """
    Looks at the active screen (where the cursor/active app is) or a specific monitor
    and provides a spoken conversational summary.
    """
    b64_img, width, height, offset_x, offset_y, label = capture_screen_image(target_monitor=monitor)
    if not b64_img:
        return {
            "success": False,
            "reply": "I was unable to capture your screen right now.",
            "error": "Screen capture returned empty buffer",
        }

    q = question.strip() if question else f"Describe what is currently visible on {label} in 2 to 3 natural sentences."
    system_prompt = (
        "You are Willy, an AI voice assistant running on the user's Windows computer. "
        f"You are currently viewing {label} (resolution {width}x{height}). "
        "Answer the user's question about what is on their screen directly, concisely, and conversationally. "
        "Your response will be spoken aloud via voice synthesis. "
        "Do not use markdown formatting, asterisks, bullet points, or headers."
    )

    summary = _get_vision_response(prompt=q, b64_img=b64_img, system_prompt=system_prompt)

    return {
        "success": True,
        "monitor": label,
        "screen_resolution": f"{width}x{height}",
        "question": q,
        "reply": summary,
    }


def click_screen_element(target_description: str, monitor: Union[str, int] = "active") -> Dict[str, Any]:
    """
    Visually locates an element or button on the active or specified screen
    and moves/clicks the mouse cursor onto it, taking into account multi-monitor offsets.
    """
    if not target_description:
        return {"success": False, "reply": "Please tell me what you want me to click on the screen."}

    b64_img, width, height, offset_x, offset_y, label = capture_screen_image(target_monitor=monitor)
    if not b64_img:
        return {"success": False, "reply": f"I couldn't capture {label} to find that element."}

    prompt = (
        f"The user wants to click on this UI element: '{target_description}'.\n"
        f"The screen image resolution is {width}x{height}.\n"
        "Locate this element in the image.\n"
        "Return ONLY a JSON object with this exact structure:\n"
        "{\n"
        '  "found": true,\n'
        '  "x": <integer x pixel coordinate of the center of the element>,\n'
        '  "y": <integer y pixel coordinate of the center of the element>,\n'
        '  "element_name": "<short name of what you found>"\n'
        "}\n"
        "If the element is not found, return:\n"
        '{\n  "found": false,\n  "reason": "<why not found>"\n}'
    )

    raw_resp = _get_vision_response(
        prompt=prompt,
        b64_img=b64_img,
        system_prompt="You are a precise GUI element detector. Output only raw JSON."
    )

    try:
        clean_json = re.sub(r"^```(?:json)?\s*", "", raw_resp.strip(), flags=re.MULTILINE)
        clean_json = re.sub(r"```$", "", clean_json.strip(), flags=re.MULTILINE).strip()
        data = json.loads(clean_json)

        if data.get("found"):
            local_x = int(data.get("x", 0))
            local_y = int(data.get("y", 0))

            # Clamp within monitor dimensions
            local_x = max(0, min(width - 1, local_x))
            local_y = max(0, min(height - 1, local_y))

            # Map to absolute virtual desktop coordinates
            global_x = offset_x + local_x
            global_y = offset_y + local_y

            _attach_thread_to_desktop()
            if pyautogui:
                pyautogui.click(x=global_x, y=global_y)
            else:
                user32 = ctypes.windll.user32
                user32.SetCursorPos(global_x, global_y)
                user32.mouse_event(0x0002, 0, 0, 0, 0)
                user32.mouse_event(0x0004, 0, 0, 0, 0)

            elem_name = data.get("element_name", target_description)
            return {
                "success": True,
                "action": "click",
                "monitor": label,
                "coordinates": [global_x, global_y],
                "element": elem_name,
                "reply": f"Clicked on {elem_name} on {label}.",
            }
        else:
            return {
                "success": False,
                "reply": f"I couldn't find '{target_description}' on {label}.",
                "reason": data.get("reason", "not found on screen"),
            }

    except Exception as parse_err:
        return {
            "success": False,
            "error": f"Failed to parse vision coordinates: {str(parse_err)}",
            "raw": raw_resp,
            "reply": f"I was unable to locate '{target_description}' on {label}.",
        }


def take_screenshot(filepath: Optional[str] = None) -> Dict[str, Any]:
    """Captures a screenshot of the primary screen and saves it locally."""
    try:
        import time
        if not filepath:
            desktop = os.path.expanduser("~/Desktop")
            if not os.path.exists(desktop):
                desktop = os.getcwd()
            filepath = os.path.join(desktop, f"screenshot_{int(time.time())}.png")

        img = ImageGrab.grab(all_screens=True)
        img.save(filepath, "PNG")
        return {
            "success": True,
            "file_path": filepath,
            "message": f"Screenshot saved to {filepath}",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

