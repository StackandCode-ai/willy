"""
First-run setup for WillyPC.exe: connect this PC to your Willy hub.

Shown when the app starts without a saved key (a fresh download). The person enters their hub
address, the browser opens the hub's sign-in page, they approve, and this PC receives its own
key. Everything is saved to %LOCALAPPDATA%\\WillyPC\\.env, which survives app updates.
(Advanced: a hub access token can be pasted instead.)
"""

import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

USER_ENV = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "WillyPC" / ".env"


def normalize_hub(text: str) -> Optional[str]:
    """'willy.example.com', 'http://192.168.1.5:8000/' ... -> 'https://willy.example.com' (no trailing slash)."""
    text = (text or "").strip().rstrip("/")
    if not text or " " in text:
        return None
    if not re.match(r"^https?://", text, re.I):
        local = re.match(r"^(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)", text)
        text = ("http://" if local else "https://") + text
    parsed = urlparse(text)
    if not parsed.hostname or "." not in parsed.hostname and parsed.hostname != "localhost":
        return None
    for suffix in ("/ws/devices", "/dashboard"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text


def ws_url(hub: str) -> str:
    return hub.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/ws/devices"


def save_settings(hub: str, token: str, path: Path = USER_ENV) -> Path:
    """Writes the hub address and key (other lines in the file are kept)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    lines = [l for l in lines if l.split("=", 1)[0].strip() not in ("WILLY_SERVER_URL", "WILLY_REMOTE_TOKEN")]
    lines += [f"WILLY_SERVER_URL={ws_url(hub)}", f"WILLY_REMOTE_TOKEN={token}"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run_setup(device_id: str, device_name: str, default_hub: str = "") -> bool:
    """Shows the window; True once this PC is connected to a hub (settings saved)."""
    import tkinter as tk
    from tkinter import ttk

    from pc_client import pairing

    result = {"ok": False}
    stop = threading.Event()
    root = tk.Tk()
    root.title("Connect Willy to your hub")
    root.geometry("470x400")
    root.resizable(False, False)
    pad = {"padx": 22}

    ttk.Label(root, text="Connect this PC to your Willy", font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(18, 4), **pad)
    ttk.Label(root, wraplength=410, justify="left",
              text="Enter your hub's address and the code from your dashboard (Set up > Add your phone and PC). "
                   "No code? Sign in in your browser instead.").pack(anchor="w", **pad)
    ttk.Label(root, text="Hub address").pack(anchor="w", pady=(14, 2), **pad)
    hub_var = tk.StringVar(value=default_hub)
    hub_entry = ttk.Entry(root, textvariable=hub_var, width=52)
    hub_entry.pack(anchor="w", **pad)
    hub_entry.focus_set()

    ttk.Label(root, text="Code from the dashboard").pack(anchor="w", pady=(10, 2), **pad)
    code_var = tk.StringVar()
    ttk.Entry(root, textvariable=code_var, width=18, font=("Consolas", 13)).pack(anchor="w", **pad)

    token_var = tk.StringVar()
    token_row = ttk.Frame(root)
    ttk.Label(token_row, text="Hub access token").pack(anchor="w", pady=(10, 2))
    ttk.Entry(token_row, textvariable=token_var, width=52, show="•").pack(anchor="w")

    status = tk.StringVar(value="")
    ttk.Label(root, textvariable=status, wraplength=410, justify="left", foreground="#555").pack(anchor="w", pady=(10, 0), **pad)

    buttons = ttk.Frame(root)
    buttons.pack(anchor="w", pady=(10, 0), **pad)
    go = ttk.Button(buttons, text="Connect")
    go.pack(side="left")
    use_token = {"on": False}

    def toggle_token() -> None:
        use_token["on"] = not use_token["on"]
        if use_token["on"]:
            token_row.pack(anchor="w", before=buttons, **pad)
            alt.config(text="Use a code or browser instead")
        else:
            token_row.pack_forget()
            alt.config(text="Use a hub access token instead")

    alt = ttk.Button(buttons, text="Use a hub access token instead", command=toggle_token)
    alt.pack(side="left", padx=10)

    def finish(res: Dict[str, Any], hub: str) -> None:
        if res.get("success"):
            save_settings(hub, res["device_key"])
            result["ok"] = True
            root.destroy()
        else:
            status.set(res.get("error") or "That didn't work.")
            go.config(state="normal")

    def start() -> None:
        hub = normalize_hub(hub_var.get())
        if not hub:
            status.set("That doesn't look like an address. Example: willy.example.com")
            return
        if use_token["on"]:
            token = token_var.get().strip()
            if len(token) < 20:
                status.set("Paste the full hub access token.")
                return
            finish({"success": True, "device_key": token}, hub)
            return
        code = code_var.get().strip()
        if code:
            go.config(state="disabled")
            status.set("Connecting…")

            def redeem() -> None:
                res = pairing.redeem_code(hub, code, device_id, "pc", device_name)
                if not stop.is_set():
                    root.after(0, lambda: finish(res, hub))

            threading.Thread(target=redeem, name="willy-setup-code", daemon=True).start()
            return
        go.config(state="disabled")
        status.set("No code entered: opening your browser. Sign in and press Approve.")

        def work() -> None:
            res = pairing.pair_device(
                hub, device_id, "pc", device_name,
                on_code=lambda code, _url: root.after(0, lambda: status.set(
                    f"Check the code in your browser is  {code}  and press Approve.")),
                should_stop=stop.is_set)
            if not stop.is_set():
                root.after(0, lambda: finish(res, hub))

        threading.Thread(target=work, name="willy-setup-pair", daemon=True).start()

    go.config(command=start)
    root.bind("<Return>", lambda _e: start())

    def on_close() -> None:
        stop.set()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()
    return result["ok"]
