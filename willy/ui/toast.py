"""
Desktop Toast & HUD Overlay for Willy.
Displays visual notifications and responses on the Windows desktop
whenever a command is executed via voice, Siri, or mobile.
"""

import sys
import threading
from typing import Optional

_active_toast_thread = None


def show_desktop_toast(
    title: str = "Willy Voice Assistant",
    message: str = "",
    query: Optional[str] = None,
    duration_sec: float = 4.5,
):
    """
    Displays a non-blocking, modern floating dark-mode HUD notification
    in the bottom-right corner of the primary Windows screen.
    """
    if not message:
        return

    def _render():
        try:
            import tkinter as tk

            root = tk.Tk()
            root.overrideredirect(True)
            root.attributes("-topmost", True)
            try:
                root.attributes("-alpha", 0.94)
            except Exception:
                pass

            root.configure(bg="#0B0F19")

            sw = root.winfo_screenwidth()
            sh = root.winfo_screenheight()
            w, h = 420, 120
            x = max(10, sw - w - 24)
            y = max(10, sh - h - 50)
            root.geometry(f"{w}x{h}+{x}+{y}")

            # Outer border frame
            border = tk.Frame(root, bg="#0B0F19", highlightthickness=1, highlightbackground="#38BDF8")
            border.pack(fill="both", expand=True, padx=2, pady=2)

            # Header row
            header = tk.Frame(border, bg="#0B0F19")
            header.pack(fill="x", padx=12, pady=(8, 2))

            tk.Label(
                header,
                text="⚡ WILLY",
                font=("Segoe UI", 10, "bold"),
                bg="#0B0F19",
                fg="#38BDF8",
            ).pack(side=tk.LEFT)

            if query:
                q_display = f'"{query[:28]}..."' if len(query) > 30 else f'"{query}"'
                tk.Label(
                    header,
                    text=q_display,
                    font=("Segoe UI", 9, "italic"),
                    bg="#0B0F19",
                    fg="#94A3B8",
                ).pack(side=tk.LEFT, padx=(8, 0))

            # Close button
            tk.Label(
                header,
                text="✕",
                font=("Segoe UI", 9),
                bg="#0B0F19",
                fg="#64748B",
                cursor="hand2",
            ).pack(side=tk.RIGHT)
            header.bind("<Button-1>", lambda e: root.destroy())

            # Response Message
            msg_text = message if len(message) <= 150 else message[:147] + "..."
            msg_label = tk.Label(
                border,
                text=msg_text,
                font=("Segoe UI", 10),
                bg="#0B0F19",
                fg="#F8FAFC",
                wraplength=390,
                justify="left",
                anchor="nw",
            )
            msg_label.pack(fill="both", expand=True, padx=12, pady=(2, 8))

            # Click anywhere to dismiss
            root.bind("<Button-1>", lambda e: root.destroy())
            border.bind("<Button-1>", lambda e: root.destroy())
            msg_label.bind("<Button-1>", lambda e: root.destroy())

            # Auto-destroy after duration
            root.after(int(duration_sec * 1000), root.destroy)
            root.mainloop()

        except Exception as e:
            # Fallback to standard console or pass if GUI unavailable
            pass

    t = threading.Thread(target=_render, daemon=True)
    t.start()
    return t
