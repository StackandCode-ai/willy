"""
Willy PC Agent Bridge (Runs on Windows PC).
Maintains a persistent, outbound WebSocket connection to the EC2 Cloud Relay.
Zero port-forwarding, works behind NAT, home Wi-Fi, or phone hotspot.
"""

import os
import sys
import time
import json
import socket
import asyncio
import threading
from typing import Optional

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from willy.config import settings
from willy.agent.orchestrator import AgentOrchestrator
from willy.tools.power_tools import (
    lock_workstation,
    unlock_or_wake,
    shutdown_system,
    restart_system,
    sleep_system,
    cancel_shutdown,
)
from willy.tools.system_tools import get_system_status
from willy.tools.network_tools import check_network_status

DEFAULT_TOKEN = os.getenv("WILLY_REMOTE_TOKEN", "")
DEFAULT_RELAY_URL = os.getenv("WILLY_RELAY_URL", "wss://truewilly.com/willy/ws/pc")


class WindowsAgentBridge:
    def __init__(self, relay_url: str = DEFAULT_RELAY_URL, token: str = DEFAULT_TOKEN):
        self.relay_url = relay_url
        self.token = token
        self.orchestrator = AgentOrchestrator()
        self.running = True

    async def execute_request(self, action: str, payload: dict) -> dict:
        t0 = time.time()
        try:
            if action == "command":
                query = payload.get("query", "")
                speak_on_pc = payload.get("speak_on_pc", True)
                show_toast = payload.get("show_toast", True)
                last_tool = {"name": None, "result": None}

                def on_tool_start(name, args):
                    last_tool["name"] = name

                def on_tool_done(name, result):
                    last_tool["result"] = result

                reply = self.orchestrator.process_query(
                    query,
                    on_tool_start=on_tool_start,
                    on_tool_done=on_tool_done,
                )

                # 1. Speak on PC speakers
                if speak_on_pc:
                    try:
                        from willy.audio.tts import TextToSpeech
                        tts = TextToSpeech()
                        tts.speak(reply, block=False)
                    except Exception as e:
                        print(f"[-] TTS speak error: {e}")

                # 2. Show on-screen desktop HUD toast
                if show_toast:
                    try:
                        from willy.ui.toast import show_desktop_toast
                        show_desktop_toast(title="Willy Assistant", message=reply, query=query)
                    except Exception as e:
                        print(f"[-] Toast display error: {e}")

                return {
                    "success": True,
                    "query": query,
                    "reply": reply,
                    "tool_used": last_tool["name"],
                    "tool_result": last_tool["result"],
                    "duration_sec": round(time.time() - t0, 3),
                }

            elif action == "power":
                sub_action = payload.get("action", "").lower().strip()
                if sub_action == "lock":
                    return lock_workstation()
                elif sub_action == "unlock":
                    return unlock_or_wake(pin=payload.get("pin"))
                elif sub_action == "shutdown":
                    return shutdown_system(delay_sec=payload.get("delay_sec", 30))
                elif sub_action == "restart":
                    return restart_system(delay_sec=payload.get("delay_sec", 30))
                elif sub_action == "sleep":
                    return sleep_system()
                elif sub_action in ("cancel", "abort"):
                    return cancel_shutdown()
                else:
                    return {"success": False, "error": f"Unknown power action: {sub_action}"}

            elif action == "status":
                sys_status = get_system_status()
                net_status = check_network_status()
                return {
                    "success": True,
                    "system": sys_status.get("status", {}),
                    "network": net_status,
                    "timestamp": time.time(),
                }

            else:
                return {"success": False, "error": f"Unknown action: {action}"}

        except Exception as e:
            return {"success": False, "error": str(e), "reply": f"Execution error: {e}"}

    async def run_client(self):
        import websockets

        url = f"{self.relay_url}?token={self.token}"
        print(f"[+] Willy Bridge connecting to Cloud Relay: {self.relay_url}")

        retry_count = 0
        while self.running:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    print(f"[✓] Connected to Cloud Relay! Windows PC is LIVE for Siri & Google Assistant.")
                    retry_count = 0

                    # Initial handshake
                    await ws.send(json.dumps({
                        "type": "handshake",
                        "hostname": socket.gethostname(),
                        "platform": sys.platform,
                    }))

                    while self.running:
                        msg_text = await ws.recv()
                        data = json.loads(msg_text)

                        req_id = data.get("req_id")
                        action = data.get("action")
                        payload = data.get("payload", {})

                        if req_id and action:
                            # Execute task asynchronously
                            asyncio.create_task(self._process_and_reply(ws, req_id, action, payload))

            except (websockets.exceptions.ConnectionClosed, Exception) as e:
                retry_count += 1
                delay = min(60, 5 * (2 ** min(retry_count - 1, 3)))
                if retry_count <= 2 or retry_count % 5 == 0:
                    print(f"[-] Cloud connection lost ({e}). Reconnecting in {delay}s...")
                await asyncio.sleep(delay)

    async def _process_and_reply(self, ws, req_id: str, action: str, payload: dict):
        result = await self.execute_request(action, payload)
        try:
            await ws.send(json.dumps({
                "req_id": req_id,
                "result": result,
            }))
        except Exception as e:
            print(f"[-] Error sending reply back to relay: {e}")

    def start_background(self):
        thread = threading.Thread(target=lambda: asyncio.run(self.run_client()), daemon=True)
        thread.start()
        return thread


def main():
    relay_url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_RELAY_URL
    bridge = WindowsAgentBridge(relay_url=relay_url)
    print("=" * 60)
    print("⚡ WILLY WINDOWS AGENT BRIDGE INITIALIZED")
    print(f"   Relay Target: {relay_url}")
    print("=" * 60)
    asyncio.run(bridge.run_client())


if __name__ == "__main__":
    main()
