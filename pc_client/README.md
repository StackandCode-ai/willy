# Willy PC Client (Physical Laptop Node)

The lightweight execution worker that runs on your physical Windows laptop or desktop.

---

## Capabilities
- **Automatic Outbound Discovery**: Connects directly to the Central Server Hub over WebSocket. No inbound ports or public IP needed on your laptop!
- **Real-Time Telemetry**: Broadcasts battery %, charging status, CPU load %, RAM %, and active focused window every 10 seconds.
- **Physical Windows Automation**:
  - **App Launcher**: Opens and switches to Chrome, VS Code, Spotify, Notepad, Calculator, etc.
  - **Power Controls**: Lock workstation, put PC to sleep, scheduled shutdown, restart, cancel shutdown.
  - **Audio & Volume**: Mute, unmute, set volume %, volume up/down.
  - **Window Controls**: Minimize all, maximize, snap, close windows.
  - **Vision**: Capture full-resolution desktop screenshots.
  - **Desktop HUD**: Floating non-blocking dark-mode toast notifications.

---

## Setup & Running

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Environment
Copy `.env.example` to `.env` and set the server URL:
```bash
# Set your Central Server WebSocket URL:
WILLY_SERVER_URL=ws://localhost:8000/ws/devices
WILLY_REMOTE_TOKEN=change-me-to-a-long-random-token
```

### 3. Start PC Worker
```bash
# Windows 1-Click
run_client.bat

# Or Python CLI
python main.py            # desktop app (closing the window keeps it running in the tray)
python main.py --tray     # start hidden in the notification area
python main.py --cli      # console only
```

### 4. Standalone exe + Start with Windows
From the project root, `python build_pc_exe.py --autostart` builds `dist\WillyPC\WillyPC.exe`, adds a
**Willy PC** Start menu entry and starts it hidden in the tray at every sign-in. The tray icon (hidden
icons flyout, ^) has Open, Web dashboard, Voice call, Reconnect, Start with Windows and Quit. See the
main README for details.
