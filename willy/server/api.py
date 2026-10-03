"""
Willy Remote Gateway API Server.
Connects iPhone Siri, Android Google Assistant, Gemini, and webhooks to Willy.
"""

import os
import sys
import time
from typing import Optional, Dict, Any
from fastapi import FastAPI, Request, HTTPException, Security, Depends, Header, UploadFile, File
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

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
from willy.tools.process_tools import check_app_running, close_app
from willy.tools.network_tools import check_network_status
from willy.tools.settings_tools import set_windows_theme

# Remote Security Token
DEFAULT_TOKEN = os.getenv("WILLY_REMOTE_TOKEN", "")

app = FastAPI(
    title="Willy Remote Gateway API",
    description="Remote control API for Willy Windows Assistant (Siri, Google Assistant, Gemini, Webhooks)",
    version="1.0.0",
)

# Enable CORS for mobile Web App / PWA
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer(auto_error=False)
orchestrator = AgentOrchestrator()


def verify_auth(
    auth: Optional[HTTPAuthorizationCredentials] = Security(security),
    x_willy_token: Optional[str] = Header(None),
    token: Optional[str] = None,
) -> bool:
    """Validates authorization token from header, bearer, or query param."""
    expected = os.getenv("WILLY_REMOTE_TOKEN", DEFAULT_TOKEN)
    provided = None

    if auth and auth.credentials:
        provided = auth.credentials
    elif x_willy_token:
        provided = x_willy_token
    elif token:
        provided = token

    if not provided or provided.strip() != expected.strip():
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid or missing Willy remote token.")
    return True


# --- Models ---
class CommandRequest(BaseModel):
    query: str
    speak_on_pc: bool = False


class PowerRequest(BaseModel):
    action: str  # 'lock', 'unlock', 'shutdown', 'restart', 'sleep', 'cancel'
    delay_sec: int = 30
    pin: Optional[str] = None


# --- Endpoints ---

@app.get("/health")
def health_check():
    """Simple ping endpoint."""
    return {"status": "ok", "time": time.time(), "system": "Willy Remote Gateway Online"}


@app.post("/api/v1/command")
@app.get("/api/v1/command")
async def execute_command(
    request: Request,
    query: Optional[str] = None,
    q: Optional[str] = None,
    text: Optional[str] = None,
    command: Optional[str] = None,
    format: Optional[str] = None,
    speak_on_pc: bool = False,
    authorized: bool = Depends(verify_auth),
):
    """
    Executes a voice or text command on the Windows PC via Willy.
    Ultra-resilient: supports JSON, raw string, form, and query parameters.
    """
    user_query = query or q or text or command or ""

    if request.method == "POST":
        content_type = request.headers.get("content-type", "").lower()
        try:
            if "application/json" in content_type:
                body = await request.json()
                if isinstance(body, dict):
                    for k in ["query", "Query", "text", "Text", "command", "Command", "message", "Message", "prompt", "Prompt", "q", "input"]:
                        if k in body and body[k]:
                            user_query = str(body[k]).strip()
                            break
                    if "speak_on_pc" in body:
                        speak_on_pc = bool(body["speak_on_pc"])
                elif isinstance(body, str):
                    user_query = body.strip()
            else:
                raw_bytes = await request.body()
                raw_str = raw_bytes.decode("utf-8", errors="replace").strip()
                if raw_str:
                    try:
                        parsed = json.loads(raw_str)
                        if isinstance(parsed, dict):
                            for k in ["query", "Query", "text", "Text", "command", "Command", "message", "Message", "prompt", "Prompt", "q"]:
                                if k in parsed and parsed[k]:
                                    user_query = str(parsed[k]).strip()
                                    break
                        elif isinstance(parsed, str):
                            user_query = parsed
                    except Exception:
                        user_query = raw_str
        except Exception:
            pass

    if user_query.startswith('"') and user_query.endswith('"') and len(user_query) > 1:
        user_query = user_query[1:-1].strip()

    if not user_query:
        fallback_reply = "I'm listening! What command would you like me to run on your PC?"
        if format == "text" or "text/plain" in request.headers.get("accept", ""):
            return PlainTextResponse(fallback_reply)
        return {
            "success": True,
            "query": "",
            "reply": fallback_reply,
            "tool_used": None,
            "tool_result": None,
            "duration_sec": 0.0,
        }

    t0 = time.time()
    last_tool = {"name": None, "args": None, "result": None}

    def on_tool_start(name, args):
        last_tool["name"] = name
        last_tool["args"] = args

    def on_tool_done(name, result):
        last_tool["result"] = result

    reply = orchestrator.process_query(
        str(user_query).strip(),
        on_tool_start=on_tool_start,
        on_tool_done=on_tool_done,
    )

    if speak_on_pc:
        try:
            from willy.audio.tts import TextToSpeech
            tts = TextToSpeech()
            tts.speak(reply, block=False)
        except Exception as e:
            print(f"[-] Local speak error: {e}")

    duration = round(time.time() - t0, 3)

    if format == "text" or "text/plain" in request.headers.get("accept", ""):
        return PlainTextResponse(reply)

    return {
        "success": True,
        "query": str(user_query).strip(),
        "reply": reply,
        "tool_used": last_tool["name"],
        "tool_result": last_tool["result"],
        "duration_sec": duration,
    }


