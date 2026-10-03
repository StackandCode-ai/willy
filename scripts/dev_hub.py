"""Runs a throwaway hub for trying the dashboard locally: temporary data folder, fixed dev token.

    python scripts/dev_hub.py            ->  http://localhost:5179/#token=dev-token-for-local-testing-only
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["WILLY_DATA_DIR"] = tempfile.mkdtemp(prefix="willy_dev_")
os.environ["WILLY_REMOTE_TOKEN"] = "dev-token-for-local-testing-only"
os.environ.setdefault("WILLY_SIGNUP", "open")
os.environ["WILLY_TELEMETRY"] = "off"
for key in ("GROQ_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY"):
    os.environ[key] = ""

import uvicorn  # noqa: E402

uvicorn.run("server.app:app", host="127.0.0.1", port=int(os.getenv("PORT", "5179")), log_level="warning")
