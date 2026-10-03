# Willy Central Server Hub (Brain & Gateway)

The central brain of the Willy ecosystem. Can be hosted anywhere: local PC, home lab, EC2, or Linux VPS.

---

## Capabilities
- **Device Registry & Discovery**: Real-time WebSockets (`/ws/devices`) and REST endpoints (`/api/v1/devices`) tracking all connected PCs and mobile clients.
- **Bi-directional Command Routing**: Intelligently routes commands from Mobile/Web to the appropriate online Windows PC.
- **Groq Whisper STT**: High-speed speech transcription supporting webm, ogg, wav, and m4a containers.
- **Edge-TTS Neural Voice**: In-memory speech synthesis with English and Tamil neural voices.
- **Web Voice Call**: Interactive full-duplex voice call interface at `/call` with speech barge-in and audio visualizer.
- **Central LLM Orchestrator**: Supports Groq (Llama 3.3 70B), Google Gemini (2.5 Flash), and OpenAI (GPT-4o mini).

---

## Setup & Running

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Environment
Copy `.env.example` to `.env` and set your API keys:
```bash
cp .env.example .env
```

### 3. Start Server
```bash
# Windows
run_server.bat

# Linux / Mac / CLI
python main.py
```
- Listening on: `http://0.0.0.0:8000`
- Web Call Interface: `http://localhost:8000/call`
- Device Gateway: `ws://localhost:8000/ws/devices`