@app.post("/api/v1/voice")
async def execute_voice_file(
    file: UploadFile = File(...),
    speak_on_pc: bool = False,
    authorized: bool = Depends(verify_auth),
):
    """
    Accepts an audio recording (from iPhone or Android voice notes),
    transcribes it, and executes the command on your PC.
    """
    from willy.audio.stt import SpeechToText
    stt = SpeechToText()

    audio_bytes = await file.read()
    transcript = stt.transcribe(audio_bytes)
    if not transcript:
        return {"success": False, "error": "Could not transcribe audio speech."}

    req = CommandRequest(query=transcript, speak_on_pc=speak_on_pc)
    res = execute_command(req, authorized=True)
    res["transcript"] = transcript
    return res


async def synthesize_speech_base64(text: str) -> Optional[str]:
    """Synthesizes text to MP3 bytes directly in memory using Edge-TTS and returns base64 string."""
    import base64
    try:
        import edge_tts
        active_voice = getattr(settings, "TTS_VOICE", "en-US-ChristopherNeural")
        communicate = edge_tts.Communicate(text=text, voice=active_voice, rate=settings.TTS_RATE)
        audio_buffer = bytearray()
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                audio_buffer.extend(chunk.get("data", b""))
        if audio_buffer:
            return base64.b64encode(bytes(audio_buffer)).decode("utf-8")
    except Exception as e:
        print(f"[-] Web Call speech synthesis error: {e}")
    return None


@app.post("/api/v1/call/interact")
async def call_interact(
    file: UploadFile = File(...),
    token: Optional[str] = None,
    x_willy_token: Optional[str] = Header(None),
    auth: Optional[HTTPAuthorizationCredentials] = Security(security),
):
    """
    Real-time Web Voice Call interface:
    Accepts speech audio from mobile/browser, transcribes with Groq Whisper,
    executes PC actions, synthesizes neural voice, and returns base64 audio for instant playback.
    """
    verify_auth(auth=auth, x_willy_token=x_willy_token, token=token)
    t0 = time.time()
    from willy.audio.stt import SpeechToText
    stt = SpeechToText()

    audio_bytes = await file.read()
    if not audio_bytes or len(audio_bytes) < 300:
        return JSONResponse(status_code=400, content={"success": False, "error": "Empty audio data."})

    transcript = stt.transcribe(audio_bytes)
    if not transcript or not transcript.strip():
        return {"success": False, "error": "No speech detected in audio.", "reply": ""}

    last_tool = {"name": None, "result": None}

    def on_tool_start(name, args):
        last_tool["name"] = name

    def on_tool_done(name, result):
        last_tool["result"] = result

    reply = orchestrator.process_query(
        transcript.strip(),
        on_tool_start=on_tool_start,
        on_tool_done=on_tool_done
    )

    audio_b64 = await synthesize_speech_base64(reply)
    duration = round(time.time() - t0, 3)

    return {
        "success": True,
        "transcript": transcript.strip(),
        "reply": reply,
        "audio_base64": audio_b64,
        "tool_used": last_tool["name"],
        "tool_result": last_tool["result"],
        "duration_sec": duration,
    }


