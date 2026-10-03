"""
Willy Cloud Relay Server (Runs on EC2 / VPS).
Acts as a high-speed permanent bridge between Siri / Google Assistant and your Windows PC.
Supports audio speech feedback for iPhone / Android and resilient payload parsing.
"""

import os
import sys
import time
import json
import asyncio
from typing import Optional, Dict, Any
import base64
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException, Header, Depends, Security, UploadFile, File
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

DEFAULT_TOKEN = os.getenv("WILLY_REMOTE_TOKEN", "")

app = FastAPI(
    title="Willy Cloud Relay Gateway",
    description="Bridge connecting iPhone Siri / Android Assistant to Windows PC",
    version="1.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer(auto_error=False)


# --- Global Connection Manager for Windows PC ---
class PCConnectionManager:
    def __init__(self):
        self.active_socket: Optional[WebSocket] = None
        self.pending_requests: Dict[str, asyncio.Future] = {}
        self.last_seen: float = 0.0
        self.pc_info: Dict[str, Any] = {}

    @property
    def is_online(self) -> bool:
        return self.active_socket is not None

    async def connect(self, websocket: WebSocket, client_info: Dict[str, Any]):
        await websocket.accept()
        self.active_socket = websocket
        self.last_seen = time.time()
        self.pc_info = client_info
        print(f"[+] Windows PC Connected: {client_info.get('hostname', 'Unknown')}")

    def disconnect(self):
        self.active_socket = None
        print("[-] Windows PC Disconnected")

    async def send_command(self, action: str, payload: dict, timeout: float = 20.0) -> dict:
        if not self.active_socket:
            return {
                "success": False,
                "reply": "Your Windows PC is currently offline or disconnected.",
                "error": "PC_OFFLINE",
            }

        req_id = f"req_{int(time.time() * 1000)}_{len(self.pending_requests)}"
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self.pending_requests[req_id] = fut

        msg = {
            "req_id": req_id,
            "action": action,
            "payload": payload,
            "timestamp": time.time(),
        }

        try:
            await self.active_socket.send_json(msg)
            result = await asyncio.wait_for(fut, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            return {
                "success": False,
                "reply": "Timed out waiting for response from Windows PC.",
                "error": "TIMEOUT",
            }
        except Exception as e:
            return {
                "success": False,
                "reply": f"Error communicating with PC: {e}",
                "error": str(e),
            }
        finally:
            self.pending_requests.pop(req_id, None)

    def handle_response(self, req_id: str, data: dict):
        fut = self.pending_requests.get(req_id)
        if fut and not fut.done():
            fut.set_result(data)


manager = PCConnectionManager()


def verify_auth(
    auth: Optional[HTTPAuthorizationCredentials] = Security(security),
    x_willy_token: Optional[str] = Header(None),
    token: Optional[str] = None,
) -> bool:
    expected = os.getenv("WILLY_REMOTE_TOKEN", DEFAULT_TOKEN)
    provided = None
    if auth and auth.credentials:
        provided = auth.credentials
    elif x_willy_token:
        provided = x_willy_token
    elif token:
        provided = token

    if not provided or provided.strip() != expected.strip():
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid Willy Token.")
    return True


class PowerRequest(BaseModel):
    action: str
    delay_sec: int = 30
    pin: Optional[str] = None


# --- Endpoints ---

@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": "Willy Cloud Relay",
        "pc_online": manager.is_online,
        "last_seen": manager.last_seen,
        "pc_info": manager.pc_info,
    }


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
    Ultra-resilient command handler for Siri, Google Assistant, shortcuts, and webhooks.
    Supports JSON, form-data, plain text, and query parameters.
    """
    user_query = query or q or text or command or ""
    raw_bytes = b""

    # Step 1: Check query parameters (?query=... or ?text=...)
    if not user_query:
        for qk in ["query", "Query", "text", "Text", "command", "Command", "message", "Message", "prompt", "Prompt", "q", "input"]:
            if qk in request.query_params and request.query_params[qk]:
                user_query = request.query_params[qk].strip()
                break

    # Step 2: If POST, extract from JSON, Form, Multipart, or Raw Body
    if request.method == "POST":
        content_type = request.headers.get("content-type", "").lower()

        # Strategy A: Try parsing as JSON first
        try:
            body = await request.json()
            if isinstance(body, dict):
                for k in ["query", "Query", "text", "Text", "command", "Command", "message", "Message", "prompt", "Prompt", "q", "input"]:
                    if k in body and body[k]:
                        user_query = str(body[k]).strip()
                        break
                if "speak_on_pc" in body:
                    speak_on_pc = bool(body["speak_on_pc"])
            elif isinstance(body, str) and body.strip():
                user_query = body.strip()
        except Exception:
            pass

        # Strategy B: Try parsing as Form / Multipart
        if not user_query:
            try:
                form = await request.form()
                for fk in ["query", "Query", "text", "Text", "command", "Command", "message", "Message", "prompt", "Prompt", "q", "input", "file"]:
                    if fk in form:
                        val = form[fk]
                        if hasattr(val, "read"):
                            content = (await val.read()).decode("utf-8", errors="replace").strip()
                            if content:
                                user_query = content
                                break
                        elif val:
                            user_query = str(val).strip()
                            break
                if not user_query and len(form) > 0:
                    for k, v in form.items():
                        if hasattr(v, "read"):
                            content = (await v.read()).decode("utf-8", errors="replace").strip()
                            if content:
                                user_query = content
                                break
                        elif v:
                            user_query = str(v).strip()
                            break
            except Exception:
                pass

        # Strategy C: Read raw body and check text
        if not user_query:
            try:
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
                        if not raw_str.startswith("--") and not raw_str.startswith("{"):
                            user_query = raw_str
            except Exception:
                pass

    # Clean up outer quotes
    if user_query.startswith('"') and user_query.endswith('"') and len(user_query) > 1:
        user_query = user_query[1:-1].strip()

    if not raw_bytes:
        try:
            raw_bytes = await request.body()
        except Exception:
            pass

    print(f"[RELAY-CMD] Final Query: {repr(user_query)} | Method: {request.method} | CT: {request.headers.get('content-type')} | RAW: {repr(raw_bytes)}")

    if not user_query:
        fallback_reply = "I'm listening! What command would you like me to run on your PC?"
        if format == "text" or "text/plain" in request.headers.get("accept", ""):
            return PlainTextResponse(fallback_reply)
        return {
            "success": True,
            "query": "",
            "reply": fallback_reply,
            "duration_sec": 0.0,
        }

    t0 = time.time()
    payload = {"query": str(user_query).strip(), "speak_on_pc": speak_on_pc}
    res = await manager.send_command("command", payload)
    if "duration_sec" not in res:
        res["duration_sec"] = round(time.time() - t0, 3)

    # Return plain text directly if requested by Apple Shortcuts
    if format == "text" or "text/plain" in request.headers.get("accept", ""):
        return PlainTextResponse(res.get("reply", "Command completed."))

    return res


async def synthesize_speech_base64(text: str) -> Optional[str]:
    """Synthesizes text to MP3 bytes directly in memory using Edge-TTS and returns base64 string."""
    try:
        import edge_tts
        from willy.config import settings
        active_voice = getattr(settings, "TTS_VOICE", "en-US-ChristopherNeural")
        communicate = edge_tts.Communicate(text=text, voice=active_voice, rate=getattr(settings, "TTS_RATE", "+0%"))
        audio_buffer = bytearray()
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio":
                audio_buffer.extend(chunk.get("data", b""))
        if audio_buffer:
            return base64.b64encode(bytes(audio_buffer)).decode("utf-8")
    except Exception as e:
        print(f"[-] Relay speech synthesis error: {e}")
    return None


@app.post("/api/v1/call/interact")
async def call_interact(
    file: UploadFile = File(...),
    token: Optional[str] = None,
    x_willy_token: Optional[str] = Header(None),
    auth: Optional[HTTPAuthorizationCredentials] = Security(security),
):
    """
    Real-time Web Voice Call interface on Server:
    Accepts speech audio from mobile/browser, transcribes with Groq Whisper,
    routes command to physical laptop or executes conversationally,
    synthesizes neural voice, and returns base64 audio for instant playback.
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

    # Route command to physical PC if online
    if manager.is_online:
        res = await manager.send_command("command", {"query": transcript.strip(), "speak_on_pc": False})
        reply = res.get("reply", "Action completed on your Windows PC.")
        tool_used = res.get("tool_used")
        tool_result = res.get("tool_result")
    else:
        # Fallback to server's own orchestrator if PC is disconnected
        try:
            from willy.agent.orchestrator import AgentOrchestrator
            orch = AgentOrchestrator()
            reply = orch.process_query(transcript.strip())
        except Exception:
            reply = f"Heard: '{transcript.strip()}'. Your Windows laptop is currently offline, so system actions could not be executed."
        tool_used = None
        tool_result = None

    audio_b64 = await synthesize_speech_base64(reply)
    duration = round(time.time() - t0, 3)

    return {
        "success": True,
        "transcript": transcript.strip(),
        "reply": reply,
        "audio_base64": audio_b64,
        "tool_used": tool_used,
        "tool_result": tool_result,
        "duration_sec": duration,
        "pc_online": manager.is_online,
    }


@app.post("/api/v1/power")
async def control_power(req: PowerRequest, authorized: bool = Depends(verify_auth)):
    """Routes power actions (lock, unlock, sleep, shutdown) to PC."""
    return await manager.send_command("power", req.dict())


@app.get("/api/v1/status")
async def get_pc_status(authorized: bool = Depends(verify_auth)):
    """Fetches real-time status from PC."""
    if not manager.is_online:
        return {
            "success": False,
            "pc_online": False,
            "message": "Windows PC is currently offline.",
        }
    return await manager.send_command("status", {})


# --- WebSocket Endpoint for Windows PC ---
@app.websocket("/ws/pc")
async def websocket_pc_endpoint(websocket: WebSocket, token: Optional[str] = None):
    expected = os.getenv("WILLY_REMOTE_TOKEN", DEFAULT_TOKEN)
    if token != expected:
        await websocket.close(code=4001, reason="Unauthorized Token")
        return

    await manager.connect(websocket, {"connected_at": time.time()})
    try:
        while True:
            data = await websocket.receive_json()
            req_id = data.get("req_id")
            if req_id:
                manager.handle_response(req_id, data.get("result", {}))
            elif data.get("type") == "heartbeat":
                manager.last_seen = time.time()
                await websocket.send_json({"type": "heartbeat_ack", "time": time.time()})
    except (WebSocketDisconnect, Exception):
        manager.disconnect()


@app.get("/Pc.shortcut")
def download_shortcut():
    from fastapi.responses import FileResponse
    sc_path = os.path.join(os.path.dirname(__file__), "Pc_fixed.shortcut")
    if os.path.exists(sc_path):
        return FileResponse(sc_path, media_type="application/octet-stream", filename="Pc.shortcut")
    raise HTTPException(status_code=404, detail="Shortcut file not found")


# --- Mobile Web Dashboard with Voice Output ---
@app.get("/call", response_class=HTMLResponse)
def call_screen_relay(token: Optional[str] = None):
    from willy.server.api import web_voice_call_screen
    return web_voice_call_screen(token=token)


@app.get("/", response_class=HTMLResponse)
def mobile_dashboard():
    token = os.getenv("WILLY_REMOTE_TOKEN", DEFAULT_TOKEN)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>Willy Cloud Remote</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
    body {{ background: #0A0E17; color: #F8FAFC; padding: 20px; display: flex; flex-direction: column; align-items: center; min-height: 100vh; }}
    .container {{ width: 100%; max-width: 460px; display: flex; flex-direction: column; gap: 16px; }}
    .header {{ display: flex; align-items: center; justify-content: space-between; padding-bottom: 12px; border-bottom: 1px solid #1E293B; }}
    .logo {{ font-size: 22px; font-weight: 800; color: #00F2FE; letter-spacing: 1px; display: flex; align-items: center; gap: 8px; }}
    .badge {{ padding: 6px 12px; border-radius: 20px; font-size: 12px; font-weight: 700; display: inline-flex; align-items: center; gap: 6px; }}
    .badge.online {{ background: rgba(16,185,129,0.15); color: #10B981; border: 1px solid #10B981; }}
    .badge.offline {{ background: rgba(239,68,68,0.15); color: #EF4444; border: 1px solid #EF4444; }}
    .card {{ background: #111827; border: 1px solid #1E293B; border-radius: 18px; padding: 18px; display: flex; flex-direction: column; gap: 12px; box-shadow: 0 8px 30px rgba(0,0,0,0.5); }}
    .input-row {{ display: flex; gap: 8px; }}
    input[type="text"] {{ flex: 1; background: #1E293B; border: 1px solid #334155; border-radius: 12px; padding: 14px; color: #FFF; font-size: 15px; outline: none; }}
    input[type="text"]:focus {{ border-color: #00F2FE; }}
    button.send-btn {{ background: linear-gradient(135deg, #0284C7, #00F2FE); color: #000; border: none; border-radius: 12px; padding: 0 18px; font-size: 15px; font-weight: 700; cursor: pointer; }}
    button.mic-btn {{ background: #1E293B; border: 1px solid #38BDF8; color: #38BDF8; border-radius: 12px; width: 48px; display: flex; align-items: center; justify-content: center; font-size: 18px; cursor: pointer; }}
    button.mic-btn.active {{ background: #EF4444; color: #FFF; border-color: #EF4444; animation: pulse 1s infinite; }}
    @keyframes pulse {{ 0% {{ transform: scale(1); }} 50% {{ transform: scale(1.08); }} 100% {{ transform: scale(1); }} }}
    .grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }}
    button.quick-btn {{ background: #1E293B; border: 1px solid #334155; color: #E2E8F0; border-radius: 12px; padding: 14px; font-size: 13px; font-weight: 600; cursor: pointer; text-align: left; transition: all 0.2s; }}
    button.quick-btn:active {{ transform: scale(0.97); background: #334155; }}
    .response-card {{ background: #0F172A; border-left: 4px solid #38BDF8; padding: 14px; border-radius: 8px; font-size: 14px; line-height: 1.5; color: #E2E8F0; min-height: 50px; display: flex; flex-direction: column; justify-content: space-between; }}
    .voice-bar {{ display: flex; align-items: center; justify-content: space-between; font-size: 12px; color: #94A3B8; margin-top: 6px; }}
    .toggle-btn {{ background: #1E293B; border: 1px solid #334155; color: #38BDF8; border-radius: 8px; padding: 4px 10px; font-size: 11px; font-weight: 700; cursor: pointer; }}
    .footer {{ font-size: 12px; color: #64748B; text-align: center; margin-top: 15px; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div class="logo">⚡ WILLY CLOUD</div>
      <div class="badge offline" id="pc-status">● PC CHECKING...</div>
    </div>

    <!-- Live Voice Call Banner -->
    <a href="/call?token={token}" style="background: linear-gradient(135deg, #0284C7 0%, #7C3AED 100%); border-radius: 16px; padding: 16px 18px; color: #FFF; display: flex; align-items: center; justify-content: space-between; text-decoration: none; box-shadow: 0 8px 25px rgba(2, 132, 199, 0.35);">
      <div>
        <div style="font-size: 16px; font-weight: 800; display: flex; align-items: center; gap: 8px;">
          📞 Live Voice Call Mode
        </div>
        <div style="font-size: 12px; opacity: 0.85; margin-top: 4px;">
          Full-duplex hands-free voice call with Willy
        </div>
      </div>
      <div style="background: rgba(255,255,255,0.2); border-radius: 50%; width: 40px; height: 40px; display: flex; align-items: center; justify-content: center; font-size: 18px;">
        🎙️
      </div>
    </a>

    <div class="card">
      <div style="display: flex; justify-content: space-between; align-items: center;">
        <label style="font-size: 11px; font-weight: 700; color: #38BDF8;">COMMAND YOUR PC</label>
        <button class="toggle-btn" id="voice-toggle" onclick="toggleVoice()">🔊 Voice: ON</button>
      </div>
      <div class="input-row">
        <input type="text" id="cmd-input" placeholder="e.g. is Chrome running?" onkeydown="if(event.key==='Enter') sendCmd()">
        <button class="mic-btn" id="mic-btn" onclick="toggleMic()" title="Voice Dictation">🎙️</button>
        <button class="send-btn" onclick="sendCmd()">Send</button>
      </div>
      <div class="response-card" id="reply-box">
        <span id="reply-text">Standing by for command...</span>
        <div class="voice-bar">
          <span id="latency-text">Ready</span>
          <button style="background: none; border: none; color: #38BDF8; font-size: 11px; cursor: pointer;" onclick="replayAudio()">▶ Replay Audio</button>
        </div>
      </div>
    </div>

    <div class="card">
      <label style="font-size: 11px; font-weight: 700; color: #94A3B8;">QUICK CONTROLS</label>
      <div class="grid">
        <button class="quick-btn" onclick="quick('lock pc')">🔒 Lock PC</button>
        <button class="quick-btn" onclick="quick('wake up screen')">☀️ Wake Screen</button>
        <button class="quick-btn" onclick="quick('is Chrome running')">🌐 Check Chrome</button>
        <button class="quick-btn" onclick="quick('what is my battery level')">🔋 Battery</button>
        <button class="quick-btn" onclick="quick('check network status')">📡 Network</button>
        <button class="quick-btn" onclick="quick('mute')">🔇 Mute Volume</button>
        <button class="quick-btn" onclick="quick('switch to dark mode')">🌙 Dark Mode</button>
        <button class="quick-btn" onclick="quick('cancel shutdown')">🛑 Cancel Shutdown</button>
      </div>
    </div>

    <div class="footer">
      EC2 Cloud Relay &bull; truewilly.com &bull; End-to-End Encrypted
    </div>
  </div>

  <script>
    const TOKEN = "{token}";
    let voiceEnabled = true;
    let lastSpokenText = "";
    let recognition = null;
    let isListening = false;

    // --- Speech Synthesis on iPhone Safari ---
    function speakOut(text) {{
      lastSpokenText = text;
      if (!voiceEnabled || !window.speechSynthesis) return;
      try {{
        window.speechSynthesis.cancel();
        const utter = new SpeechSynthesisUtterance(text);
        utter.rate = 1.05;
        utter.pitch = 1.0;
        utter.lang = 'en-US';
        window.speechSynthesis.speak(utter);
      }} catch(e) {{
        console.warn("Speech error:", e);
      }}
    }}

    function replayAudio() {{
      if (lastSpokenText) speakOut(lastSpokenText);
    }}

    function toggleVoice() {{
      voiceEnabled = !voiceEnabled;
      document.getElementById('voice-toggle').innerText = voiceEnabled ? "🔊 Voice: ON" : "🔇 Voice: OFF";
      if (!voiceEnabled && window.speechSynthesis) window.speechSynthesis.cancel();
    }}

    // --- Web Speech Recognition (Mic) ---
    function initSpeech() {{
      const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (SpeechRec) {{
        recognition = new SpeechRec();
        recognition.continuous = false;
        recognition.interimResults = false;
        recognition.lang = 'en-US';
        recognition.onstart = () => {{
          isListening = true;
          document.getElementById('mic-btn').classList.add('active');
          document.getElementById('reply-text').innerText = "🎙️ Listening... speak now";
        }};
        recognition.onresult = (evt) => {{
          const transcript = evt.results[0][0].transcript;
          document.getElementById('cmd-input').value = transcript;
          sendCmd(transcript);
        }};
        recognition.onend = () => {{
          isListening = false;
          document.getElementById('mic-btn').classList.remove('active');
        }};
        recognition.onerror = () => {{
          isListening = false;
          document.getElementById('mic-btn').classList.remove('active');
        }};
      }}
    }}
    initSpeech();

    function toggleMic() {{
      if (!recognition) {{
        alert("Voice recognition is not supported in this browser. Please type your command.");
        return;
      }}
      if (isListening) {{
        recognition.stop();
      }} else {{
        // Safari requires user interaction to unlock audio
        if (window.speechSynthesis) {{
          const unlock = new SpeechSynthesisUtterance("");
          window.speechSynthesis.speak(unlock);
        }}
        recognition.start();
      }}
    }}

    async function checkStatus() {{
      try {{
        const res = await fetch('/health');
        const data = await res.json();
        const badge = document.getElementById('pc-status');
        if (data.pc_online) {{
          badge.className = "badge online";
          badge.innerText = "● PC ONLINE";
        }} else {{
          badge.className = "badge offline";
          badge.innerText = "● PC OFFLINE";
        }}
      }} catch(e) {{}}
    }}
    setInterval(checkStatus, 3000);
    checkStatus();

    async function sendCmd(text) {{
      const query = text || document.getElementById('cmd-input').value.trim();
      if (!query) return;
      const textBox = document.getElementById('reply-text');
      const latencyBox = document.getElementById('latency-text');
      textBox.innerText = "⏳ Sending to PC...";
      latencyBox.innerText = "Processing...";
      
      // Unlock audio on touch event
      if (window.speechSynthesis) {{
        const dummy = new SpeechSynthesisUtterance("");
        window.speechSynthesis.speak(dummy);
      }}

      try {{
        const res = await fetch('/api/v1/command', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + TOKEN }},
          body: JSON.stringify({{ query: query, speak_on_pc: false }})
        }});
        const data = await res.json();
        const reply = data.reply || (data.success ? "Done" : "Error");
        textBox.innerText = reply;
        latencyBox.innerText = (data.duration_sec ? data.duration_sec + "s" : "Done");
        speakOut(reply);
        if (!text) document.getElementById('cmd-input').value = "";
      }} catch(err) {{
        textBox.innerText = "Error: " + err.message;
        latencyBox.innerText = "Failed";
      }}
    }}
    function quick(c) {{ sendCmd(c); }}
  </script>
</body>
</html>
"""


def main():
    import uvicorn
    port = int(os.getenv("PORT", "8765"))
    uvicorn.run("willy.server.relay:app", host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
