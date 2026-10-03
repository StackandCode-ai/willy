"""
Entry point to run Willy Central Server Hub directly.
Usage:
    python -m server.main
or from server directory:
    python main.py
"""

import sys
from pathlib import Path

# Add server directory and parent to sys.path
SERVER_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SERVER_DIR.parent

if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import uvicorn
from server.config import settings

if __name__ == "__main__":
    print("=" * 60)
    print("  WILLY CENTRAL SERVER HUB (Cloud / VPS / Local Brain)")
    print(f"  Listening on: http://{settings.HOST}:{settings.PORT}")
    print(f"  Web Voice Call: http://localhost:{settings.PORT}/call")
    print(f"  Device Gateway: ws://localhost:{settings.PORT}/ws/devices")
    print("=" * 60)

    uvicorn.run(
        "server.app:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=False,
    )
