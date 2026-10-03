"""
Willy Remote Gateway Server Launcher.
Starts the FastAPI server on port 8765 and optionally creates a Cloudflare Tunnel
for secure HTTPS access from iPhone Siri and Android Google Assistant.
"""

import os
import sys
import time
import socket
import subprocess
import threading

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

CLOUDFLARED_EXE = r"C:\Program Files (x86)\cloudflared\cloudflared.exe"
if not os.path.exists(CLOUDFLARED_EXE):
    CLOUDFLARED_EXE = "cloudflared"

DEFAULT_TOKEN = os.getenv("WILLY_REMOTE_TOKEN", "")
PORT = 8765


def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def run_tunnel():
    """Starts cloudflared tunnel in the background, logs to file, and saves the public URL."""
    if not os.path.exists(CLOUDFLARED_EXE):
        print("[-] Cloudflare tunnel binary not found.")
        return

    os.makedirs("logs", exist_ok=True)
    log_file = open("logs/tunnel.log", "w", encoding="utf-8", buffering=1)

    cmd = [CLOUDFLARED_EXE, "tunnel", "--config", "NUL", "--url", f"http://127.0.0.1:{PORT}", "--no-autoupdate"]
    try:
        proc = subprocess.Popen(
            cmd,
            stderr=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        tunnel_url_found = False

        for line in proc.stderr:
            log_file.write(line)
            log_file.flush()

            if not tunnel_url_found and "trycloudflare.com" in line:
                import re
                match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
                if match:
                    tunnel_url = match.group(0)
                    with open("logs/tunnel_url.txt", "w", encoding="utf-8") as tf:
                        tf.write(tunnel_url)
                    tunnel_url_found = True
                    print("\n" + "=" * 65)
                    print(f"PUBLIC SECURE HTTPS URL (For Siri & Google Assistant):")
                    print(f"   {tunnel_url}")
                    print(f"AUTH TOKEN:")
                    print(f"   {DEFAULT_TOKEN}")
                    print(f"MOBILE WEB DASHBOARD:")
                    print(f"   {tunnel_url}/")
                    print("=" * 65 + "\n")

        proc.wait()
    except Exception as e:
        print(f"[-] Cloudflare Tunnel notice: {e}")
    finally:
        try:
            log_file.close()
        except Exception:
            pass


def main():
    import uvicorn
    local_ip = get_local_ip()

    print("\n" + "=" * 65)
    print("⚡ WILLY REMOTE GATEWAY INITIALIZING")
    print(f"   Local IP URL: http://{local_ip}:{PORT}")
    print(f"   Localhost URL: http://localhost:{PORT}")
    print(f"   Auth Token: {DEFAULT_TOKEN}")
    print("=" * 65)

    # Start Cloudflare tunnel in a background thread (fallback quick tunnel)
    t = threading.Thread(target=run_tunnel, daemon=True)
    t.start()

    # Start Cloud Relay Bridge (Connects Windows PC to EC2 truewilly.com)
    try:
        from willy.server.agent_bridge import WindowsAgentBridge
        relay_url = os.getenv("WILLY_RELAY_URL", "wss://truewilly.com/willy/ws/pc")
        bridge = WindowsAgentBridge(relay_url=relay_url)
        bridge.start_background()
        print(f"   Cloud Relay Bridge: Active -> {relay_url}")
    except Exception as e:
        print(f"[-] Cloud Relay Bridge notice: {e}")

    # Run local Uvicorn server
    uvicorn.run("willy.server.api:app", host="0.0.0.0", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
