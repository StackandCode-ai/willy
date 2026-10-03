"""
Network and Internet Connectivity Detection Tools for Willy.
"""

import socket
import time
import subprocess
from typing import Dict, Any


def is_internet_available(timeout: float = 1.5) -> bool:
    """
    Fast, reliable check for active internet connectivity via direct socket connection.
    """
    # Try multiple highly reliable DNS servers (Google, Cloudflare)
    for host in ("8.8.8.8", "1.1.1.1"):
        try:
            socket.create_connection((host, 53), timeout=timeout)
            return True
        except OSError:
            continue
    return False


def check_network_status() -> Dict[str, Any]:
    """
    Returns full network connectivity diagnostics (online status, latency, local IP).
    """
    start_time = time.time()
    online = False
    latency_ms = None

    for host in ("8.8.8.8", "1.1.1.1"):
        t0 = time.time()
        try:
            sock = socket.create_connection((host, 53), timeout=2.0)
            sock.close()
            online = True
            latency_ms = round((time.time() - t0) * 1000, 1)
            break
        except OSError:
            continue

    # Get local IP
    local_ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass

    if online:
        message = f"Internet connection is active with {latency_ms}ms latency. Local IP is {local_ip}."
    else:
        message = "No internet connection detected. Running in offline mode."

    return {
        "success": True,
        "online": online,
        "latency_ms": latency_ms,
        "local_ip": local_ip,
        "message": message,
    }