@app.get("/api/v1/status")
def get_pc_status(authorized: bool = Depends(verify_auth)):
    """Returns live Windows PC status (battery, CPU, RAM, active window, internet)."""
    sys_status = get_system_status()
    net_status = check_network_status()
    return {
        "success": True,
        "system": sys_status.get("status", {}),
        "network": net_status,
        "timestamp": time.time(),
    }


@app.post("/api/v1/power")
def control_power(req: PowerRequest, authorized: bool = Depends(verify_auth)):
    """Direct power and session actions (lock, unlock, shutdown, sleep, restart)."""
    action = req.action.lower().strip()
    if action == "lock":
        return lock_workstation()
    elif action == "unlock":
        return unlock_or_wake(pin=req.pin)
    elif action == "shutdown":
        return shutdown_system(delay_sec=req.delay_sec)
    elif action == "restart":
        return restart_system(delay_sec=req.delay_sec)
    elif action == "sleep":
        return sleep_system()
    elif action in ("cancel", "abort"):
        return cancel_shutdown()
    else:
        raise HTTPException(status_code=400, detail=f"Unknown power action: {action}")


# --- Sleek Mobile Web UI for iPhone & Android ---
@app.get("/", response_class=HTMLResponse)
def mobile_web_dashboard(token: Optional[str] = None):
    """
    Returns a responsive, premium mobile dashboard accessible from phone browsers.
    """
    active_token = token or os.getenv("WILLY_REMOTE_TOKEN", DEFAULT_TOKEN)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>Willy Remote Mobile Controller</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; }}
    body {{ background: #0B0F19; color: #F8FAFC; padding: 20px; display: flex; flex-direction: column; align-items: center; min-height: 100vh; }}
    .container {{ width: 100%; max-width: 480px; display: flex; flex-direction: column; gap: 16px; }}
    .header {{ display: flex; align-items: center; justify-content: space-between; padding-bottom: 12px; border-bottom: 1px solid #1E293B; }}
    .logo {{ font-size: 22px; font-weight: 800; color: #00F2FE; letter-spacing: 1px; }}
    .badge {{ background: #10B98122; color: #10B981; border: 1px solid #10B98155; padding: 4px 10px; border-radius: 12px; font-size: 11px; font-weight: bold; }}
    .card {{ background: #111827; border: 1px solid #1E293B; border-radius: 16px; padding: 18px; display: flex; flex-direction: column; gap: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.4); }}
    .call-banner {{ background: linear-gradient(135deg, #0284C7 0%, #7C3AED 100%); border-radius: 16px; padding: 18px; color: #FFF; display: flex; align-items: center; justify-content: space-between; text-decoration: none; box-shadow: 0 8px 25px rgba(2, 132, 199, 0.35); transition: transform 0.15s ease; }}
    .call-banner:active {{ transform: scale(0.98); }}
    .input-row {{ display: flex; gap: 8px; }}
    input[type="text"] {{ flex: 1; background: #1E293B; border: 1px solid #334155; border-radius: 12px; padding: 12px 14px; color: #FFF; font-size: 15px; outline: none; }}
    input[type="text"]:focus {{ border-color: #00F2FE; }}
    button.send-btn {{ background: #0284C7; color: #FFF; border: none; border-radius: 12px; padding: 0 18px; font-size: 16px; font-weight: bold; cursor: pointer; }}
    .grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }}
    button.quick-btn {{ background: #1E293B; border: 1px solid #334155; color: #E2E8F0; border-radius: 12px; padding: 14px; font-size: 13px; font-weight: 600; cursor: pointer; text-align: left; display: flex; align-items: center; gap: 8px; }}
    button.quick-btn:active {{ background: #334155; }}
    .response-card {{ background: #0F172A; border-left: 4px solid #A855F7; padding: 14px; border-radius: 8px; font-size: 14px; line-height: 1.5; color: #E2E8F0; min-height: 50px; }}
    .footer {{ font-size: 12px; color: #64748B; text-align: center; margin-top: 20px; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div class="logo">⚡ WILLY REMOTE</div>
      <div class="badge" id="status-badge">● PC ONLINE</div>
    </div>

    <!-- Live Voice Call Banner -->
    <a href="/call?token={active_token}" class="call-banner">
      <div>
        <div style="font-size: 17px; font-weight: 800; display: flex; align-items: center; gap: 8px;">
          📞 Live Voice Call Mode
        </div>
        <div style="font-size: 12px; opacity: 0.85; margin-top: 4px;">
          Full-duplex hands-free voice call with Willy
        </div>
      </div>
      <div style="background: rgba(255,255,255,0.2); border-radius: 50%; width: 42px; height: 42px; display: flex; align-items: center; justify-content: center; font-size: 20px;">
        🎙️
      </div>
    </a>

    <div class="card">
      <label style="font-size: 11px; font-weight: 700; color: #38BDF8; letter-spacing: 0.5px;">TALK TO YOUR PC</label>
      <div class="input-row">
        <input type="text" id="cmd-input" placeholder="e.g. open chrome with work profile" onkeydown="if(event.key==='Enter') sendCommand()">
        <button class="send-btn" onclick="sendCommand()">Send</button>
      </div>
      <div class="response-card" id="reply-box">Standing by for your command...</div>
    </div>

    <div class="card">
      <label style="font-size: 11px; font-weight: 700; color: #94A3B8;">QUICK ACTIONS</label>
      <div class="grid">
        <button class="quick-btn" onclick="quick('is Chrome open')">🌐 Check Chrome</button>
        <button class="quick-btn" onclick="quick('is Genshin running')">🎮 Check Genshin</button>
        <button class="quick-btn" onclick="quick('lock pc')">🔒 Lock PC</button>
        <button class="quick-btn" onclick="quick('wake up screen')">☀️ Wake Screen</button>
        <button class="quick-btn" onclick="quick('switch to dark mode')">🌙 Dark Mode</button>
        <button class="quick-btn" onclick="quick('what is my battery')">🔋 Battery Status</button>
        <button class="quick-btn" onclick="quick('mute')">🔇 Mute PC</button>
        <button class="quick-btn" onclick="quick('cancel shutdown')">🛑 Cancel Shutdown</button>
      </div>
    </div>

    <div class="footer">
      Connected to Willy on Windows 11 &bull; Token Auth Protected
    </div>
  </div>

  <script>
    const TOKEN = "{active_token}";
    async function sendCommand(text) {{
      const query = text || document.getElementById('cmd-input').value.trim();
      if (!query) return;
      const box = document.getElementById('reply-box');
      box.innerText = "⏳ Executing on PC...";
      try {{
        const res = await fetch('/api/v1/command', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + TOKEN }},
          body: JSON.stringify({{ query: query, speak_on_pc: false }})
        }});
        const data = await res.json();
        box.innerText = data.reply || "Action completed.";
        if (!text) document.getElementById('cmd-input').value = "";
      }} catch (err) {{
        box.innerText = "Error: " + err.message;
      }}
    }}
    function quick(cmd) {{
      sendCommand(cmd);
    }}
  </script>
</body>
</html>
"""


# --- Real-Time Web Voice Call Screen (Full Duplex with Interruption) ---
@app.get("/call", response_class=HTMLResponse)
def web_voice_call_screen(token: Optional[str] = None):
    """
    Renders the live Web Voice Call interface for phone or desktop.
    Allows real-time speaking with Willy, neural voice replies, and speech barge-in.
    """
    active_token = token or os.getenv("WILLY_REMOTE_TOKEN", DEFAULT_TOKEN)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>Willy Live Voice Call</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; }}
    body {{
      background: #060913;
      color: #F8FAFC;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: space-between;
      padding: 24px 20px;
      overflow-x: hidden;
    }}
    .call-container {{
      width: 100%;
      max-width: 440px;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 20px;
      flex: 1;
      justify-content: space-between;
    }}
    .top-bar {{
      width: 100%;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }}
    .back-btn {{
      color: #94A3B8;
      text-decoration: none;
      font-size: 13px;
      font-weight: 600;
      display: flex;
      align-items: center;
      gap: 4px;
      padding: 6px 12px;
      background: #111827;
      border: 1px solid #1F2937;
      border-radius: 20px;
    }}
    .call-badge {{
      background: #10B98122;
      color: #10B981;
      border: 1px solid #10B98155;
      padding: 5px 12px;
      border-radius: 20px;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.5px;
    }}
    .caller-info {{
      text-align: center;
      margin-top: 10px;
    }}
    .caller-name {{
      font-size: 28px;
      font-weight: 800;
      color: #00F2FE;
      letter-spacing: 1px;
    }}
    .call-timer {{
      font-size: 15px;
      color: #94A3B8;
      margin-top: 6px;
      font-variant-numeric: tabular-nums;
    }}
    .status-text {{
      font-size: 13px;
      color: #38BDF8;
      font-weight: 600;
      margin-top: 6px;
      min-height: 20px;
    }}

    /* Animated Pulsing Glowing Orb */
    .orb-stage {{
      position: relative;
      width: 220px;
      height: 220px;
      display: flex;
      align-items: center;
      justify-content: center;
      margin: 10px 0;
    }}
    .glow-ring {{
      position: absolute;
      border-radius: 50%;
      transition: all 0.2s ease;
    }}
    .ring-3 {{
      width: 210px;
      height: 210px;
      background: radial-gradient(circle, rgba(0, 242, 254, 0.08) 0%, rgba(124, 58, 237, 0) 70%);
      animation: pulse-ring 3s infinite ease-in-out;
    }}
    .ring-2 {{
      width: 160px;
      height: 160px;
      background: radial-gradient(circle, rgba(0, 242, 254, 0.2) 0%, rgba(168, 85, 247, 0) 70%);
      animation: pulse-ring 2s infinite ease-in-out reverse;
    }}
    .ring-1 {{
      width: 110px;
      height: 110px;
      background: linear-gradient(135deg, #00F2FE 0%, #7C3AED 100%);
      box-shadow: 0 0 35px rgba(0, 242, 254, 0.5), 0 0 70px rgba(124, 58, 237, 0.4);
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 42px;
      cursor: pointer;
    }}
    @keyframes pulse-ring {{
      0% {{ transform: scale(0.92); opacity: 0.6; }}
      50% {{ transform: scale(1.08); opacity: 1.0; }}
      100% {{ transform: scale(0.92); opacity: 0.6; }}
    }}

    /* Live Dialogue Cards */
    .transcript-box {{
      width: 100%;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }}
    .bubble {{
      padding: 12px 16px;
      border-radius: 14px;
      font-size: 14px;
      line-height: 1.45;
    }}
    .bubble-user {{
      background: #1E293B;
      border-left: 3px solid #38BDF8;
      color: #F1F5F9;
    }}
    .bubble-willy {{
      background: #131B2E;
      border-left: 3px solid #A855F7;
      color: #E2E8F0;
    }}

    /* Bottom Floating Action Bar */
    .action-bar {{
      width: 100%;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 20px;
      padding-top: 10px;
    }}
    .btn-circle {{
      width: 60px;
      height: 60px;
      border-radius: 50%;
      border: none;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      font-size: 22px;
      box-shadow: 0 6px 20px rgba(0,0,0,0.5);
      transition: transform 0.15s ease, background-color 0.2s ease;
    }}
    .btn-circle:active {{ transform: scale(0.92); }}
    .btn-end {{ background: #DC2626; color: #FFF; }}
    .btn-mute {{ background: #1F2937; color: #F1F5F9; border: 1px solid #374151; }}
    .btn-interrupt {{ background: #D97706; color: #FFF; }}
    .btn-label {{ font-size: 11px; color: #94A3B8; margin-top: 6px; text-align: center; }}
  </style>
</head>
<body>
  <div class="call-container">
    <div class="top-bar">
      <a href="/?token={active_token}" class="back-btn">← Dashboard</a>
      <div class="call-badge" id="call-badge">● STANDBY</div>
    </div>

    <div class="caller-info">
      <div class="caller-name">WILLY</div>
      <div class="call-timer" id="call-timer">00:00</div>
      <div class="status-text" id="call-status">Tap 'Call' to begin voice conversation</div>
    </div>

    <!-- Center Glowing Orb Visualizer -->
    <div class="orb-stage" id="orb-stage">
      <div class="glow-ring ring-3" id="ring-3"></div>
      <div class="glow-ring ring-2" id="ring-2"></div>
      <div class="glow-ring ring-1" id="ring-center" onclick="toggleCall()">
        <span id="orb-icon">📞</span>
      </div>
    </div>

    <!-- Live Transcript Box -->
    <div class="transcript-box">
      <div class="bubble bubble-user" id="user-bubble">
        <span style="font-size: 11px; font-weight: 700; color: #38BDF8; display: block; margin-bottom: 2px;">YOU SAID</span>
        <span id="user-text">Say something after call starts...</span>
      </div>
      <div class="bubble bubble-willy" id="willy-bubble">
        <span style="font-size: 11px; font-weight: 700; color: #A855F7; display: block; margin-bottom: 2px;">WILLY REPLIED</span>
        <span id="willy-text">I'm ready to talk and run tasks on your Windows PC.</span>
      </div>
    </div>

    <!-- Bottom Controls -->
    <div class="action-bar">
      <div>
        <button class="btn-circle btn-mute" id="mute-btn" onclick="toggleMute()">🎙️</button>
        <div class="btn-label" id="mute-label">Mute</div>
      </div>
      <div>
        <button class="btn-circle btn-end" id="call-btn" onclick="toggleCall()" style="background: #10B981;">📞</button>
        <div class="btn-label" id="call-label">Start Call</div>
      </div>
      <div>
        <button class="btn-circle btn-interrupt" id="interrupt-btn" onclick="interruptWilly()">✋</button>
        <div class="btn-label">Interrupt</div>
      </div>
    </div>
  </div>

  <script>
    const TOKEN = "{active_token}";
    let inCall = false;
    let isMuted = false;
    let callTimerInterval = null;
    let callSeconds = 0;

    let mediaStream = null;
    let audioContext = null;
    let analyser = null;
    let mediaRecorder = null;
    let audioChunks = [];

    let currentAudio = null;
    let isSpeaking = false;
    let isWillyReplying = false;

    let silenceTimer = null;
    let speechStartTime = 0;
    let isRecordingUtterance = false;

    function formatTime(sec) {{
      const m = Math.floor(sec / 60).toString().padStart(2, '0');
      const s = (sec % 60).toString().padStart(2, '0');
      return m + ':' + s;
    }}

    function setStatus(text, color = "#38BDF8") {{
      const el = document.getElementById('call-status');
      el.innerText = text;
      el.style.color = color;
    }}

    function setBadge(text, color = "#10B981") {{
      const el = document.getElementById('call-badge');
      el.innerText = text;
      el.style.color = color;
      el.style.borderColor = color + "55";
      el.style.background = color + "22";
    }}

    async function toggleCall() {{
      if (!inCall) {{
        await startCall();
      }} else {{
        endCall();
      }}
    }}

    async function startCall() {{
      try {{
        mediaStream = await navigator.mediaDevices.getUserMedia({{
          audio: {{
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          }}
        }});

        inCall = true;
        callSeconds = 0;
        document.getElementById('call-timer').innerText = "00:00";
        callTimerInterval = setInterval(() => {{
          callSeconds++;
          document.getElementById('call-timer').innerText = formatTime(callSeconds);
        }}, 1000);

        document.getElementById('call-btn').style.background = "#DC2626";
        document.getElementById('call-btn').innerText = "📵";
        document.getElementById('call-label').innerText = "End Call";
        document.getElementById('orb-icon').innerText = "🎙️";
        setBadge("● CALL ACTIVE", "#10B981");
        setStatus("Connected! Speak to Willy anytime.", "#10B981");

        initAudioProcessing();
      }} catch (err) {{
        alert("Microphone permission required for voice call: " + err.message);
      }}
    }}

    function endCall() {{
      inCall = false;
      clearInterval(callTimerInterval);
      if (mediaStream) {{
        mediaStream.getTracks().forEach(t => t.stop());
        mediaStream = null;
      }}
      if (audioContext) {{
        try {{ audioContext.close(); }} catch(e) {{}}
        audioContext = null;
      }}
      if (currentAudio) {{
        currentAudio.pause();
        currentAudio = null;
      }}

      document.getElementById('call-btn').style.background = "#10B981";
      document.getElementById('call-btn').innerText = "📞";
      document.getElementById('call-label').innerText = "Start Call";
      document.getElementById('orb-icon').innerText = "📞";
      setBadge("● DISCONNECTED", "#94A3B8");
      setStatus("Call ended. Tap 'Start Call' to resume.", "#94A3B8");
    }}

    function toggleMute() {{
      if (!mediaStream) return;
      isMuted = !isMuted;
      mediaStream.getAudioTracks().forEach(t => t.enabled = !isMuted);
      document.getElementById('mute-btn').innerText = isMuted ? "🔇" : "🎙️";
      document.getElementById('mute-label').innerText = isMuted ? "Unmute" : "Mute";
      setStatus(isMuted ? "Microphone muted" : "Microphone active", isMuted ? "#EF4444" : "#10B981");
    }}

    function interruptWilly() {{
      if (currentAudio) {{
        currentAudio.pause();
        currentAudio.currentTime = 0;
        currentAudio = null;
      }}
      isWillyReplying = false;
      document.getElementById('ring-center').style.background = "linear-gradient(135deg, #00F2FE 0%, #7C3AED 100%)";
      setStatus("Interrupted! Listening to your command...", "#EF4444");
    }}

    function initAudioProcessing() {{
      audioContext = new (window.AudioContext || window.webkitAudioContext)();
      const source = audioContext.createMediaStreamSource(mediaStream);
      analyser = audioContext.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser);

      const bufferLength = analyser.frequencyBinCount;
      const dataArray = new Uint8Array(bufferLength);

      function checkAudio() {{
        if (!inCall) return;

        analyser.getByteTimeDomainData(dataArray);
        let sum = 0;
        for (let i = 0; i < bufferLength; i++) {{
          const val = (dataArray[i] - 128) / 128;
          sum += val * val;
        }}
        const rms = Math.sqrt(sum / bufferLength);

        // Visualizer scale
        const scale = Math.min(1.4, 0.95 + rms * 2.5);
        document.getElementById('ring-3').style.transform = `scale(${{scale * 1.05}})`;
        document.getElementById('ring-2').style.transform = `scale(${{scale}})`;

        // Barge-In Interruption: if Willy is speaking and user speaks firmly
        if (isWillyReplying && rms > 0.05) {{
          interruptWilly();
        }}

        // Voice Activity Detection (VAD)
        if (!isMuted && !isWillyReplying) {{
          if (rms > 0.025) {{
            // Speech detected
            if (!isRecordingUtterance) {{
              startRecordingUtterance();
            }}
            clearTimeout(silenceTimer);
            silenceTimer = null;
          }} else if (isRecordingUtterance) {{
            // Silence after speech
            if (!silenceTimer) {{
              silenceTimer = setTimeout(() => {{
                stopAndSendUtterance();
              }}, 600);
            }}
          }}
        }}

        requestAnimationFrame(checkAudio);
      }}

      checkAudio();
    }}

    function startRecordingUtterance() {{
      isRecordingUtterance = true;
      audioChunks = [];
      setStatus("🎙️ Hearing speech...", "#EF4444");
      document.getElementById('ring-center').style.background = "linear-gradient(135deg, #EF4444 0%, #F59E0B 100%)";

      try {{
        mediaRecorder = new MediaRecorder(mediaStream);
        mediaRecorder.ondataavailable = e => {{
          if (e.data && e.data.size > 0) audioChunks.push(e.data);
        }};
        mediaRecorder.start(100);
      }} catch (e) {{
        console.error("MediaRecorder start error:", e);
      }}
    }}

    function stopAndSendUtterance() {{
      if (!isRecordingUtterance) return;
      isRecordingUtterance = false;
      document.getElementById('ring-center').style.background = "linear-gradient(135deg, #00F2FE 0%, #7C3AED 100%)";
      setStatus("⚡ Processing voice...", "#38BDF8");

      if (!mediaRecorder) return;

      mediaRecorder.onstop = async () => {{
        const audioBlob = new Blob(audioChunks, {{ type: mediaRecorder.mimeType || 'audio/webm' }});
        if (audioBlob.size < 1200) {{
          setStatus("Listening...", "#10B981");
          return;
        }}

        const formData = new FormData();
        formData.append("file", audioBlob, "call_speech.webm");

        try {{
          const res = await fetch('/api/v1/call/interact', {{
            method: 'POST',
            headers: {{ 'Authorization': 'Bearer ' + TOKEN }},
            body: formData
          }});
          const data = await res.json();

          if (data.success) {{
            document.getElementById('user-text').innerText = `"${{data.transcript}}"`;
            document.getElementById('willy-text').innerText = data.reply;

            if (data.audio_base64) {{
              playWillyAudio(data.audio_base64);
            }} else {{
              setStatus("Ready (Listening)", "#10B981");
            }}
          }} else {{
            setStatus("Ready (Listening)", "#10B981");
          }}
        }} catch (err) {{
          console.error("Call interact error:", err);
          setStatus("Error communicating with PC", "#EF4444");
        }}
      }};

      try {{
        mediaRecorder.stop();
      }} catch (e) {{}}
    }}

    function playWillyAudio(base64Audio) {{
      if (currentAudio) {{
        currentAudio.pause();
      }}
      isWillyReplying = true;
      setStatus("🔊 Willy Speaking (Speak to Interrupt)", "#A855F7");
      document.getElementById('ring-center').style.background = "linear-gradient(135deg, #8B5CF6 0%, #EC4899 100%)";

      currentAudio = new Audio("data:audio/mp3;base64," + base64Audio);
      currentAudio.onended = () => {{
        isWillyReplying = false;
        document.getElementById('ring-center').style.background = "linear-gradient(135deg, #00F2FE 0%, #7C3AED 100%)";
        setStatus("Listening for your command...", "#10B981");
      }};
      currentAudio.onerror = (e) => {{
        isWillyReplying = false;
        setStatus("Listening for your command...", "#10B981");
      }};
      currentAudio.play().catch(e => console.log("Audio autoplay notice:", e));
    }}
  </script>
</body>
</html>
"""

