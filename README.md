# Willy

A voice assistant that runs on **your own server**. It listens on your Windows PC and Android phone, and sends each request to the device that can do it: open an app, find a file and send it to your phone, set an alarm, check how the server is doing.

Website and downloads: **https://truewilly.com**

```
 Windows PC app ─┐
 Android app ────┼──►  Willy hub (your Linux server)  ──►  AI brain (Groq)
 Server agent ───┘            │
                              └── dashboard + voice call page in any browser
```

## Install

### 1. The server (do this first)

You need a Linux machine that stays on (a small VPS, a Raspberry Pi, an old laptop) with Python 3.10+.

```bash
git clone https://github.com/StackandCode-ai/willy.git
cd willy
./install
```

The installer creates the Python environment, generates an access token, starts Willy as a service and (if you give it a domain) sets up HTTPS with Caddy. It asks which AI Willy should use (Groq, Gemini, OpenAI, OpenRouter or any OpenAI-compatible address, including a local model) and tests your key. At the end it prints a link that signs you in as the owner and a first code for your phone. Run it again any time: your settings are kept.

The AI choice can also be changed later in the dashboard under **Set up**. Voice input needs a Groq or OpenAI key for speech-to-text, which you can add there next to a different main AI.

### 2. The Windows app

Download `WillyPC-windows.zip` from the [latest release](../../releases/latest), unzip it and run `WillyPC.exe`. Enter your server address and the code from your dashboard (**Set up** > *Add your phone and PC*). After that it lives in the tray.

### 3. The Android app

Download `Willy.apk` from the [latest release](../../releases/latest) and open it (Android asks you to allow installing from your browser). In the app open Settings > **Sign in with a code** and enter your server address and a code from the dashboard.

## Accounts and devices

- Each PC, phone and server gets its **own key**; remove a device from the dashboard and its key stops working.
- Sign in with the link the installer prints, with an **email and password**, or with **Google**. Google sign-in works out of the box: it goes through the Willy project's server (truewilly.com), which only learns your name and email. (A hub can also use its own Firebase project: set the `FIREBASE_*` values.) Set `WILLY_SIGNUP=open` if friends and family should each get a private space on your hub.

## What the Willy project sees

- Signing in with Google records your name and email at truewilly.com. Email sign-in does not touch it.
- Unless you turn it off (`WILLY_TELEMETRY=off` in `server/.env`), your hub checks in once a day with: its address, version, platform, owner email, number of accounts and devices (PC/phone/server), the **name** of your AI provider, and per-day counts of commands and tool names.
- Never sent: commands, replies, files, conversations, API keys or tokens. The exact payload is built in `server/registry.py`.

## Running the master (the Willy project's own server)

Set `WILLY_MASTER=1` plus the `FIREBASE_*` values for the Google login, and open `/master` (owner only) to see sign-ins, hubs and usage.
- Settings live in `server/.env`; see `server/.env.example` for all of them. The accounts database is SQLite by default; set `WILLY_DATABASE_URL` to use MariaDB or PostgreSQL.

## Safety

Willy asks for a "yes" before it deletes, installs, restarts or runs commands that change your server, and it opens messages ready for you to press Send. It never stores your files or commands anywhere except your own server.

## Build from source

| Part | Folder | How |
| --- | --- | --- |
| Hub | `server/` | `python -m server.main` |
| Server agent | `server_agent/` | `python -m server_agent.agent` (after `python -m server_agent.pair --hub URL`) |
| Windows app | `pc_client/` | `pip install -r pc_client/requirements.txt && python build_pc_exe.py` |
| Android app | `mobile_app/` | `flutter build apk --release` |

Releases are built by GitHub Actions when you push a tag like `v1.0.0` (`.github/workflows/release.yml`). More notes for developers: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

Docker files (`Dockerfile`, `docker-compose.yml`) are included but still experimental.

## Licence

MIT, see [LICENSE](LICENSE).
