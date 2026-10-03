"""
Willy PC's notification-area (system tray) icon.

Windows puts the icon in the taskbar's hidden-icons flyout (^) by default; drag it onto the
taskbar to keep it in view. Left-click opens the window; right-click shows the menu. The dot
on the icon follows the hub connection: green online, amber connecting, red offline.

pystray runs the icon's own Win32 message loop on a daemon thread. Menu actions are handed
to `dispatch` (the app's thread-safe queue), so they never touch Tk from that thread.
"""

import threading
from typing import Any, Callable, Dict, Optional

from pc_client import autostart
from pc_client.brand import logo_image, status_icon

try:
    import pystray
except Exception:  # pragma: no cover - optional dependency; the app then runs without a tray icon
    pystray = None

TIP_LIMIT = 127  # NOTIFYICONDATA.szTip holds 128 characters
STATE_TEXT = {"online": "Online", "connecting": "Connecting…", "offline": "Offline", "stopped": "Stopped"}


def tooltip(state: str, latency_ms: Optional[int] = None, hub: str = "") -> str:
    text = STATE_TEXT.get(state, state.title())
    if state == "online" and latency_ms is not None:
        text += f" · {latency_ms} ms"
    return (f"Willy PC — {text}" + (f"\n{hub}" if hub else ""))[:TIP_LIMIT]


def menu_header(state: str, hub: str = "") -> str:
    where = hub or "the hub"
    return {
        "online": f"Online · {where}",
        "connecting": f"Connecting to {where}…",
        "stopped": "Stopped · another Willy took over",
    }.get(state, "Offline · retrying automatically")


class TrayIcon:
    """`actions` maps open / dashboard / call / ring_phone / reconnect / quit to app callbacks
    (run on the Tk thread via `dispatch`); `autostart_changed` receives the result of the menu
    toggle."""

    def __init__(self, dispatch: Callable[..., None], actions: Dict[str, Callable[..., Any]], hub: str = ""):
        self.dispatch = dispatch
        self.actions = actions
        self.hub = hub
        self.state = "connecting"
        self.header = menu_header(self.state, hub)
        self._tip = tooltip(self.state, hub=hub)
        self._images: Dict[str, Any] = {}
        self._autostart = False
        self._icon: Optional[Any] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def available(self) -> bool:
        return pystray is not None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        if pystray is None or self._icon is not None:
            return self.running
        logo = logo_image(64)
        self._images = {state: status_icon(state if state != "stopped" else "offline", 64, logo)
                        for state in STATE_TEXT}
        self._autostart = self._read_autostart()
        self._icon = pystray.Icon("WillyPC", self._images[self.state], self._tip, self.build_menu())
        self._thread = threading.Thread(target=self._icon.run, name="willy-tray", daemon=True)
        self._thread.start()
        return True

    def build_menu(self) -> Any:
        """The right-click menu (building it shows nothing; the icon does once started)."""
        item, separator = pystray.MenuItem, pystray.Menu.SEPARATOR
        return pystray.Menu(
            item(lambda _item: self.header, None, enabled=False),
            separator,
            item("Open Willy", self._action("open"), default=True),
            item("Web dashboard", self._action("dashboard")),
            item("Voice call", self._action("call")),
            item("Ring my phone", self._action("ring_phone")),
            separator,
            item('Talk to Willy now', self._action("talk")),
            item('"Hey Willy" (wake word)', self._action("toggle_voice"), checked=lambda _item: self._flag("voice_enabled")),
            item("Mute microphone", self._action("toggle_mic"), checked=lambda _item: self._flag("mic_muted")),
            item('Train "Hey Willy" on my voice', self._action("train_voice")),
            separator,
            item("Sign in / pair this PC with my Google account", self._action("pair")),
            item("Reconnect to hub", self._action("reconnect")),
            item("Start with Windows", self._toggle_autostart, checked=lambda _item: self._autostart),
            separator,
            item("Quit Willy", self._action("quit")),
        )

    def _flag(self, name: str) -> bool:
        """Checkbox state read from the app (a plain attribute read, safe from the tray thread)."""
        getter = self.actions.get(name)
        try:
            return bool(getter()) if getter else False
        except Exception:
            return False

    def refresh(self) -> None:
        """Redraws the menu so checkmarks follow the app's state."""
        if self._icon is not None:
            try:
                self._icon.update_menu()
            except Exception:
                pass

    def _action(self, name: str) -> Callable[[Any, Any], None]:
        def handler(_icon: Any, _item: Any) -> None:
            fn = self.actions.get(name)
            if fn:
                self.dispatch(fn)
        return handler

    @staticmethod
    def _read_autostart() -> bool:
        try:
            return autostart.is_enabled()
        except Exception:
            return False

    def _toggle_autostart(self, icon: Any, _item: Any) -> None:
        try:
            result = autostart.disable() if self._autostart else autostart.enable()
        except Exception as e:  # noqa: BLE001 - reported to the app
            result = {"success": False, "error": str(e)}
        self._autostart = self._read_autostart()
        icon.update_menu()
        callback = self.actions.get("autostart_changed")
        if callback:
            self.dispatch(callback, result)

    def set_status(self, state: str, latency_ms: Optional[int] = None) -> None:
        """Updates the dot, tooltip and menu header; cheap when nothing changed."""
        icon = self._icon
        if icon is None:
            return
        state = state if state in STATE_TEXT else "offline"
        try:
            if state != self.state:
                self.state = state
                icon.icon = self._images[state]
            tip = tooltip(state, latency_ms, self.hub)
            if tip != self._tip:
                self._tip = tip
                icon.title = tip
            header = menu_header(state, self.hub)
            if header != self.header:
                self.header = header
                icon.update_menu()
        except Exception:
            pass

    def refresh_autostart(self) -> None:
        """Re-reads the startup entry (it can also change in Task Manager or from the CLI)."""
        value = self._read_autostart()
        if value != self._autostart and self._icon is not None:
            self._autostart = value
            try:
                self._icon.update_menu()
            except Exception:
                pass

    def notify(self, title: str, message: str) -> None:
        """A Windows notification from the tray icon (title ≤ 63, message ≤ 255 characters)."""
        if self._icon is not None:
            try:
                self._icon.notify(message[:255], title[:63])
            except Exception:
                pass

    def stop(self, timeout: float = 2.0) -> None:
        """Removes the icon (waits briefly so no ghost icon is left in the tray)."""
        icon, thread = self._icon, self._thread
        if icon is None:
            return
        try:
            icon.stop()
        except Exception:
            pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
