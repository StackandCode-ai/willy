"""
Camera & Photo Capture Tools for Willy.
Supports triggering the Windows Camera app shutter or snapping directly via webcam.
"""

import os
import time
import subprocess
from datetime import datetime
from typing import Dict, Any, Optional

try:
    import pyautogui
except ImportError:
    pyautogui = None

try:
    import cv2
except ImportError:
    cv2 = None

from willy.tools.process_tools import check_app_running
from willy.tools.window_tools import focus_window


def open_camera_app() -> Dict[str, Any]:
    """Launches the Windows Camera application."""
    try:
        subprocess.Popen("start microsoft.windows.camera:", shell=True)
        time.sleep(0.5)
        focus_window("camera")
        return {
            "success": True,
            "message": "Opened Windows Camera.",
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to open Camera: {str(e)}"}


def take_photo(save_to_disk: bool = False) -> Dict[str, Any]:
    """
    Takes a photo. If the Windows Camera app is running, brings it to the front
    and triggers the shutter. Otherwise, snaps directly from the webcam or opens Camera.
    """
    # 1. Check if Windows Camera app is running or open
    cam_status = check_app_running("camera")
    is_cam_open = cam_status.get("is_running", False)

    if is_cam_open:
        # Focus Windows Camera window
        focus_window("camera")
        time.sleep(0.3)
        if pyautogui:
            # Spacebar or Enter triggers photo capture in Windows Camera app
            pyautogui.press("space")
            return {
                "success": True,
                "method": "windows_camera_shutter",
                "message": "Photo captured in Windows Camera.",
            }

    # 2. If save_to_disk is requested and cv2 is available, snap directly
    if save_to_disk and cv2:
        try:
            cap = cv2.VideoCapture(0)
            if cap.isOpened():
                # Allow camera sensor auto-exposure to settle
                for _ in range(5):
                    cap.read()
                ret, frame = cap.read()
                cap.release()

                if ret and frame is not None:
                    pictures_dir = os.path.expanduser(r"~\Pictures\Willy")
                    os.makedirs(pictures_dir, exist_ok=True)
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    filepath = os.path.join(pictures_dir, f"photo_{timestamp}.jpg")
                    cv2.imwrite(filepath, frame)
                    return {
                        "success": True,
                        "method": "webcam_direct",
                        "file_path": filepath,
                        "message": f"Photo snapped and saved to '{filepath}'.",
                    }
        except Exception:
            pass

    # 3. If Camera wasn't open, open it and prompt
    open_camera_app()
    time.sleep(1.0)
    focus_window("camera")
    if pyautogui:
        time.sleep(0.4)
        pyautogui.press("space")
        return {
            "success": True,
            "method": "launched_camera_and_shutter",
            "message": "Opened Camera and captured photo.",
        }

    return {
        "success": True,
        "message": "Opened Windows Camera for your photo.",
    }
