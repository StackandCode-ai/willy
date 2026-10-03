"""
Willy voice call page, served at /call (and /willy/call behind the proxy).

Hands-free conversation in the browser: the microphone is watched by a small voice-activity
detector, each utterance is recorded with MediaRecorder and posted to /api/v1/call/interact,
and Willy's spoken reply plays back. Talking over Willy interrupts him (barge-in).

The page looks like a phone call: a central avatar with an audio-reactive ring (Web Audio
AnalyserNodes on the mic and on a silent "shadow" copy of each reply, so the audible
<audio> path is untouched), a call timer, big round controls and a slide-in transcript/chat
drawer. Replies may carry "ui": {"panel": "files" | "screenshot" | "camera", ...}; the
matching panel (PC file browser, PC screenshot, browser camera) then slides open while the
call continues.

Like the dashboard, the page never embeds the access token; it reads it from the URL
fragment / legacy query / localStorage, validates it and asks for it otherwise.
The markup is a plain raw string (not an f-string), so CSS/JS braces and backslashes need
no escaping. It must never contain three double quotes in a row.
"""

WEB_CALL_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#05070F">
<meta name="referrer" content="no-referrer">
<title>Willy · Voice call</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 40 40'%3E%3Cdefs%3E%3ClinearGradient id='g' x1='0' y1='0' x2='1' y2='1'%3E%3Cstop offset='0' stop-color='%2300F2FE'/%3E%3Cstop offset='1' stop-color='%237C3AED'/%3E%3C/linearGradient%3E%3C/defs%3E%3Crect x='1' y='1' width='38' height='38' rx='11' fill='url(%23g)'/%3E%3Cpath d='M9.5 14.5l4.8 12 5.7-9.5 5.7 9.5 4.8-12' fill='none' stroke='%23070913' stroke-width='3.2' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E">
<style>
@property --tint { syntax: '<color>'; inherits: true; initial-value: rgba(100, 116, 139, .10); }
:root {
  --bg: #05070F;
  --glass: rgba(14, 19, 34, .78);
  --glass-2: rgba(20, 26, 44, .92);
  --well: rgba(255, 255, 255, .04);
  --line: rgba(255, 255, 255, .08);
  --line-2: rgba(255, 255, 255, .14);
  --cyan: #00F2FE;
  --purple: #7C3AED;
  --violet: #A78BFA;
  --green: #10B981;
  --red: #EF4444;
  --amber: #F59E0B;
  --text: #F8FAFC;
  --text-2: #CBD5E1;
  --muted: #94A3B8;
  --faint: #64748B;
  --ui: system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
  --mono: ui-monospace, SFMono-Regular, 'Cascadia Mono', Menlo, Consolas, monospace;
  --ease: cubic-bezier(.2, .8, .2, 1);
  --spring: cubic-bezier(.32, .72, 0, 1);
  --bar-h: 56px;
  --sheet-w: clamp(440px, 44vw, 720px);
  --drawer-w: 400px;
  --mini-h: 88px;
}
*, *::before, *::after { box-sizing: border-box; }
[hidden] { display: none !important; }
html { color-scheme: dark; -webkit-text-size-adjust: 100%; text-size-adjust: 100%; }
body { margin: 0; min-height: 100vh; background: var(--bg); color: var(--text); font: 400 14px/1.5 var(--ui); -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale; }
h1, h2, p, ol, ul { margin: 0; padding: 0; }
ol, ul { list-style: none; }
button, input, select, output { font: inherit; color: inherit; }
button { cursor: pointer; -webkit-tap-highlight-color: transparent; }
code { font-family: var(--mono); font-size: .88em; background: rgba(255, 255, 255, .07); border: 1px solid var(--line); border-radius: 6px; padding: 1px 5px; }
.sr-only { position: absolute !important; width: 1px !important; height: 1px !important; min-width: 0 !important; min-height: 0 !important; margin: -1px !important; padding: 0 !important; overflow: hidden !important; clip: rect(0 0 0 0); clip-path: inset(50%); white-space: nowrap; border: 0 !important; }
:focus-visible { outline: 2px solid var(--cyan); outline-offset: 2px; }
* { scrollbar-width: thin; scrollbar-color: rgba(148, 163, 184, .28) transparent; }
.i { width: 18px; height: 18px; flex: none; fill: none; stroke: currentColor; stroke-width: 1.9; stroke-linecap: round; stroke-linejoin: round; }
.sprite { position: absolute; width: 0; height: 0; overflow: hidden; }
.card { position: relative; min-width: 0; background: var(--glass); border: 1px solid var(--line); border-radius: 18px; box-shadow: inset 0 1px 0 rgba(255, 255, 255, .045), 0 26px 50px -34px rgba(0, 0, 0, .85); }
.grow { flex: 1; }

.btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 8px; min-height: 38px; padding: 0 14px; position: relative;
  border-radius: 11px; border: 1px solid var(--line-2); background: rgba(255, 255, 255, .05); color: var(--text);
  font: 600 13px/1 var(--ui); white-space: nowrap; text-decoration: none; transition: background .15s, border-color .15s, filter .15s, transform .1s;
}
.btn:hover:not(:disabled) { background: rgba(255, 255, 255, .09); border-color: rgba(255, 255, 255, .24); }
.btn:active:not(:disabled) { transform: translateY(1px); }
.btn:disabled { opacity: .42; cursor: not-allowed; }
.btn-primary { border: 0; color: #03141A; background: linear-gradient(135deg, var(--cyan), #6A7CF7 62%, var(--purple)); box-shadow: 0 8px 22px -12px rgba(0, 242, 254, .75); }
.btn-primary:hover:not(:disabled) { background: linear-gradient(135deg, var(--cyan), #6A7CF7 62%, var(--purple)); filter: brightness(1.1); }
.btn-danger { border: 0; color: #fff; background: linear-gradient(135deg, #EF4444, #B91C1C); }
.btn-danger:hover:not(:disabled) { background: linear-gradient(135deg, #EF4444, #B91C1C); filter: brightness(1.1); }
.btn-ghost { background: transparent; }
.btn-sm { min-height: 32px; padding: 0 11px; font-size: 12.5px; border-radius: 9px; gap: 6px; }
.btn-sm .i { width: 15px; height: 15px; }
.btn-block { width: 100%; }
.icon-btn { position: relative; display: inline-grid; place-items: center; width: 36px; height: 36px; padding: 0; flex: none; border-radius: 10px; border: 1px solid var(--line); background: rgba(255, 255, 255, .03); color: var(--text-2); transition: background .15s, color .15s, border-color .15s; }
.icon-btn:hover:not(:disabled) { background: rgba(255, 255, 255, .08); color: var(--text); border-color: var(--line-2); }
.icon-btn:disabled { opacity: .35; cursor: not-allowed; }
.icon-btn.sm { width: 30px; height: 30px; border-radius: 8px; }
.icon-btn.sm .i { width: 15px; height: 15px; }
.is-busy { pointer-events: none; }
.is-busy > .i, .is-busy > span, .is-busy > svg { opacity: .25; }
.is-busy::after { content: ''; position: absolute; left: 50%; top: 50%; width: 16px; height: 16px; margin: -8px 0 0 -8px; border-radius: 50%; border: 2px solid rgba(255, 255, 255, .2); border-top-color: var(--cyan); animation: spin .8s linear infinite; }
.btn-primary.is-busy::after { border-color: rgba(3, 20, 26, .25); border-top-color: #03141A; }
input[type=text], input[type=password], input[type=search], .select {
  width: 100%; min-width: 0; min-height: 40px; padding: 8px 12px; border-radius: 11px; border: 1px solid var(--line-2);
  background: rgba(3, 6, 15, .6); color: var(--text); font-size: 14px; outline: none; transition: border-color .15s, box-shadow .15s;
}
input:focus, .select:focus { border-color: rgba(0, 242, 254, .6); box-shadow: 0 0 0 3px rgba(0, 242, 254, .12); }
input::placeholder { color: #5B6B82; }

/* ---------------------------------------------------------------- boot + login */
.boot { min-height: 100vh; display: grid; place-content: center; justify-items: center; gap: 14px; color: var(--muted); font-size: 13px; }
.boot .mark { width: 46px; height: 46px; animation: breathe 1.6s ease-in-out infinite; }
.login { min-height: 100vh; display: grid; place-items: center; padding: 24px 16px; background: radial-gradient(700px 520px at 50% 30%, rgba(0, 242, 254, .08), transparent 65%), radial-gradient(700px 600px at 90% 110%, rgba(124, 58, 237, .14), transparent 62%); }
.login-card { width: min(400px, 100%); padding: 30px 28px 26px; display: grid; gap: 14px; text-align: center; }
.login-card .mark { width: 56px; height: 56px; margin: 0 auto 2px; filter: drop-shadow(0 10px 28px rgba(0, 242, 254, .35)); }
.login-title { font: 700 23px/1.2 var(--ui); letter-spacing: .01em; }
.login-hint { color: var(--muted); font-size: 13.5px; margin-top: -6px; }
.pw-wrap { position: relative; text-align: left; }
.pw-wrap input { height: 46px; padding-right: 46px; font-family: var(--mono); font-size: 15px; }
.pw-eye { position: absolute; right: 6px; top: 6px; width: 34px; height: 34px; border: 0; border-radius: 8px; background: transparent; color: var(--muted); display: grid; place-items: center; }
.pw-eye:hover { color: var(--text); background: rgba(255, 255, 255, .06); }
.login-card .btn { min-height: 44px; font-size: 14px; }
.login-err { color: #FECACA; font-size: 13px; text-align: left; background: rgba(239, 68, 68, .09); border: 1px solid rgba(239, 68, 68, .35); border-radius: 10px; padding: 9px 12px; }
.login-foot { font-size: 12px; color: var(--faint); }

/* ---------------------------------------------------------------- app frame + state palette */
.app {
  --c1: #64748B; --c2: #334155; --glow: rgba(148, 163, 184, .22); --tint: rgba(100, 116, 139, .10); --lvl: 0;
  position: relative; height: 100vh; height: 100dvh; display: flex; flex-direction: column; overflow: hidden; isolation: isolate;
  transition: --tint .8s ease;
}
.app[data-state=connecting] { --c1: #F59E0B; --c2: #64748B; --glow: rgba(245, 158, 11, .28); --tint: rgba(245, 158, 11, .08); }
.app[data-state=listening] { --c1: #00F2FE; --c2: #7C3AED; --glow: rgba(0, 242, 254, .34); --tint: rgba(0, 242, 254, .10); }
.app[data-state=hearing] { --c1: #34D399; --c2: #00F2FE; --glow: rgba(16, 185, 129, .42); --tint: rgba(16, 185, 129, .12); }
.app[data-state=thinking] { --c1: #7C3AED; --c2: #A78BFA; --glow: rgba(124, 58, 237, .45); --tint: rgba(124, 58, 237, .14); }
.app[data-state=speaking] { --c1: #A78BFA; --c2: #00F2FE; --glow: rgba(167, 139, 250, .45); --tint: rgba(167, 139, 250, .13); }
.app[data-state=muted] { --c1: #F87171; --c2: #475569; --glow: rgba(239, 68, 68, .26); --tint: rgba(239, 68, 68, .08); }
.app::before {
  content: ''; position: absolute; inset: 0; z-index: -1; pointer-events: none;
  background:
    radial-gradient(70vmax 55vmax at 50% 34%, var(--tint), transparent 62%),
    radial-gradient(60vmax 50vmax at 100% 115%, rgba(124, 58, 237, .13), transparent 60%),
    radial-gradient(50vmax 40vmax at 0% -10%, rgba(0, 242, 254, .06), transparent 60%),
    var(--bg);
}

.bar { flex: none; display: flex; align-items: center; gap: 10px; min-height: var(--bar-h); padding: env(safe-area-inset-top, 0px) 16px 0; z-index: 3; }
.back { display: inline-flex; align-items: center; gap: 8px; height: 36px; padding: 0 12px 0 8px; border-radius: 11px; border: 1px solid var(--line); background: rgba(255, 255, 255, .03); color: var(--text-2); text-decoration: none; font-weight: 600; font-size: 13px; flex: none; transition: background .15s, color .15s; }
.back:hover { background: rgba(255, 255, 255, .07); color: var(--text); }
.bar-brand { display: flex; align-items: center; gap: 8px; margin-right: auto; min-width: 0; color: var(--muted); font-size: 12.5px; font-weight: 600; letter-spacing: .02em; }
.bar-brand .enc { width: 14px; height: 14px; color: var(--green); }
.pills { display: flex; align-items: center; gap: 6px; min-width: 0; }
.pill { display: inline-flex; align-items: center; gap: 7px; height: 30px; padding: 0 11px; border-radius: 999px; border: 1px solid var(--line-2); background: rgba(255, 255, 255, .03); font: 600 12px/1 var(--ui); color: var(--text-2); white-space: nowrap; }
.pill .i { width: 15px; height: 15px; }
.pill-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--faint); flex: none; }
.pill[data-state=ok], .pill[data-state=on] { color: #A7F3D0; border-color: rgba(16, 185, 129, .38); background: rgba(16, 185, 129, .08); }
.pill[data-state=ok] .pill-dot { background: var(--green); animation: live 2.2s ease-out infinite; }
.pill[data-state=down], .pill[data-state=off] { color: #FECACA; border-color: rgba(239, 68, 68, .42); background: rgba(239, 68, 68, .08); }
.pill[data-state=down] .pill-dot { background: var(--red); }
.pill[data-state=checking] .pill-dot { background: var(--amber); animation: blink 1s ease-in-out infinite; }

.main { position: relative; flex: 1; min-height: 0; display: flex; z-index: 1; }

/* ---------------------------------------------------------------- stage */
.stage { flex: 1; min-width: 0; min-height: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px; padding: 8px 16px 12px; text-align: center; overflow: hidden; }
.avatar { --size: clamp(170px, min(62vw, 40dvh), 300px); position: relative; width: var(--size); height: var(--size); flex: none; display: grid; place-items: center; transition: width .35s var(--spring), height .35s var(--spring); }
.avatar::before { content: ''; position: absolute; inset: 12%; border-radius: 50%; background: radial-gradient(circle, var(--glow) 0%, transparent 68%); transform: scale(calc(.92 + var(--lvl) * .32)); opacity: calc(.55 + var(--lvl) * .45); transition: transform .12s linear, opacity .12s linear, background .5s; pointer-events: none; }
.ring { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }
.orb {
  position: absolute; inset: 24%; border-radius: 50%; border: 0; padding: 0; display: grid; place-items: center;
  background: radial-gradient(circle at 32% 26%, rgba(255, 255, 255, .22), transparent 42%), linear-gradient(140deg, var(--c1), var(--c2));
  box-shadow: 0 0 calc(18px + var(--lvl) * 50px) var(--glow), inset 0 1px 0 rgba(255, 255, 255, .3), inset 0 -12px 26px rgba(0, 0, 0, .25);
  transition: box-shadow .2s linear, transform .15s var(--ease), background .5s;
}
.orb:hover { transform: scale(1.025); }
.orb:active { transform: scale(.97); }
.orb .mark { width: 46%; height: 46%; filter: drop-shadow(0 4px 12px rgba(0, 0, 0, .35)); }
.id-block { display: grid; justify-items: center; gap: 6px; min-width: 0; }
.name { font: 700 30px/1.1 var(--ui); letter-spacing: -.01em; }
.state-line { display: inline-flex; align-items: center; gap: 8px; min-height: 22px; font-size: 15px; font-weight: 600; color: var(--text-2); white-space: nowrap; }
.state-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--c1); flex: none; transition: background .4s; }
.app.in-call .state-dot { animation: pulse 1.8s ease-out infinite; }
.timer { font: 600 13px/1 var(--mono); color: var(--muted); font-variant-numeric: tabular-nums; padding-left: 8px; border-left: 1px solid var(--line-2); }
.hint { max-width: 360px; color: var(--muted); font-size: 13.5px; }
.lvl { position: relative; width: 132px; height: 4px; border-radius: 2px; background: rgba(255, 255, 255, .08); }
.app:not(.in-call) .lvl { visibility: hidden; }
.lvl i { position: absolute; inset: 0; border-radius: 2px; background: linear-gradient(90deg, var(--green), var(--cyan)); transform-origin: left center; transform: scaleX(0); transition: transform .06s linear; }
.lvl b { position: absolute; top: -3px; bottom: -3px; left: 22%; width: 2px; border-radius: 1px; background: rgba(248, 250, 252, .7); transition: left .3s var(--ease); }
.stop-chip { display: inline-flex; align-items: center; gap: 8px; height: 36px; padding: 0 16px 0 12px; border-radius: 999px; border: 1px solid rgba(245, 158, 11, .5); background: rgba(245, 158, 11, .12); color: #FCD34D; font-weight: 700; font-size: 13px; animation: rise .22s var(--ease); transition: background .15s; }
.stop-chip:hover { background: rgba(245, 158, 11, .2); }
.stop-chip .i { width: 14px; height: 14px; }
.caption { width: min(560px, 100%); padding: 12px 16px; border-radius: 16px; background: rgba(255, 255, 255, .045); border: 1px solid var(--line); text-align: left; animation: rise .3s var(--ease); backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px); }
.cap-you { font-size: 12.5px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cap-you:empty { display: none; }
.cap-text { margin-top: 3px; font-size: 14.5px; line-height: 1.5; color: var(--text); display: -webkit-box; -webkit-line-clamp: 3; line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; overflow-wrap: anywhere; }
.caption.is-sys .cap-text { color: var(--muted); font-style: italic; }
.caption.is-error .cap-text { color: #FCA5A5; }
.cap-more { margin-top: 6px; padding: 0; border: 0; background: none; color: var(--cyan); font-weight: 600; font-size: 12.5px; }
.cap-more:hover { text-decoration: underline; }
.notice { max-width: 400px; font-size: 13px; color: #FDE68A; background: rgba(245, 158, 11, .08); border: 1px solid rgba(245, 158, 11, .35); border-radius: 12px; padding: 9px 12px; text-align: left; }

/* ---------------------------------------------------------------- dock */
.dock { position: relative; z-index: 4; flex: none; display: flex; justify-content: center; align-items: flex-start; gap: clamp(10px, 2.6vw, 22px); padding: 10px 16px calc(14px + env(safe-area-inset-bottom, 0px)); }
.ctl { position: relative; display: grid; justify-items: center; gap: 6px; min-width: 56px; font-size: 11.5px; font-weight: 600; color: var(--muted); }
.round {
  position: relative; width: 58px; height: 58px; border-radius: 50%; display: grid; place-items: center; padding: 0;
  border: 1px solid var(--line-2); background: rgba(255, 255, 255, .07); color: var(--text);
  backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px);
  transition: background .18s, border-color .18s, transform .12s, opacity .2s, box-shadow .2s, color .18s;
}
.round .i { width: 24px; height: 24px; }
.round:hover:not(:disabled) { background: rgba(255, 255, 255, .13); }
.round:active:not(:disabled) { transform: scale(.93); }
.round:disabled { opacity: .35; cursor: not-allowed; }
.round.is-on, .round.is-on:hover:not(:disabled) { background: rgba(248, 250, 252, .92); color: #0B1020; border-color: transparent; }
.round-mute[aria-pressed=true], .round-mute[aria-pressed=true]:hover:not(:disabled) { background: rgba(239, 68, 68, .18); color: #FCA5A5; border-color: rgba(239, 68, 68, .55); }
.round-call { width: 70px; height: 70px; border: 0; color: #fff; background: linear-gradient(135deg, #22C55E, #059669); box-shadow: 0 12px 32px -12px rgba(16, 185, 129, .9); }
.round-call:hover:not(:disabled) { background: linear-gradient(135deg, #22C55E, #059669); filter: brightness(1.08); }
.round-call .i { width: 28px; height: 28px; transition: transform .3s var(--spring); }
.round-call.is-end { background: linear-gradient(135deg, #EF4444, #B91C1C); box-shadow: 0 12px 32px -12px rgba(239, 68, 68, .9); }
.round-call.is-end:hover:not(:disabled) { background: linear-gradient(135deg, #EF4444, #B91C1C); }
.round-call.is-end .i { transform: rotate(135deg); }
.round.sm { width: 46px; height: 46px; }
.round.sm .i { width: 20px; height: 20px; }
.badge { position: absolute; top: -2px; right: -2px; min-width: 20px; height: 20px; padding: 0 5px; border-radius: 10px; background: var(--cyan); color: #03141A; font: 800 11px/20px var(--ui); text-align: center; box-shadow: 0 0 0 2px var(--bg); animation: pop .25s var(--spring); }
.pop { position: absolute; left: 0; bottom: calc(100% + 12px); z-index: 20; display: flex; align-items: center; gap: 10px; width: 250px; padding: 10px 12px; border-radius: 16px; background: var(--glass-2); border: 1px solid var(--line-2); box-shadow: 0 24px 50px -16px rgba(0, 0, 0, .8); animation: rise .2s var(--ease); backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px); }
.pop input[type=range] { flex: 1; min-width: 0; accent-color: var(--cyan); height: 28px; }
.pop output { width: 40px; text-align: right; font: 600 12px/1 var(--mono); color: var(--text-2); }
.icon-btn[aria-pressed=true] { background: rgba(239, 68, 68, .15); color: #FCA5A5; border-color: rgba(239, 68, 68, .45); }

/* ---------------------------------------------------------------- side panels (sheet = tools, drawer = transcript) */
.sheet, .drawer { position: relative; z-index: 2; flex: none; width: 0; min-height: 0; overflow: hidden; visibility: hidden; transition: width .36s var(--spring), transform .38s var(--spring), visibility 0s linear .38s; }
.sheet.is-open, .drawer.is-open { visibility: visible; transition: width .36s var(--spring), transform .38s var(--spring), visibility 0s; }
.sheet.is-open { width: var(--sheet-w); }
.drawer.is-open { width: var(--drawer-w); }
.sheet-in, .drawer-in { height: 100%; }
.sheet-in { width: var(--sheet-w); padding: 4px 0 8px 16px; }
.drawer-in { width: var(--drawer-w); padding: 4px 16px 8px 0; }
.panel-card { height: 100%; display: flex; flex-direction: column; min-height: 0; background: var(--glass); border: 1px solid var(--line); border-radius: 22px; box-shadow: inset 0 1px 0 rgba(255, 255, 255, .05), 0 30px 60px -30px rgba(0, 0, 0, .9); backdrop-filter: blur(20px) saturate(140%); -webkit-backdrop-filter: blur(20px) saturate(140%); overflow: hidden; container-type: inline-size; }
.p-head { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 10px 8px; padding: 14px 14px 10px 18px; border-bottom: 1px solid var(--line); position: relative; }
.grab { display: none; }
.p-titles { min-width: 0; }
.p-title { font: 700 17px/1.2 var(--ui); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.p-sub { margin-top: 2px; font-size: 12px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.p-actions { display: flex; align-items: center; gap: 6px; }
.tabs { grid-column: 1 / -1; display: flex; gap: 4px; padding: 3px; border-radius: 12px; background: rgba(3, 6, 15, .55); border: 1px solid var(--line); }
.tab { flex: 1; display: inline-flex; align-items: center; justify-content: center; gap: 7px; height: 34px; border: 0; border-radius: 9px; background: transparent; color: var(--muted); font-weight: 650; font-size: 13px; transition: background .18s, color .18s; }
.tab .i { width: 16px; height: 16px; }
.tab:hover { color: var(--text); }
.tab[aria-selected=true] { background: rgba(255, 255, 255, .1); color: var(--text); box-shadow: inset 0 1px 0 rgba(255, 255, 255, .08); }
.pane { position: relative; flex: 1; min-height: 0; display: flex; flex-direction: column; }
.pane.is-loading::before { content: ''; position: absolute; left: 0; right: 0; top: 0; height: 2px; z-index: 3; background: linear-gradient(90deg, transparent, var(--cyan), transparent); background-size: 40% 100%; background-repeat: no-repeat; animation: sweep 1s linear infinite; }
.p-state { margin: auto; padding: 28px 20px; max-width: 320px; display: grid; justify-items: center; gap: 8px; text-align: center; color: var(--muted); font-size: 13.5px; }
.p-state .ico { width: 52px; height: 52px; border-radius: 16px; display: grid; place-items: center; background: rgba(255, 255, 255, .05); border: 1px solid var(--line); color: var(--text-2); margin-bottom: 4px; }
.p-state .ico .i { width: 24px; height: 24px; }
.p-state b { color: var(--text); font-size: 15px; }
.p-state .btn { margin-top: 6px; }

/* files */
.f-top { display: flex; align-items: center; gap: 6px; padding: 10px 12px 0; }
.crumbs { flex: 1; min-width: 0; display: flex; align-items: center; gap: 2px; overflow-x: auto; scrollbar-width: none; white-space: nowrap; mask-image: linear-gradient(90deg, transparent 0, #000 14px); -webkit-mask-image: linear-gradient(90deg, transparent 0, #000 14px); padding-left: 6px; }
.crumbs::-webkit-scrollbar { display: none; }
.crumb { display: inline-flex; align-items: center; gap: 6px; height: 30px; padding: 0 8px; border: 0; border-radius: 8px; background: transparent; color: var(--muted); font-weight: 600; font-size: 13px; flex: none; }
.crumb .i { width: 15px; height: 15px; }
button.crumb:hover { background: rgba(255, 255, 255, .07); color: var(--text); }
.crumb.is-here { color: var(--text); }
.crumb-sep { display: inline-grid; color: var(--faint); flex: none; }
.crumb-sep .i { width: 13px; height: 13px; }
.chips { display: flex; gap: 6px; padding: 10px 12px 0; overflow-x: auto; scrollbar-width: none; }
.chips::-webkit-scrollbar { display: none; }
.chip { --use: 0; position: relative; display: inline-flex; align-items: center; gap: 6px; height: 34px; padding: 0 11px; border-radius: 10px; border: 1px solid var(--line-2); background: rgba(255, 255, 255, .04); color: var(--text-2); font-size: 12.5px; white-space: nowrap; flex: none; overflow: hidden; transition: background .15s, border-color .15s; }
.chip::after { content: ''; position: absolute; left: 0; bottom: 0; height: 2px; width: calc(var(--use) * 100%); background: linear-gradient(90deg, var(--cyan), var(--purple)); opacity: .7; }
.chip .i { width: 15px; height: 15px; }
.chip b { font-weight: 700; color: var(--text); }
.chip small { color: var(--faint); font-size: 11px; }
.chip:hover { background: rgba(255, 255, 255, .08); }
.chip.is-on { border-color: rgba(0, 242, 254, .5); background: rgba(0, 242, 254, .08); }
.f-tools { display: flex; align-items: center; gap: 6px; padding: 10px 12px; }
.search { position: relative; flex: 1; min-width: 0; }
.search .i { position: absolute; left: 11px; top: 50%; margin-top: -8px; width: 16px; height: 16px; color: var(--faint); pointer-events: none; }
.search input { padding-left: 34px; min-height: 36px; font-size: 13.5px; border-radius: 10px; }
.select { width: auto; min-height: 36px; padding: 0 8px; font-size: 13px; border-radius: 10px; }
.f-tools .btn { min-height: 36px; }
.f-pick { margin: 0 12px 10px; padding: 10px 12px; border-radius: 12px; border: 1px solid rgba(0, 242, 254, .35); background: rgba(0, 242, 254, .07); display: flex; align-items: center; gap: 10px; flex-wrap: wrap; font-size: 13px; color: var(--text-2); animation: rise .2s var(--ease); }
.f-pick p { flex: 1 1 200px; min-width: 0; }
.f-pick-btns { display: flex; gap: 6px; margin-left: auto; }
.f-scroll { flex: 1; min-height: 0; overflow-y: auto; overscroll-behavior: contain; padding: 0 8px 10px; display: flex; flex-direction: column; }
.f-list { display: grid; gap: 2px; }
.f-row { display: grid; grid-template-columns: 34px minmax(0, 1fr); align-items: center; border-radius: 12px; transition: background .12s; }
.f-row:hover { background: rgba(255, 255, 255, .04); }
.f-row.is-sel { background: rgba(0, 242, 254, .09); box-shadow: inset 0 0 0 1px rgba(0, 242, 254, .28); }
.f-row.is-dim { opacity: .4; }
.f-check { justify-self: center; width: 17px; height: 17px; margin: 0; accent-color: var(--cyan); cursor: pointer; }
.f-main { display: grid; grid-template-columns: 36px minmax(0, 1fr); grid-template-rows: auto auto; column-gap: 10px; align-items: center; min-height: 52px; padding: 7px 10px 7px 0; border: 0; background: none; text-align: left; border-radius: 12px; }
.f-main:disabled { cursor: default; }
.f-ico { grid-row: 1 / 3; width: 36px; height: 36px; border-radius: 10px; display: grid; place-items: center; background: rgba(255, 255, 255, .05); color: var(--text-2); }
.f-ico .i { width: 19px; height: 19px; }
.t-folder { color: #FBBF24; background: rgba(251, 191, 36, .12); }
.t-image { color: #F472B6; background: rgba(244, 114, 182, .12); }
.t-video { color: #F87171; background: rgba(248, 113, 113, .12); }
.t-audio { color: #34D399; background: rgba(52, 211, 153, .12); }
.t-pdf { color: #FB7185; background: rgba(251, 113, 133, .12); }
.t-doc { color: #60A5FA; background: rgba(96, 165, 250, .12); }
.t-sheet { color: #4ADE80; background: rgba(74, 222, 128, .12); }
.t-slides { color: #FB923C; background: rgba(251, 146, 60, .12); }
.t-archive { color: #C4B5FD; background: rgba(196, 181, 253, .12); }
.t-code { color: #22D3EE; background: rgba(34, 211, 238, .12); }
.t-app { color: #CBD5E1; background: rgba(203, 213, 225, .1); }
.f-name { font-size: 14px; font-weight: 600; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.f-meta { font-size: 12px; color: var(--faint); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-variant-numeric: tabular-nums; }
.f-row.is-edit { grid-template-columns: minmax(0, 1fr); padding: 6px 8px 6px 34px; background: rgba(255, 255, 255, .05); }
.f-rename { display: flex; align-items: center; gap: 8px; }
.f-rename .f-ico { width: 32px; height: 32px; }
.f-rename input { min-height: 36px; font-size: 14px; }
.f-more-note { padding: 10px; text-align: center; font-size: 12px; color: var(--faint); }
.selbar { display: flex; align-items: center; gap: 8px; padding: 10px 12px calc(10px + env(safe-area-inset-bottom, 0px)); border-top: 1px solid var(--line); background: rgba(8, 12, 24, .7); animation: rise .2s var(--ease); }
.sel-count { flex: 1 1 auto; min-width: 0; font-weight: 700; font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sel-acts { display: flex; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
.sel-acts .btn { min-height: 34px; padding: 0 10px; }
.btn.danger { color: #FCA5A5; border-color: rgba(239, 68, 68, .4); }
.btn.danger:hover:not(:disabled) { background: rgba(239, 68, 68, .14); border-color: rgba(239, 68, 68, .6); }
@container (max-width: 560px) {
  .sel-acts .lbl, .f-tools .lbl, .x-tools .lbl { display: none; }
  .sel-acts .btn, .f-tools .btn, .x-tools .btn.ic { padding: 0; width: 36px; }
  .sel-acts { flex-wrap: nowrap; }
}

/* screenshot */
.x-tools { display: flex; align-items: center; gap: 8px; padding: 10px 12px; flex-wrap: wrap; }
.x-time { font: 500 11.5px/1 var(--mono); color: var(--faint); white-space: nowrap; }
.toggle { display: inline-flex; align-items: center; gap: 8px; font-size: 12.5px; font-weight: 600; color: var(--text-2); cursor: pointer; user-select: none; }
.toggle input { position: absolute; opacity: 0; width: 1px; height: 1px; }
.tg { position: relative; width: 34px; height: 20px; border-radius: 10px; background: rgba(255, 255, 255, .14); transition: background .2s; flex: none; }
.tg::after { content: ''; position: absolute; left: 3px; top: 3px; width: 14px; height: 14px; border-radius: 50%; background: #fff; transition: transform .2s var(--spring); }
.toggle input:checked + .tg { background: var(--green); }
.toggle input:checked + .tg::after { transform: translateX(14px); }
.toggle input:focus-visible + .tg { outline: 2px solid var(--cyan); outline-offset: 2px; }
.x-view { position: relative; flex: 1; min-height: 0; margin: 0 12px 12px; border-radius: 14px; background: #02040A; border: 1px solid var(--line); overflow: auto; display: flex; }
.x-zoom { margin: auto; padding: 0; border: 0; background: none; cursor: zoom-in; display: block; line-height: 0; }
.x-zoom img { display: block; max-width: 100%; max-height: 100%; object-fit: contain; }
.x-view:not(.is-zoom) .x-zoom { width: 100%; height: 100%; }
.x-view:not(.is-zoom) .x-zoom img { width: 100%; height: 100%; }
.x-view.is-zoom .x-zoom { cursor: zoom-out; margin: 0; }
.x-view.is-zoom .x-zoom img { max-width: none; max-height: none; }
.spinner { position: absolute; left: 50%; top: 50%; width: 34px; height: 34px; margin: -17px 0 0 -17px; border-radius: 50%; border: 3px solid rgba(255, 255, 255, .12); border-top-color: var(--cyan); animation: spin .8s linear infinite; }

/* camera */
.k-view { position: relative; flex: 1; min-height: 0; margin: 12px 12px 0; border-radius: 16px; overflow: hidden; background: #000; display: grid; place-items: center; }
.k-view video, .k-view img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; }
.k-view video.is-mirror { transform: scaleX(-1); }
.k-view .p-state { position: relative; z-index: 1; }
.k-flash { position: absolute; inset: 0; background: #fff; opacity: 0; pointer-events: none; }
.k-flash.go { animation: flash .35s ease-out; }
.k-bar { display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: 12px; padding: 14px 16px calc(14px + env(safe-area-inset-bottom, 0px)); }
.k-side { display: flex; justify-content: flex-end; }
.k-side:first-child { justify-content: flex-start; }
.shutter { width: 72px; height: 72px; border-radius: 50%; border: 4px solid #fff; background: transparent; padding: 5px; transition: transform .12s, opacity .2s; }
.shutter span { display: block; width: 100%; height: 100%; border-radius: 50%; background: #fff; transition: transform .12s; }
.shutter:hover:not(:disabled) span { transform: scale(.94); }
.shutter:active:not(:disabled) { transform: scale(.92); }
.shutter:disabled { opacity: .35; cursor: not-allowed; }
.k-review { display: flex; justify-content: center; flex-wrap: wrap; gap: 8px; }

/* transcript */
.d-head { grid-template-columns: minmax(0, 1fr) auto; }
.turns { flex: 1; min-height: 0; overflow-y: auto; overscroll-behavior: contain; padding: 16px; display: flex; flex-direction: column; gap: 16px; }
.turns-empty { margin: auto; max-width: 300px; text-align: center; color: var(--muted); font-size: 13.5px; }
.turns-empty b { display: block; color: var(--text); font-size: 15px; margin-bottom: 4px; }
.turn { display: flex; flex-direction: column; gap: 10px; animation: rise .28s var(--ease); }
.who { display: block; margin-bottom: 3px; font: 700 10px/1 var(--ui); letter-spacing: .12em; text-transform: uppercase; }
.b-you { align-self: flex-end; max-width: 88%; padding: 10px 14px; border-radius: 18px 18px 6px 18px; background: linear-gradient(135deg, rgba(0, 242, 254, .16), rgba(124, 58, 237, .22)); border: 1px solid rgba(0, 242, 254, .24); }
.b-you .who { color: var(--cyan); }
.b-you .typed { margin-left: 6px; font: 500 10px/1 var(--mono); color: var(--muted); letter-spacing: 0; text-transform: none; }
.b-willy { align-self: flex-start; max-width: 96%; padding: 10px 14px 12px; border-radius: 18px 18px 18px 6px; background: rgba(255, 255, 255, .05); border: 1px solid var(--line); }
.b-willy .who { color: var(--violet); }
.b-willy.is-error { border-color: rgba(239, 68, 68, .4); background: rgba(239, 68, 68, .08); }
.b-text { font-size: 14.5px; line-height: 1.55; white-space: pre-wrap; overflow-wrap: anywhere; }
.md { font-size: 14.5px; line-height: 1.55; overflow-wrap: anywhere; }
.md p + p, .md p + ul, .md p + ol, .md ul + p, .md ol + p, .md pre, .md .md-h { margin-top: 8px; }
.md > :first-child { margin-top: 0; }
.md ul { list-style: disc; padding-left: 20px; margin-top: 4px; }
.md ol { list-style: decimal; padding-left: 22px; margin-top: 4px; }
.md li + li { margin-top: 3px; }
.md li::marker { color: var(--violet); }
.md strong { font-weight: 700; color: #fff; }
.md .md-h { font-weight: 700; color: #fff; }
.md a { color: var(--cyan); text-decoration: underline; text-underline-offset: 2px; }
.md pre { padding: 10px 12px; border-radius: 10px; background: rgba(3, 6, 15, .7); border: 1px solid var(--line); overflow-x: auto; }
.md pre code { background: none; border: 0; padding: 0; font-size: 12.5px; white-space: pre; }
.md hr { border: 0; border-top: 1px solid var(--line-2); margin: 10px 0; }
.t-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 9px; }
.t-break { margin-top: 6px; font: 500 11px/1.5 var(--mono); color: var(--faint); }
.t-break span { white-space: nowrap; }
.sys { align-self: center; max-width: 92%; padding: 5px 12px; border-radius: 999px; border: 1px dashed var(--line-2); color: var(--muted); font-size: 12.5px; text-align: center; animation: rise .28s var(--ease); }
.fast { display: inline-flex; align-items: center; gap: 3px; height: 20px; padding: 0 7px 0 5px; border-radius: 6px; background: rgba(0, 242, 254, .1); border: 1px solid rgba(0, 242, 254, .32); color: var(--cyan); font: 700 10.5px/1 var(--ui); white-space: nowrap; }
.fast .i { width: 12px; height: 12px; }
.tool { display: inline-block; height: 20px; padding: 0 7px; border-radius: 6px; background: rgba(245, 158, 11, .08); border: 1px solid rgba(245, 158, 11, .28); color: #FCD34D; font: 500 10.5px/18px var(--mono); white-space: nowrap; max-width: 100%; overflow: hidden; text-overflow: ellipsis; }
.tool.is-fail { background: rgba(239, 68, 68, .1); border-color: rgba(239, 68, 68, .38); color: #FCA5A5; }
.lat { font: 600 11px/1 var(--mono); color: var(--text-2); white-space: nowrap; }
.ribbon {
  position: relative; display: inline-flex; width: 76px; height: 7px; flex: none; border-radius: 4px; overflow: hidden;
  background:
    linear-gradient(90deg, transparent calc(25% - 1px), rgba(255, 255, 255, .16) calc(25% - 1px) 25%, transparent 25%) 0 0 / 100% 100%,
    linear-gradient(90deg, transparent calc(50% - 1px), rgba(255, 255, 255, .16) calc(50% - 1px) 50%, transparent 50%) 0 0 / 100% 100%,
    linear-gradient(90deg, transparent calc(75% - 1px), rgba(255, 255, 255, .16) calc(75% - 1px) 75%, transparent 75%) 0 0 / 100% 100%,
    rgba(255, 255, 255, .07);
}
.rb-fill { display: flex; height: 100%; min-width: 3px; border-radius: 4px; overflow: hidden; }
.rb-fill i { display: block; height: 100%; flex: 1 1 0; min-width: 0; }
.rb-stt { background: var(--cyan); } .rb-llm { background: var(--violet); } .rb-tool { background: var(--amber); } .rb-tts { background: var(--green); } .rb-other { background: #94A3B8; }
.ribbon.is-over::after { content: ''; position: absolute; right: 0; top: 0; bottom: 0; width: 7px; background: repeating-linear-gradient(90deg, rgba(7, 9, 19, .85) 0 1px, transparent 1px 2px); }
.type { display: flex; align-items: center; gap: 8px; margin: 0; padding: 12px; border-top: 1px solid var(--line); }
.type input { flex: 1; border-radius: 14px; min-height: 44px; font-size: 15px; }
.type-send { width: 46px; height: 44px; min-height: 44px; padding: 0; flex: none; border-radius: 14px; }

/* confirm dialog */
.dialog { width: min(420px, calc(100vw - 32px)); padding: 0; border: 1px solid var(--line-2); border-radius: 20px; background: #0D1220; color: var(--text); box-shadow: 0 40px 80px -20px rgba(0, 0, 0, .9); }
.dialog::backdrop { background: rgba(2, 4, 10, .66); backdrop-filter: blur(4px); -webkit-backdrop-filter: blur(4px); }
.dialog[open] { animation: rise .22s var(--ease); }
.dlg-body { padding: 22px 22px 18px; display: grid; gap: 10px; }
.dlg-ico { width: 44px; height: 44px; border-radius: 14px; display: grid; place-items: center; background: rgba(239, 68, 68, .12); color: #FCA5A5; }
.dlg-ico .i { width: 22px; height: 22px; }
.dlg-body h2 { font: 700 17px/1.3 var(--ui); overflow-wrap: anywhere; }
.dlg-body p { color: var(--text-2); font-size: 13.5px; }
.dlg-list { display: grid; gap: 3px; max-height: 150px; overflow: auto; font: 500 12.5px/1.4 var(--mono); color: var(--muted); }
.dlg-list:empty { display: none; }
.dlg-list li { overflow-wrap: anywhere; }
.dlg-foot { display: flex; justify-content: flex-end; gap: 8px; padding: 0 22px 20px; }

/* toasts */
.toasts { position: fixed; z-index: 1000; right: 20px; bottom: 112px; width: min(360px, calc(100vw - 32px)); display: flex; flex-direction: column; gap: 10px; pointer-events: none; }
.toast { --tc: var(--cyan); position: relative; overflow: hidden; pointer-events: auto; display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 10px; align-items: start; padding: 12px 10px 12px 15px; border-radius: 14px; background: #0D1220; border: 1px solid var(--line-2); box-shadow: 0 22px 44px -12px rgba(0, 0, 0, .75); animation: rise .26s var(--ease); font-size: 13px; color: var(--text-2); overflow-wrap: anywhere; }
.toast::before { content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 3px; background: var(--tc); }
.toast-success { --tc: var(--green); } .toast-error { --tc: var(--red); } .toast-warn { --tc: var(--amber); }
.toast-x { width: 24px; height: 24px; border: 0; border-radius: 7px; background: transparent; color: var(--faint); display: grid; place-items: center; }
.toast-x .i { width: 14px; height: 14px; }

@keyframes spin { to { transform: rotate(360deg); } }
@keyframes breathe { 0%, 100% { transform: scale(.95); opacity: .8; } 50% { transform: scale(1.05); opacity: 1; } }
@keyframes live { 0% { box-shadow: 0 0 0 0 rgba(16, 185, 129, .65); } 70%, 100% { box-shadow: 0 0 0 7px rgba(16, 185, 129, 0); } }
@keyframes pulse { 0% { box-shadow: 0 0 0 0 var(--glow); } 70%, 100% { box-shadow: 0 0 0 8px transparent; } }
@keyframes blink { 50% { opacity: .35; } }
@keyframes rise { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
@keyframes pop { from { transform: scale(.4); } to { transform: none; } }
@keyframes sweep { from { background-position: -40% 0; } to { background-position: 140% 0; } }
@keyframes flash { 0% { opacity: .85; } 100% { opacity: 0; } }

/* desktop: shrink the avatar when a panel is open; the transcript overlays when space is short */
@media (min-width: 900px) {
  .app.sheet-open .avatar { --size: clamp(150px, min(20vw, 34dvh), 240px); }
  .app.sheet-open .hint { display: none; }
}
@media (min-width: 900px) and (max-width: 1279px) {
  .app.sheet-open .drawer { position: absolute; right: 0; top: 0; bottom: 0; z-index: 5; }
  .app.sheet-open .drawer .panel-card { box-shadow: 0 30px 80px -10px rgba(0, 0, 0, .95); }
}

/* phones and small tablets: panels are bottom sheets above the dock, the call shrinks to a strip */
@media (max-width: 899px) {
  .bar-brand { display: none; }
  .pills { margin-left: auto; }
  .sheet, .drawer { position: absolute; left: 0; right: 0; bottom: 0; top: var(--mini-h); width: auto; z-index: 5; transform: translateY(calc(100% + 30px)); }
  .sheet.is-open, .drawer.is-open { width: auto; transform: none; }
  .sheet-in, .drawer-in { width: auto; padding: 0; }
  .panel-card { border-radius: 24px 24px 0 0; border-bottom: 0; background: var(--glass-2); }
  .grab { display: block; position: absolute; left: 50%; top: 6px; width: 38px; height: 4px; margin-left: -19px; border-radius: 2px; background: rgba(255, 255, 255, .2); }
  .p-head { padding-top: 16px; }
  .app.sheet-open .stage, .app.drawer-open .stage { flex-direction: row; justify-content: flex-start; align-items: flex-start; gap: 12px; padding: 8px 16px; text-align: left; }
  .app.sheet-open .avatar, .app.drawer-open .avatar { --size: 70px; }
  .app.sheet-open .id-block, .app.drawer-open .id-block { justify-items: start; gap: 2px; padding-top: 10px; }
  .app.sheet-open .name, .app.drawer-open .name { font-size: 19px; }
  .app.sheet-open .state-line, .app.drawer-open .state-line { font-size: 13px; }
  .app.sheet-open .stop-chip, .app.drawer-open .stop-chip { margin: 16px 0 0 auto; height: 34px; padding: 0 12px 0 10px; font-size: 12px; }
  .app.sheet-open .hint, .app.drawer-open .hint,
  .app.sheet-open .lvl, .app.drawer-open .lvl,
  .app.sheet-open .caption, .app.drawer-open .caption,
  .app.sheet-open .notice, .app.drawer-open .notice { display: none; }
  .app.in-call .hint { display: none; }
  .app.typing .dock { display: none; }
  .type { padding-bottom: calc(12px + env(safe-area-inset-bottom, 0px)); }
  .toasts { left: 16px; right: 16px; width: auto; bottom: calc(108px + env(safe-area-inset-bottom, 0px)); }
}
@media (max-width: 899px) and (max-height: 640px) {
  .caption .cap-text { -webkit-line-clamp: 2; line-clamp: 2; }
  .stage { gap: 8px; }
}
@media (max-width: 480px) {
  .bar { gap: 8px; }
  .back { padding: 0 9px; }
  .back span { display: none; }
  .pill { padding: 0 9px; font-size: 11.5px; }
  .pill .pill-label-long { display: none; }
  .dock { gap: 8px; padding-left: 10px; padding-right: 10px; }
  .ctl { min-width: 0; font-size: 11px; }
  .round { width: 52px; height: 52px; }
  .round .i { width: 22px; height: 22px; }
  .round-call { width: 64px; height: 64px; }
  .name { font-size: 26px; }
  input[type=text], input[type=password], input[type=search], .type input { font-size: 16px; }
  .f-tools .select { max-width: 92px; }
  .pop { width: 220px; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: .001ms !important; animation-iteration-count: 1 !important; transition-duration: .001ms !important; transition-delay: 0s !important; scroll-behavior: auto !important; }
}
</style>
</head>
<body>
<svg class="sprite" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false">
  <defs>
    <linearGradient id="lg-brand" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#00F2FE"/><stop offset="1" stop-color="#7C3AED"/></linearGradient>
  </defs>
  <symbol id="i-logo" viewBox="0 0 40 40"><rect x="1" y="1" width="38" height="38" rx="11" fill="url(#lg-brand)" stroke="none"/><path d="M9.5 14.5l4.8 12 5.7-9.5 5.7 9.5 4.8-12" fill="none" stroke="#070913" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"/></symbol>
  <symbol id="i-call" viewBox="0 0 24 24"><path d="M21.5 16.8v3a2 2 0 0 1-2.2 2 19.6 19.6 0 0 1-8.5-3 19.3 19.3 0 0 1-6-6 19.6 19.6 0 0 1-3-8.6A2 2 0 0 1 3.8 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.7a2 2 0 0 1-.5 2.1L7.8 9.7a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.7.7a2 2 0 0 1 1.6 2.1z"/></symbol>
  <symbol id="i-mic" viewBox="0 0 24 24"><rect x="9" y="2.5" width="6" height="12" rx="3"/><path d="M5 10.5a7 7 0 0 0 14 0M12 17.5v4"/></symbol>
  <symbol id="i-mic-off" viewBox="0 0 24 24"><path d="M15 9.3V5.5a3 3 0 0 0-5.7-1.3M9 9v2.5a3 3 0 0 0 5.1 2.1"/><path d="M19 10.5a7 7 0 0 1-1.2 3.9M5 10.5a7 7 0 0 0 11.1 5.7M12 17.5v4M3 3l18 18"/></symbol>
  <symbol id="i-stop" viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="2.5" fill="currentColor" stroke="none"/></symbol>
  <symbol id="i-vol" viewBox="0 0 24 24"><path d="M11 5L6.5 9H3.5v6h3L11 19z"/><path d="M15.5 9a4.5 4.5 0 0 1 0 6M18.5 6a8.5 8.5 0 0 1 0 12"/></symbol>
  <symbol id="i-vol-low" viewBox="0 0 24 24"><path d="M11 5L6.5 9H3.5v6h3L11 19z"/><path d="M15.5 9a4.5 4.5 0 0 1 0 6"/></symbol>
  <symbol id="i-vol-off" viewBox="0 0 24 24"><path d="M11 5L6.5 9H3.5v6h3L11 19z"/><path d="M16 9.5l5 5M21 9.5l-5 5"/></symbol>
  <symbol id="i-spark" viewBox="0 0 24 24"><path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M19 15.5l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z"/></symbol>
  <symbol id="i-back" viewBox="0 0 24 24"><path d="M19 12H5M11 18l-6-6 6-6"/></symbol>
  <symbol id="i-logout" viewBox="0 0 24 24"><path d="M9 21H5.5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2H9"/><path d="M16 17l5-5-5-5M21 12H9"/></symbol>
  <symbol id="i-bolt" viewBox="0 0 24 24"><path d="M13.5 2L4 13.5h7.5L10.5 22 20 10.5h-7.5z" fill="currentColor" stroke="none"/></symbol>
  <symbol id="i-laptop" viewBox="0 0 24 24"><rect x="4" y="5" width="16" height="10.5" rx="1.6"/><path d="M2 19h20"/></symbol>
  <symbol id="i-reset" viewBox="0 0 24 24"><path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1L3.5 8.5"/><path d="M3.5 3.5v5h5"/></symbol>
  <symbol id="i-arrow" viewBox="0 0 24 24"><path d="M5 12h14M13 6l6 6-6 6"/></symbol>
  <symbol id="i-x" viewBox="0 0 24 24"><path d="M18 6L6 18M6 6l12 12"/></symbol>
  <symbol id="i-eye" viewBox="0 0 24 24"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></symbol>
  <symbol id="i-eye-off" viewBox="0 0 24 24"><path d="M10.6 5.1A10 10 0 0 1 12 5c6.4 0 10 7 10 7a17.6 17.6 0 0 1-2.9 3.9M6.3 6.3C3.6 8 2 12 2 12s3.6 7 10 7a9.7 9.7 0 0 0 5.7-1.8"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2M3 3l18 18"/></symbol>
  <symbol id="i-lock" viewBox="0 0 24 24"><rect x="4.5" y="10.5" width="15" height="10" rx="2.5"/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/></symbol>
  <symbol id="i-chat" viewBox="0 0 24 24"><path d="M20.5 12a8 8 0 0 1-11.8 7L3.5 20.5l1.5-5A8 8 0 1 1 20.5 12z"/><path d="M8.5 10.5h7M8.5 14h4.5"/></symbol>
  <symbol id="i-panels" viewBox="0 0 24 24"><rect x="3.5" y="3.5" width="7" height="7" rx="1.8"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.8"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.8"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.8"/></symbol>
  <symbol id="i-folder" viewBox="0 0 24 24"><path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2.5h7.5A2.5 2.5 0 0 1 21 10v7.5a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"/></symbol>
  <symbol id="i-folder-plus" viewBox="0 0 24 24"><path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2.5h7.5A2.5 2.5 0 0 1 21 10v7.5a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"/><path d="M12 10.5v6M9 13.5h6"/></symbol>
  <symbol id="i-file" viewBox="0 0 24 24"><path d="M14 2.5H6.5a2 2 0 0 0-2 2v15a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V8z"/><path d="M14 2.5V8h5.5"/></symbol>
  <symbol id="i-doc" viewBox="0 0 24 24"><path d="M14 2.5H6.5a2 2 0 0 0-2 2v15a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V8z"/><path d="M14 2.5V8h5.5M8.5 13h7M8.5 17h5"/></symbol>
  <symbol id="i-image" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2.5"/><circle cx="9" cy="9.5" r="1.8"/><path d="M21 16l-5-5-9 9"/></symbol>
  <symbol id="i-film" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M7.5 4v16M16.5 4v16M3 9h4.5M3 15h4.5M16.5 9H21M16.5 15H21"/></symbol>
  <symbol id="i-music" viewBox="0 0 24 24"><path d="M9 18V5l11-2v13"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="17.5" cy="16" r="2.5"/></symbol>
  <symbol id="i-code" viewBox="0 0 24 24"><path d="M8.5 8l-4.5 4 4.5 4M15.5 8l4.5 4-4.5 4M13.5 5l-3 14"/></symbol>
  <symbol id="i-archive" viewBox="0 0 24 24"><rect x="3" y="3.5" width="18" height="5" rx="1.5"/><path d="M4.5 8.5v10a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-10M10 12.5h4"/></symbol>
  <symbol id="i-sheet" viewBox="0 0 24 24"><rect x="3.5" y="3.5" width="17" height="17" rx="2.5"/><path d="M3.5 9h17M3.5 14.5h17M9.5 9v11.5"/></symbol>
  <symbol id="i-app" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M3 8.5h18M6.5 6.2h.01M9 6.2h.01"/></symbol>
  <symbol id="i-monitor" viewBox="0 0 24 24"><rect x="2.5" y="3.5" width="19" height="13" rx="2"/><path d="M8 20.5h8M12 16.5v4"/></symbol>
  <symbol id="i-drive" viewBox="0 0 24 24"><rect x="2.5" y="13" width="19" height="7" rx="2"/><path d="M5 13l2.2-7.2A2 2 0 0 1 9.1 4.5h5.8a2 2 0 0 1 1.9 1.3L19 13M6.5 16.5h.01M10 16.5h.01"/></symbol>
  <symbol id="i-home" viewBox="0 0 24 24"><path d="M3.5 10.5L12 3.5l8.5 7"/><path d="M5.5 9v11h13V9"/><path d="M10 20v-6h4v6"/></symbol>
  <symbol id="i-up" viewBox="0 0 24 24"><path d="M12 19V5M6 11l6-6 6 6"/></symbol>
  <symbol id="i-refresh" viewBox="0 0 24 24"><path d="M20.5 12a8.5 8.5 0 1 1-2.6-6.1l2.6 2.6"/><path d="M20.5 3.5v5h-5"/></symbol>
  <symbol id="i-search" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M20.5 20.5l-4.5-4.5"/></symbol>
  <symbol id="i-download" viewBox="0 0 24 24"><path d="M12 3.5v12M7 10.5l5 5 5-5M4 20.5h16"/></symbol>
  <symbol id="i-phone" viewBox="0 0 24 24"><rect x="6.5" y="2.5" width="11" height="19" rx="2.5"/><path d="M11 18.5h2"/></symbol>
  <symbol id="i-open" viewBox="0 0 24 24"><path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v4.5a2 2 0 0 1-2 2H5.5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2H10"/></symbol>
  <symbol id="i-edit" viewBox="0 0 24 24"><path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16z"/><path d="M13.5 6.5l4 4"/></symbol>
  <symbol id="i-copy" viewBox="0 0 24 24"><rect x="8.5" y="8.5" width="12" height="12" rx="2.5"/><path d="M15.5 8.5V6a2.5 2.5 0 0 0-2.5-2.5H6A2.5 2.5 0 0 0 3.5 6v7A2.5 2.5 0 0 0 6 15.5h2.5"/></symbol>
  <symbol id="i-move" viewBox="0 0 24 24"><path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2.5h7.5A2.5 2.5 0 0 1 21 10v7.5a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"/><path d="M8.5 13.5h7M13 11l2.5 2.5L13 16"/></symbol>
  <symbol id="i-trash" viewBox="0 0 24 24"><path d="M4 6.5h16M9.5 6.5v-2h5v2M6 6.5l1 13a1.5 1.5 0 0 0 1.5 1.5h7a1.5 1.5 0 0 0 1.5-1.5l1-13M10 11v6M14 11v6"/></symbol>
  <symbol id="i-check" viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></symbol>
  <symbol id="i-chev" viewBox="0 0 24 24"><path d="M9.5 6l6 6-6 6"/></symbol>
  <symbol id="i-camera" viewBox="0 0 24 24"><path d="M3 8.5A2.5 2.5 0 0 1 5.5 6h2l1.6-2.2h5.8L16.5 6h2A2.5 2.5 0 0 1 21 8.5v9a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"/><circle cx="12" cy="12.8" r="3.8"/></symbol>
  <symbol id="i-flip" viewBox="0 0 24 24"><path d="M4 12a8 8 0 0 1 13.7-5.6L20 8.5"/><path d="M20 3.5v5h-5"/><path d="M20 12a8 8 0 0 1-13.7 5.6L4 15.5"/><path d="M4 20.5v-5h5"/></symbol>
  <symbol id="i-send" viewBox="0 0 24 24"><path d="M21 3L10.5 13.5M21 3l-6.5 18-4-7.5L3 9.5z"/></symbol>
  <symbol id="i-alert" viewBox="0 0 24 24"><path d="M12 3.5L2.5 20h19z"/><path d="M12 10v4.5M12 17.2h.01"/></symbol>
</svg>

<div id="boot" class="boot" role="status">
  <svg class="mark" aria-hidden="true"><use href="#i-logo"/></svg>
  <p>Connecting to Willy…</p>
</div>

<main id="login" class="login" hidden>
  <form id="login-form" class="card login-card" autocomplete="off" novalidate>
    <svg class="mark" aria-hidden="true"><use href="#i-logo"/></svg>
    <h1 class="login-title">Connect to Willy</h1>
    <p class="login-hint">Enter your <code>WILLY_REMOTE_TOKEN</code></p>
    <input class="sr-only" type="text" name="username" autocomplete="username" value="willy" tabindex="-1" aria-hidden="true">
    <div class="pw-wrap">
      <label for="login-token" class="sr-only">Access token</label>
      <input id="login-token" type="password" autocomplete="current-password" spellcheck="false" autocapitalize="off" autocorrect="off" placeholder="Access token">
      <button type="button" class="pw-eye" id="login-eye" aria-label="Show token" aria-pressed="false"><svg class="i"><use href="#i-eye"/></svg></button>
    </div>
    <button class="btn btn-primary btn-block" id="login-btn" type="submit"><span>Connect</span></button>
    <p class="login-err" id="login-err" role="alert" hidden></p>
    <p class="login-foot">Saved only in this browser. Sign out to remove it.</p>
  </form>
</main>

<div id="app" class="app" data-state="idle" hidden>
  <header class="bar">
    <a class="back" id="back-link" href="dashboard" aria-label="Back to dashboard"><svg class="i" aria-hidden="true"><use href="#i-back"/></svg><span>Dashboard</span></a>
    <div class="bar-brand"><svg class="i enc" aria-hidden="true"><use href="#i-lock"/></svg><span>Willy voice call</span></div>
    <div class="pills">
      <span class="pill" id="hub-pill" data-state="checking" role="status"><i class="pill-dot" aria-hidden="true"></i><span id="hub-text">Connecting</span></span>
      <span class="pill" id="pc-pill" data-state="unknown"><svg class="i" aria-hidden="true"><use href="#i-laptop"/></svg><span id="pc-text">PC</span></span>
    </div>
    <button class="icon-btn" id="signout" type="button" title="Sign out of this browser" aria-label="Sign out"><svg class="i" aria-hidden="true"><use href="#i-logout"/></svg></button>
  </header>

  <div class="main" id="main">
    <aside class="sheet" id="sheet" aria-labelledby="sheet-title" aria-hidden="true" inert>
      <div class="sheet-in"><div class="panel-card">
        <header class="p-head">
          <span class="grab" aria-hidden="true"></span>
          <div class="p-titles"><h2 class="p-title" id="sheet-title">Files</h2><p class="p-sub" id="sheet-sub">Your PC</p></div>
          <button class="icon-btn" id="sheet-close" type="button" aria-label="Close panel" title="Close panel (the call keeps going)"><svg class="i" aria-hidden="true"><use href="#i-x"/></svg></button>
          <div class="tabs" id="tabs" role="tablist" aria-label="Panels">
            <button class="tab" id="tab-files" type="button" role="tab" aria-controls="pane-files" aria-selected="true" tabindex="0" data-tab="files"><svg class="i" aria-hidden="true"><use href="#i-folder"/></svg><span>Files</span></button>
            <button class="tab" id="tab-screenshot" type="button" role="tab" aria-controls="pane-screenshot" aria-selected="false" tabindex="-1" data-tab="screenshot"><svg class="i" aria-hidden="true"><use href="#i-monitor"/></svg><span>Screen</span></button>
            <button class="tab" id="tab-camera" type="button" role="tab" aria-controls="pane-camera" aria-selected="false" tabindex="-1" data-tab="camera"><svg class="i" aria-hidden="true"><use href="#i-camera"/></svg><span>Camera</span></button>
          </div>
        </header>

        <section class="pane" id="pane-files" role="tabpanel" aria-labelledby="tab-files">
          <div class="f-top">
            <button class="icon-btn" id="f-up" type="button" aria-label="Up one folder" title="Up one folder"><svg class="i" aria-hidden="true"><use href="#i-up"/></svg></button>
            <nav class="crumbs" id="f-crumbs" aria-label="Folder path"></nav>
            <button class="icon-btn" id="f-refresh" type="button" aria-label="Refresh folder" title="Refresh"><svg class="i" aria-hidden="true"><use href="#i-refresh"/></svg></button>
          </div>
          <div class="chips" id="f-drives" role="group" aria-label="Drives" hidden></div>
          <div class="f-tools">
            <div class="search"><svg class="i" aria-hidden="true"><use href="#i-search"/></svg><label class="sr-only" for="f-filter">Filter this folder</label><input id="f-filter" type="search" placeholder="Filter this folder" autocomplete="off" spellcheck="false" enterkeyhint="search"></div>
            <label class="sr-only" for="f-sort">Sort by</label>
            <select class="select" id="f-sort"><option value="name">Name</option><option value="modified">Newest</option><option value="size">Largest</option></select>
            <button class="btn btn-sm" id="f-new" type="button" aria-label="New folder" title="New folder"><svg class="i" aria-hidden="true"><use href="#i-folder-plus"/></svg><span class="lbl">New folder</span></button>
          </div>
          <div class="f-pick" id="f-pick" role="status" hidden>
            <p id="f-pick-text"></p>
            <div class="f-pick-btns"><button class="btn btn-sm btn-ghost" id="f-pick-cancel" type="button">Cancel</button><button class="btn btn-sm btn-primary" id="f-pick-go" type="button"><span id="f-pick-go-text">Move here</span></button></div>
          </div>
          <div class="f-scroll" id="f-scroll">
            <ul class="f-list" id="f-list" aria-label="Folder contents"></ul>
            <div class="p-state" id="f-state" hidden></div>
          </div>
          <div class="selbar" id="f-selbar" hidden>
            <button class="icon-btn sm" id="f-clear" type="button" aria-label="Clear selection" title="Clear selection"><svg class="i" aria-hidden="true"><use href="#i-x"/></svg></button>
            <span class="sel-count" id="f-selcount" aria-live="polite"></span>
            <div class="sel-acts">
              <button class="btn btn-sm" id="fa-open" type="button" aria-label="Open on PC" title="Open on PC"><svg class="i" aria-hidden="true"><use href="#i-open"/></svg><span class="lbl" id="fa-open-text">Open on PC</span></button>
              <button class="btn btn-sm" id="fa-download" type="button" aria-label="Download to this device" title="Download to this device"><svg class="i" aria-hidden="true"><use href="#i-download"/></svg><span class="lbl">Download</span></button>
              <button class="btn btn-sm" id="fa-phone" type="button" aria-label="Send to phone" title="Send to phone"><svg class="i" aria-hidden="true"><use href="#i-phone"/></svg><span class="lbl">To phone</span></button>
              <button class="btn btn-sm" id="fa-rename" type="button" aria-label="Rename" title="Rename"><svg class="i" aria-hidden="true"><use href="#i-edit"/></svg><span class="lbl">Rename</span></button>
              <button class="btn btn-sm" id="fa-copy" type="button" aria-label="Copy to another folder" title="Copy to…"><svg class="i" aria-hidden="true"><use href="#i-copy"/></svg><span class="lbl">Copy to…</span></button>
              <button class="btn btn-sm" id="fa-move" type="button" aria-label="Move to another folder" title="Move to…"><svg class="i" aria-hidden="true"><use href="#i-move"/></svg><span class="lbl">Move to…</span></button>
              <button class="btn btn-sm danger" id="fa-delete" type="button" aria-label="Delete (to the Recycle Bin)" title="Delete (to the Recycle Bin)"><svg class="i" aria-hidden="true"><use href="#i-trash"/></svg><span class="lbl">Delete</span></button>
            </div>
          </div>
        </section>

        <section class="pane" id="pane-screenshot" role="tabpanel" aria-labelledby="tab-screenshot" hidden>
          <div class="x-tools">
            <button class="btn btn-sm" id="x-refresh" type="button"><svg class="i" aria-hidden="true"><use href="#i-refresh"/></svg><span>Refresh</span></button>
            <label class="toggle" title="Refresh every few seconds while this panel is open"><input type="checkbox" id="x-auto"><span class="tg" aria-hidden="true"></span><span>Live</span></label>
            <span class="x-time" id="x-time" aria-live="polite"></span>
            <span class="grow"></span>
            <button class="btn btn-sm ic" id="x-save" type="button" aria-label="Save screenshot" title="Save screenshot" disabled><svg class="i" aria-hidden="true"><use href="#i-download"/></svg><span class="lbl">Save</span></button>
            <button class="btn btn-sm ic" id="x-phone" type="button" aria-label="Send a screenshot to your phone" title="Send a screenshot to your phone"><svg class="i" aria-hidden="true"><use href="#i-phone"/></svg><span class="lbl">To phone</span></button>
          </div>
          <div class="x-view" id="x-view">
            <button class="x-zoom" id="x-zoom" type="button" aria-pressed="false" aria-label="Show the screenshot at actual size" hidden><img id="x-img" alt="Your PC’s screen"></button>
            <div class="p-state" id="x-state" hidden></div>
            <span class="spinner" id="x-spin" aria-hidden="true" hidden></span>
          </div>
        </section>

        <section class="pane" id="pane-camera" role="tabpanel" aria-labelledby="tab-camera" hidden>
          <div class="k-view" id="k-view">
            <video id="k-video" playsinline muted autoplay aria-label="Camera preview" hidden></video>
            <img id="k-photo" alt="The photo you just took" hidden>
            <span class="k-flash" id="k-flash" aria-hidden="true"></span>
            <div class="p-state" id="k-state" hidden></div>
          </div>
          <div class="k-bar" id="k-live">
            <span class="k-side"></span>
            <button class="shutter" id="k-shutter" type="button" aria-label="Take photo" disabled><span></span></button>
            <span class="k-side"><button class="round sm" id="k-flip" type="button" aria-label="Switch between front and back camera" title="Switch camera"><svg class="i" aria-hidden="true"><use href="#i-flip"/></svg></button></span>
          </div>
          <div class="k-bar k-review" id="k-review" hidden>
            <button class="btn" id="k-retake" type="button"><svg class="i" aria-hidden="true"><use href="#i-reset"/></svg><span>Retake</span></button>
            <button class="btn" id="k-save" type="button"><svg class="i" aria-hidden="true"><use href="#i-download"/></svg><span>Save</span></button>
            <button class="btn btn-primary" id="k-send" type="button"><svg class="i" aria-hidden="true"><use href="#i-send"/></svg><span>Send to PC</span></button>
          </div>
        </section>
      </div></div>
    </aside>

    <section class="stage" id="stage" aria-label="Call with Willy">
      <div class="avatar" id="avatar">
        <canvas class="ring" id="ring" aria-hidden="true"></canvas>
        <button class="orb" id="orb" type="button" aria-label="Start call with Willy"><svg class="mark" aria-hidden="true"><use href="#i-logo"/></svg></button>
      </div>
      <div class="id-block">
        <h1 class="name">Willy</h1>
        <p class="state-line"><i class="state-dot" aria-hidden="true"></i><span id="state-label" aria-live="polite">Ready</span><span class="timer" id="timer" hidden>00:00</span></p>
      </div>
      <p class="hint" id="state-hint">Tap the green button to call Willy. Speak over him any time to interrupt.</p>
      <div class="lvl" aria-hidden="true"><i id="meter-fill"></i><b id="meter-thr"></b></div>
      <button class="stop-chip" id="stop-btn" type="button" hidden><svg class="i" aria-hidden="true"><use href="#i-stop"/></svg><span id="stop-label">Tap to interrupt</span></button>
      <div class="caption" id="caption" hidden>
        <p class="cap-you" id="cap-you"></p>
        <p class="cap-text" id="cap-text"></p>
        <button class="cap-more" id="cap-more" type="button" hidden>Read the full reply</button>
      </div>
      <p class="notice" id="notice" role="alert" hidden></p>
    </section>

    <aside class="drawer" id="drawer" aria-labelledby="drawer-title" aria-hidden="true" inert>
      <div class="drawer-in"><div class="panel-card">
        <header class="p-head d-head">
          <span class="grab" aria-hidden="true"></span>
          <div class="p-titles"><h2 class="p-title" id="drawer-title">Transcript</h2><p class="p-sub">Session <code id="session-id">voice_web</code></p></div>
          <div class="p-actions">
            <button class="btn btn-ghost btn-sm" id="reset-btn" type="button" title="Clear Willy's memory of this conversation"><svg class="i" aria-hidden="true"><use href="#i-reset"/></svg><span>New chat</span></button>
            <button class="icon-btn" id="drawer-close" type="button" aria-label="Close transcript"><svg class="i" aria-hidden="true"><use href="#i-x"/></svg></button>
          </div>
        </header>
        <ol class="turns" id="turns" aria-live="polite">
          <li class="turns-empty" id="turns-empty"><b>Nothing said yet.</b>Everything you and Willy say shows up here, with how long each answer took.</li>
        </ol>
        <form class="type" id="type-form" autocomplete="off">
          <label class="sr-only" for="type-input">Type a message to Willy</label>
          <input id="type-input" type="text" enterkeyhint="send" maxlength="500" placeholder="Type to Willy…">
          <button class="btn btn-primary type-send" type="submit" aria-label="Send message"><svg class="i" aria-hidden="true"><use href="#i-arrow"/></svg></button>
        </form>
      </div></div>
    </aside>
  </div>

  <nav class="dock" aria-label="Call controls">
    <div class="ctl" id="spk-ctl">
      <button class="round" id="spk-btn" type="button" aria-haspopup="dialog" aria-expanded="false" aria-controls="spk-pop" aria-label="Willy’s voice volume"><svg class="i" aria-hidden="true"><use id="spk-use" href="#i-vol"/></svg></button><span id="spk-label">Speaker</span>
      <div class="pop" id="spk-pop" role="dialog" aria-label="Willy’s voice volume" hidden>
        <button class="icon-btn" id="spk-mute" type="button" aria-pressed="false" aria-label="Mute Willy’s voice" title="Mute Willy’s voice"><svg class="i" aria-hidden="true"><use id="spk-mute-use" href="#i-vol"/></svg></button>
        <input type="range" id="spk-vol" min="0" max="100" step="5" value="100" aria-label="Volume">
        <output id="spk-val" for="spk-vol">100%</output>
      </div>
    </div>
    <div class="ctl"><button class="round round-mute" id="mute-btn" type="button" aria-pressed="false" aria-label="Mute microphone" disabled><svg class="i" aria-hidden="true"><use id="mute-use" href="#i-mic"/></svg></button><span id="mute-label">Mute</span></div>
    <div class="ctl"><button class="round round-call" id="call-btn" type="button" aria-label="Start call"><svg class="i" aria-hidden="true"><use href="#i-call"/></svg></button><span id="call-label">Call</span></div>
    <div class="ctl"><button class="round" id="panel-btn" type="button" aria-expanded="false" aria-controls="sheet" aria-label="Panels: files, screen and camera"><svg class="i" aria-hidden="true"><use href="#i-panels"/></svg></button><span>Panels</span></div>
    <div class="ctl"><button class="round" id="chat-btn" type="button" aria-expanded="false" aria-controls="drawer" aria-label="Chat and transcript"><svg class="i" aria-hidden="true"><use href="#i-chat"/></svg><span class="badge" id="chat-badge" aria-hidden="true" hidden></span></button><span>Chat</span></div>
  </nav>
</div>

<dialog class="dialog" id="confirm" aria-labelledby="cf-title" aria-describedby="cf-body">
  <div class="dlg-body">
    <span class="dlg-ico" aria-hidden="true"><svg class="i"><use href="#i-trash"/></svg></span>
    <h2 id="cf-title">Delete?</h2>
    <p id="cf-body"></p>
    <ul class="dlg-list" id="cf-list"></ul>
  </div>
  <div class="dlg-foot">
    <button class="btn" id="cf-cancel" type="button">Cancel</button>
    <button class="btn btn-danger" id="cf-ok" type="button">Delete</button>
  </div>
</dialog>

<div class="toasts" id="toasts" aria-live="polite"></div>
<div class="sr-only" id="sr-live" aria-live="polite"></div>

<script>
(function () {
'use strict';

/* ================================================================ constants */
var BASE = location.pathname.startsWith('/willy') ? '/willy' : '';
var CLIENT = 'voice';
var SESSION_ID = 'voice_web';
var KEYS = { token: 'willy_token', turns: 'willy_call_turns', vol: 'willy_call_volume', spk: 'willy_call_voice_muted', tab: 'willy_call_panel' };
var SILENT_WAV = 'data:audio/wav;base64,UklGRhQBAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YfAAAACAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIA=';
var RIBBON_MAX_MS = 4000;
var SEGMENTS = [['stt_ms', 'stt', 'Speech-to-text'], ['llm_ms', 'llm', 'AI'], ['tool_ms', 'tool', 'Device'], ['tts_ms', 'tts', 'Voice']];
// Voice activity detection (all times in ms)
var TICK_MS = 50;           // analyser polling interval
var START_TICKS = 2;        // consecutive loud ticks that start an utterance
var SILENCE_MS = 1100;      // quiet time that ends an utterance
var SHORT_SILENCE_MS = 1500; // ... after under 1.2 s of speech ("open ... chrome"): people pause mid-command
var MIN_SPEECH_MS = 400;    // shorter blips (coughs, clicks) are dropped
var MAX_UTTER_MS = 30000;   // hard cap per utterance
var PREROLL_MS = 2500;      // recorder restarts this often while idle, so a little audio before speech is kept
var COOLDOWN_MS = 400;      // ignore speech onset right after Willy stops talking (room echo)
var BARGE_GRACE_MS = 600;   // no barge-in during the first moments of a reply
var BARGE_TICKS = 4;        // sustained loud ticks needed to interrupt Willy
var STATES = {
  idle: ['Ready', 'Tap the green button to call Willy. Speak over him any time to interrupt.'],
  connecting: ['Connecting…', 'Allow microphone access if your browser asks.'],
  listening: ['Listening', 'Go ahead. Willy answers when you pause.'],
  hearing: ['Listening', 'Hearing you… pause when you’re done.'],
  thinking: ['Thinking…', 'Willy is working on it.'],
  speaking: ['Speaking', 'Start talking to interrupt him.'],
  muted: ['Muted', 'Your microphone is off. Unmute to talk.']
};
var RING_COLORS = {
  idle: ['#64748B', '#334155'], connecting: ['#F59E0B', '#64748B'], listening: ['#00F2FE', '#7C3AED'], hearing: ['#34D399', '#00F2FE'],
  thinking: ['#7C3AED', '#A78BFA'], speaking: ['#A78BFA', '#00F2FE'], muted: ['#F87171', '#475569']
};
var RING_BARS = 72;          // bars around the avatar (mirrored left/right)
var CAPTION_MAX = 200;       // characters of a reply shown under the avatar; the transcript has all of it
var PANELS = ['files', 'screenshot', 'camera'];
var PANEL_TITLES = { files: 'Files', screenshot: 'Screen', camera: 'Camera' };
var LIST_CAP = 600;          // rows rendered per folder (filter to narrow)
var AUTO_SHOT_MS = 4000;     // "Live" screenshot refresh interval
function mq(q) { try { return window.matchMedia(q); } catch (e) { return { matches: false }; } }
var RM = mq('(prefers-reduced-motion: reduce)');
var MOBILE = mq('(max-width: 899px)');

/* ==================================================================== state */
var S = { token: null, wired: false, turns: [], statsTimer: 0, loginRetry: 0 };
var C = {
  inCall: false, starting: false, state: 'idle', muted: false,
  stream: null, ctx: null, analyser: null, buf: null, tick: 0, clock: 0, callStart: 0,
  rec: null, recStart: 0, mime: '', uttStart: 0, speechMs: 0, silenceMs: 0, above: 0, noise: 0.006,
  cooldownUntil: 0, speakStart: 0, barge: 0, playing: false, inflight: null, wake: null, meterN: -1, ended: ''
};
var U = { sheet: false, tab: 'files', drawer: false, unread: 0 };           // layout
var O = { vol: 1, voiceMuted: false };                                     // speaker prefs
var P = { id: null, name: '' };                                            // the PC the panels act on
var F = { path: '', parent: null, entries: [], view: [], drives: [], loaded: false, loading: false, seq: 0, sel: {}, filter: '', sort: 'name', pick: null, renaming: null, creating: false, offline: false, err: '' };
var X = { b64: '', w: 0, h: 0, ts: 0, loading: false, auto: false, timer: 0, offline: false, err: '' };
var K = { stream: null, facing: 'user', blob: null, url: '', starting: false, err: '', seq: 0 };
var R = { raf: 0, cv: null, g: null, vals: null, tmp: null, lvl: -1 };     // avatar ring
var PB = { vctx: null, an: null, src: null, live: false, token: 0 };      // playback "shadow" analyser

/* ================================================================== helpers */
function $(sel, root) { return (root || document).querySelector(sel); }
function noop() { /* ignore */ }
function isNum(v) { return typeof v === 'number' && isFinite(v); }
function pad(n) { return String(n).padStart(2, '0'); }
function store(area) {
  return {
    get: function (k) { try { return window[area].getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { window[area].setItem(k, v); } catch (e) { /* storage unavailable */ } },
    del: function (k) { try { window[area].removeItem(k); } catch (e) { /* storage unavailable */ } }
  };
}
var local = store('localStorage');
var session = store('sessionStorage');
function h(tag, props) {
  var el = document.createElement(tag);
  if (props) {
    Object.keys(props).forEach(function (k) {
      var v = props[k];
      if (v == null || v === false) return;
      if (k === 'class') el.className = v;
      else if (k.slice(0, 2) === 'on' && typeof v === 'function') el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? '' : String(v));
    });
  }
  for (var i = 2; i < arguments.length; i++) append(el, arguments[i]);
  return el;
}
function append(el, kid) {
  if (kid == null || kid === false || kid === '') return;
  if (Array.isArray(kid)) { kid.forEach(function (k) { append(el, k); }); return; }
  el.appendChild(kid instanceof Node ? kid : document.createTextNode(String(kid)));
}
function icon(name) {
  var ns = 'http://www.w3.org/2000/svg';
  var svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('class', 'i');
  svg.setAttribute('aria-hidden', 'true');
  var use = document.createElementNS(ns, 'use');
  use.setAttribute('href', '#i-' + name);
  svg.appendChild(use);
  return svg;
}
function setText(el, v) { v = v == null ? '' : String(v); if (el && el.textContent !== v) el.textContent = v; }
function fmtMs(ms) {
  if (!isNum(ms)) return '—';
  if (ms < 1) return '<1 ms';
  if (ms < 1000) return Math.round(ms) + ' ms';
  if (ms < 10000) return (ms / 1000).toFixed(2) + ' s';
  return (ms / 1000).toFixed(1) + ' s';
}
function fmtSize(n) {
  if (!isNum(n) || n < 0) return '';
  if (n < 1024) return n + ' B';
  var u = ['KB', 'MB', 'GB', 'TB'], i = -1;
  do { n /= 1024; i++; } while (n >= 1024 && i < u.length - 1);
  return (n < 10 ? n.toFixed(1) : Math.round(n)) + ' ' + u[i];
}
function fmtGb(g) { return g >= 1000 ? (g / 1024).toFixed(1) + ' TB' : (g < 10 ? g.toFixed(1) : Math.round(g)) + ' GB'; }
function fmtDate(epoch) {
  if (!isNum(epoch) || epoch <= 0) return '';
  var d = new Date(epoch < 1e12 ? epoch * 1000 : epoch), now = new Date();
  try {
    if (d.toDateString() === now.toDateString()) return 'Today ' + d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    return d.toLocaleDateString([], d.getFullYear() === now.getFullYear() ? { month: 'short', day: 'numeric' } : { year: 'numeric', month: 'short', day: 'numeric' });
  } catch (e) { return d.toISOString().slice(0, 10); }
}
function stamp() { var d = new Date(); return d.getFullYear() + pad(d.getMonth() + 1) + pad(d.getDate()) + '-' + pad(d.getHours()) + pad(d.getMinutes()) + pad(d.getSeconds()); }
function busy(btn, on) { if (btn) { btn.classList.toggle('is-busy', !!on); btn.setAttribute('aria-busy', on ? 'true' : 'false'); } }
function focusSoon(sel) { setTimeout(function () { var el = typeof sel === 'string' ? $(sel) : sel; if (el && el.focus) { try { el.focus({ preventScroll: true }); } catch (e) { el.focus(); } } }, 60); }
function announce(text) { var el = $('#sr-live'); el.textContent = ''; setTimeout(function () { el.textContent = text; }, 30); }
function toast(msg, type) {
  var el = h('div', { class: 'toast toast-' + (type || 'info'), role: type === 'error' ? 'alert' : 'status' }, h('p', null, msg));
  var x = h('button', { class: 'toast-x', type: 'button', 'aria-label': 'Dismiss' }, icon('x'));
  el.appendChild(x);
  var box = $('#toasts');
  box.appendChild(el);
  while (box.children.length > 3) box.firstElementChild.remove();
  var close = function () { if (el.isConnected) el.remove(); };
  x.addEventListener('click', close);
  setTimeout(close, type === 'error' ? 7000 : 4500);
}
function errToast(e) { if (e && e.status === 401) return; toast((e && e.message) || 'Something went wrong.', 'error'); }
function saveBlob(blob, name) {
  var url = URL.createObjectURL(blob);
  var a = h('a', { href: url, download: name || 'download' });
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(function () { URL.revokeObjectURL(url); }, 60000);
}
function b64ToBlob(b64, type) {
  var bin = atob(b64), bytes = new Uint8Array(bin.length);
  for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Blob([bytes], { type: type });
}
function stopTracks(stream) { if (stream) stream.getTracks().forEach(function (t) { try { t.stop(); } catch (e) { /* ignore */ } }); }

/* ====================================================== markdown (safe: DOM text nodes only) */
function mdInline(text, out) {
  var re = /(`[^`\n]+`)|(\*\*[^*\n]+?\*\*|__[^_\n]+?__)|(https?:\/\/[^\s<>]+)/g;
  var last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) out.appendChild(document.createTextNode(text.slice(last, m.index)));
    if (m[1]) out.appendChild(h('code', null, m[1].slice(1, -1)));
    else if (m[2]) { var b = h('strong'); mdInline(m[2].slice(2, -2), b); out.appendChild(b); }
    else {
      var url = m[3], trail = '';
      while (/[.,;:!?)'"\]]$/.test(url)) { trail = url.slice(-1) + trail; url = url.slice(0, -1); }
      out.appendChild(h('a', { href: url, target: '_blank', rel: 'noopener noreferrer' }, url));
      if (trail) out.appendChild(document.createTextNode(trail));
    }
    last = re.lastIndex;
  }
  if (last < text.length) out.appendChild(document.createTextNode(text.slice(last)));
}
function renderMd(text) {
  var root = h('div', { class: 'md' });
  var lines = String(text || '').replace(/\r\n?/g, '\n').split('\n');
  var para = [], list = null, listType = '';
  function flushPara() {
    if (!para.length) return;
    var p = h('p');
    para.forEach(function (l, k) { if (k) p.appendChild(h('br')); mdInline(l, p); });
    root.appendChild(p);
    para = [];
  }
  function flushList() { if (list) { root.appendChild(list); list = null; listType = ''; } }
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i];
    if (/^\s*```/.test(line)) {
      flushPara(); flushList();
      var code = [];
      for (i++; i < lines.length && !/^\s*```/.test(lines[i]); i++) code.push(lines[i]);
      root.appendChild(h('pre', null, h('code', null, code.join('\n'))));
      continue;
    }
    var mB = /^\s*[-*•]\s+(.*)$/.exec(line), mN = /^\s*(\d+)[.)]\s+(.*)$/.exec(line), mH = /^\s*#{1,6}\s+(.*)$/.exec(line);
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { flushPara(); flushList(); root.appendChild(h('hr')); }
    else if (mB || mN) {
      flushPara();
      var t = mB ? 'ul' : 'ol';
      if (listType !== t) { flushList(); list = h(t); listType = t; if (mN && +mN[1] > 1) list.setAttribute('start', mN[1]); }
      var li = h('li');
      mdInline(mB ? mB[1] : mN[2], li);
      list.appendChild(li);
    } else if (mH) { flushPara(); flushList(); var hd = h('p', { class: 'md-h' }); mdInline(mH[1], hd); root.appendChild(hd); }
    else if (!line.trim()) { flushPara(); flushList(); }
    else if (list && /^\s{2,}\S/.test(line)) { mdInline(' ' + line.trim(), list.lastChild); }
    else { flushList(); para.push(line.trim()); }
  }
  flushPara(); flushList();
  return root;
}
function stripMd(text) {
  return String(text || '')
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/\*\*([^*]+)\*\*|__([^_]+)__/g, function (m, a, b) { return a || b; })
    .replace(/^\s*(#{1,6}|[-*•]|\d+[.)])\s+/gm, '')
    .replace(/\s+/g, ' ')
    .trim();
}

/* ====================================================================== API */
function ApiError(message, status) { var e = new Error(message); e.status = status || 0; return e; }
function api(path, opts) {
  opts = opts || {};
  var headers = { 'Authorization': 'Bearer ' + S.token, 'X-Willy-Client': CLIENT };
  if (opts.body !== undefined && !opts.form) headers['Content-Type'] = 'application/json';
  var ctrl = new AbortController();
  var timedOut = false;
  var timer = setTimeout(function () { timedOut = true; ctrl.abort(); }, opts.timeout || 25000);
  var outer = opts.signal;
  var onAbort = function () { ctrl.abort(); };
  if (outer) { if (outer.aborted) ctrl.abort(); else outer.addEventListener('abort', onAbort); }
  var cleanup = function () { clearTimeout(timer); if (outer) outer.removeEventListener('abort', onAbort); };
  return fetch(BASE + path, {
    method: opts.method || 'GET', headers: headers, cache: 'no-store', signal: ctrl.signal,
    body: opts.form ? opts.form : (opts.body === undefined ? undefined : JSON.stringify(opts.body))
  }).then(function (res) {
    return res.text().then(function (txt) {
      cleanup();
      var data = null;
      try { data = txt ? JSON.parse(txt) : null; } catch (e) { data = null; }
      if (res.status === 401) { onUnauthorized(); throw ApiError('Your access token was rejected.', 401); }
      if (!res.ok) throw ApiError((data && (typeof data.detail === 'string' ? data.detail : (data.error || data.reply))) || ('The hub answered with HTTP ' + res.status + '.'), res.status);
      return data;
    });
  }, function (err) {
    cleanup();
    if (outer && outer.aborted) throw err;
    throw ApiError(timedOut ? 'The hub took too long to answer.' : 'Can’t reach the Willy hub.', 0);
  });
}
// Downloads a hub file with the Bearer header (never a token in the URL). Only same-hub paths are fetched.
function fetchBlob(url) {
  if (typeof url !== 'string' || url.charAt(0) !== '/' || url.charAt(1) === '/') return Promise.reject(ApiError('The hub sent an unexpected download link.', 0));
  var path = BASE && url.indexOf(BASE + '/') === 0 ? url : BASE + url;
  return fetch(path, { headers: { 'Authorization': 'Bearer ' + S.token, 'X-Willy-Client': CLIENT }, cache: 'no-store' }).then(function (res) {
    if (res.status === 401) { onUnauthorized(); throw ApiError('Your access token was rejected.', 401); }
    if (!res.ok) throw ApiError('The download failed (HTTP ' + res.status + ').', res.status);
    return res.blob();
  }, function () { throw ApiError('Can’t reach the Willy hub.', 0); });
}

/* ===================================================================== auth */
function readUrlToken() {
  var token = null, changed = false;
  var hash = location.hash.replace(/^#/, '');
  if (hash) {
    var hp = new URLSearchParams(hash);
    if (hp.has('token')) { token = hp.get('token'); hp.delete('token'); hash = hp.toString(); changed = true; }
  }
  var qp = new URLSearchParams(location.search);
  if (qp.has('token')) { if (!token) token = qp.get('token'); qp.delete('token'); changed = true; }
  if (changed) {
    var qs = qp.toString();
    try { history.replaceState(history.state, '', location.pathname + (qs ? '?' + qs : '') + (hash ? '#' + hash : '')); } catch (e) { /* ignore */ }
  }
  token = token ? token.trim() : '';
  return token || null;
}
function checkToken(token) {
  var ctrl = new AbortController();
  var timer = setTimeout(function () { ctrl.abort(); }, 10000);
  return fetch(BASE + '/api/v1/auth/check', {
    headers: { 'Authorization': 'Bearer ' + token, 'X-Willy-Client': CLIENT }, cache: 'no-store', signal: ctrl.signal
  }).then(function (res) {
    clearTimeout(timer);
    if (res.status === 200) return res.json().catch(function () { return {}; }).then(function (d) { return { ok: true, server: d && d.server }; });
    if (res.status === 401 || res.status === 403) return { ok: false, reason: 'invalid' };
    return { ok: false, reason: 'http', status: res.status };
  }, function () { clearTimeout(timer); return { ok: false, reason: 'network' }; });
}
function showOnly(id) { ['boot', 'login', 'app'].forEach(function (x) { $('#' + x).hidden = x !== id; }); }
function showLogin(message, prefill) {
  showOnly('login');
  var err = $('#login-err');
  err.textContent = message || '';
  err.hidden = !message;
  if (prefill) $('#login-token').value = prefill;
  setTimeout(function () { try { $('#login-token').focus(); } catch (e) { /* ignore */ } }, 30);
}
function loginFailText(r) {
  if (r.reason === 'invalid') return 'That token was rejected. Check WILLY_REMOTE_TOKEN on the hub and try again.';
  if (r.reason === 'network') return 'Can’t reach the Willy hub. Check that it’s running — this page retries every few seconds.';
  return 'The hub answered with HTTP ' + r.status + '. Try again in a moment.';
}
function scheduleLoginRetry(token) {
  clearTimeout(S.loginRetry);
  S.loginRetry = setTimeout(function () {
    if (S.token || $('#login').hidden || $('#login-token').value.trim() !== token) return;
    if (document.hidden) { scheduleLoginRetry(token); return; }
    checkToken(token).then(function (r) {
      if (S.token || $('#login').hidden) return;
      if (r.ok) { local.set(KEYS.token, token); $('#login-token').value = ''; startApp(token, r.server); }
      else if (r.reason !== 'invalid') scheduleLoginRetry(token);
    });
  }, 5000);
}
function boot() {
  var urlToken = readUrlToken();
  if (urlToken) local.set(KEYS.token, urlToken);
  var token = urlToken || local.get(KEYS.token);
  if (!token) { showLogin(''); return; }
  checkToken(token).then(function (r) {
    if (r.ok) { startApp(token, r.server); return; }
    if (r.reason === 'invalid') { local.del(KEYS.token); showLogin('Your saved token was rejected. Enter the current WILLY_REMOTE_TOKEN.'); return; }
    showLogin(loginFailText(r), token);
    scheduleLoginRetry(token);
  });
}
function onHashToken() {
  var token = readUrlToken();
  if (!token || token === S.token) return;
  checkToken(token).then(function (r) {
    if (!r.ok) { if (!S.token) showLogin(loginFailText(r), r.reason === 'invalid' ? '' : token); return; }
    local.set(KEYS.token, token);
    if (S.token) { S.token = token; pollStats(); return; }
    startApp(token, r.server);
  });
}
function wireLogin() {
  window.addEventListener('hashchange', onHashToken);
  $('#login-form').addEventListener('submit', function (e) {
    e.preventDefault();
    var input = $('#login-token'), err = $('#login-err');
    var token = input.value.trim();
    if (!token) { err.textContent = 'Paste your token to connect.'; err.hidden = false; input.focus(); return; }
    var btn = $('#login-btn');
    busy(btn, true);
    checkToken(token).then(function (r) {
      busy(btn, false);
      if (r.ok) { local.set(KEYS.token, token); input.value = ''; err.hidden = true; startApp(token, r.server); return; }
      err.textContent = loginFailText(r);
      err.hidden = false;
      if (r.reason !== 'invalid') scheduleLoginRetry(token);
    });
  });
  $('#login-eye').addEventListener('click', function () {
    var input = $('#login-token');
    var show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    this.setAttribute('aria-pressed', String(show));
    this.setAttribute('aria-label', show ? 'Hide token' : 'Show token');
    this.querySelector('use').setAttribute('href', show ? '#i-eye-off' : '#i-eye');
  });
}
function onUnauthorized() { if (S.token) signOut('Your access token was rejected. Enter the current WILLY_REMOTE_TOKEN.'); }
function signOut(message) {
  endCall();
  abortInflight();
  stopPlayback();
  closePanel();
  setDrawer(false);
  stopCamera();
  clearPhoto();
  X.b64 = ''; F.loaded = false; F.entries = []; F.sel = {}; F.pick = null; P.id = null; P.name = '';
  local.del(KEYS.token);
  session.del(KEYS.turns);
  S.token = null;
  S.turns = [];
  clearTimeout(S.statsTimer);
  renderTurns();
  showLogin(message || '');
}
function startApp(token, server) {
  clearTimeout(S.loginRetry);
  S.token = token;
  showOnly('app');
  if (!S.wired) { wireApp(); S.wired = true; }
  S.turns = loadTurns();
  renderTurns();
  if (server) applyServer(server, null);
  pollStats();
  setState(C.inCall ? C.state : 'idle');
}

/* ============================================================ hub status */
function pollStats() {
  clearTimeout(S.statsTimer);
  if (!S.token) return;
  var t0 = performance.now();
  api('/api/v1/stats', { timeout: 8000 }).then(function (r) {
    applyServer(r && r.server, Math.round(performance.now() - t0));
  }, function (e) {
    if (e.status === 401) return;
    var pill = $('#hub-pill');
    pill.setAttribute('data-state', 'down');
    setText($('#hub-text'), 'Hub unreachable');
    pill.title = e.message;
  }).then(function () {
    if (S.token) S.statsTimer = setTimeout(pollStats, 10000);
  });
}
function applyServer(sv, ms) {
  var pill = $('#hub-pill');
  pill.setAttribute('data-state', 'ok');
  setText($('#hub-text'), isNum(ms) ? 'Hub · ' + ms + ' ms' : 'Hub online');
  if (!sv) return;
  var llm = sv.llm || {};
  pill.title = 'Willy hub v' + (sv.version || '?') + (llm.provider ? ' · AI ' + llm.provider + (llm.ready ? '' : ' (unavailable)') : '');
  var pcs = isNum(sv.pcs_online) ? sv.pcs_online : 0;
  var pc = $('#pc-pill');
  pc.setAttribute('data-state', pcs > 0 ? 'on' : 'off');
  setText($('#pc-text'), pcs > 1 ? pcs + ' PCs' : pcs === 1 ? 'PC online' : 'PC offline');
  pc.title = pcs > 0 ? 'Willy can act on your PC' : 'Start the Willy PC client so Willy can act on your PC';
}

/* ============================================================== transcript */
function loadTurns() {
  try { var v = JSON.parse(session.get(KEYS.turns) || '[]'); return Array.isArray(v) ? v.slice(-60) : []; } catch (e) { return []; }
}
function saveTurns() {
  session.set(KEYS.turns, JSON.stringify(S.turns.slice(-60).map(function (t) { var c = Object.assign({}, t); delete c.audio; return c; })));
}
function renderTurns() {
  var list = $('#turns');
  var empty = $('#turns-empty');
  list.replaceChildren.apply(list, [empty].concat(S.turns.map(turnEl)));
  empty.hidden = S.turns.length > 0;
  scrollTurns();
  renderCaption();
}
function scrollTurns() {
  var list = $('#turns');
  list.scrollTop = list.scrollHeight;
}
function pushTurn(t) {
  S.turns.push(t);
  if (S.turns.length > 60) S.turns.shift();
  var list = $('#turns');
  list.appendChild(turnEl(t));
  while (list.children.length > 61) list.children[1].remove();
  $('#turns-empty').hidden = true;
  saveTurns();
  scrollTurns();
  renderCaption();
  if (!t.sys && !U.drawer) { U.unread++; renderBadge(); }
}
function addSys(text) { pushTurn({ sys: text, ts: Date.now() }); }
function breakdownParts(tm, total, rtt) {
  tm = tm || {};
  var parts = SEGMENTS.filter(function (s) { return isNum(tm[s[0]]); }).map(function (s) { return s[2] + ' ' + fmtMs(tm[s[0]]); });
  if (isNum(total)) parts.push('Hub total ' + fmtMs(total));
  if (isNum(rtt)) parts.push('Round trip ' + fmtMs(rtt));
  return parts;
}
function breakdownText(tm, total, rtt) { return breakdownParts(tm, total, rtt).join(' · '); }
function breakdownEl(tm, total, rtt) {
  var el = h('p', { class: 't-break' });
  breakdownParts(tm, total, rtt).forEach(function (p, i) {
    if (i) el.appendChild(document.createTextNode(' · '));
    el.appendChild(h('span', null, p));
  });
  return el;
}
function ribbon(tm, total) {
  tm = tm || {};
  var label = breakdownText(tm, total) || 'No timing data';
  var wrap = h('span', { class: 'ribbon', role: 'img', 'aria-label': 'Latency: ' + label, title: label });
  var fill = h('span', { class: 'rb-fill' });
  wrap.appendChild(fill);
  if (!isNum(total)) { fill.style.width = '0'; return wrap; }
  fill.style.width = Math.min(100, total / RIBBON_MAX_MS * 100).toFixed(2) + '%';
  var sum = 0;
  SEGMENTS.forEach(function (s) {
    var v = tm[s[0]];
    if (isNum(v) && v > 0) { sum += v; var seg = h('i', { class: 'rb-' + s[1] }); seg.style.flexGrow = String(v); fill.appendChild(seg); }
  });
  var other = Math.max(0, total - sum);
  if (other > 0.5 || !fill.childNodes.length) { var o = h('i', { class: 'rb-other' }); o.style.flexGrow = String(other || 1); fill.appendChild(o); }
  if (total > RIBBON_MAX_MS) wrap.classList.add('is-over');
  return wrap;
}
function turnEl(t) {
  if (t.sys) return h('li', { class: 'sys' }, t.sys);
  var you = h('div', { class: 'b-you' }, h('span', { class: 'who' }, 'You', t.typed ? h('span', { class: 'typed' }, 'typed') : null), h('p', { class: 'b-text' }, t.you || '…'));
  var total = t.timings && isNum(t.timings.total_ms) ? t.timings.total_ms : null;
  var meta = h('div', { class: 't-meta' });
  if (t.fast) meta.appendChild(h('span', { class: 'fast', title: 'Fast path: answered without the AI round-trip' }, icon('bolt'), 'Fast path'));
  (t.tools || []).forEach(function (tool) {
    var name = String((tool && tool.name) || tool || '').replace(/_/g, ' ');
    if (name) meta.appendChild(h('span', { class: 'tool' + (tool && tool.success === false ? ' is-fail' : '') }, name));
  });
  meta.appendChild(ribbon(t.timings, total));
  meta.appendChild(h('span', { class: 'lat' }, fmtMs(total)));
  if (t.audio) {
    var replay = h('button', { class: 'icon-btn sm', type: 'button', title: 'Play again', 'aria-label': 'Play reply again' }, icon('vol'));
    replay.addEventListener('click', function () { unlockAudio(); primeVis(); playReply(t.audio); });
    meta.appendChild(replay);
  }
  var willy = h('div', { class: 'b-willy' + (t.ok === false ? ' is-error' : '') },
    h('span', { class: 'who' }, 'Willy'), renderMd(t.text || ''), meta,
    breakdownEl(t.timings, total, t.rtt));
  return h('li', { class: 'turn' }, you, willy);
}
function renderCaption() {
  var box = $('#caption');
  var t = S.turns.length ? S.turns[S.turns.length - 1] : null;
  if (!t) { box.hidden = true; return; }
  box.hidden = false;
  box.classList.toggle('is-sys', !!t.sys);
  box.classList.toggle('is-error', !t.sys && t.ok === false);
  setText($('#cap-you'), t.sys ? '' : (t.you ? 'You: ' + t.you : ''));
  var plain = t.sys ? t.sys : stripMd(t.text || '');
  var long = plain.length > CAPTION_MAX;
  setText($('#cap-text'), long ? plain.slice(0, CAPTION_MAX).replace(/\s+\S*$/, '') + '…' : plain);
  $('#cap-more').hidden = !long;
}
function renderBadge() {
  var b = $('#chat-badge');
  b.hidden = !U.unread;
  setText(b, U.unread > 9 ? '9+' : String(U.unread));
  $('#chat-btn').setAttribute('aria-label', 'Chat and transcript' + (U.unread ? ', ' + U.unread + ' new' : ''));
}

/* ================================================================ call UI */
function setState(s) {
  C.state = s;
  renderStateLabel();
  renderControls();
  ringKick();
}
function shownState() { return C.inCall && C.muted && (C.state === 'listening' || C.state === 'hearing') ? 'muted' : C.state; }
function renderStateLabel() {
  var st = shownState();
  var txt = STATES[st] || STATES.idle;
  $('#app').setAttribute('data-state', st);
  setText($('#state-label'), txt[0]);
  setText($('#state-hint'), st === 'idle' && C.ended ? C.ended : txt[1]);
  $('#orb').setAttribute('aria-label', !C.inCall && !C.starting ? 'Start call with Willy' : (C.playing ? 'Interrupt Willy' : 'Willy: ' + txt[0]));
}
function renderControls() {
  var active = C.inCall || C.starting;
  $('#app').classList.toggle('in-call', C.inCall);
  var call = $('#call-btn');
  call.classList.toggle('is-end', active);
  call.setAttribute('aria-label', active ? 'End call' : 'Start call');
  setText($('#call-label'), active ? 'End' : 'Call');
  var mute = $('#mute-btn');
  mute.disabled = !C.inCall;
  mute.setAttribute('aria-pressed', String(C.muted));
  mute.setAttribute('aria-label', C.muted ? 'Unmute microphone' : 'Mute microphone');
  $('#mute-use').setAttribute('href', C.muted ? '#i-mic-off' : '#i-mic');
  setText($('#mute-label'), C.muted ? 'Unmute' : 'Mute');
  var stop = $('#stop-btn');
  stop.hidden = !(C.playing || C.inflight || C.state === 'hearing');
  setText($('#stop-label'), C.playing ? 'Tap to interrupt' : C.inflight ? 'Stop waiting' : 'Cancel');
}
function renderTimer() {
  var el = $('#timer');
  if (!C.inCall) { el.hidden = true; return; }
  el.hidden = false;
  setText(el, fmtDur(Date.now() - C.callStart));
}
function fmtDur(ms) {
  var s = Math.max(0, Math.floor(ms / 1000));
  var hh = Math.floor(s / 3600), mm = Math.floor(s % 3600 / 60);
  return (hh ? hh + ':' + pad(mm) : pad(mm)) + ':' + pad(s % 60);
}
function levelOf(rms) { return Math.min(1, Math.sqrt(Math.max(0, rms) / 0.25)); }
function renderMeter(rms, thr) {
  var lvl = levelOf(rms);
  var n = Math.round(lvl * 50);
  if (n !== C.meterN) { C.meterN = n; $('#meter-fill').style.transform = 'scaleX(' + (n / 50).toFixed(2) + ')'; }
  if (isNum(thr)) $('#meter-thr').style.left = (levelOf(thr) * 100).toFixed(1) + '%';
}
function showNotice(text) { var n = $('#notice'); setText(n, text); n.hidden = !text; }

/* ======================================================== avatar ring (canvas) */
function ringInit() {
  R.cv = $('#ring');
  try { R.g = R.cv.getContext('2d'); } catch (e) { R.g = null; }
  R.vals = new Float32Array(RING_BARS / 2 + 1);
  R.tmp = new Float32Array(RING_BARS / 2 + 1);
}
function ringAnimating() { return !RM.matches && !document.hidden && (C.inCall || C.starting || C.playing || !!C.inflight); }
function ringKick() { if (R.g && !R.raf) R.raf = requestAnimationFrame(ringFrame); }
function ringFrame(ts) {
  R.raf = 0;
  drawRing(ts || performance.now());
  if (ringAnimating()) R.raf = requestAnimationFrame(ringFrame);
}
// Log-spaced bands between 90 Hz and 4.2 kHz (where speech lives), 0..1 each.
function spectrum(an) {
  var buf = an.willyBuf || (an.willyBuf = new Uint8Array(an.frequencyBinCount));
  an.getByteFrequencyData(buf);
  var H = R.tmp.length, n = buf.length, ny = an.context.sampleRate / 2;
  for (var k = 0; k < H; k++) {
    var b0 = Math.max(1, Math.floor(90 * Math.pow(4200 / 90, k / H) / ny * n));
    var b1 = Math.max(b0 + 1, Math.ceil(90 * Math.pow(4200 / 90, (k + 1) / H) / ny * n));
    var s = 0, c = 0;
    for (var b = b0; b < b1 && b < n; b++) { s += buf[b]; c++; }
    var v = c ? s / c / 255 : 0;
    R.tmp[k] = Math.max(0, Math.min(1, (v - 0.3) / 0.55));
  }
  return R.tmp;
}
function drawRing(ts) {
  var cv = R.cv, g = R.g, size = cv.clientWidth;
  if (!g || !size) return;
  var dpr = Math.min(2, window.devicePixelRatio || 1), px = Math.round(size * dpr);
  if (cv.width !== px) { cv.width = px; cv.height = px; }
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, size, size);
  var st = shownState(), col = RING_COLORS[st] || RING_COLORS.idle, motion = !RM.matches;
  var c = size / 2, orbR = ($('#orb').clientWidth || size * 0.52) / 2;
  var r0 = orbR + Math.max(5, size * 0.035), maxLen = Math.max(4, c - r0 - 3);
  var H = R.vals.length, t = ts / 1000, data = null;
  if (motion && (st === 'listening' || st === 'hearing') && C.analyser) data = spectrum(C.analyser);
  else if (motion && st === 'speaking' && PB.live && PB.an) data = spectrum(PB.an);
  for (var k = 0; k < H; k++) {
    var v;
    if (data) v = data[k];
    else if (!motion) v = st === 'idle' ? 0 : 0.1;
    else if (st === 'speaking') v = 0.14 + 0.18 * Math.abs(Math.sin(t * 6.5 + k * 0.5) * Math.sin(t * 2.1 + k * 0.23));
    else if (st === 'thinking') v = 0.05 + 0.05 * (1 + Math.sin(t * 3 - k * 0.45)) / 2;
    else if (st === 'connecting') v = 0.04 + 0.05 * (1 + Math.sin(t * 4)) / 2;
    else v = 0.02;
    var prev = R.vals[k];
    R.vals[k] = !motion ? v : v > prev ? prev + (v - prev) * 0.6 : prev * 0.86 + v * 0.14;
  }
  var N = (H - 1) * 2, sum = 0;
  var grad = g.createLinearGradient(0, 0, size, size);
  grad.addColorStop(0, col[0]);
  grad.addColorStop(1, col[1]);
  g.lineWidth = 1;
  g.strokeStyle = 'rgba(255,255,255,.08)';
  g.beginPath(); g.arc(c, c, r0 - 2, 0, Math.PI * 2); g.stroke();
  g.strokeStyle = grad;
  g.lineCap = 'round';
  g.lineWidth = Math.max(1.5, Math.min(4, (2 * Math.PI * r0 / N) * 0.46));
  g.beginPath();
  for (var i = 0; i < N; i++) {
    var j = i <= N / 2 ? i : N - i, val = R.vals[j];
    sum += val;
    var a = -Math.PI / 2 + i / N * Math.PI * 2, len = 1 + val * maxLen, cs = Math.cos(a), sn = Math.sin(a);
    g.moveTo(c + cs * r0, c + sn * r0);
    g.lineTo(c + cs * (r0 + len), c + sn * (r0 + len));
  }
  g.globalAlpha = st === 'idle' ? 0.45 : 1;
  g.stroke();
  g.globalAlpha = 1;
  if (st === 'thinking' || st === 'connecting') {
    var rot = motion ? t * 2.6 : 0, rr = r0 + Math.min(maxLen * 0.5, 22);
    g.lineWidth = Math.max(2, size * 0.01);
    g.beginPath(); g.arc(c, c, rr, rot, rot + 1.2); g.stroke();
    g.beginPath(); g.arc(c, c, rr, rot + Math.PI, rot + Math.PI + 0.55); g.stroke();
  }
  var lv = Math.min(1, (sum / N) * 2.4);
  if (Math.abs(lv - R.lvl) > 0.01) { R.lvl = lv; $('#app').style.setProperty('--lvl', lv.toFixed(3)); }
}

/* ================================================================== audio out */
var player = null, audioUnlocked = false;
function getPlayer() {
  if (!player) {
    player = new Audio();
    player.preload = 'auto';
    player.addEventListener('ended', onPlaybackEnd);
    player.addEventListener('error', function () { if (C.playing) onPlaybackEnd(); });
    applySpeaker();
  }
  return player;
}
// iOS only lets a page play audio later if it started playback during a user gesture.
function unlockAudio() {
  if (audioUnlocked) return;
  audioUnlocked = true;
  try { var p = getPlayer(); p.src = SILENT_WAV; var pr = p.play(); if (pr && pr.catch) pr.catch(noop); } catch (e) { /* ignore */ }
}
function applySpeaker() {
  if (!player) return;
  try { player.volume = O.vol; } catch (e) { /* read-only on iOS */ }
  player.muted = O.voiceMuted;
}
function playReply(b64) {
  var p = getPlayer();
  try { p.pause(); } catch (e) { /* ignore */ }
  discardRecorder();
  shadowStop();
  C.playing = true;
  C.speakStart = performance.now();
  C.barge = 0;
  setState('speaking');
  applySpeaker();
  p.src = 'data:audio/mpeg;base64,' + b64;
  var pr = p.play();
  if (pr && pr.catch) pr.catch(function () {
    if (!C.playing) return;
    C.playing = false;
    shadowStop();
    addSys('The browser blocked audio. Use the speaker button on the reply to hear it.');
    afterTurn();
  });
  shadowStart(b64, PB.token);
}
function stopPlayback() {
  shadowStop();
  if (!C.playing) return;
  C.playing = false;
  try { getPlayer().pause(); } catch (e) { /* ignore */ }
}
function onPlaybackEnd() {
  shadowStop();
  if (!C.playing) return;
  C.playing = false;
  afterTurn();
}
function afterTurn() {
  if (C.inCall) toListening(COOLDOWN_MS);
  else setState('idle');
}

/* The ring reacts to Willy's voice through a silent copy of the reply decoded into Web Audio and
   fed to an AnalyserNode (gain 0), started in sync with the <audio> element. The audible path stays
   a plain <audio> element, so iOS silent-switch/routing behaviour is unchanged. */
function primeVis() {
  if (RM.matches) return;
  var AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return;
  try {
    if (!PB.vctx) PB.vctx = new AC();
    if (PB.vctx.state === 'suspended' && PB.vctx.resume) PB.vctx.resume().catch(noop);
  } catch (e) { PB.vctx = null; }
}
function shadowCtx() {
  if (C.ctx && C.ctx.state === 'running') return C.ctx;
  if (PB.vctx && PB.vctx.state === 'running') return PB.vctx;
  return null;
}
function decodeAudio(ctx, ab) {
  return new Promise(function (resolve, reject) {
    try { var p = ctx.decodeAudioData(ab, resolve, reject); if (p && p.then) p.then(resolve, reject); } catch (e) { reject(e); }
  });
}
function shadowStart(b64, token) {
  if (RM.matches) return;
  var ctx = shadowCtx();
  if (!ctx) return;
  var bytes;
  try { var bin = atob(b64); bytes = new Uint8Array(bin.length); for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i); } catch (e) { return; }
  decodeAudio(ctx, bytes.buffer).then(function (buf) {
    if (token !== PB.token || !C.playing || ctx.state !== 'running') return;
    var p = getPlayer();
    var go = function () {
      if (token !== PB.token || !C.playing || ctx.state !== 'running') return;
      var an = ctx.willyAn;
      if (!an) {
        an = ctx.createAnalyser();
        an.fftSize = 512;
        an.smoothingTimeConstant = 0.7;
        var mute = ctx.createGain();
        mute.gain.value = 0;
        an.connect(mute);
        mute.connect(ctx.destination);
        ctx.willyAn = an;
      }
      var src = ctx.createBufferSource();
      src.buffer = buf;
      src.connect(an);
      try { src.start(0, Math.min(Math.max(0, p.currentTime || 0), Math.max(0, buf.duration - 0.05))); } catch (e) { return; }
      PB.src = src; PB.an = an; PB.live = true;
      src.onended = function () { if (PB.src === src) { PB.live = false; PB.src = null; } };
    };
    if (!p.paused && p.currentTime > 0) go();
    else {
      var once = function () { p.removeEventListener('playing', once); go(); };
      p.addEventListener('playing', once);
    }
  }, noop);
}
function shadowStop() {
  PB.token++;
  PB.live = false;
  var s = PB.src;
  PB.src = null;
  if (s) { s.onended = null; try { s.stop(); } catch (e) { /* ignore */ } try { s.disconnect(); } catch (e) { /* ignore */ } }
}

/* ============================================================ microphone */
function pickMime() {
  if (!window.MediaRecorder || !MediaRecorder.isTypeSupported) return '';
  var list = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4;codecs=mp4a.40.2', 'audio/mp4', 'audio/aac'];
  for (var i = 0; i < list.length; i++) { try { if (MediaRecorder.isTypeSupported(list[i])) return list[i]; } catch (e) { /* ignore */ } }
  return '';
}
function extFor(type) {
  type = String(type || '').toLowerCase();
  if (type.indexOf('webm') >= 0) return 'webm';
  if (type.indexOf('ogg') >= 0) return 'ogg';
  if (type.indexOf('mp4') >= 0 || type.indexOf('aac') >= 0 || type.indexOf('m4a') >= 0) return 'm4a';
  if (type.indexOf('wav') >= 0) return 'wav';
  if (type.indexOf('mpeg') >= 0) return 'mp3';
  return 'webm';
}
function startRecorder() {
  if (!C.stream || C.recFailed) return null;
  var rec = null;
  try { rec = C.mime ? new MediaRecorder(C.stream, { mimeType: C.mime, audioBitsPerSecond: 32000 }) : new MediaRecorder(C.stream); }
  catch (e) { try { rec = new MediaRecorder(C.stream); } catch (e2) { rec = null; } }
  if (!rec) { C.recFailed = true; showNotice('This browser can’t record audio here. Type to Willy in the chat instead.'); return null; }
  var chunks = [];
  rec.ondataavailable = function (ev) { if (ev.data && ev.data.size) chunks.push(ev.data); };
  rec.willyChunks = chunks;
  try { rec.start(250); } catch (e) { return null; }
  C.rec = rec;
  C.recStart = performance.now();
  return rec;
}
function discardRecorder() {
  var rec = C.rec;
  C.rec = null;
  if (!rec) return;
  rec.ondataavailable = null;
  rec.onstop = null;
  try { if (rec.state !== 'inactive') rec.stop(); } catch (e) { /* ignore */ }
}
function finishRecorder() {
  var rec = C.rec;
  C.rec = null;
  return new Promise(function (resolve) {
    if (!rec) { resolve(null); return; }
    var chunks = rec.willyChunks || [];
    var done = function () {
      var type = rec.mimeType || C.mime || (chunks[0] && chunks[0].type) || 'audio/webm';
      resolve(chunks.length ? new Blob(chunks, { type: type }) : null);
    };
    rec.onstop = done;
    try { if (rec.state !== 'inactive') rec.stop(); else done(); } catch (e) { resolve(null); }
  });
}
function readRms() {
  if (!C.analyser) return 0;
  C.analyser.getByteTimeDomainData(C.buf);
  var sum = 0;
  for (var i = 0; i < C.buf.length; i++) { var v = (C.buf[i] - 128) / 128; sum += v * v; }
  return Math.sqrt(sum / C.buf.length);
}
function vadTick() {
  if (!C.inCall) return;
  var rms = C.muted ? 0 : readRms();
  var now = performance.now();
  var thr = Math.max(0.012, C.noise * 3.2);
  renderMeter(rms, thr);
  if (C.muted) return;
  if (C.state === 'listening') {
    if (rms > thr && now >= C.cooldownUntil) {
      if (++C.above >= START_TICKS) beginUtterance();
    } else {
      C.above = 0;
      if (rms < thr) C.noise = C.noise * 0.96 + rms * 0.04;
      if (!C.rec) startRecorder();
      else if (now - C.recStart > PREROLL_MS) { discardRecorder(); startRecorder(); }
    }
  } else if (C.state === 'hearing') {
    if (rms > thr * 0.65) { C.silenceMs = 0; C.speechMs += TICK_MS; }
    else if ((C.silenceMs += TICK_MS) >= (C.speechMs < 1200 ? SHORT_SILENCE_MS : SILENCE_MS)) { endUtterance(); return; }
    if (now - C.uttStart > MAX_UTTER_MS) endUtterance();
  } else if (C.state === 'speaking') {
    if (now - C.speakStart < BARGE_GRACE_MS) return;
    if (rms > Math.max(0.045, C.noise * 7)) { if (++C.barge >= BARGE_TICKS) bargeIn(); }
    else C.barge = Math.max(0, C.barge - 1);
  }
}
function toListening(cooldown) {
  if (!C.inCall) { setState('idle'); return; }
  C.above = 0; C.barge = 0; C.speechMs = 0; C.silenceMs = 0;
  C.cooldownUntil = performance.now() + (cooldown || 0);
  if (!C.rec) startRecorder();
  setState('listening');
}
function beginUtterance() {
  C.uttStart = performance.now();
  C.speechMs = TICK_MS * START_TICKS;
  C.silenceMs = 0;
  if (!C.rec) startRecorder();
  setState('hearing');
}
function endUtterance() {
  var speech = C.speechMs;
  setState('thinking');
  finishRecorder().then(function (blob) {
    if (!C.inCall || C.state !== 'thinking') return;
    if (!blob || speech < MIN_SPEECH_MS || blob.size < 1500) { toListening(); return; }
    sendUtterance(blob);
  });
}
function bargeIn() {
  stopPlayback();
  if (!C.inCall) return;
  startRecorder();
  beginUtterance();
}
function abortInflight() {
  var c = C.inflight;
  C.inflight = null;
  if (c) { try { c.abort(); } catch (e) { /* ignore */ } }
}
function handleReply(d, text, typed, t0) {
  var turn = { you: typed ? text : (d.transcript || '…'), typed: typed, text: d.reply || 'Done.', ok: d.success !== false, fast: !!d.fast_path, tools: d.tools || [], timings: d.timings || {}, rtt: Math.round(performance.now() - t0), ts: Date.now() };
  if (d.audio_base64) turn.audio = d.audio_base64;
  pushTurn(turn);
  if (d.ui) handleUi(d.ui);
  if (d.audio_base64) playReply(d.audio_base64); else afterTurn();
}
function sendUtterance(blob) {
  var ctrl = new AbortController();
  C.inflight = ctrl;
  setState('thinking');
  var fd = new FormData();
  fd.append('file', blob, 'speech.' + extFor(blob.type));
  var t0 = performance.now();
  fetch(BASE + '/api/v1/call/interact?session_id=' + encodeURIComponent(SESSION_ID), {
    method: 'POST', headers: { 'Authorization': 'Bearer ' + S.token, 'X-Willy-Client': CLIENT }, body: fd, signal: ctrl.signal, cache: 'no-store'
  }).then(function (res) {
    return res.text().then(function (txt) { var d = null; try { d = txt ? JSON.parse(txt) : null; } catch (e) { d = null; } return { status: res.status, data: d }; });
  }).then(function (r) {
    if (C.inflight !== ctrl) return;
    C.inflight = null;
    if (r.status === 401) { onUnauthorized(); return; }
    var d = r.data || {};
    if (r.status === 400) { afterTurn(); return; }
    if (!r.data || r.status >= 500) { addSys('The hub couldn’t process that audio (HTTP ' + r.status + ').'); afterTurn(); return; }
    if (d.success === false && !d.reply) {
      addSys(/no speech/i.test(String(d.error || '')) ? 'Didn’t catch that. Try again.' : String(d.error || 'Something went wrong.'));
      afterTurn();
      return;
    }
    handleReply(d, '', false, t0);
  }).catch(function (err) {
    if (C.inflight !== ctrl) return;
    C.inflight = null;
    if (err && err.name === 'AbortError') return;
    addSys('Can’t reach the Willy hub. Check your connection.');
    afterTurn();
  });
}
function secureEnough() {
  return window.isSecureContext || location.hostname === 'localhost' || location.hostname === '127.0.0.1';
}
function startCall() {
  if (C.inCall || C.starting) return;
  showNotice('');
  C.ended = '';
  if (!secureEnough() || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    showNotice('Voice needs a secure (https) connection to this hub. You can still type to Willy in the chat.');
    return;
  }
  if (!window.MediaRecorder) { showNotice('This browser can’t record audio. Type to Willy in the chat instead.'); return; }
  var AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) { showNotice('This browser can’t analyse audio. Type to Willy in the chat instead.'); return; }
  unlockAudio();
  try { C.ctx = new AC(); if (C.ctx.state === 'suspended' && C.ctx.resume) C.ctx.resume(); } catch (e) { C.ctx = null; }
  if (!C.ctx) { showNotice('The browser refused to start audio processing.'); return; }
  C.starting = true;
  setState('connecting');
  navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } }).then(function (stream) {
    C.starting = false;
    if (!S.token || !C.ctx) { stopTracks(stream); return; }
    C.stream = stream;
    var src = C.ctx.createMediaStreamSource(stream);
    C.analyser = C.ctx.createAnalyser();
    C.analyser.fftSize = 1024;
    C.analyser.smoothingTimeConstant = 0;
    src.connect(C.analyser);
    C.buf = new Uint8Array(C.analyser.fftSize);
    C.mime = pickMime();
    stream.getAudioTracks().forEach(function (t) {
      t.addEventListener('ended', function () { if (C.inCall) { endCall(); showNotice('The microphone was disconnected.'); } });
    });
    C.inCall = true;
    C.muted = false;
    C.noise = 0.006;
    C.callStart = Date.now();
    clearInterval(C.clock);
    C.clock = setInterval(renderTimer, 1000);
    renderTimer();
    clearInterval(C.tick);
    C.tick = setInterval(vadTick, TICK_MS);
    requestWake();
    announce('Call started. Willy is listening.');
    if (C.playing) setState('speaking'); else toListening(0);
  }, function (err) {
    C.starting = false;
    teardownAudio();
    setState('idle');
    var name = err && err.name;
    showNotice(name === 'NotAllowedError' || name === 'SecurityError'
      ? 'Microphone access is blocked. Allow it in your browser’s site settings, then start the call again.'
      : name === 'NotFoundError' ? 'No microphone was found. Connect one, or type to Willy in the chat.'
      : 'Couldn’t start the microphone: ' + ((err && err.message) || 'unknown error') + '.');
  });
}
function teardownAudio() {
  if (C.stream) { stopTracks(C.stream); C.stream = null; }
  if (C.ctx) { try { C.ctx.close(); } catch (e) { /* ignore */ } C.ctx = null; }
  C.analyser = null;
}
function endCall() {
  if (!C.inCall && !C.starting) return;
  var wasIn = C.inCall, dur = Date.now() - C.callStart;
  C.inCall = false;
  C.starting = false;
  C.muted = false;
  clearInterval(C.tick); clearInterval(C.clock);
  discardRecorder();
  abortInflight();
  stopPlayback();
  teardownAudio();
  releaseWake();
  stopCamera();
  C.meterN = -1;
  renderMeter(0);
  renderTimer();
  if (wasIn) { C.ended = 'Call ended · ' + fmtDur(dur) + '. Call again any time.'; announce('Call ended.'); }
  setState('idle');
}
function toggleMute() {
  if (!C.inCall || !C.stream) return;
  C.muted = !C.muted;
  C.stream.getAudioTracks().forEach(function (t) { t.enabled = !C.muted; });
  if (C.muted && C.state === 'hearing') { discardRecorder(); toListening(); }
  renderControls();
  renderStateLabel();
  ringKick();
}
function interrupt() {
  if (C.playing) { stopPlayback(); afterTurn(); return; }
  if (C.inflight) {
    abortInflight();
    addSys('Stopped waiting. If Willy already started an action on your PC, it may still finish.');
    afterTurn();
    return;
  }
  if (C.state === 'hearing') { discardRecorder(); toListening(300); }
}
function requestWake() {
  if (!('wakeLock' in navigator) || C.wake) return;
  navigator.wakeLock.request('screen').then(function (w) {
    if (!C.inCall) { w.release().catch(noop); return; }
    C.wake = w;
    w.addEventListener('release', function () { if (C.wake === w) C.wake = null; });
  }).catch(noop);
}
function releaseWake() { var w = C.wake; C.wake = null; if (w) w.release().catch(noop); }

/* ============================================================ typed input */
function sendTyped(text) {
  unlockAudio();
  primeVis();
  discardRecorder();
  stopPlayback();
  abortInflight();
  var ctrl = new AbortController();
  C.inflight = ctrl;
  setState('thinking');
  var t0 = performance.now();
  api('/api/v1/command', { method: 'POST', body: { query: text, return_audio: true, session_id: SESSION_ID, source: CLIENT }, signal: ctrl.signal, timeout: 120000 }).then(function (d) {
    if (C.inflight !== ctrl) return;
    C.inflight = null;
    handleReply(d || {}, text, true, t0);
  }, function (err) {
    if (C.inflight !== ctrl) return;
    C.inflight = null;
    if (err && err.name === 'AbortError') return;
    if (err.status !== 401) { pushTurn({ you: text, typed: true, text: err.message, ok: false, timings: {}, ts: Date.now() }); afterTurn(); }
  });
}

/* ============================================================ speaker volume */
function loadSpeaker() {
  var v = parseFloat(local.get(KEYS.vol));
  O.vol = isNum(v) ? Math.max(0, Math.min(1, v)) : 1;
  O.voiceMuted = local.get(KEYS.spk) === '1';
  renderSpeaker();
}
function renderSpeaker() {
  var off = O.voiceMuted || O.vol === 0;
  var ic = off ? 'vol-off' : O.vol < 0.5 ? 'vol-low' : 'vol';
  $('#spk-use').setAttribute('href', '#i-' + ic);
  $('#spk-mute-use').setAttribute('href', O.voiceMuted ? '#i-vol-off' : '#i-vol');
  $('#spk-mute').setAttribute('aria-pressed', String(O.voiceMuted));
  $('#spk-mute').setAttribute('aria-label', O.voiceMuted ? 'Unmute Willy’s voice' : 'Mute Willy’s voice');
  $('#spk-vol').value = String(Math.round(O.vol * 100));
  setText($('#spk-val'), Math.round(O.vol * 100) + '%');
  setText($('#spk-label'), O.voiceMuted ? 'Silenced' : 'Speaker');
  $('#spk-btn').classList.toggle('is-on', O.voiceMuted);
  $('#spk-btn').setAttribute('aria-label', 'Willy’s voice volume' + (O.voiceMuted ? ' (muted)' : ', ' + Math.round(O.vol * 100) + '%'));
  applySpeaker();
}
function openSpk(open) {
  var pop = $('#spk-pop'), btn = $('#spk-btn');
  if (open === !pop.hidden) return;
  pop.hidden = !open;
  btn.setAttribute('aria-expanded', String(open));
  if (open) focusSoon('#spk-vol');
}

/* ======================================================= panels: layout */
function setDrawer(open, opts) {
  opts = opts || {};
  if (U.drawer !== open) {
    U.drawer = open;
    if (open && MOBILE.matches && U.sheet) closePanel();
    var d = $('#drawer');
    var hadFocus = d.contains(document.activeElement);
    d.classList.toggle('is-open', open);
    d.setAttribute('aria-hidden', String(!open));
    d.inert = !open;
    $('#app').classList.toggle('drawer-open', open);
    var btn = $('#chat-btn');
    btn.setAttribute('aria-expanded', String(open));
    btn.classList.toggle('is-on', open);
    if (open) { U.unread = 0; renderBadge(); scrollTurns(); }
    else if (hadFocus || opts.returnFocus) btn.focus();
    ringKick();
  }
  if (open && opts.focus) focusSoon('#type-input');
}
function showSheet(open) {
  var s = $('#sheet');
  s.classList.toggle('is-open', open);
  s.setAttribute('aria-hidden', String(!open));
  s.inert = !open;
  $('#app').classList.toggle('sheet-open', open);
  var b = $('#panel-btn');
  b.setAttribute('aria-expanded', String(open));
  b.classList.toggle('is-on', open);
  ringKick();
  if (open) setTimeout(scrollCrumbs, 420);
}
function openPanel(tab, opts) {
  opts = opts || {};
  if (PANELS.indexOf(tab) < 0) tab = U.tab;
  if (MOBILE.matches && U.drawer) setDrawer(false);
  var fresh = !U.sheet;
  U.sheet = true;
  showSheet(true);
  switchTab(tab, opts, fresh);
  if (opts.focus) focusSoon('#tab-' + tab);
  if (opts.auto) announce('Opened the ' + PANEL_TITLES[tab] + ' panel. The call is still on.');
}
function closePanel(opts) {
  if (!U.sheet) return;
  var s = $('#sheet'), hadFocus = s.contains(document.activeElement);
  U.sheet = false;
  showSheet(false);
  stopCamera();
  stopAuto();
  if (hadFocus || (opts && opts.returnFocus)) $('#panel-btn').focus();
}
function switchTab(tab, opts, fresh) {
  opts = opts || {};
  var changed = tab !== U.tab;
  if (changed && U.tab === 'camera') stopCamera();
  if (changed && U.tab === 'screenshot') stopAuto();
  U.tab = tab;
  local.set(KEYS.tab, tab);
  PANELS.forEach(function (p) {
    var t = $('#tab-' + p), on = p === tab;
    t.setAttribute('aria-selected', String(on));
    t.tabIndex = on ? 0 : -1;
    $('#pane-' + p).hidden = !on;
  });
  setText($('#sheet-title'), (opts.title && String(opts.title).slice(0, 80)) || PANEL_TITLES[tab]);
  renderSheetSub();
  enterPane(tab, opts, fresh || changed);
}
function renderSheetSub() {
  setText($('#sheet-sub'), U.tab === 'camera' ? 'This device’s camera' : (P.name ? P.name : 'Your PC'));
}
function enterPane(tab, opts, fresh) {
  if (tab === 'files') {
    if (opts.path != null || opts.device) loadFiles(opts.path || '', opts.device || null);
    else if ((!F.loaded && !F.loading) || F.offline) loadFiles(F.path, null, F.offline);
  } else if (tab === 'screenshot') {
    if (fresh || opts.auto || opts.device || !X.b64) snap(opts.device || null);
  } else if (tab === 'camera') {
    if (!K.blob) startCamera();
  }
}
// "ui": {"panel": "files"|"screenshot"|"camera", "device_id", "path", "title"} on a reply
function handleUi(ui) {
  if (!ui || typeof ui !== 'object') return;
  var p = String(ui.panel || '').toLowerCase();
  if (p === 'screen' || p === 'screenshots') p = 'screenshot';
  if (p === 'file' || p === 'folder') p = 'files';
  if (PANELS.indexOf(p) < 0) return;
  openPanel(p, {
    auto: true,
    device: typeof ui.device_id === 'string' && ui.device_id ? ui.device_id : null,
    path: typeof ui.path === 'string' && ui.path ? ui.path : null,
    title: typeof ui.title === 'string' ? ui.title : ''
  });
}

/* ======================================================= panels: the PC */
function ensurePc(pref, force) {
  if (!force && P.id && (!pref || pref === P.id)) return Promise.resolve(true);
  return api('/api/v1/devices', { timeout: 10000 }).then(function (r) {
    var list = r && Array.isArray(r.devices) ? r.devices.filter(Boolean) : [];
    var want = pref || P.id, pick = null;
    if (want) pick = list.filter(function (d) { return d.device_id === want && d.online; })[0] || null;
    if (!pick && pref && !list.some(function (d) { return d.device_id === pref; })) pick = { device_id: pref, name: '' };
    if (!pick) pick = list.filter(function (d) { return d.device_type === 'pc' && d.online; })[0] || null;
    if (!pick) { P.id = null; P.name = ''; renderSheetSub(); return false; }
    P.id = pick.device_id;
    P.name = pick.name || pick.hostname || 'Your PC';
    renderSheetSub();
    return true;
  });
}
function act(action, payload, timeout) {
  if (!P.id) return Promise.reject(ApiError('Your PC is offline.', 0));
  return api('/api/v1/devices/' + encodeURIComponent(P.id) + '/action', {
    method: 'POST', body: { action: action, payload: payload || {}, source: CLIENT }, timeout: timeout || 30000
  }).then(function (r) {
    r = r || {};
    if (r.success === false) throw ApiError(String(r.error || r.message || r.reply || 'That didn’t work on your PC.'), 0);
    return r;
  });
}
function stateBox(el, ico, title, text, btnLabel, fn) {
  var kids = [h('span', { class: 'ico' }, icon(ico)), h('b', null, title)];
  if (text) kids.push(h('p', null, text));
  if (btnLabel) kids.push(h('button', { class: 'btn btn-sm', type: 'button', onclick: fn }, btnLabel));
  el.replaceChildren.apply(el, kids);
  el.hidden = false;
}

/* ======================================================= panels: files */
var EXT = {};
function extMap(list, t) { list.split(' ').forEach(function (e) { EXT[e] = t; }); }
extMap('jpg jpeg png gif webp bmp svg heic heif tif tiff ico avif raw', 'image');
extMap('mp4 mov mkv avi webm wmv m4v flv mpg mpeg 3gp', 'video');
extMap('mp3 wav m4a flac ogg aac wma opus mid', 'audio');
extMap('pdf', 'pdf');
extMap('doc docx rtf odt txt md pages epub', 'doc');
extMap('xls xlsx csv ods numbers tsv', 'sheet');
extMap('ppt pptx odp key', 'slides');
extMap('zip rar 7z tar gz tgz bz2 xz iso cab', 'archive');
extMap('py js ts jsx tsx json html htm css scss c cpp h hpp cs java go rs rb php sh ps1 yml yaml toml ini cfg xml sql log ipynb kt swift', 'code');
extMap('exe msi lnk app dmg apk appx msix bat cmd', 'app');
var TYPE_ICON = { folder: 'folder', image: 'image', video: 'film', audio: 'music', pdf: 'doc', doc: 'doc', sheet: 'sheet', slides: 'monitor', archive: 'archive', code: 'code', app: 'app', file: 'file' };
var TYPE_NAME = { image: 'Image', video: 'Video', audio: 'Audio', pdf: 'PDF', doc: 'Document', sheet: 'Spreadsheet', slides: 'Slides', archive: 'Archive', code: 'Code', app: 'App' };
function typeOf(e) {
  if (e.folder) return 'folder';
  var m = /\.([^.\\\/]+)$/.exec(e.name || '');
  return (m && EXT[m[1].toLowerCase()]) || 'file';
}
var collator = window.Intl && Intl.Collator ? new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' }) : null;
function cmpName(a, b) { return collator ? collator.compare(a.name, b.name) : (a.name.toLowerCase() < b.name.toLowerCase() ? -1 : 1); }
function sepOf(p) { return !p || /\\/.test(p) || /^[A-Za-z]:/.test(p) ? '\\' : '/'; }
function joinPath(dir, name) { return dir.replace(/[\\\/]+$/, '') + sepOf(dir) + name; }
function crumbsOf(path) {
  var out = [{ label: P.name || 'This PC', path: '' }];
  if (!path) return out;
  var sep = sepOf(path), unc = /^\\\\/.test(path), posix = path.charAt(0) === '/';
  var parts = path.split(/[\\\/]+/).filter(Boolean), acc = '';
  if (posix) out.push({ label: '/', path: '/' });
  parts.forEach(function (p, i) {
    var drive = i === 0 && /^[A-Za-z]:$/.test(p);
    if (i === 0) acc = unc ? '\\\\' + p : posix ? '/' + p : (drive ? p + '\\' : p);
    else acc = acc.replace(/[\\\/]+$/, '') + sep + p;
    out.push({ label: drive ? p + '\\' : p, path: acc });
  });
  return out;
}
function loadFiles(path, dev, force) {
  var seq = ++F.seq;
  F.loading = true;
  F.err = '';
  renderFilesChrome();
  if (!F.loaded) stateBox($('#f-state'), 'folder', 'Opening your files…', '', null);
  return ensurePc(dev, force).then(function (ok) {
    if (seq !== F.seq) return;
    if (!ok) { F.loading = false; F.offline = true; renderFiles(); return; }
    F.offline = false;
    return act('list_dir', { path: path || '' }, 30000).then(function (r) {
      if (seq !== F.seq) return;
      var here = typeof r.path === 'string' ? r.path : (path || '');
      F.loading = false;
      F.loaded = true;
      F.path = here;
      F.parent = r.parent == null || r.parent === here ? null : String(r.parent);
      F.entries = (Array.isArray(r.entries) ? r.entries : []).filter(function (e) { return e && typeof e.name === 'string'; }).map(function (e) {
        return { name: e.name, path: typeof e.path === 'string' && e.path ? e.path : joinPath(here || '', e.name), folder: !!e.folder, size: isNum(e.size) ? e.size : null, modified: isNum(e.modified) ? e.modified : null };
      });
      if (Array.isArray(r.drives) && r.drives.length) F.drives = r.drives.filter(function (d) { return d && typeof d.name === 'string'; });
      F.sel = {};
      F.renaming = null;
      F.creating = false;
      F.filter = '';
      $('#f-filter').value = '';
      renderFiles();
      $('#f-scroll').scrollTop = 0;
    });
  }).catch(function (e) {
    if (seq !== F.seq) return;
    F.loading = false;
    if (F.loaded) errToast(e); else F.err = (e && e.message) || 'Couldn’t open that folder.';
    renderFiles();
  });
}
function visibleEntries() {
  var q = F.filter.trim().toLowerCase();
  var list = F.entries.filter(function (e) { return !q || e.name.toLowerCase().indexOf(q) >= 0; });
  list.sort(function (a, b) {
    if (a.folder !== b.folder) return a.folder ? -1 : 1;
    if (F.sort === 'modified') return (b.modified || 0) - (a.modified || 0) || cmpName(a, b);
    if (F.sort === 'size') return (b.size || 0) - (a.size || 0) || cmpName(a, b);
    return cmpName(a, b);
  });
  return list;
}
function renderFilesChrome() {
  $('#pane-files').classList.toggle('is-loading', F.loading);
  $('#f-list').setAttribute('aria-busy', String(F.loading));
  $('#f-up').disabled = !F.path || F.loading || F.offline;
  $('#f-new').disabled = !F.path || !!F.pick || F.offline || !F.loaded;
}
function renderFiles() {
  renderFilesChrome();
  renderCrumbs();
  renderDrives();
  var pk = $('#f-pick');
  pk.hidden = !F.pick;
  if (F.pick) {
    var n = F.pick.items.length, verb = F.pick.op === 'move' ? 'Move' : 'Copy';
    setText($('#f-pick-text'), verb + ' ' + (n === 1 ? '“' + F.pick.items[0].name + '”' : n + ' items') + ': open the folder you want, then press ' + verb + ' here.');
    setText($('#f-pick-go-text'), verb + ' here');
    $('#f-pick-go').disabled = !F.path || F.loading;
  }
  var st = $('#f-state');
  if (F.offline) {
    $('#f-list').replaceChildren();
    $('#f-selbar').hidden = true;
    $('#f-drives').hidden = true;
    stateBox(st, 'laptop', 'Your PC is offline', 'Start the Willy PC client on your computer, then try again.', 'Try again', function () { loadFiles(F.path, null, true); });
    return;
  }
  if (!F.loaded) {
    $('#f-list').replaceChildren();
    if (F.err) stateBox(st, 'alert', 'Couldn’t open your files', F.err, 'Try again', function () { loadFiles(F.path, null, true); });
    return;
  }
  renderList();
}
function renderCrumbs() {
  var nav = $('#f-crumbs'), list = crumbsOf(F.path), frag = document.createDocumentFragment();
  list.forEach(function (c, i) {
    if (i) frag.appendChild(h('span', { class: 'crumb-sep', 'aria-hidden': 'true' }, icon('chev')));
    var last = i === list.length - 1;
    frag.appendChild(last
      ? h('span', { class: 'crumb is-here', 'aria-current': 'location' }, i === 0 ? icon('laptop') : null, c.label)
      : h('button', { class: 'crumb', type: 'button', 'data-path': c.path }, i === 0 ? icon('laptop') : null, c.label));
  });
  nav.replaceChildren(frag);
  scrollCrumbs();
}
function scrollCrumbs() {
  var nav = $('#f-crumbs');
  nav.scrollLeft = nav.scrollWidth;
  requestAnimationFrame(function () { nav.scrollLeft = nav.scrollWidth; });
}
function renderDrives() {
  var box = $('#f-drives'), ds = F.drives || [];
  box.hidden = !ds.length || F.offline;
  if (!ds.length) return;
  var low = F.path.toLowerCase();
  var kids = [h('button', { class: 'chip' + (!F.path ? ' is-on' : ''), type: 'button', 'data-path': '', 'aria-pressed': String(!F.path), title: 'Drives and home folders' }, icon('home'), h('b', null, 'Home'))];
  ds.forEach(function (d) {
    var name = String(d.name), on = !!F.path && low.indexOf(name.toLowerCase()) === 0;
    var total = isNum(d.total_gb) ? d.total_gb : 0, free = isNum(d.free_gb) ? d.free_gb : null;
    var tip = name + (d.label ? ' (' + d.label + ')' : '') + (free != null ? ' · ' + fmtGb(free) + ' free' + (total ? ' of ' + fmtGb(total) : '') : '');
    var chip = h('button', { class: 'chip' + (on ? ' is-on' : ''), type: 'button', 'data-path': name, 'aria-pressed': String(on), title: tip, 'aria-label': tip },
      icon('drive'), h('b', null, name.replace(/[\\\/]+$/, '')), d.label ? h('span', null, String(d.label).slice(0, 24)) : null, free != null ? h('small', null, fmtGb(free) + ' free') : null);
    if (total && free != null) chip.style.setProperty('--use', Math.max(0, Math.min(1, 1 - free / total)).toFixed(3));
    kids.push(chip);
  });
  box.replaceChildren.apply(box, kids);
}
function renderList() {
  var ul = $('#f-list'), st = $('#f-state'), view = visibleEntries();
  F.view = view;
  var frag = document.createDocumentFragment();
  if (F.creating) frag.appendChild(nameRow(null));
  view.slice(0, LIST_CAP).forEach(function (e, i) { frag.appendChild(F.renaming === e.path ? nameRow(e) : fileRow(e, i)); });
  if (view.length > LIST_CAP) frag.appendChild(h('li', { class: 'f-more-note' }, 'Showing the first ' + LIST_CAP + ' of ' + view.length + '. Filter to find the rest.'));
  ul.replaceChildren(frag);
  if (!view.length && !F.creating) {
    if (F.filter.trim()) stateBox(st, 'search', 'No matches', 'Nothing in this folder matches “' + F.filter.trim() + '”.', null);
    else stateBox(st, 'folder', 'This folder is empty', F.path ? '' : 'Pick a drive above.', null);
  } else st.hidden = true;
  renderSel();
}
function fileRow(e, i) {
  var sel = !!F.sel[e.path], t = typeOf(e), picking = !!F.pick;
  var date = fmtDate(e.modified);
  var meta = e.folder ? ['Folder', date].filter(Boolean).join(' · ') : [fmtSize(e.size), TYPE_NAME[t], date].filter(Boolean).join(' · ');
  var li = h('li', { class: 'f-row' + (sel ? ' is-sel' : '') + (picking && !e.folder ? ' is-dim' : ''), 'data-i': i });
  if (!picking) {
    var cb = h('input', { type: 'checkbox', class: 'f-check', 'data-i': i, 'aria-label': 'Select ' + e.name });
    cb.checked = sel;
    li.appendChild(cb);
  } else li.appendChild(h('span'));
  li.appendChild(h('button', {
    class: 'f-main', type: 'button', 'data-i': i, disabled: picking && !e.folder,
    'aria-label': e.folder ? e.name + ', folder' : e.name + (meta ? ', ' + meta : ''),
    'aria-pressed': e.folder ? null : String(sel)
  }, h('span', { class: 'f-ico t-' + t }, icon(TYPE_ICON[t])), h('span', { class: 'f-name' }, e.name), h('span', { class: 'f-meta' }, meta || ' ')));
  return li;
}
function nameRow(e) {
  var creating = !e;
  var input = h('input', { type: 'text', value: creating ? 'New folder' : e.name, maxlength: 255, spellcheck: 'false', autocomplete: 'off', 'aria-label': creating ? 'New folder name' : 'New name for ' + e.name });
  var ok = h('button', { class: 'icon-btn sm', type: 'submit', 'aria-label': creating ? 'Create folder' : 'Save new name' }, icon('check'));
  var form = h('form', { class: 'f-rename' },
    h('span', { class: 'f-ico t-' + (creating ? 'folder' : typeOf(e)) }, icon(creating ? 'folder' : TYPE_ICON[typeOf(e)])), input, ok,
    h('button', { class: 'icon-btn sm', type: 'button', 'aria-label': 'Cancel', onclick: cancelName }, icon('x')));
  form.addEventListener('submit', function (ev) { ev.preventDefault(); commitName(e, input.value, ok); });
  input.addEventListener('keydown', function (ev) { if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); cancelName(); } });
  setTimeout(function () {
    try {
      input.focus();
      var dot = !creating && !e.folder ? input.value.lastIndexOf('.') : -1;
      input.setSelectionRange(0, dot > 0 ? dot : input.value.length);
    } catch (x) { /* ignore */ }
  }, 0);
  return h('li', { class: 'f-row is-edit' }, form);
}
function cancelName() { F.renaming = null; F.creating = false; renderList(); focusSoon('#f-filter'); }
function commitName(e, raw, okBtn) {
  var name = String(raw || '').trim();
  if (!name) { toast('Type a name first.', 'warn'); return; }
  if (/[\\\/:*?"<>|]/.test(name) || name === '.' || name === '..') { toast('A name can’t contain \\ / : * ? " < > |', 'warn'); return; }
  if (e && name === e.name) { cancelName(); return; }
  busy(okBtn, true);
  var job = e
    ? act('manage_file', { op: 'rename', path: e.path, destination: name }, 60000)
    : act('manage_file', { op: 'mkdir', path: joinPath(F.path, name) }, 60000);
  job.then(function (r) {
    toast(r.message || (e ? 'Renamed to ' + name + '.' : 'Created folder ' + name + '.'), 'success');
    F.renaming = null;
    F.creating = false;
    loadFiles(F.path);
  }, function (err) { busy(okBtn, false); errToast(err); });
}
function selected() { return Object.keys(F.sel).map(function (k) { return F.sel[k]; }); }
function toggleSel(e, on) {
  if (on === undefined) on = !F.sel[e.path];
  if (on) F.sel[e.path] = e; else delete F.sel[e.path];
  var i = F.view.indexOf(e);
  var li = $('#f-list').querySelector('li[data-i="' + i + '"]');
  if (li) {
    li.classList.toggle('is-sel', on);
    var cb = li.querySelector('.f-check');
    if (cb) cb.checked = on;
    var m = li.querySelector('.f-main');
    if (m && !e.folder) m.setAttribute('aria-pressed', String(on));
  }
  renderSel();
}
function renderSel() {
  var items = selected(), n = items.length, bar = $('#f-selbar');
  bar.hidden = !n || !!F.pick;
  if (!n) return;
  var anyFolder = items.some(function (e) { return e.folder; });
  setText($('#f-selcount'), n === 1 ? items[0].name : n + ' selected');
  $('#fa-open').hidden = n !== 1;
  var openLabel = items[0].folder ? 'Show on PC' : 'Open on PC';
  setText($('#fa-open-text'), openLabel);
  $('#fa-open').setAttribute('aria-label', openLabel);
  $('#fa-open').title = openLabel;
  $('#fa-rename').hidden = n !== 1;
  $('#fa-download').hidden = anyFolder;
  $('#fa-phone').hidden = anyFolder;
}
function runSeq(items, fn, btn) {
  busy(btn, true);
  var res = { ok: 0, errs: [], msg: '' };
  return items.reduce(function (p, e) {
    return p.then(function () {
      return fn(e).then(function (r) { res.ok++; res.msg = (r && (r.message || r.reply)) || ''; }, function (err) {
        if (err && err.status === 401) throw err;
        res.errs.push({ name: e.name, msg: (err && err.message) || 'failed' });
      });
    });
  }, Promise.resolve()).then(function () { busy(btn, false); return res; }, function (err) { busy(btn, false); throw err; });
}
function report(res, n, verb) {
  if (res.errs.length) toast(n === 1 ? res.errs[0].msg : res.errs.length + ' of ' + n + ' failed. ' + res.errs[0].name + ': ' + res.errs[0].msg, 'error');
  if (res.ok) toast(n === 1 && res.msg ? res.msg : verb + ' ' + res.ok + (res.ok === 1 ? ' item.' : ' items.'), 'success');
}
function downloadPcFile(e) {
  return act('file_to_hub', { path: e.path }, 180000).then(function (r) {
    var f = r.file || {};
    return fetchBlob(f.url).then(function (blob) {
      saveBlob(blob, f.name || e.name);
      return { message: 'Downloaded ' + (f.name || e.name) + '.' };
    });
  });
}
function doOpen() {
  var e = selected()[0];
  if (!e) return;
  runSeq([e], function (x) { return act('open_file', { path: x.path }, 60000); }, $('#fa-open')).then(function (res) {
    if (!res.msg && res.ok) res.msg = 'Opened ' + e.name + ' on your PC.';
    report(res, 1, 'Opened');
  }, noop);
}
function doDownload() {
  var items = selected().filter(function (e) { return !e.folder; });
  if (!items.length) return;
  runSeq(items, downloadPcFile, $('#fa-download')).then(function (res) { report(res, items.length, 'Downloaded'); }, noop);
}
function doPhone() {
  var items = selected().filter(function (e) { return !e.folder; });
  if (!items.length) return;
  runSeq(items, function (e) { return act('send_file_to_phone', { path: e.path }, 180000); }, $('#fa-phone')).then(function (res) { report(res, items.length, 'Sent'); }, noop);
}
function doRename() {
  var e = selected()[0];
  if (!e) return;
  F.renaming = e.path;
  F.creating = false;
  renderList();
}
function startPick(op) {
  var items = selected();
  if (!items.length) return;
  F.pick = { op: op, items: items };
  F.sel = {};
  renderFiles();
  focusSoon('#f-pick-cancel');
}
function pickGo() {
  var pk = F.pick;
  if (!pk || !F.path) return;
  var dest = F.path;
  runSeq(pk.items, function (e) { return act('manage_file', { op: pk.op, path: e.path, destination: dest }, 180000); }, $('#f-pick-go')).then(function (res) {
    report(res, pk.items.length, pk.op === 'move' ? 'Moved' : 'Copied');
    F.pick = null;
    loadFiles(F.path);
  }, noop);
}
function doDelete() {
  var items = selected(), n = items.length;
  if (!n) return;
  confirmBox({
    title: n === 1 ? 'Delete “' + items[0].name + '”?' : 'Delete these ' + n + ' items?',
    body: (n === 1 ? (items[0].folder ? 'This folder and everything in it' : 'It') : 'They') + ' will go to the Recycle Bin on ' + (P.name || 'your PC') + ', so you can restore ' + (n === 1 ? 'it' : 'them') + ' from there.',
    items: n > 1 ? items.map(function (e) { return e.name; }) : [items[0].path],
    ok: 'Delete'
  }).then(function (yes) {
    if (!yes) return;
    runSeq(items, function (e) { return act('manage_file', { op: 'delete', path: e.path }, 120000); }, $('#fa-delete')).then(function (res) {
      report(res, n, 'Moved to the Recycle Bin:');
      F.sel = {};
      loadFiles(F.path);
    }, noop);
  });
}
function confirmBox(opts) {
  var dlg = $('#confirm');
  setText($('#cf-title'), opts.title);
  setText($('#cf-body'), opts.body);
  var ul = $('#cf-list'), names = opts.items || [];
  ul.replaceChildren.apply(ul, names.slice(0, 8).map(function (n) { return h('li', null, n); }).concat(names.length > 8 ? [h('li', null, '…and ' + (names.length - 8) + ' more')] : []));
  setText($('#cf-ok'), opts.ok || 'OK');
  if (typeof dlg.showModal !== 'function') return Promise.resolve(window.confirm(opts.title + '\n\n' + opts.body));
  return new Promise(function (resolve) {
    var prev = document.activeElement, settled = false;
    function finish(v) {
      if (settled) return;
      settled = true;
      dlg.removeEventListener('close', onClose);
      $('#cf-ok').onclick = null;
      $('#cf-cancel').onclick = null;
      if (dlg.open) dlg.close();
      resolve(v);
      if (prev && prev.isConnected && prev.focus) prev.focus();
    }
    function onClose() { finish(false); }
    dlg.addEventListener('close', onClose);
    $('#cf-ok').onclick = function () { finish(true); };
    $('#cf-cancel').onclick = function () { finish(false); };
    dlg.showModal();
    $('#cf-cancel').focus();
  });
}

/* ======================================================= panels: screenshot */
function snap(dev, force) {
  if (X.loading) return;
  X.loading = true;
  renderShot();
  ensurePc(dev, force).then(function (ok) {
    if (!ok) { X.offline = true; return; }
    X.offline = false;
    return act('get_screen_snapshot', { quality: 70, max_width: 1600 }, 30000).then(function (r) {
      var b64 = String(r.image_base64 || r.image || '').replace(/^data:image\/[\w.+-]+;base64,/, '');
      if (!b64) throw ApiError(String(r.error || 'Your PC didn’t send a picture.'), 0);
      X.b64 = b64; X.w = r.width | 0; X.h = r.height | 0; X.ts = Date.now(); X.err = '';
    });
  }).catch(function (e) { X.err = (e && e.message) || 'Couldn’t get the screen.'; if (X.b64) errToast(e); }).then(function () {
    X.loading = false;
    renderShot();
    scheduleAuto();
  });
}
function renderShot() {
  var img = $('#x-img'), zoom = $('#x-zoom'), st = $('#x-state');
  $('#pane-screenshot').classList.toggle('is-loading', X.loading);
  $('#x-spin').hidden = !(X.loading && !X.b64);
  $('#x-refresh').disabled = X.loading;
  $('#x-save').disabled = !X.b64;
  if (X.b64) {
    var src = 'data:image/jpeg;base64,' + X.b64;
    if (img.getAttribute('src') !== src) img.setAttribute('src', src);
    zoom.hidden = false;
    st.hidden = true;
    setText($('#x-time'), (X.w && X.h ? X.w + '×' + X.h + ' · ' : '') + new Date(X.ts).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' }));
  } else {
    zoom.hidden = true;
    setText($('#x-time'), '');
    if (X.loading) st.hidden = true;
    else if (X.offline) stateBox(st, 'laptop', 'Your PC is offline', 'Start the Willy PC client on your computer, then try again.', 'Try again', function () { snap(null, true); });
    else if (X.err) stateBox(st, 'alert', 'Couldn’t get the screen', X.err, 'Try again', function () { snap(null, true); });
    else stateBox(st, 'monitor', 'No screenshot yet', '', 'Take one', function () { snap(); });
  }
}
function stopAuto() { clearTimeout(X.timer); X.timer = 0; }
function scheduleAuto() {
  stopAuto();
  if (X.auto && U.sheet && U.tab === 'screenshot' && !document.hidden && S.token) X.timer = setTimeout(function () { snap(); }, AUTO_SHOT_MS);
}
function saveShot() {
  if (!X.b64) return;
  try { saveBlob(b64ToBlob(X.b64, 'image/jpeg'), 'screenshot-' + stamp() + '.jpg'); } catch (e) { toast('Couldn’t save the screenshot.', 'error'); }
}
function shotToPhone() {
  var btn = $('#x-phone');
  busy(btn, true);
  ensurePc(null).then(function (ok) {
    if (!ok) throw ApiError('Your PC is offline.', 0);
    return act('take_screenshot', {}, 60000);
  }).then(function () {
    return act('send_file_to_phone', { which: 'latest_screenshot' }, 180000);
  }).then(function (r) { toast(r.message || r.reply || 'Sent the screenshot to your phone.', 'success'); }, errToast).then(function () { busy(btn, false); });
}

/* ======================================================= panels: camera */
function startCamera() {
  if (K.stream || K.starting || K.blob) return;
  if (!secureEnough() || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    K.err = 'The camera needs a secure (https) connection to this hub.';
    renderCam();
    return;
  }
  var seq = ++K.seq;
  K.starting = true;
  K.err = '';
  renderCam();
  navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: K.facing }, width: { ideal: 1920 }, height: { ideal: 1080 } }, audio: false }).then(function (stream) {
    if (seq !== K.seq) { stopTracks(stream); return; }
    K.starting = false;
    if (!(U.sheet && U.tab === 'camera') || K.blob || !S.token) { stopTracks(stream); renderCam(); return; }
    K.stream = stream;
    var v = $('#k-video');
    v.srcObject = stream;
    var pr = v.play();
    if (pr && pr.catch) pr.catch(noop);
    renderCam();
  }, function (err) {
    if (seq !== K.seq) return;
    K.starting = false;
    var n = err && err.name;
    K.err = n === 'NotAllowedError' || n === 'SecurityError' ? 'Camera access is blocked. Allow it in your browser’s site settings, then try again.'
      : n === 'NotFoundError' || n === 'OverconstrainedError' ? 'No camera was found on this device.'
      : n === 'NotReadableError' ? 'Another app is using the camera.'
      : 'Couldn’t start the camera: ' + ((err && err.message) || 'unknown error') + '.';
    renderCam();
  });
}
function stopCamera() {
  K.seq++;
  K.starting = false;
  if (K.stream) { stopTracks(K.stream); K.stream = null; }
  var v = $('#k-video');
  if (v) { try { v.pause(); } catch (e) { /* ignore */ } v.srcObject = null; }
  renderCam();
}
function clearPhoto() {
  if (K.url) URL.revokeObjectURL(K.url);
  K.url = '';
  K.blob = null;
  var img = $('#k-photo');
  if (img) img.removeAttribute('src');
}
function renderCam() {
  var v = $('#k-video'), img = $('#k-photo'), st = $('#k-state');
  if (!v) return;
  v.hidden = !K.stream || !!K.blob;
  v.classList.toggle('is-mirror', K.facing === 'user');
  img.hidden = !K.blob;
  if (K.blob && img.getAttribute('src') !== K.url) img.setAttribute('src', K.url);
  $('#k-live').hidden = !!K.blob;
  $('#k-review').hidden = !K.blob;
  $('#k-shutter').disabled = !K.stream;
  $('#k-flip').disabled = K.starting;
  if (K.blob || K.stream) st.hidden = true;
  else if (K.starting) stateBox(st, 'camera', 'Starting the camera…', 'Allow camera access if your browser asks.', null);
  else if (K.err) stateBox(st, 'alert', 'Camera unavailable', K.err, 'Try again', function () { startCamera(); });
  else stateBox(st, 'camera', 'Camera is off', 'Nothing is captured until you press the shutter.', 'Start camera', function () { startCamera(); });
}
function shutter() {
  var v = $('#k-video');
  if (!K.stream || !v.videoWidth) { toast('The camera isn’t ready yet.', 'warn'); return; }
  var c = document.createElement('canvas');
  c.width = v.videoWidth;
  c.height = v.videoHeight;
  c.getContext('2d').drawImage(v, 0, 0);
  var fl = $('#k-flash');
  fl.classList.remove('go');
  void fl.offsetWidth;
  fl.classList.add('go');
  c.toBlob(function (blob) {
    if (!blob) { toast('Couldn’t capture the photo.', 'error'); return; }
    clearPhoto();
    K.blob = blob;
    K.url = URL.createObjectURL(blob);
    stopCamera();
    announce('Photo taken. Retake, save, or send it to your PC.');
    focusSoon('#k-send');
  }, 'image/jpeg', 0.9);
}
function retake() { clearPhoto(); renderCam(); startCamera(); }
function flipCamera() {
  K.facing = K.facing === 'user' ? 'environment' : 'user';
  stopCamera();
  startCamera();
}
function savePhoto() { if (K.blob) saveBlob(K.blob, 'photo-' + stamp() + '.jpg'); }
function photoToPc() {
  if (!K.blob) return;
  var btn = $('#k-send');
  busy(btn, true);
  var fd = new FormData();
  fd.append('file', K.blob, 'photo-' + stamp() + '.jpg');
  api('/api/v1/files?target=pc', { method: 'POST', form: fd, body: null, timeout: 180000 }).then(function (d) {
    d = d || {};
    toast(d.reply || 'Sent the photo to your PC.', d.delivered === false ? 'warn' : 'success');
  }, errToast).then(function () { busy(btn, false); });
}

/* ================================================================== wiring */
function wireApp() {
  ringInit();
  loadSpeaker();
  setText($('#session-id'), SESSION_ID);
  var savedTab = local.get(KEYS.tab);
  if (PANELS.indexOf(savedTab) >= 0) U.tab = savedTab;
  $('#back-link').setAttribute('href', BASE + '/dashboard');
  $('#call-btn').addEventListener('click', function () { if (C.inCall || C.starting) endCall(); else { primeVis(); startCall(); } });
  $('#orb').addEventListener('click', function () {
    if (!C.inCall && !C.starting) { primeVis(); startCall(); }
    else if (C.playing) interrupt();
  });
  $('#mute-btn').addEventListener('click', toggleMute);
  $('#stop-btn').addEventListener('click', interrupt);
  $('#signout').addEventListener('click', function () { signOut(''); });

  // speaker
  $('#spk-btn').addEventListener('click', function () { openSpk($('#spk-pop').hidden); });
  $('#spk-mute').addEventListener('click', function () {
    O.voiceMuted = !O.voiceMuted;
    local.set(KEYS.spk, O.voiceMuted ? '1' : '0');
    renderSpeaker();
  });
  $('#spk-vol').addEventListener('input', function () {
    O.vol = Math.max(0, Math.min(1, (+this.value || 0) / 100));
    if (O.vol > 0 && O.voiceMuted) { O.voiceMuted = false; local.set(KEYS.spk, '0'); }
    local.set(KEYS.vol, String(O.vol));
    renderSpeaker();
  });
  document.addEventListener('pointerdown', function (e) { if (!$('#spk-pop').hidden && !$('#spk-ctl').contains(e.target)) openSpk(false); });
  $('#spk-ctl').addEventListener('focusout', function (e) { if (e.relatedTarget && !$('#spk-ctl').contains(e.relatedTarget)) openSpk(false); });

  // chat drawer
  $('#chat-btn').addEventListener('click', function () { setDrawer(!U.drawer, { focus: !U.drawer, returnFocus: true }); });
  $('#drawer-close').addEventListener('click', function () { setDrawer(false, { returnFocus: true }); });
  $('#cap-more').addEventListener('click', function () { setDrawer(true); focusSoon('#drawer-close'); });
  $('#type-form').addEventListener('submit', function (e) {
    e.preventDefault();
    var input = $('#type-input');
    var text = input.value.trim();
    if (!text) return;
    input.value = '';
    sendTyped(text);
  });
  $('#type-input').addEventListener('focus', function () { $('#app').classList.add('typing'); });
  $('#type-input').addEventListener('blur', function () { $('#app').classList.remove('typing'); });
  $('#reset-btn').addEventListener('click', function () {
    var btn = this;
    busy(btn, true);
    api('/api/v1/session/reset', { method: 'POST', body: { session_id: SESSION_ID } }).then(function () {
      S.turns = []; saveTurns(); renderTurns();
      toast('Willy has forgotten this conversation.', 'success');
    }, function (e) { if (e.status !== 401) toast(e.message, 'error'); }).then(function () { busy(btn, false); });
  });

  // panels
  $('#panel-btn').addEventListener('click', function () { if (U.sheet) closePanel({ returnFocus: true }); else openPanel(U.tab, { focus: true }); });
  $('#sheet-close').addEventListener('click', function () { closePanel({ returnFocus: true }); });
  PANELS.forEach(function (p) { $('#tab-' + p).addEventListener('click', function () { if (p !== U.tab) switchTab(p, {}); }); });
  $('#tabs').addEventListener('keydown', function (e) {
    var i = PANELS.indexOf(U.tab), n = null;
    if (e.key === 'ArrowRight') n = (i + 1) % PANELS.length;
    else if (e.key === 'ArrowLeft') n = (i + PANELS.length - 1) % PANELS.length;
    else if (e.key === 'Home') n = 0;
    else if (e.key === 'End') n = PANELS.length - 1;
    if (n === null) return;
    e.preventDefault();
    switchTab(PANELS[n], {});
    $('#tab-' + PANELS[n]).focus();
  });

  // files
  $('#f-up').addEventListener('click', function () { if (F.parent != null) loadFiles(F.parent); else if (F.path) loadFiles(''); });
  $('#f-refresh').addEventListener('click', function () { loadFiles(F.path, null, F.offline || !F.loaded); });
  $('#f-crumbs').addEventListener('click', function (e) { var b = e.target.closest('button[data-path]'); if (b) loadFiles(b.getAttribute('data-path')); });
  $('#f-drives').addEventListener('click', function (e) { var b = e.target.closest('button[data-path]'); if (b) loadFiles(b.getAttribute('data-path')); });
  $('#f-filter').addEventListener('input', function () { F.filter = this.value; renderList(); });
  $('#f-sort').addEventListener('change', function () { F.sort = this.value; renderList(); });
  $('#f-new').addEventListener('click', function () { if (!F.path) return; F.creating = true; F.renaming = null; renderList(); $('#f-scroll').scrollTop = 0; });
  $('#f-list').addEventListener('click', function (ev) {
    var b = ev.target.closest('.f-main');
    if (!b) return;
    var e = F.view[+b.getAttribute('data-i')];
    if (!e) return;
    if (e.folder) loadFiles(e.path); else if (!F.pick) toggleSel(e);
  });
  $('#f-list').addEventListener('change', function (ev) {
    var cb = ev.target.closest('.f-check');
    if (!cb) return;
    var e = F.view[+cb.getAttribute('data-i')];
    if (e) toggleSel(e, cb.checked);
  });
  $('#f-clear').addEventListener('click', function () { F.sel = {}; renderList(); focusSoon('#f-filter'); });
  $('#fa-open').addEventListener('click', doOpen);
  $('#fa-download').addEventListener('click', doDownload);
  $('#fa-phone').addEventListener('click', doPhone);
  $('#fa-rename').addEventListener('click', doRename);
  $('#fa-copy').addEventListener('click', function () { startPick('copy'); });
  $('#fa-move').addEventListener('click', function () { startPick('move'); });
  $('#fa-delete').addEventListener('click', doDelete);
  $('#f-pick-go').addEventListener('click', pickGo);
  $('#f-pick-cancel').addEventListener('click', function () { F.pick = null; renderFiles(); });

  // screenshot
  $('#x-refresh').addEventListener('click', function () { snap(null, X.offline); });
  $('#x-auto').addEventListener('change', function () { X.auto = this.checked; if (X.auto && !X.loading) snap(); else scheduleAuto(); });
  $('#x-save').addEventListener('click', saveShot);
  $('#x-phone').addEventListener('click', shotToPhone);
  $('#x-zoom').addEventListener('click', function () {
    var on = !$('#x-view').classList.contains('is-zoom');
    $('#x-view').classList.toggle('is-zoom', on);
    this.setAttribute('aria-pressed', String(on));
    this.setAttribute('aria-label', on ? 'Fit the screenshot to the panel' : 'Show the screenshot at actual size');
  });

  // camera
  $('#k-shutter').addEventListener('click', shutter);
  $('#k-flip').addEventListener('click', flipCamera);
  $('#k-retake').addEventListener('click', retake);
  $('#k-save').addEventListener('click', savePhoto);
  $('#k-send').addEventListener('click', photoToPc);

  // keyboard + lifecycle
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape' || e.defaultPrevented) return;
    if (document.querySelector('dialog[open]')) return;
    if (!$('#spk-pop').hidden) { openSpk(false); $('#spk-btn').focus(); return; }
    if (C.playing || C.inflight) { interrupt(); return; }
    var a = document.activeElement;
    if (F.pick && $('#sheet').contains(a)) { F.pick = null; renderFiles(); return; }
    if (U.drawer && $('#drawer').contains(a)) { setDrawer(false, { returnFocus: true }); return; }
    if (U.sheet && $('#sheet').contains(a)) closePanel({ returnFocus: true });
  });
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) { stopCamera(); stopAuto(); return; }
    if (C.inCall) requestWake();
    if (S.token) pollStats();
    scheduleAuto();
    ringKick();
  });
  var onMq = function () { if (MOBILE.matches && U.sheet && U.drawer) setDrawer(false); ringKick(); };
  if (MOBILE.addEventListener) MOBILE.addEventListener('change', onMq); else if (MOBILE.addListener) MOBILE.addListener(onMq);
  if (RM.addEventListener) RM.addEventListener('change', ringKick); else if (RM.addListener) RM.addListener(ringKick);
  window.addEventListener('resize', ringKick);
  $('#avatar').addEventListener('transitionend', ringKick);
  window.addEventListener('pagehide', function () { endCall(); stopCamera(); });
  renderBadge();
  renderCam();
  renderShot();
}

wireLogin();
boot();
})();
</script>
</body>
</html>
"""


def get_web_voice_call_html() -> str:
    """Returns the voice call page. It contains no secrets; auth happens in the browser."""
    return WEB_CALL_HTML
