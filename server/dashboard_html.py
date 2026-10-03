"""
Willy Command Center: the web dashboard served at / and /dashboard.

A single self-contained page (inline CSS, vanilla JS, inline SVG). It never embeds the
access token: the browser supplies it once (URL fragment #token=..., legacy ?token=...,
or the login card), keeps it in localStorage, validates it against /api/v1/auth/check and
then follows the hub's live event stream on /ws/events.

The markup is a plain raw string (not an f-string), so CSS/JS braces need no escaping.
"""

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#070913">
<meta name="referrer" content="no-referrer">
<title>Willy · Command Center</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 40 40'%3E%3Cdefs%3E%3ClinearGradient id='g' x1='0' y1='0' x2='1' y2='1'%3E%3Cstop offset='0' stop-color='%2300F2FE'/%3E%3Cstop offset='1' stop-color='%237C3AED'/%3E%3C/linearGradient%3E%3C/defs%3E%3Crect x='1' y='1' width='38' height='38' rx='11' fill='url(%23g)'/%3E%3Cpath d='M9.5 14.5l4.8 12 5.7-9.5 5.7 9.5 4.8-12' fill='none' stroke='%23070913' stroke-width='3.2' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@500;600;700&family=JetBrains+Mono:wght@400;500;600&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #070913;
  --glass: rgba(17, 24, 39, .7);
  --well: rgba(3, 6, 15, .45);
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
  --ui: 'Plus Jakarta Sans', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
  --mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Consolas, monospace;
  --display: 'Chakra Petch', 'Plus Jakarta Sans', system-ui, sans-serif;
  --r: 16px;
  --ease: cubic-bezier(.2, .8, .2, 1);
  --gutter: 24px;
}
*, *::before, *::after { box-sizing: border-box; }
[hidden] { display: none !important; }
html { color-scheme: dark; -webkit-text-size-adjust: 100%; text-size-adjust: 100%; }
body {
  margin: 0; min-height: 100vh; background: var(--bg); color: var(--text);
  font: 400 14px/1.5 var(--ui); -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale;
}
body::before {
  content: ''; position: fixed; inset: 0; z-index: -1; pointer-events: none;
  background:
    radial-gradient(900px 620px at 6% -12%, rgba(124, 58, 237, .17), transparent 62%),
    radial-gradient(820px 620px at 104% 112%, rgba(0, 242, 254, .09), transparent 60%),
    linear-gradient(rgba(255, 255, 255, .018) 1px, transparent 1px) 0 0 / 56px 56px,
    linear-gradient(90deg, rgba(255, 255, 255, .018) 1px, transparent 1px) 0 0 / 56px 56px;
}
h1, h2, h3, p, ul, ol, dl, dd, figure { margin: 0; padding: 0; }
ul, ol { list-style: none; }
button, input, select, textarea { font: inherit; color: inherit; }
button { cursor: pointer; }
code { font-family: var(--mono); font-size: .9em; background: rgba(255, 255, 255, .06); border: 1px solid var(--line); border-radius: 6px; padding: 1px 6px; overflow-wrap: anywhere; }
.mono { font-family: var(--mono); font-variant-numeric: tabular-nums; }
.sr-only { position: absolute !important; width: 1px !important; height: 1px !important; min-width: 0 !important; min-height: 0 !important; margin: -1px !important; padding: 0 !important; overflow: hidden !important; clip: rect(0 0 0 0); clip-path: inset(50%); white-space: nowrap; border: 0 !important; }
:focus-visible { outline: 2px solid var(--cyan); outline-offset: 2px; }
* { scrollbar-width: thin; scrollbar-color: rgba(148, 163, 184, .28) transparent; }
.i { width: 18px; height: 18px; flex: none; fill: none; stroke: currentColor; stroke-width: 1.9; stroke-linecap: round; stroke-linejoin: round; }
.sprite { position: absolute; width: 0; height: 0; overflow: hidden; }

/* ---------- controls ---------- */
.btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 8px; min-height: 36px; padding: 0 14px;
  border-radius: 10px; border: 1px solid var(--line-2); background: rgba(255, 255, 255, .04); color: var(--text);
  font: 600 13px/1 var(--ui); white-space: nowrap; text-decoration: none; position: relative;
  transition: background .15s, border-color .15s, color .15s, transform .1s, box-shadow .15s, filter .15s;
}
.btn:hover:not(:disabled) { background: rgba(255, 255, 255, .08); border-color: rgba(255, 255, 255, .24); }
.btn:active:not(:disabled) { transform: translateY(1px); }
.btn:disabled { opacity: .45; cursor: not-allowed; }
.btn-primary { border: 0; color: #03141A; background: linear-gradient(135deg, var(--cyan), #6A7CF7 62%, var(--purple)); box-shadow: 0 8px 22px -12px rgba(0, 242, 254, .75); }
.btn-primary:hover:not(:disabled) { background: linear-gradient(135deg, var(--cyan), #6A7CF7 62%, var(--purple)); filter: brightness(1.1); }
.btn-ghost { background: transparent; }
.btn-danger { background: rgba(239, 68, 68, .12); border-color: rgba(239, 68, 68, .45); color: #FCA5A5; }
.btn-danger:hover:not(:disabled) { background: rgba(239, 68, 68, .22); border-color: rgba(239, 68, 68, .65); }
.btn-call { border: 0; color: #fff; background: linear-gradient(135deg, #10B981, #059669); box-shadow: 0 8px 20px -10px rgba(16, 185, 129, .8); }
.btn-call:hover:not(:disabled) { background: linear-gradient(135deg, #10B981, #059669); filter: brightness(1.1); }
.btn-sm { min-height: 30px; padding: 0 10px; font-size: 12px; border-radius: 8px; gap: 6px; }
.btn-sm .i { width: 15px; height: 15px; }
.btn-block { width: 100%; }
.icon-btn {
  display: inline-grid; place-items: center; width: 34px; height: 34px; padding: 0; flex: none; border-radius: 9px;
  border: 1px solid var(--line); background: rgba(255, 255, 255, .03); color: var(--text-2); position: relative;
  transition: background .15s, color .15s, border-color .15s;
}
.icon-btn:hover:not(:disabled) { background: rgba(255, 255, 255, .08); color: var(--text); border-color: var(--line-2); }
.icon-btn:disabled { opacity: .4; cursor: not-allowed; }
.icon-btn.sm { width: 28px; height: 28px; border-radius: 8px; }
.icon-btn.sm .i { width: 15px; height: 15px; }
.is-busy { pointer-events: none; }
.is-busy > .i, .is-busy > span { opacity: .25; }
.is-busy::after {
  content: ''; position: absolute; left: 50%; top: 50%; width: 16px; height: 16px; margin: -8px 0 0 -8px;
  border-radius: 50%; border: 2px solid rgba(255, 255, 255, .18); border-top-color: var(--cyan); animation: spin .8s linear infinite;
}
.btn-primary.is-busy::after { border-color: rgba(3, 20, 26, .25); border-top-color: #03141A; }
input[type=text], input[type=password], input[type=time], input[type=date], input[type=number], input[type=url], select, textarea {
  width: 100%; min-width: 0; min-height: 38px; padding: 8px 12px; border-radius: 10px; border: 1px solid var(--line-2);
  background: rgba(3, 6, 15, .6); color: var(--text); font-size: 14px; outline: none;
  transition: border-color .15s, box-shadow .15s;
}
textarea { resize: vertical; line-height: 1.45; }
input:focus, select:focus, textarea:focus { border-color: rgba(0, 242, 254, .6); box-shadow: 0 0 0 3px rgba(0, 242, 254, .12); }
input::placeholder, textarea::placeholder { color: #5B6B82; }
select {
  appearance: none; -webkit-appearance: none; padding-right: 32px; cursor: pointer;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%2394A3B8' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E");
  background-repeat: no-repeat; background-position: right 9px center; background-size: 16px;
}
select option { background: #0D1222; color: var(--text); }
input[type=time], input[type=date] { -webkit-appearance: none; appearance: none; }
.switch { display: inline-flex; align-items: center; gap: 8px; font-size: 12.5px; font-weight: 600; color: var(--text-2); cursor: pointer; user-select: none; position: relative; white-space: nowrap; }
.switch input { position: absolute; opacity: 0; width: 1px; height: 1px; }
.switch-track { position: relative; width: 34px; height: 20px; flex: none; border-radius: 20px; background: rgba(148, 163, 184, .26); transition: background .2s; }
.switch-track::after { content: ''; position: absolute; top: 3px; left: 3px; width: 14px; height: 14px; border-radius: 50%; background: #E2E8F0; transition: transform .2s var(--ease); }
.switch input:checked + .switch-track { background: linear-gradient(135deg, var(--cyan), var(--purple)); }
.switch input:checked + .switch-track::after { transform: translateX(14px); }
.switch input:focus-visible + .switch-track { outline: 2px solid var(--cyan); outline-offset: 2px; }
.switch input:disabled + .switch-track { opacity: .4; }
.chip {
  display: inline-flex; align-items: center; gap: 6px; height: 30px; padding: 0 12px; flex: none; border-radius: 999px;
  border: 1px solid var(--line-2); background: rgba(255, 255, 255, .03); color: var(--text-2);
  font: 500 12.5px/1 var(--ui); white-space: nowrap; transition: border-color .15s, color .15s, background .15s;
}
.chip:hover { border-color: rgba(0, 242, 254, .45); color: var(--text); background: rgba(0, 242, 254, .06); }
.chip[aria-pressed=true] { border-color: rgba(0, 242, 254, .55); color: var(--text); background: rgba(0, 242, 254, .1); }
.seg { display: inline-flex; gap: 2px; padding: 3px; border-radius: 10px; background: rgba(3, 6, 15, .6); border: 1px solid var(--line); max-width: 100%; }
.seg button {
  display: inline-flex; align-items: center; justify-content: center; gap: 6px; min-height: 30px; padding: 0 12px;
  border: 0; border-radius: 8px; background: transparent; color: var(--muted); font: 600 12.5px/1 var(--ui); white-space: nowrap;
  transition: background .15s, color .15s;
}
.seg button .i { width: 15px; height: 15px; }
.seg button:hover { color: var(--text); }
.seg button[aria-selected=true], .seg button[aria-pressed=true] { background: rgba(255, 255, 255, .09); color: var(--text); box-shadow: inset 0 0 0 1px var(--line-2); }
.seg-n { font: 600 10.5px/1 var(--mono); color: var(--cyan); }
.seg-xs button { min-height: 24px; padding: 0 9px; font-size: 11.5px; }
.dot { width: 7px; height: 7px; border-radius: 50%; background: var(--faint); flex: none; }
.dot.ok { background: var(--green); box-shadow: 0 0 8px rgba(16, 185, 129, .8); }
.dot.bad { background: var(--red); box-shadow: 0 0 8px rgba(239, 68, 68, .7); }

/* ---------- boot + login ---------- */
.boot { min-height: 100vh; display: grid; place-content: center; justify-items: center; gap: 14px; color: var(--muted); font-size: 13px; }
.boot .mark { width: 46px; height: 46px; animation: breathe 1.6s ease-in-out infinite; }
.login { min-height: 100vh; display: grid; place-items: center; padding: 24px 16px; }
.login-card { width: min(400px, 100%); padding: 30px 28px 26px; display: grid; gap: 14px; text-align: center; }
.login-card .mark { width: 56px; height: 56px; margin: 0 auto 2px; filter: drop-shadow(0 10px 28px rgba(0, 242, 254, .35)); }
.login-title { font: 700 23px/1.2 var(--display); letter-spacing: .02em; }
.login-hint { color: var(--muted); font-size: 13.5px; margin-top: -6px; }
.pw-wrap { position: relative; text-align: left; }
.pw-wrap input { height: 46px; padding-right: 46px; font-family: var(--mono); font-size: 15px; letter-spacing: .02em; }
.pw-eye { position: absolute; right: 6px; top: 6px; width: 34px; height: 34px; border: 0; border-radius: 8px; background: transparent; color: var(--muted); display: grid; place-items: center; }
.pw-eye:hover { color: var(--text); background: rgba(255, 255, 255, .06); }
.login-card .btn { min-height: 44px; font-size: 14px; }
.login-err { color: #FECACA; font-size: 13px; text-align: left; background: rgba(239, 68, 68, .09); border: 1px solid rgba(239, 68, 68, .35); border-radius: 10px; padding: 9px 12px; }
.login-foot { font-size: 12px; color: var(--faint); }
.btn-google { display: flex; align-items: center; justify-content: center; gap: 10px; width: 100%; padding: 11px 16px; border-radius: 10px; border: 1px solid #dadce0; background: #fff; color: #1f1f1f; font: 600 14.5px/1.2 inherit; cursor: pointer; }
.btn-google:disabled { opacity: .6; cursor: default; }
.login-or { display: flex; align-items: center; gap: 10px; color: var(--faint); font-size: 12px; }
.login-or::before, .login-or::after { content: ""; flex: 1; height: 1px; background: rgba(255, 255, 255, .08); }
.login-token-toggle { background: none; border: 0; color: var(--muted); font-size: 13px; cursor: pointer; text-decoration: underline; padding: 0; }

/* ---------- app frame ---------- */
.app { min-height: 100vh; overflow-x: clip; }
.appbar {
  position: sticky; top: 0; z-index: 50; display: flex; align-items: center; gap: 14px; padding: 12px var(--gutter);
  background: rgba(7, 9, 19, .8); border-bottom: 1px solid var(--line);
  backdrop-filter: blur(18px) saturate(140%); -webkit-backdrop-filter: blur(18px) saturate(140%);
}
.brand { display: flex; align-items: center; gap: 11px; min-width: 0; flex: none; }
.brand .mark { width: 34px; height: 34px; flex: none; }
.brand-name { display: block; font: 700 19px/1 var(--display); letter-spacing: .1em; text-transform: uppercase; }
.brand-sub { display: block; margin-top: 4px; font: 600 9.5px/1 var(--display); letter-spacing: .24em; text-transform: uppercase; color: var(--cyan); }
.conn {
  display: inline-flex; align-items: center; gap: 8px; height: 30px; padding: 0 12px; flex: none; border-radius: 999px;
  border: 1px solid var(--line-2); background: rgba(255, 255, 255, .03); font: 600 12.5px/1 var(--ui); white-space: nowrap; color: var(--text-2);
}
.conn-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--faint); flex: none; }
.conn-rtt { font: 500 11.5px/1 var(--mono); opacity: .85; }
.conn-rtt:empty { display: none; }
.conn[data-state=live] { color: #A7F3D0; border-color: rgba(16, 185, 129, .38); background: rgba(16, 185, 129, .08); }
.conn[data-state=live] .conn-dot { background: var(--green); animation: live 2.2s ease-out infinite; }
.conn[data-state=connecting], .conn[data-state=reconnecting] { color: #FDE68A; border-color: rgba(245, 158, 11, .38); background: rgba(245, 158, 11, .08); }
.conn[data-state=connecting] .conn-dot, .conn[data-state=reconnecting] .conn-dot { background: var(--amber); animation: blink 1s ease-in-out infinite; }
.conn[data-state=offline] { color: #FECACA; border-color: rgba(239, 68, 68, .42); background: rgba(239, 68, 68, .08); }
.conn[data-state=offline] .conn-dot { background: var(--red); }
.srv { display: flex; align-items: center; gap: 6px; min-width: 0; }
.srv-item {
  display: inline-flex; align-items: center; gap: 7px; height: 26px; padding: 0 9px; border-radius: 8px; min-width: 0;
  font-size: 12px; color: var(--muted); background: rgba(255, 255, 255, .03); border: 1px solid var(--line); white-space: nowrap;
}
.srv-item > span { overflow: hidden; text-overflow: ellipsis; }
#srv-llm { max-width: 280px; }
.appbar-actions { margin-left: auto; display: flex; align-items: center; gap: 8px; flex: none; }
.layout {
  width: 100%; max-width: 1560px; margin: 0 auto; padding: 20px var(--gutter) 56px;
  display: grid; grid-template-columns: minmax(0, 1fr) 400px; gap: 20px; align-items: start;
}
.banner { grid-column: 1 / -1; display: flex; align-items: flex-start; gap: 12px; padding: 12px 12px 12px 16px; border-radius: 14px; background: linear-gradient(135deg, rgba(239, 68, 68, .18), rgba(239, 68, 68, .06)); border: 1px solid rgba(239, 68, 68, .5); color: #FEE2E2; }
.banner > .i { color: var(--red); width: 20px; height: 20px; margin-top: 1px; }
.banner p { flex: 1; font-size: 13.5px; line-height: 1.5; min-width: 0; }
.banner strong { color: #fff; }
.banner .icon-btn { border-color: rgba(239, 68, 68, .35); background: transparent; color: #FECACA; }
.col-main, .col-rail { display: grid; grid-template-columns: minmax(0, 1fr); gap: 20px; min-width: 0; align-content: start; }
.devices { min-width: 0; }
.card {
  position: relative; min-width: 0; background: var(--glass); border: 1px solid var(--line); border-radius: var(--r);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, .045), 0 26px 50px -34px rgba(0, 0, 0, .85);
}
.card-head { display: flex; align-items: center; justify-content: space-between; gap: 10px 12px; padding: 16px 18px 0; flex-wrap: wrap; }
.title { font: 600 13px/1.2 var(--display); letter-spacing: .16em; text-transform: uppercase; color: var(--text-2); }
.head-tools { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.sec-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; padding: 2px 4px 12px; }
.sec-count { font: 500 12px/1 var(--mono); color: var(--muted); }
.muted { color: var(--muted); }

/* ---------- console ---------- */
.console { display: flex; flex-direction: column; }
.target { width: auto; max-width: 220px; min-height: 30px; padding: 4px 30px 4px 10px; font-size: 12.5px; border-radius: 8px; }
.conv {
  display: flex; flex-direction: column; gap: 14px; padding: 18px 18px 16px; min-height: 190px; max-height: clamp(240px, 46vh, 500px);
  overflow-y: auto; overscroll-behavior: contain;
  -webkit-mask-image: linear-gradient(to bottom, transparent 0, #000 16px, #000 calc(100% - 8px), transparent 100%);
  mask-image: linear-gradient(to bottom, transparent 0, #000 16px, #000 calc(100% - 8px), transparent 100%);
}
.conv-empty { margin: auto 0; max-width: 540px; color: var(--muted); font-size: 13.5px; }
.conv-empty b { display: block; color: var(--text); font-size: 17px; font-weight: 700; margin-bottom: 4px; letter-spacing: -.01em; }
.msg { display: flex; gap: 10px; min-width: 0; animation: rise .28s var(--ease); }
.msg-user { justify-content: flex-end; }
.bubble { padding: 10px 14px; font-size: 14px; line-height: 1.55; white-space: pre-wrap; overflow-wrap: anywhere; min-width: 0; }
.msg-user .bubble { max-width: min(80%, 580px); border-radius: 14px 14px 4px 14px; background: linear-gradient(135deg, rgba(0, 242, 254, .15), rgba(124, 58, 237, .2)); border: 1px solid rgba(0, 242, 254, .24); }
.msg-willy .bubble { border-radius: 14px 14px 14px 4px; background: rgba(255, 255, 255, .045); border: 1px solid var(--line); }
.msg-col { display: flex; flex-direction: column; align-items: flex-start; gap: 7px; min-width: 0; max-width: min(88%, 660px); }
.avatar { width: 28px; height: 28px; flex: none; margin-top: 2px; }
.msg.is-error .bubble { border-color: rgba(239, 68, 68, .4); background: rgba(239, 68, 68, .08); }
.msg-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.typing { display: inline-flex; gap: 4px; padding: 4px 0; }
.typing i { width: 6px; height: 6px; border-radius: 50%; background: var(--muted); animation: typing 1.2s infinite ease-in-out; }
.typing i:nth-child(2) { animation-delay: .15s; }
.typing i:nth-child(3) { animation-delay: .3s; }
.chips { display: flex; gap: 8px; padding: 0 18px 12px; flex-wrap: wrap; }
.prompt { display: flex; align-items: center; gap: 8px; margin: 0 18px; padding: 6px 6px 6px 14px; border-radius: 14px; border: 1px solid var(--line-2); background: rgba(3, 6, 15, .7); transition: border-color .15s, box-shadow .15s; }
.prompt:focus-within { border-color: rgba(0, 242, 254, .6); box-shadow: 0 0 0 3px rgba(0, 242, 254, .12), 0 0 30px -10px rgba(0, 242, 254, .5); }
.prompt-caret { font: 600 18px/1 var(--mono); color: var(--cyan); flex: none; }
.prompt input { flex: 1; min-width: 0; border: 0; background: transparent; box-shadow: none; padding: 8px 4px; font-size: 15px; }
.prompt input:focus { box-shadow: none; }
.prompt-send { width: 42px; height: 40px; min-height: 40px; padding: 0; flex: none; border-radius: 10px; }
.prompt-hint { padding: 8px 18px 16px; font-size: 11.5px; color: var(--faint); }
.prompt-hint kbd { font: 500 10.5px/1 var(--mono); padding: 2px 5px; border-radius: 4px; border: 1px solid var(--line-2); color: var(--muted); }

/* badges + latency ribbon (the per-command timing instrument) */
.fast { display: inline-flex; align-items: center; gap: 3px; height: 20px; padding: 0 7px 0 5px; border-radius: 6px; background: rgba(0, 242, 254, .1); border: 1px solid rgba(0, 242, 254, .32); color: var(--cyan); font: 700 10.5px/1 var(--ui); letter-spacing: .02em; white-space: nowrap; }
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
.rb-stt { background: var(--cyan); }
.rb-llm { background: var(--violet); }
.rb-tool { background: var(--amber); }
.rb-tts { background: var(--green); }
.rb-other { background: #94A3B8; }
.ribbon.is-over::after { content: ''; position: absolute; right: 0; top: 0; bottom: 0; width: 7px; background: repeating-linear-gradient(90deg, rgba(7, 9, 19, .85) 0 1px, transparent 1px 2px); }
.ribbon.is-empty { opacity: .5; }

/* ---------- devices ---------- */
.dev-grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fill, minmax(min(100%, 330px), 1fr)); }
.dev { padding: 16px 18px 18px; container-type: inline-size; container-name: dev; transition: opacity .3s, filter .3s; }
.dev-pc { grid-column: 1 / -1; }
.dev.is-offline { opacity: .6; filter: saturate(.35); }
.dev-head { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: 12px; }
.dev-ico { width: 42px; height: 42px; border-radius: 12px; display: grid; place-items: center; color: var(--cyan); background: linear-gradient(145deg, rgba(0, 242, 254, .14), rgba(124, 58, 237, .16)); border: 1px solid rgba(255, 255, 255, .09); }
.dev-ico .i { width: 22px; height: 22px; }
.dev-name { font: 700 16.5px/1.25 var(--ui); letter-spacing: -.01em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.dev-sub { margin-top: 2px; font: 500 12px/1.3 var(--mono); color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.dev-state { display: flex; align-items: center; gap: 9px; }
.beat { width: 8px; height: 8px; border-radius: 50%; background: var(--green); opacity: .45; }
.is-offline .beat { background: var(--faint); }
.badge { height: 22px; padding: 0 8px; border-radius: 6px; font: 700 10.5px/20px var(--ui); letter-spacing: .08em; text-transform: uppercase; border: 1px solid; white-space: nowrap; }
.badge.on { color: var(--green); background: rgba(16, 185, 129, .1); border-color: rgba(16, 185, 129, .32); }
.badge.off { color: #FCA5A5; background: rgba(239, 68, 68, .1); border-color: rgba(239, 68, 68, .32); }
.meta { display: flex; flex-wrap: wrap; gap: 7px 18px; margin-top: 14px; padding: 10px 12px; border-radius: 12px; background: var(--well); border: 1px solid rgba(255, 255, 255, .05); }
.meta > div { display: flex; align-items: baseline; gap: 7px; min-width: 0; max-width: 100%; }
.meta dt { font: 600 9.5px/1 var(--display); letter-spacing: .14em; text-transform: uppercase; color: var(--faint); flex: none; }
.meta dd { font: 500 12px/1.2 var(--mono); color: var(--text-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; max-width: 200px; }
.meta dd.is-stale { color: var(--amber); }
.dev-body { display: grid; grid-template-columns: minmax(0, 1fr); gap: 16px; margin-top: 16px; }
.dev-col, .dev-panels { display: grid; grid-template-columns: minmax(0, 1fr); gap: 14px; align-content: start; min-width: 0; }
@container dev (min-width: 740px) {
  .dev-body { grid-template-columns: minmax(0, 1.08fr) minmax(0, 1fr); gap: 16px 22px; }
  .dev-panels { grid-column: 1 / -1; grid-template-columns: minmax(0, 1.5fr) minmax(0, 1fr); gap: 14px 22px; align-items: start; }
}
.well { border-radius: 12px; background: var(--well); border: 1px solid rgba(255, 255, 255, .05); }
.k { font: 600 9.5px/1 var(--display); letter-spacing: .14em; text-transform: uppercase; color: var(--faint); }

/* instrument dials */
.gauges { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; padding: 14px 10px 12px; }
.gauge { --gc: var(--cyan); --gg: rgba(0, 242, 254, .5); display: grid; justify-items: center; gap: 6px; min-width: 0; }
.gauge[data-g=ram] { --gc: var(--violet); --gg: rgba(167, 139, 250, .5); }
.gauge[data-g=disk] { --gc: #CBD5E1; --gg: rgba(203, 213, 225, .35); }
.gauge[data-g=bat] { --gc: var(--green); --gg: rgba(16, 185, 129, .5); }
.gauge[data-level=warm] { --gc: var(--amber); --gg: rgba(245, 158, 11, .5); }
.gauge[data-level=hot] { --gc: var(--red); --gg: rgba(239, 68, 68, .55); }
.g-dial { position: relative; width: 100%; max-width: 96px; aspect-ratio: 1; }
.g-svg { display: block; width: 100%; height: 100%; overflow: visible; }
.g-ticks { fill: none; stroke: rgba(255, 255, 255, .2); stroke-width: 1.4; stroke-linecap: round; }
.g-track { fill: none; stroke: rgba(255, 255, 255, .075); stroke-width: 7; stroke-linecap: round; stroke-dasharray: 188.5 251.33; }
.g-val {
  fill: none; stroke: var(--gc); stroke-width: 7; stroke-linecap: round; stroke-dasharray: 188.5 251.33; stroke-dashoffset: 188.5;
  filter: drop-shadow(0 0 4px var(--gg)); transition: stroke-dashoffset .8s var(--ease), stroke .4s;
}
.gauge.is-empty .g-val { opacity: 0; }
.g-read { position: absolute; inset: 0 0 8% 0; display: flex; align-items: center; justify-content: center; gap: 1px; }
.g-num { font: 600 21px/1 var(--mono); letter-spacing: -.03em; color: var(--text); }
.g-unit { align-self: flex-start; margin-top: calc(50% - 16px); font: 500 10px/1 var(--mono); color: var(--muted); }
.g-bolt { width: 13px; height: 13px; color: var(--green); fill: currentColor; stroke: none; margin-right: 1px; display: none; }
.gauge.is-charging .g-bolt { display: block; }
.g-k { position: absolute; left: 0; right: 0; bottom: 5%; text-align: center; font: 600 9.5px/1 var(--display); letter-spacing: .16em; text-transform: uppercase; color: var(--muted); }
.g-sub { max-width: 100%; font: 500 11px/1.25 var(--mono); color: var(--muted); text-align: center; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
@container dev (max-width: 400px) { .gauges { gap: 6px; padding: 12px 6px 10px; } .g-num { font-size: 17px; } .g-sub { font-size: 10px; } .g-k { font-size: 8.5px; letter-spacing: .12em; } }

/* sparklines */
.sparks { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
@container dev (max-width: 440px) { .sparks { grid-template-columns: minmax(0, 1fr); } }
.spark { padding: 10px 12px 8px; min-width: 0; }
.spark figcaption { display: flex; align-items: center; gap: 4px 12px; flex-wrap: wrap; font-size: 11.5px; color: var(--muted); }
.spark figcaption .k { margin-right: auto; }
.sp-v { white-space: nowrap; }
.sp-v b { font: 600 12px/1 var(--mono); color: var(--text); }
.sw { display: inline-block; width: 9px; height: 3px; border-radius: 2px; margin-right: 5px; vertical-align: middle; position: relative; top: -1px; }
.sw-cpu { background: var(--cyan); } .sw-ram { background: var(--violet); } .sw-down { background: var(--green); } .sw-up { background: var(--amber); }
.sp-svg { display: block; width: 100%; height: 46px; margin-top: 8px; overflow: visible; }
.sp-grid { fill: none; stroke: rgba(255, 255, 255, .05); stroke-width: 1; vector-effect: non-scaling-stroke; }
.sp-line { fill: none; stroke-width: 1.7; vector-effect: non-scaling-stroke; stroke-linejoin: round; stroke-linecap: round; }
.sp-cpu { stroke: var(--cyan); } .sp-ram { stroke: var(--violet); } .sp-down { stroke: var(--green); } .sp-up { stroke: var(--amber); }
.sp-area { stroke: none; }
.sp-area-cpu { fill: rgba(0, 242, 254, .08); } .sp-area-down { fill: rgba(16, 185, 129, .08); }

/* active window */
.focus { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: 10px; padding: 11px 12px; }
.focus-v { display: flex; align-items: center; gap: 8px; min-width: 0; }
.focus-proc { flex: none; max-width: 42%; padding: 3px 6px; border-radius: 5px; font: 600 11px/1.2 var(--mono); color: var(--cyan); background: rgba(0, 242, 254, .08); border: 1px solid rgba(0, 242, 254, .22); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.focus-win { min-width: 0; font-size: 13px; color: var(--text-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.idle { display: inline-flex; align-items: center; gap: 6px; font: 600 11.5px/1 var(--ui); color: var(--muted); white-space: nowrap; }
.idle::before { content: ''; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.idle.is-active { color: var(--green); }
.idle.is-idle { color: var(--amber); }
.idle:empty { display: none; }

/* audio / brightness strips */
.ctl { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding: 10px 12px; }
.ctl-k { font-size: 12.5px; font-weight: 600; color: var(--text-2); flex: none; }
.ctl-ico { color: var(--muted); display: grid; place-items: center; width: 24px; height: 34px; flex: none; }
.ctl .range { flex: 1 1 80px; }
.ctl-v { flex: none; min-width: 40px; font: 600 12px/1 var(--mono); color: var(--text); text-align: right; }
.media { display: inline-flex; gap: 4px; flex: none; margin-left: auto; }
.is-muted [data-act=mute] { color: #FCA5A5; border-color: rgba(239, 68, 68, .4); background: rgba(239, 68, 68, .1); }
.is-muted .ctl-audio .range { --fill: var(--faint); }
.range { -webkit-appearance: none; appearance: none; height: 26px; min-width: 0; background: transparent; cursor: pointer; --p: 0%; --fill: var(--cyan); }
.range:disabled { cursor: not-allowed; opacity: .45; }
.range::-webkit-slider-runnable-track { height: 6px; border-radius: 6px; background: linear-gradient(90deg, var(--fill) 0 var(--p), rgba(255, 255, 255, .1) var(--p) 100%); }
.range::-webkit-slider-thumb { -webkit-appearance: none; width: 16px; height: 16px; margin-top: -5px; border: 0; border-radius: 50%; background: #F8FAFC; box-shadow: 0 0 0 4px rgba(0, 242, 254, .18), 0 2px 6px rgba(0, 0, 0, .45); }
.range::-moz-range-track { height: 6px; border-radius: 6px; background: rgba(255, 255, 255, .1); }
.range::-moz-range-progress { height: 6px; border-radius: 6px; background: var(--fill); }
.range::-moz-range-thumb { width: 16px; height: 16px; border: 0; border-radius: 50%; background: #F8FAFC; box-shadow: 0 0 0 4px rgba(0, 242, 254, .18); }

/* action tiles */
.acts { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); grid-auto-flow: row dense; gap: 8px; }
.act {
  position: relative; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 6px;
  min-height: 66px; padding: 10px 6px; border-radius: 12px; border: 1px solid var(--line); background: rgba(255, 255, 255, .03);
  color: var(--text-2); font: 600 11.5px/1.2 var(--ui); text-align: center; transition: background .15s, border-color .15s, color .15s, transform .1s;
}
.act .i { width: 20px; height: 20px; color: var(--cyan); }
.act:hover:not(:disabled) { background: rgba(255, 255, 255, .07); border-color: rgba(0, 242, 254, .38); color: var(--text); }
.act:active:not(:disabled) { transform: translateY(1px); }
.act:disabled { opacity: .4; cursor: not-allowed; }
.act-wide { grid-column: span 2; flex-direction: row; gap: 10px; font-size: 12.5px; }
.act-accent { color: var(--text); border-color: rgba(0, 242, 254, .32); background: linear-gradient(135deg, rgba(0, 242, 254, .13), rgba(124, 58, 237, .18)); }
.act[aria-pressed=true] { color: var(--text); border-color: rgba(245, 158, 11, .5); background: rgba(245, 158, 11, .12); }
.act[aria-pressed=true] .i { color: var(--amber); }
@container dev (max-width: 380px) { .acts { grid-template-columns: repeat(3, minmax(0, 1fr)); } .act-wide { grid-column: span 3; } }
.menu-wrap { position: relative; display: grid; }
.menu { position: absolute; z-index: 30; top: calc(100% + 6px); left: 0; min-width: 176px; padding: 6px; border-radius: 12px; background: rgba(13, 18, 34, .98); border: 1px solid var(--line-2); box-shadow: 0 20px 44px rgba(0, 0, 0, .6); }
.menu.flip { left: auto; right: 0; }
.menu button { display: flex; align-items: center; gap: 10px; width: 100%; padding: 9px 10px; border: 0; border-radius: 8px; background: transparent; color: var(--text-2); font-size: 13px; font-weight: 500; text-align: left; }
.menu button .i { width: 16px; height: 16px; color: var(--muted); }
.menu button:hover, .menu button:focus-visible { background: rgba(255, 255, 255, .07); color: var(--text); }

/* collapsible panels */
.panel { border-radius: 12px; border: 1px solid var(--line); background: rgba(3, 6, 15, .35); min-width: 0; }
.panel > summary { list-style: none; display: flex; align-items: center; gap: 10px; padding: 11px 12px; cursor: pointer; font-size: 13px; font-weight: 600; color: var(--text-2); user-select: none; border-radius: 12px; }
.panel > summary::-webkit-details-marker { display: none; }
.panel > summary::after { content: ''; flex: none; margin-left: auto; width: 7px; height: 7px; border-right: 1.6px solid var(--muted); border-bottom: 1.6px solid var(--muted); transform: translateY(-2px) rotate(45deg); transition: transform .2s var(--ease); }
.panel[open] > summary::after { transform: translateY(2px) rotate(-135deg); }
.panel > summary > span:first-child { flex: none; }
.panel-meta { min-width: 0; font: 500 11px/1.2 var(--mono); color: var(--faint); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.panel-body { padding: 0 12px 12px; display: grid; gap: 10px; }
.panel-tools { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
.panel-note { font-size: 11.5px; color: var(--faint); }
.table-wrap { overflow-x: auto; overscroll-behavior-x: contain; }
.ptable { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.ptable th { padding: 6px 8px; text-align: left; font: 600 9.5px/1 var(--display); letter-spacing: .12em; text-transform: uppercase; color: var(--faint); border-bottom: 1px solid var(--line); white-space: nowrap; }
.ptable td { padding: 6px 8px; border-bottom: 1px solid rgba(255, 255, 255, .04); white-space: nowrap; vertical-align: middle; }
.ptable tr:last-child td { border-bottom: 0; }
.ptable .num { text-align: right; font-family: var(--mono); font-variant-numeric: tabular-nums; color: var(--text-2); }
.p-name { max-width: 190px; overflow: hidden; text-overflow: ellipsis; font-weight: 600; }
.p-bar { display: block; height: 2px; margin-top: 3px; border-radius: 2px; background: linear-gradient(90deg, var(--cyan), var(--violet)); opacity: .7; }
.p-act { width: 34px; text-align: right; }
.kill:hover:not(:disabled) { color: #FCA5A5; border-color: rgba(239, 68, 68, .45); background: rgba(239, 68, 68, .12); }
@container dev (max-width: 440px) {
  .ptable .c-pid { display: none; }
  .ptable th, .ptable td { padding: 6px 5px; }
  .p-name { max-width: 130px; }
}
.specs { display: grid; gap: 9px; }
.spec { display: grid; grid-template-columns: 104px minmax(0, 1fr); gap: 10px; font-size: 12.5px; }
.spec dt { color: var(--faint); font-weight: 600; }
.spec dd { color: var(--text-2); font: 500 12px/1.45 var(--mono); overflow-wrap: anywhere; white-space: pre-line; }

/* phone */
.phone-main { display: grid; grid-template-columns: 112px minmax(0, 1fr); gap: 16px; align-items: center; margin-top: 14px; }
.phone-main .gauge { padding: 4px 0; }
.phone-main .g-dial { max-width: 108px; }
.counts { display: grid; padding: 4px 12px; min-width: 0; }
.counts li { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; padding: 8px 0; min-width: 0; border-bottom: 1px solid rgba(255, 255, 255, .05); }
.counts li:last-child { border-bottom: 0; }
.counts span { min-width: 0; font-size: 12.5px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.counts b { flex: none; font: 600 15px/1 var(--mono); color: var(--text); white-space: nowrap; }
.counts b.is-hot { color: var(--amber); }
.acts-phone { grid-template-columns: repeat(3, minmax(0, 1fr)); margin-top: 14px; }
.dev-note { margin-top: 12px; font-size: 12px; color: #FDE68A; }
.dev-empty { padding: 26px; display: grid; gap: 14px; border-style: dashed; border-color: rgba(255, 255, 255, .14); }
.dev-empty h3 { font-size: 17px; font-weight: 700; }
.dev-empty ol { display: grid; gap: 12px; counter-reset: step; }
.dev-empty li { display: grid; grid-template-columns: 28px minmax(0, 1fr); gap: 10px; font-size: 13.5px; color: var(--text-2); }
.dev-empty li::before { counter-increment: step; content: counter(step); width: 26px; height: 26px; border-radius: 8px; display: grid; place-items: center; font: 600 12px/1 var(--mono); color: var(--cyan); background: rgba(0, 242, 254, .08); border: 1px solid rgba(0, 242, 254, .25); }
.dev-empty .cmd { display: block; margin-top: 6px; padding: 8px 10px; border-radius: 8px; background: rgba(3, 6, 15, .7); border: 1px solid var(--line); font: 500 12px/1.5 var(--mono); color: var(--text); overflow-wrap: anywhere; }

/* ---------- server card + panel ---------- */
.dev-server .dev-ico { color: var(--green); background: linear-gradient(145deg, rgba(16, 185, 129, .16), rgba(0, 242, 254, .1)); }
.gauge[data-g=swap] { --gc: #F472B6; --gg: rgba(244, 114, 182, .45); }
.acts-server { margin-top: 14px; }
@container dev (max-width: 380px) { .acts-server { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.srvp { scroll-margin-top: 80px; }
.srvp-note { margin: 12px 18px 0; padding: 9px 12px; border-radius: 10px; font-size: 12.5px; color: #FECACA; background: rgba(239, 68, 68, .08); border: 1px solid rgba(239, 68, 68, .32); }
.srvp-tabs { padding: 14px 18px 0; }
.srvp-tabs .seg { flex-wrap: wrap; }
.srvp-pane { display: grid; gap: 16px; padding: 16px 18px 18px; min-width: 0; }
.hchip { display: inline-flex; align-items: center; height: 22px; padding: 0 8px; border-radius: 6px; border: 1px solid; font: 700 11px/1 var(--ui); white-space: nowrap; }
.hchip.ok { color: #6EE7B7; background: rgba(16, 185, 129, .1); border-color: rgba(16, 185, 129, .35); }
.hchip.warn { color: #FCD34D; background: rgba(245, 158, 11, .1); border-color: rgba(245, 158, 11, .35); }
.hchip.bad { color: #FCA5A5; background: rgba(239, 68, 68, .1); border-color: rgba(239, 68, 68, .38); }
.hchip.idle { color: var(--muted); background: rgba(148, 163, 184, .08); border-color: rgba(148, 163, 184, .25); }
.srvp-summary { display: grid; gap: 3px; }
.srvp-summary p:first-child { font-size: 14px; color: var(--text-2); }
.srvp-checked { font: 500 11.5px/1.3 var(--mono); color: var(--faint); }
.problems { display: grid; gap: 6px; }
.problems li { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: 10px; padding: 7px 8px 7px 11px; border-radius: 10px; font-size: 13px; color: var(--text-2); background: rgba(245, 158, 11, .06); border: 1px solid rgba(245, 158, 11, .25); }
.problems li > .i { width: 16px; height: 16px; color: var(--amber); }
.problems li.is-bad { background: rgba(239, 68, 68, .07); border-color: rgba(239, 68, 68, .3); }
.problems li.is-bad > .i { color: var(--red); }
.problems li.is-muted { background: transparent; border-color: var(--line); color: var(--faint); }
.problems li.is-muted > .i { color: var(--faint); }
.sgrid { display: grid; gap: 16px; grid-template-columns: minmax(0, 1fr); }
@media (min-width: 1100px) { .sgrid { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
.sgroup { display: grid; gap: 8px; align-content: start; min-width: 0; }
.sgroup-head { display: flex; align-items: center; gap: 10px; min-width: 0; }
.sgroup-head h3 { font: 600 11px/1.2 var(--display); letter-spacing: .14em; text-transform: uppercase; color: var(--muted); }
.sgroup-head .sec-count { margin-right: auto; }
.ulist { display: grid; gap: 6px; }
.unit { display: grid; gap: 6px; padding: 9px 10px; border-radius: 10px; background: var(--well); border: 1px solid rgba(255, 255, 255, .05); min-width: 0; }
.unit.is-bad { border-color: rgba(239, 68, 68, .35); }
.unit-top { display: flex; align-items: center; gap: 8px; min-width: 0; }
.unit-name { flex: 1; min-width: 0; font-weight: 600; font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.unit-meta { font: 500 11px/1.2 var(--mono); color: var(--faint); }
.unit-acts { display: flex; gap: 4px; flex-wrap: wrap; }
.unit-acts .btn-sm { min-height: 26px; padding: 0 8px; font-size: 11.5px; gap: 5px; }
.unit-acts .btn-sm .i { width: 13px; height: 13px; }
.unit-acts .is-stop:hover:not(:disabled) { color: #FCA5A5; border-color: rgba(239, 68, 68, .45); background: rgba(239, 68, 68, .1); }
.list-note { font-size: 12.5px; color: var(--faint); }
.site-dom { display: block; font-weight: 600; max-width: 260px; overflow: hidden; text-overflow: ellipsis; }
.act-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.act-list .btn { justify-content: flex-start; }
.site-err { display: block; max-width: 260px; overflow: hidden; text-overflow: ellipsis; font: 500 11px/1.3 var(--mono); color: #FCA5A5; }
.fbar { display: flex; align-items: center; gap: 8px; min-width: 0; }
.crumbs { display: flex; align-items: center; flex: 1; min-width: 0; overflow-x: auto; scrollbar-width: none; padding: 2px 0; }
.crumbs::-webkit-scrollbar { display: none; }
.crumbs button { flex: none; border: 0; background: transparent; color: var(--muted); font: 500 12.5px/1 var(--mono); padding: 7px 6px; border-radius: 6px; white-space: nowrap; max-width: 220px; overflow: hidden; text-overflow: ellipsis; }
.crumbs button:hover:not(:disabled) { color: var(--text); background: rgba(255, 255, 255, .05); }
.crumbs button[aria-current=page] { color: var(--cyan); font-weight: 600; cursor: default; }
.crumbs .sep { flex: none; color: var(--faint); font: 500 12px/1 var(--mono); }
.f-msg { font-size: 12.5px; color: var(--muted); }
.f-msg.is-error { color: #FCA5A5; }
.ftable td { vertical-align: middle; }
.f-name { display: flex; align-items: center; gap: 9px; min-width: 0; max-width: 420px; }
.f-name .i { width: 17px; height: 17px; color: var(--muted); }
.f-name .i.is-dir { color: var(--amber); }
.f-name .i.is-disk { color: #7DD3FC; }
.f-name button, .f-name span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.f-name button { border: 0; background: transparent; padding: 0; color: var(--text); font: 600 13px/1.4 var(--ui); text-align: left; }
.f-name button:hover { color: var(--cyan); text-decoration: underline; }
.f-acts { display: flex; justify-content: flex-end; gap: 4px; }
.f-acts .menu { left: auto; right: 0; }
.ftable tr.is-drive td { background: rgba(56, 189, 248, .03); }
.term-out { height: clamp(220px, 42vh, 460px); overflow: auto; overscroll-behavior: contain; padding: 12px 14px; border-radius: 12px; background: #03050B; border: 1px solid var(--line); font: 12.5px/1.5 var(--mono); }
.term-empty { color: var(--faint); font-family: var(--ui); font-size: 13px; }
.term-run + .term-run { margin-top: 12px; padding-top: 10px; border-top: 1px dashed rgba(255, 255, 255, .08); }
.term-head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.term-cmd { flex: 1; min-width: 0; color: var(--text); white-space: pre-wrap; overflow-wrap: anywhere; }
.term-cmd::before { content: '$ '; color: var(--green); }
.term-meta { color: var(--faint); font-size: 11px; white-space: nowrap; }
.term-pre { margin: 5px 0 0; font: inherit; color: var(--text-2); white-space: pre-wrap; overflow-wrap: anywhere; }
.term-pre.err { color: #FCA5A5; }
.term-pre.dim { color: var(--faint); }
.term-chips { display: flex; gap: 6px; flex-wrap: wrap; }
.term-chips .chip { height: 26px; padding: 0 10px; font: 500 12px/1 var(--mono); }
.term-form { display: flex; gap: 8px; flex-wrap: wrap; }
.term-form #term-cwd { flex: 0 1 190px; width: auto; font-family: var(--mono); font-size: 13px; }
.term-form #term-cmd { flex: 1 1 260px; width: auto; font-family: var(--mono); font-size: 13.5px; }
.term-form .btn { flex: none; }
.term-hint { font-size: 11.5px; color: var(--faint); }
.pcmd { max-width: 360px; overflow: hidden; text-overflow: ellipsis; font: 500 11.5px/1.3 var(--mono); color: var(--muted); }
.dlg.dlg-wide { width: min(980px, calc(100vw - 32px)); }
.dlg-sub { margin-top: -8px; font: 500 12px/1.4 var(--mono); color: var(--muted); overflow-wrap: anywhere; }
.dlg-pre { margin: 0; max-height: 62vh; overflow: auto; padding: 12px 14px; border-radius: 10px; background: #03050B; border: 1px solid var(--line); font: 12px/1.5 var(--mono); color: var(--text-2); white-space: pre-wrap; overflow-wrap: anywhere; }
.dlg-pre.is-error { color: #FCA5A5; }
.dlg-edit { min-height: 52vh; font: 12.5px/1.5 var(--mono); white-space: pre; overflow-wrap: normal; }
/* server system tabs: history, updates, storage, network & security, services & jobs, logs */
.sys-grid { display: grid; gap: 16px; grid-template-columns: minmax(0, 1fr); min-width: 0; }
@media (min-width: 1100px) { .sys-grid.two { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.sys-msg { flex: 1; min-width: 0; font-size: 13.5px; color: var(--text-2); }
.sys-msg.is-error { color: #FCA5A5; }
.sys-meta { font: 500 11.5px/1.4 var(--mono); color: var(--faint); overflow-wrap: anywhere; }
.sys-busy { display: flex; align-items: center; gap: 10px; font-size: 12.5px; color: #FCD34D; }
.sys-busy::before { content: ''; flex: none; width: 13px; height: 13px; border-radius: 50%; border: 2px solid rgba(245, 158, 11, .25); border-top-color: var(--amber); animation: spin .8s linear infinite; }
.sys-acts { display: flex; gap: 6px; flex-wrap: wrap; }
.sys-pre { margin: 0; max-height: 40vh; overflow: auto; padding: 10px 12px; border-radius: 10px; background: #03050B; border: 1px solid var(--line); font: 11.5px/1.5 var(--mono); color: var(--text-2); white-space: pre-wrap; overflow-wrap: anywhere; }
.sys-pre.is-error { color: #FCA5A5; }
.sys-logs { max-height: 62vh; min-height: 160px; }
.chips { display: flex; gap: 6px; flex-wrap: wrap; }
.chart-card { display: grid; gap: 8px; padding: 12px; border-radius: 12px; background: var(--well); border: 1px solid rgba(255, 255, 255, .05); min-width: 0; }
.chart-head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; min-width: 0; }
.chart-head h3 { font: 600 11px/1.2 var(--display); letter-spacing: .14em; text-transform: uppercase; color: var(--muted); }
.chart-legend { display: flex; gap: 4px 14px; flex-wrap: wrap; font: 500 11px/1.4 var(--mono); color: var(--muted); }
.chart-legend span { display: inline-flex; align-items: center; gap: 6px; }
.chart-legend i { flex: none; width: 10px; height: 3px; border-radius: 2px; }
.chart-box { position: relative; height: 170px; min-width: 0; touch-action: pan-y; }
.chart-box svg { display: block; width: 100%; height: 100%; overflow: visible; }
.chart-box .grid { stroke: rgba(255, 255, 255, .07); stroke-width: 1; }
.chart-box .lbl { fill: var(--faint); font: 500 9.5px var(--mono); }
.chart-box .empty { fill: var(--faint); font: 500 12px var(--ui); }
.chart-box .cursor { stroke: rgba(226, 232, 240, .45); stroke-width: 1; }
.chart-tip { position: absolute; top: 2px; z-index: 2; pointer-events: none; padding: 6px 8px; border-radius: 8px; background: rgba(8, 11, 22, .95); border: 1px solid var(--line-2); font: 500 11px/1.45 var(--mono); color: var(--text-2); white-space: nowrap; }
.chart-tip b { display: block; font-weight: 600; color: var(--text); }
.chart-tip i { display: inline-block; width: 8px; height: 3px; margin-right: 6px; border-radius: 2px; vertical-align: middle; }
.mlist { display: grid; gap: 12px; }
.mrow { display: grid; gap: 5px; min-width: 0; }
.mrow-top { display: flex; align-items: baseline; gap: 10px; min-width: 0; font-size: 12.5px; }
.mrow-top b { flex: none; max-width: 55%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 600; color: var(--text-2); }
.mrow-top .meta { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font: 500 11px/1.3 var(--mono); color: var(--faint); }
.mrow-top .pct { flex: none; font: 600 12.5px/1 var(--mono); }
.bar { height: 7px; border-radius: 4px; background: rgba(148, 163, 184, .16); overflow: hidden; }
.bar > i { display: block; height: 100%; border-radius: 4px; }
.bar.thin { height: 4px; min-width: 60px; }
.clean-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 10px; padding: 9px 10px; border-radius: 10px; background: var(--well); border: 1px solid rgba(255, 255, 255, .05); min-width: 0; }
.clean-row b { display: block; font-size: 13px; font-weight: 600; }
.clean-row span { display: block; font: 500 11px/1.4 var(--mono); color: var(--muted); overflow-wrap: anywhere; }
.big-path { display: block; max-width: 520px; overflow: hidden; text-overflow: ellipsis; font: 500 12px/1.3 var(--mono); color: var(--text-2); }
.rate-tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 8px; }
.rate-tile { padding: 9px 12px; border-radius: 10px; background: var(--well); border: 1px solid rgba(255, 255, 255, .05); min-width: 0; }
.rate-tile small { display: block; font-size: 11px; color: var(--faint); }
.rate-tile strong { display: block; margin-top: 2px; font: 600 15px/1.2 var(--mono); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.kv { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px 12px; align-items: center; font-size: 13px; color: var(--muted); }
.kv dd { margin: 0; justify-self: end; }
.ptable td.wrap { white-space: normal; }
.addr { display: inline-block; margin: 1px 8px 1px 0; font: 500 12px/1.4 var(--mono); color: var(--text-2); white-space: nowrap; }
.ptable tr.is-public td { background: rgba(245, 158, 11, .06); }
.ptable tr.is-bad td { background: rgba(239, 68, 68, .05); }
.reboot-banner { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding: 9px 12px; border-radius: 10px; font-size: 12.5px; color: #FECACA; background: rgba(239, 68, 68, .08); border: 1px solid rgba(239, 68, 68, .32); }
.reboot-banner span { flex: 1; min-width: 180px; }
.syv-q { flex: 1 1 200px; width: auto; min-height: 32px; padding: 5px 10px; font-size: 13px; }
.svc-wrap { max-height: 62vh; overflow-y: auto; }
.svc-table thead th { position: sticky; top: 0; z-index: 1; background: rgba(10, 14, 27, .98); }
.svc-name { display: block; max-width: 300px; overflow: hidden; text-overflow: ellipsis; font-weight: 600; }
.svc-desc { display: block; max-width: 300px; overflow: hidden; text-overflow: ellipsis; font-size: 11.5px; color: var(--faint); }
.svc-acts { display: flex; gap: 4px; justify-content: flex-end; }
.svc-acts .btn-sm { min-height: 26px; padding: 0 8px; font-size: 11.5px; gap: 5px; }
.svc-acts .btn-sm .i { width: 13px; height: 13px; }
.logs-form { display: grid; gap: 10px; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); align-items: end; }
.timer-line { display: block; max-width: 560px; overflow: hidden; text-overflow: ellipsis; font: 500 11px/1.3 var(--mono); color: var(--faint); }
.dlg-ack { display: flex; align-items: flex-start; gap: 10px; padding: 10px 12px; border-radius: 10px; font-size: 13px; color: var(--text-2); background: rgba(239, 68, 68, .06); border: 1px solid rgba(239, 68, 68, .3); cursor: pointer; }
.dlg-ack input { margin-top: 2px; accent-color: var(--red); }
@media (max-width: 767px) {
  .srvp-tabs { padding: 14px 16px 0; }
  .srvp-pane { padding: 14px 16px 16px; }
  .srvp-note { margin: 12px 16px 0; }
  .ftable .c-mod { display: none; }
  .term-form #term-cwd { flex: 1 1 100%; }
  .term-form #term-cmd { font-size: 16px; }
  .syv-q, .logs-form input { font-size: 16px; }
  .chart-box { height: 150px; }
  .svc-name, .svc-desc { max-width: 170px; }
}

/* ---------- activity ---------- */
.stats { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 1px; margin: 14px 18px 0; border-radius: 12px; overflow: hidden; background: var(--line); border: 1px solid var(--line); }
.stat { padding: 9px 10px 10px; min-width: 0; background: rgba(8, 11, 22, .92); }
.stat .k { display: block; letter-spacing: .08em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.stat-v { display: block; margin-top: 5px; font: 600 15px/1.1 var(--mono); color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.legend { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 12px; padding: 10px 18px 0; font-size: 11px; color: var(--muted); }
.legend span { display: inline-flex; align-items: center; gap: 5px; white-space: nowrap; }
.legend i { width: 9px; height: 5px; border-radius: 2px; }
.legend .ribbon-scale { margin-left: auto; color: var(--faint); font-family: var(--mono); font-size: 10.5px; }
.filters { display: flex; gap: 6px; padding: 12px 18px 0; flex-wrap: wrap; }
.filters .chip { height: 26px; padding: 0 10px; font-size: 12px; }
.feed { margin-top: 8px; padding: 0 8px 10px; max-height: 600px; overflow-y: auto; overscroll-behavior: contain; }
.act-item { display: grid; grid-template-columns: 24px minmax(0, 1fr); gap: 10px; padding: 10px; border-radius: 12px; transition: background .15s; }
.act-item:hover { background: rgba(255, 255, 255, .03); }
.act-item.is-new { animation: flash-in 1.2s var(--ease); }
.ai-ico { width: 24px; height: 24px; border-radius: 8px; display: grid; place-items: center; margin-top: 1px; }
.ai-ico .i { width: 14px; height: 14px; stroke-width: 2.4; }
[data-status=done] > .ai-ico { background: rgba(16, 185, 129, .12); color: var(--green); }
[data-status=error] > .ai-ico { background: rgba(239, 68, 68, .12); color: var(--red); }
[data-status=running] > .ai-ico { background: rgba(0, 242, 254, .1); color: var(--cyan); }
.spinner { width: 13px; height: 13px; border-radius: 50%; border: 2px solid rgba(0, 242, 254, .25); border-top-color: var(--cyan); animation: spin .8s linear infinite; }
.ai-top { display: flex; align-items: center; gap: 8px; min-width: 0; }
.src { flex: none; height: 18px; padding: 0 6px; border-radius: 5px; font: 600 10px/16px var(--mono); color: var(--muted); border: 1px solid var(--line-2); background: rgba(255, 255, 255, .03); white-space: nowrap; }
.ai-q { flex: 1; min-width: 0; font-size: 13.5px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ai-t { flex: none; font: 500 11px/1 var(--mono); color: var(--faint); }
.ai-reply { margin-top: 3px; font-size: 12.5px; color: var(--muted); overflow-wrap: anywhere; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.ai-reply.is-pending { font-style: italic; }
.ai-meta { margin-top: 7px; display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.feed-empty { padding: 18px; color: var(--muted); font-size: 13px; }

/* ---------- reminders ---------- */
.rem-tabs { padding: 14px 18px 0; }
.tabpanel { padding: 10px 8px 0; }
.rlist { display: grid; gap: 2px; }
.ritem { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: 10px; padding: 8px 10px; border-radius: 10px; transition: background .15s, opacity .2s; }
.ritem:hover { background: rgba(255, 255, 255, .03); }
.rtext { font-size: 13.5px; font-weight: 600; overflow-wrap: anywhere; }
.ritem.done .rtext { color: var(--faint); text-decoration: line-through; }
.rwhen { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-top: 2px; font: 500 11.5px/1.3 var(--mono); color: var(--muted); }
.tag { font: 700 9.5px/1 var(--ui); letter-spacing: .08em; text-transform: uppercase; padding: 3px 6px; border-radius: 5px; }
.tag-missed { color: #FCA5A5; background: rgba(239, 68, 68, .12); }
.tag-rang { color: #FDE68A; background: rgba(245, 158, 11, .12); }
.check { position: relative; display: grid; cursor: pointer; }
.check input { position: absolute; opacity: 0; width: 1px; height: 1px; }
.check-box { width: 20px; height: 20px; border-radius: 6px; border: 1.5px solid rgba(255, 255, 255, .22); display: grid; place-items: center; color: transparent; transition: background .15s, border-color .15s, color .15s; }
.check-box .i { width: 13px; height: 13px; stroke-width: 3; }
.check input:checked + .check-box { background: var(--green); border-color: var(--green); color: #03140D; }
.check input:focus-visible + .check-box { outline: 2px solid var(--cyan); outline-offset: 2px; }
.check:hover .check-box { border-color: rgba(16, 185, 129, .7); }
.atime { font: 600 20px/1 var(--mono); letter-spacing: -.02em; }
.alabel { font-size: 12.5px; color: var(--muted); margin-top: 3px; overflow-wrap: anywhere; }
.aitem { grid-template-columns: minmax(0, 1fr) auto auto; }
.aitem.is-off .atime, .aitem.is-off .alabel { color: var(--faint); }
.list-empty { padding: 10px 10px 4px; font-size: 13px; color: var(--muted); }
.add-form { display: grid; gap: 8px; margin: 10px 10px 0; padding: 12px 0 18px; border-top: 1px solid var(--line); }
.add-row { display: flex; gap: 8px; flex-wrap: wrap; }
.add-row > input[type=time] { flex: 1 1 108px; width: auto; }
.add-row > select { flex: 1 1 120px; width: auto; }
.add-row > input[type=date] { flex: 1 1 140px; width: auto; }
.add-row > input[type=text] { flex: 2 1 150px; width: auto; }
.add-row > .btn { flex: none; }
.form-err { font-size: 12.5px; color: #FCA5A5; }

/* ---------- quick tools ---------- */
.tool-tabs { padding: 14px 18px 0; }
.tool-tabs .seg button { flex: 1 1 auto; min-width: 0; }
@media (max-width: 360px) { .tool-tabs .seg button { padding: 0 8px; } .tool-tabs .seg button .i { display: none; } }
.tool-form { display: grid; gap: 11px; padding: 14px 18px 4px; }
.field { display: grid; gap: 6px; min-width: 0; }
.field > span { font-size: 12px; font-weight: 600; color: var(--muted); }
.field-row { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.tool-result { font-size: 13px; padding: 10px 12px; border-radius: 10px; background: rgba(255, 255, 255, .04); border: 1px solid var(--line); overflow-wrap: anywhere; }
.tool-result.is-error { border-color: rgba(239, 68, 68, .4); background: rgba(239, 68, 68, .08); color: #FECACA; }
.tool-note { padding: 8px 18px 18px; font-size: 12px; color: var(--faint); }

/* ---------- toasts ---------- */
.toasts { position: fixed; z-index: 1000; right: 20px; bottom: 20px; width: min(380px, calc(100vw - 32px)); display: flex; flex-direction: column; gap: 10px; pointer-events: none; }
.toast {
  --tc: var(--cyan); position: relative; overflow: hidden; pointer-events: auto; display: grid; grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 10px; align-items: start; padding: 12px 10px 12px 15px; border-radius: 14px; background: rgba(13, 18, 32, .97);
  border: 1px solid var(--line-2); box-shadow: 0 22px 44px -12px rgba(0, 0, 0, .75); animation: toast-in .26s var(--ease);
}
.toast::before { content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 3px; background: var(--tc); }
.toast-success { --tc: var(--green); } .toast-error { --tc: var(--red); } .toast-warn { --tc: var(--amber); }
.toast-alert { --tc: var(--amber); border-color: rgba(245, 158, 11, .5); background: linear-gradient(135deg, rgba(245, 158, 11, .16), transparent 60%), #0D1220; }
.toast-ico { color: var(--tc); padding-top: 1px; }
.toast-title { font-size: 13.5px; font-weight: 700; }
.toast-msg { font-size: 13px; color: var(--text-2); overflow-wrap: anywhere; }
.toast-x { width: 26px; height: 26px; border: 0; border-radius: 7px; background: transparent; color: var(--faint); display: grid; place-items: center; }
.toast-x:hover { color: var(--text); background: rgba(255, 255, 255, .06); }
.toast-x .i { width: 14px; height: 14px; }
.toast.is-out { animation: toast-out .2s ease forwards; }

/* ---------- dialogs ---------- */
.dlg { padding: 0; margin: auto; width: min(440px, calc(100vw - 32px)); max-height: calc(100vh - 32px); border: 1px solid var(--line-2); border-radius: 18px; background: #0C1120; color: var(--text); box-shadow: 0 40px 100px -24px rgba(0, 0, 0, .85); overflow: auto; }
.dlg[open] { animation: pop .2s var(--ease); }
.dlg::backdrop { background: rgba(2, 4, 10, .66); backdrop-filter: blur(3px); -webkit-backdrop-filter: blur(3px); }
.dlg-form { display: grid; gap: 14px; padding: 22px; }
.dlg-title { font: 700 17px/1.3 var(--ui); overflow-wrap: anywhere; }
.dlg-body { color: var(--text-2); font-size: 14px; overflow-wrap: anywhere; }
.setup-sec { display: grid; gap: 10px; padding-top: 14px; border-top: 1px solid var(--line); }
.setup-h { font: 700 15px/1.3 var(--ui); }
.setup-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.setup-link { font-size: 13px; color: var(--cyan); }
.setup-status { min-height: 1.3em; font-size: 13px; color: var(--muted); overflow-wrap: anywhere; }
.setup-status.is-ok { color: #86EFAC; } .setup-status.is-bad { color: #FCA5A5; }
.setup-code { font: 700 30px/1.2 var(--mono); letter-spacing: .14em; text-align: center; padding: 12px 0; border: 1px dashed var(--line-2); border-radius: 12px; }
.setup-hub { font: 500 13px/1.3 var(--mono); overflow-wrap: anywhere; }
.dlg-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px; flex-wrap: wrap; }
.dlg-screen { width: min(1200px, calc(100vw - 32px)); overflow: hidden; display: none; flex-direction: column; }
.dlg-screen[open] { display: flex; }
.scr-head { display: flex; align-items: center; gap: 10px 14px; padding: 14px 16px; border-bottom: 1px solid var(--line); flex-wrap: wrap; }
.scr-head .i { color: var(--cyan); }
.scr-title { font: 700 15px/1.2 var(--ui); }
.scr-sub { margin-top: 3px; font: 500 11.5px/1.3 var(--mono); color: var(--muted); overflow-wrap: anywhere; }
.scr-stats { margin-left: auto; display: flex; gap: 6px; }
.scr-stat { padding: 6px 8px; border-radius: 7px; font: 600 11.5px/1 var(--mono); background: rgba(255, 255, 255, .05); border: 1px solid var(--line); white-space: nowrap; }
.scr-stage { position: relative; display: grid; place-items: center; min-height: 260px; background: #02040A; flex: 1; overflow: hidden; }
.scr-img { display: block; max-width: 100%; max-height: calc(100vh - 200px); object-fit: contain; cursor: zoom-in; }
.scr-msg { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); max-width: calc(100% - 32px); padding: 10px 14px; border-radius: 10px; background: rgba(13, 18, 32, .92); border: 1px solid var(--line-2); font-size: 13px; color: var(--text-2); text-align: center; }
.scr-foot { display: flex; align-items: center; gap: 10px; padding: 12px 16px; border-top: 1px solid var(--line); flex-wrap: wrap; }
.scr-foot select { width: auto; min-height: 34px; font-size: 13px; }
.scr-foot .spacer { flex: 1; }

/* ---------- motion ---------- */
@keyframes spin { to { transform: rotate(360deg); } }
@keyframes breathe { 0%, 100% { transform: scale(.94); opacity: .75; } 50% { transform: scale(1.04); opacity: 1; } }
@keyframes live { 0% { box-shadow: 0 0 0 0 rgba(16, 185, 129, .65); } 70%, 100% { box-shadow: 0 0 0 7px rgba(16, 185, 129, 0); } }
@keyframes blink { 50% { opacity: .35; } }
@keyframes typing { 0%, 80%, 100% { transform: translateY(0); opacity: .45; } 40% { transform: translateY(-4px); opacity: 1; } }
@keyframes rise { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
@keyframes flash-in { from { background: rgba(0, 242, 254, .09); } to { background: transparent; } }
@keyframes toast-in { from { opacity: 0; transform: translateY(10px) scale(.98); } to { opacity: 1; transform: none; } }
@keyframes toast-out { to { opacity: 0; transform: translateY(6px); } }
@keyframes pop { from { opacity: 0; transform: translateY(8px) scale(.98); } to { opacity: 1; transform: none; } }

/* ---------- responsive ---------- */
@media (max-width: 1279px) { #srv-ver { display: none; } }
@media (max-width: 1199px) {
  .layout { grid-template-columns: minmax(0, 1fr); }
  .col-rail { grid-template-columns: repeat(2, minmax(0, 1fr)); align-items: start; }
  .col-rail .activity { grid-row: span 2; }
}
@media (max-width: 1023px) { #srv-uptime { display: none; } }
@media (min-width: 768px) and (max-width: 880px) { .srv { display: none; } }
@media (max-width: 767px) {
  :root { --gutter: 16px; }
  .appbar { position: static; flex-wrap: wrap; gap: 10px; padding-top: 10px; padding-bottom: 10px; }
  .srv { order: 3; width: 100%; flex-wrap: wrap; }
  #srv-uptime, #srv-ver { display: inline-flex; }
  #srv-llm { max-width: 100%; }
  .brand-sub { display: none; }
  .brand-name { font-size: 17px; }
  .appbar-actions .btn { min-height: 34px; padding: 0 10px; }
  .appbar-actions .btn span { display: none; }
  .layout { padding-top: 16px; gap: 16px; }
  .col-main, .col-rail { gap: 16px; }
  .col-rail { grid-template-columns: minmax(0, 1fr); }
  .col-rail .activity { grid-row: auto; }
  .card-head { padding: 14px 16px 0; }
  .conv { padding: 14px 16px; }
  .chips { padding: 0 16px 12px; flex-wrap: nowrap; overflow-x: auto; scrollbar-width: none; }
  .chips::-webkit-scrollbar { display: none; }
  .prompt { margin: 0 16px; }
  .prompt-hint { display: none; }
  .console { padding-bottom: 16px; }
  .dev { padding: 14px 14px 16px; }
  .stats { margin: 14px 16px 0; }
  .stat-v { font-size: 13.5px; }
  .legend, .filters { padding-left: 16px; padding-right: 16px; }
  .feed { max-height: 70vh; }
  input[type=text], input[type=password], input[type=time], input[type=date], input[type=number], input[type=url], select, textarea, .prompt input { font-size: 16px; }
  .toasts { left: 16px; right: 16px; bottom: 16px; width: auto; }
  .scr-img { max-height: calc(100vh - 230px); }
}
@media (max-width: 420px) {
  .conn { padding: 0 10px; }
  .field-row { grid-template-columns: minmax(0, 1fr); }
  .stat { padding: 8px 6px 9px; }
  .stat .k { letter-spacing: .03em; }
  .stat-v { font-size: 13px; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: .001ms !important; animation-iteration-count: 1 !important; transition-duration: .001ms !important; scroll-behavior: auto !important; }
}
</style>
</head>
<body>
<svg class="sprite" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false">
  <defs>
    <linearGradient id="lg-brand" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#00F2FE"/><stop offset="1" stop-color="#7C3AED"/></linearGradient>
  </defs>
  <symbol id="i-logo" viewBox="0 0 40 40"><rect x="1" y="1" width="38" height="38" rx="11" fill="url(#lg-brand)" stroke="none"/><path d="M9.5 14.5l4.8 12 5.7-9.5 5.7 9.5 4.8-12" fill="none" stroke="#070913" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"/></symbol>
  <symbol id="i-lock" viewBox="0 0 24 24"><rect x="4.5" y="10.5" width="15" height="10" rx="2.5"/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/></symbol>
  <symbol id="i-moon" viewBox="0 0 24 24"><path d="M20 14.6A8 8 0 1 1 9.4 4a6.4 6.4 0 0 0 10.6 10.6z"/></symbol>
  <symbol id="i-camera" viewBox="0 0 24 24"><path d="M4 8h3l1.8-2.6h6.4L17 8h3a1 1 0 0 1 1 1v9.5a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z"/><circle cx="12" cy="13.2" r="3.4"/></symbol>
  <symbol id="i-desktop" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M9 20h6M12 16v4M9 8.5l3 3 3-3"/></symbol>
  <symbol id="i-screen" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8M12 17v4"/><path d="M10.2 8.3v5.4l4.3-2.7z" fill="currentColor"/></symbol>
  <symbol id="i-folder" viewBox="0 0 24 24"><path d="M3 7.5A2 2 0 0 1 5 5.5h4l2 2.2h8a2 2 0 0 1 2 2v7.8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></symbol>
  <symbol id="i-note" viewBox="0 0 24 24"><path d="M20.5 14.5a2 2 0 0 1-2 2H8l-4.5 3.5V5.5a2 2 0 0 1 2-2h13a2 2 0 0 1 2 2z"/><path d="M8 8.5h8M8 12h5"/></symbol>
  <symbol id="i-prev" viewBox="0 0 24 24"><path d="M18.5 19.5L9 12l9.5-7.5z" fill="currentColor"/><path d="M5.5 5v14"/></symbol>
  <symbol id="i-next" viewBox="0 0 24 24"><path d="M5.5 4.5L15 12l-9.5 7.5z" fill="currentColor"/><path d="M18.5 5v14"/></symbol>
  <symbol id="i-playpause" viewBox="0 0 24 24"><path d="M3.5 5L12 12l-8.5 7z" fill="currentColor"/><path d="M16 5.5v13M20.5 5.5v13"/></symbol>
  <symbol id="i-vol" viewBox="0 0 24 24"><path d="M11 5L6.5 9H3.5v6h3L11 19z"/><path d="M15.5 9a4.5 4.5 0 0 1 0 6M18.5 6a8.5 8.5 0 0 1 0 12"/></symbol>
  <symbol id="i-mute" viewBox="0 0 24 24"><path d="M11 5L6.5 9H3.5v6h3L11 19z"/><path d="M16 9.5l5 5M21 9.5l-5 5"/></symbol>
  <symbol id="i-sun" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M4.6 4.6L6 6M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4L6 18M18 6l1.4-1.4"/></symbol>
  <symbol id="i-laptop" viewBox="0 0 24 24"><rect x="4" y="5" width="16" height="10.5" rx="1.6"/><path d="M2 19h20"/></symbol>
  <symbol id="i-phone" viewBox="0 0 24 24"><rect x="6.5" y="2.5" width="11" height="19" rx="2.6"/><path d="M11 18.2h2"/></symbol>
  <symbol id="i-bell" viewBox="0 0 24 24"><path d="M6 9a6 6 0 0 1 12 0c0 6.5 2.5 8.5 2.5 8.5h-17S6 15.5 6 9z"/><path d="M10.2 20.5a2 2 0 0 0 3.6 0"/></symbol>
  <symbol id="i-bell-off" viewBox="0 0 24 24"><path d="M8.6 3.9A6 6 0 0 1 18 9c0 2.6.4 4.5.9 5.8M17.5 17.5h-14S6 15.5 6 9"/><path d="M10.2 20.5a2 2 0 0 0 3.6 0M3 3l18 18"/></symbol>
  <symbol id="i-vibrate" viewBox="0 0 24 24"><rect x="7.5" y="4" width="9" height="16" rx="2"/><path d="M3.5 8.5v7M20.5 8.5v7"/></symbol>
  <symbol id="i-torch" viewBox="0 0 24 24"><path d="M7 2.5h10v4.5l-2 3.5v10a1 1 0 0 1-1 1h-4a1 1 0 0 1-1-1v-10L7 7z"/><path d="M7 7h10M12 13v2.5"/></symbol>
  <symbol id="i-send" viewBox="0 0 24 24"><path d="M21.5 2.5l-10 10"/><path d="M21.5 2.5l-6.5 19-3.5-9-9-3.5z"/></symbol>
  <symbol id="i-clip" viewBox="0 0 24 24"><rect x="8.5" y="2.5" width="7" height="4" rx="1"/><path d="M15.5 4.5h2a2 2 0 0 1 2 2v13a2 2 0 0 1-2 2h-11a2 2 0 0 1-2-2v-13a2 2 0 0 1 2-2h2"/></symbol>
  <symbol id="i-trash" viewBox="0 0 24 24"><path d="M3.5 6.5h17M9 6.5V4.5h6v2M18.5 6.5l-.9 13a2 2 0 0 1-2 1.9H8.4a2 2 0 0 1-2-1.9l-.9-13"/></symbol>
  <symbol id="i-x" viewBox="0 0 24 24"><path d="M18 6L6 18M6 6l12 12"/></symbol>
  <symbol id="i-check" viewBox="0 0 24 24"><path d="M20 6.5L9.5 17 4 11.5"/></symbol>
  <symbol id="i-plus" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></symbol>
  <symbol id="i-reset" viewBox="0 0 24 24"><path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1L3.5 8.5"/><path d="M3.5 3.5v5h5"/></symbol>
  <symbol id="i-arrow" viewBox="0 0 24 24"><path d="M5 12h14M13 6l6 6-6 6"/></symbol>
  <symbol id="i-call" viewBox="0 0 24 24"><path d="M21.5 16.8v3a2 2 0 0 1-2.2 2 19.6 19.6 0 0 1-8.5-3 19.3 19.3 0 0 1-6-6 19.6 19.6 0 0 1-3-8.6A2 2 0 0 1 3.8 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.7a2 2 0 0 1-.5 2.1L7.8 9.7a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.7.7a2 2 0 0 1 1.6 2.1z"/></symbol>
  <symbol id="i-logout" viewBox="0 0 24 24"><path d="M9 21H5.5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2H9"/><path d="M16 17l5-5-5-5M21 12H9"/></symbol>
  <symbol id="i-bolt" viewBox="0 0 24 24"><path d="M13.5 2L4 13.5h7.5L10.5 22 20 10.5h-7.5z" fill="currentColor" stroke="none"/></symbol>
  <symbol id="i-shield" viewBox="0 0 24 24"><path d="M12 21.5s8-3.8 8-9.8V5.2L12 2.5 4 5.2v6.5c0 6 8 9.8 8 9.8z"/><path d="M12 8v4.5M12 16h.01"/></symbol>
  <symbol id="i-eye" viewBox="0 0 24 24"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></symbol>
  <symbol id="i-eye-off" viewBox="0 0 24 24"><path d="M10.6 5.1A10 10 0 0 1 12 5c6.4 0 10 7 10 7a17.6 17.6 0 0 1-2.9 3.9M6.3 6.3C3.6 8 2 12 2 12s3.6 7 10 7a9.7 9.7 0 0 0 5.7-1.8"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2M3 3l18 18"/></symbol>
  <symbol id="i-external" viewBox="0 0 24 24"><path d="M14.5 3.5h6v6M10 14L20.5 3.5M18.5 13.5v5a2 2 0 0 1-2 2h-11a2 2 0 0 1-2-2v-11a2 2 0 0 1 2-2h5"/></symbol>
  <symbol id="i-pause" viewBox="0 0 24 24"><path d="M8.5 5v14M15.5 5v14"/></symbol>
  <symbol id="i-play" viewBox="0 0 24 24"><path d="M7 4.5l12 7.5-12 7.5z" fill="currentColor"/></symbol>
  <symbol id="i-alert" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9.5"/><path d="M12 7.5v5.5M12 16.5h.01"/></symbol>
  <symbol id="i-info" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9.5"/><path d="M12 11v5.5M12 7.5h.01"/></symbol>
  <symbol id="i-calendar" viewBox="0 0 24 24"><rect x="3.5" y="4.5" width="17" height="16" rx="2"/><path d="M16 2.5v4M8 2.5v4M3.5 10h17"/></symbol>
  <symbol id="i-message" viewBox="0 0 24 24"><path d="M20.5 11.5a8.4 8.4 0 0 1-12.3 7.4L3.5 20.5l1.6-4.6A8.4 8.4 0 1 1 20.5 11.5z"/></symbol>
  <symbol id="i-download" viewBox="0 0 24 24"><path d="M12 3.5v12M7 10.5l5 5 5-5M4.5 20.5h15"/></symbol>
  <symbol id="i-image" viewBox="0 0 24 24"><rect x="3.5" y="3.5" width="17" height="17" rx="2"/><circle cx="9" cy="9" r="1.8"/><path d="M20.5 15l-5-5-11 11"/></symbol>
  <symbol id="i-doc" viewBox="0 0 24 24"><path d="M14 2.5H6.5a2 2 0 0 0-2 2v15a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V8z"/><path d="M14 2.5V8h5.5M8.5 13h7M8.5 17h5"/></symbol>
  <symbol id="i-server" viewBox="0 0 24 24"><rect x="3.5" y="3.5" width="17" height="7" rx="1.8"/><rect x="3.5" y="13.5" width="17" height="7" rx="1.8"/><path d="M7.5 7h.01M7.5 17h.01M11 7h5.5M11 17h5.5"/></symbol>
  <symbol id="i-terminal" viewBox="0 0 24 24"><rect x="2.5" y="4" width="19" height="16" rx="2.5"/><path d="M7 9.5l3 2.5-3 2.5M12.5 15h4.5"/></symbol>
  <symbol id="i-pulse" viewBox="0 0 24 24"><path d="M2.5 12h4l2.5-6 4 12 2.5-6h6"/></symbol>
  <symbol id="i-chart" viewBox="0 0 24 24"><path d="M3.5 3.5v17h17"/><path d="M7 15l4-5 3.5 3 5-6.5"/></symbol>
  <symbol id="i-gear" viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M5.3 18.7l2.1-2.1M16.6 7.4l2.1-2.1"/></symbol>
  <symbol id="i-cpu" viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9.5" y="9.5" width="5" height="5" rx=".8"/><path d="M9.5 2.5V6M14.5 2.5V6M9.5 18v3.5M14.5 18v3.5M2.5 9.5H6M2.5 14.5H6M18 9.5h3.5M18 14.5h3.5"/></symbol>
  <symbol id="i-up" viewBox="0 0 24 24"><path d="M12 19.5v-15M6 10.5l6-6 6 6"/></symbol>
  <symbol id="i-more" viewBox="0 0 24 24"><circle cx="5.5" cy="12" r="1.2" fill="currentColor"/><circle cx="12" cy="12" r="1.2" fill="currentColor"/><circle cx="18.5" cy="12" r="1.2" fill="currentColor"/></symbol>
  <symbol id="i-disk" viewBox="0 0 24 24"><ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/></symbol>
</svg>

<div id="boot" class="boot" role="status">
  <svg class="mark" aria-hidden="true"><use href="#i-logo"/></svg>
  <p>Connecting to Willy…</p>
</div>

<main id="login" class="login" hidden>
  <form id="login-form" class="card login-card" autocomplete="off" novalidate>
    <svg class="mark" aria-hidden="true"><use href="#i-logo"/></svg>
    <h1 class="login-title">Connect to Willy</h1>
    <div id="login-google" hidden>
      <button class="btn-google" id="login-google-btn" type="button" hidden>__GOOGLE_ICON__<span>Sign in with Google</span></button>
      <p class="login-or" id="login-or-email" style="margin:14px 0" hidden><span>or with email</span></p>
      <div id="login-email" style="display:grid;gap:10px">
        <input id="le-email" type="text" autocomplete="username" spellcheck="false" autocapitalize="off" placeholder="Email">
        <input id="le-pass" type="password" autocomplete="current-password" placeholder="Password">
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn btn-primary" id="le-signin" type="button" style="flex:1"><span>Sign in</span></button>
          <button class="btn btn-ghost" id="le-create" type="button" style="flex:1" hidden><span>Create account</span></button>
        </div>
      </div>
      <p class="login-or" style="margin:14px 0 0"><span>or</span></p>
      <button class="login-token-toggle" id="login-token-toggle" type="button" style="margin-top:10px">Use a hub access token</button>
    </div>
    <div id="login-token-area" style="display:contents">
    <p class="login-hint">Enter your <code>WILLY_REMOTE_TOKEN</code></p>
    <input class="sr-only" type="text" name="username" autocomplete="username" value="willy" tabindex="-1" aria-hidden="true">
    <div class="pw-wrap">
      <label for="login-token" class="sr-only">Access token</label>
      <input id="login-token" type="password" autocomplete="current-password" spellcheck="false" autocapitalize="off" autocorrect="off" placeholder="Access token">
      <button type="button" class="pw-eye" id="login-eye" aria-label="Show token" aria-pressed="false"><svg class="i"><use href="#i-eye"/></svg></button>
    </div>
    <button class="btn btn-primary btn-block" id="login-btn" type="submit"><span>Connect</span></button>
    </div>
    <p class="login-err" id="login-err" role="alert" hidden></p>
    <p class="login-foot">Saved only in this browser. Sign out to remove it.</p>
  </form>
</main>

<div id="app" class="app" hidden>
  <header class="appbar">
    <div class="brand">
      <svg class="mark" aria-hidden="true"><use href="#i-logo"/></svg>
      <div><span class="brand-name">Willy</span><span class="brand-sub">Command Center</span></div>
    </div>
    <div class="conn" id="conn" data-state="connecting" role="status">
      <i class="conn-dot" aria-hidden="true"></i><span id="conn-label">Connecting</span><span class="conn-rtt" id="conn-rtt"></span>
    </div>
    <div class="srv" id="srv">
      <span class="srv-item" id="srv-llm"><i class="dot" id="srv-llm-dot"></i><span id="srv-llm-text">AI brain</span></span>
      <span class="srv-item mono" id="srv-uptime">up —</span>
      <span class="srv-item mono" id="srv-ver">v—</span>
    </div>
    <div class="appbar-actions">
      <a class="btn btn-call" id="call-link" href="call" title="Talk to Willy"><svg class="i"><use href="#i-call"/></svg><span>Voice call</span></a>
      <button class="btn btn-ghost" id="add-device" type="button" title="AI provider, your login, and adding devices"><svg class="i"><use href="#i-plus"/></svg><span>Set up</span></button>
      <button class="btn btn-ghost" id="signout" type="button" title="Sign out of this browser"><svg class="i"><use href="#i-logout"/></svg><span>Sign out</span></button>
    </div>
  </header>

  <div class="layout">
    <div class="banner" id="sec-banner" role="alert" hidden>
      <svg class="i"><use href="#i-shield"/></svg>
      <p><strong>This hub uses the default access token.</strong> Anyone who knows it can control your PC. Set a long random <code>WILLY_REMOTE_TOKEN</code> on the hub, the PC client and the phone app, then restart them.</p>
      <button class="icon-btn sm" id="sec-dismiss" type="button" aria-label="Dismiss warning"><svg class="i"><use href="#i-x"/></svg></button>
    </div>

    <div class="col-main">
      <section class="card console" aria-labelledby="console-title">
        <div class="card-head">
          <h2 class="title" id="console-title">Console</h2>
          <div class="head-tools">
            <label id="target-wrap" hidden><span class="sr-only">Run commands on</span><select id="target" class="target"><option value="">Most recent PC</option></select></label>
            <label class="switch" title="Willy reads replies aloud in this browser"><input type="checkbox" id="speak"><span class="switch-track"></span><span>Speak replies</span></label>
            <button class="btn btn-ghost btn-sm" id="new-conv" type="button" title="Clear Willy's memory of this conversation"><svg class="i"><use href="#i-reset"/></svg><span>New conversation</span></button>
          </div>
        </div>
        <div class="conv" id="conv" aria-live="polite">
          <div class="conv-empty" id="conv-empty">
            <b>Tell Willy what to do.</b>
            Short commands like “volume 40” or “lock my PC” run instantly on the fast path. Anything else goes to Willy’s AI, which can chain several actions.
          </div>
        </div>
        <div class="chips" id="chips">
          <button class="chip" type="button" data-cmd="What's my battery?">What's my battery?</button>
          <button class="chip" type="button" data-cmd="Volume 40">Volume 40</button>
          <button class="chip" type="button" data-cmd="Open YouTube">Open YouTube</button>
          <button class="chip" type="button" data-cmd="Take a screenshot">Take a screenshot</button>
          <button class="chip" type="button" data-cmd="Show desktop">Show desktop</button>
          <button class="chip" type="button" data-cmd="Pause music">Pause music</button>
        </div>
        <form class="prompt" id="prompt-form" autocomplete="off">
          <span class="prompt-caret" aria-hidden="true">›</span>
          <label for="prompt" class="sr-only">Command for Willy</label>
          <input id="prompt" type="text" enterkeyhint="send" maxlength="500" placeholder="Tell Willy what to do…">
          <button class="btn btn-primary prompt-send" type="submit" aria-label="Send command"><svg class="i"><use href="#i-arrow"/></svg></button>
        </form>
        <p class="prompt-hint"><kbd>Enter</kbd> to send · <kbd>↑</kbd> <kbd>↓</kbd> history · <kbd>/</kbd> to focus</p>
      </section>

      <section class="devices" aria-labelledby="dev-title">
        <div class="sec-head">
          <h2 class="title" id="dev-title">Devices</h2>
          <span class="sec-count" id="dev-count"></span>
        </div>
        <div class="dev-grid" id="dev-grid"></div>
        <div class="card dev-empty" id="dev-empty" hidden>
          <h3>No devices connected yet</h3>
          <p class="muted">Willy shows your PC and phone here, live, as soon as they connect to this hub.</p>
          <ol>
            <li><div><b>Start the PC client</b> on your Windows PC: run WillyPC.exe, or from the project folder:<span class="cmd">python -m pc_client.client</span>Set <code>WILLY_SERVER_URL</code> to <code id="ws-url">ws://…/ws/devices</code> and <code>WILLY_REMOTE_TOKEN</code> to this hub’s token.</div></li>
            <li><div><b>Open the Willy app on your phone</b> and enter this hub’s address and the same token in Settings.</div></li>
          </ol>
        </div>
      </section>
      <section class="card srvp" id="srvp" aria-labelledby="srvp-title" hidden>
        <div class="card-head">
          <h2 class="title" id="srvp-title"><span id="srvp-name">Server</span></h2>
          <div class="head-tools">
            <span class="hchip idle" id="srvp-health" hidden></span>
            <button class="btn btn-ghost btn-sm" id="srvp-refresh" type="button" title="Reload the server's status, apps and containers"><svg class="i"><use href="#i-reset"/></svg><span>Refresh</span></button>
          </div>
        </div>
        <p class="srvp-note" id="srvp-agent" role="status" hidden></p>
        <div class="srvp-tabs">
          <div class="seg" role="tablist" aria-label="Server">
            <button type="button" role="tab" id="stab-overview" data-stab="overview" aria-selected="true" aria-controls="spane-overview"><svg class="i"><use href="#i-pulse"/></svg>Overview</button>
            <button type="button" role="tab" id="stab-files" data-stab="files" aria-selected="false" aria-controls="spane-files"><svg class="i"><use href="#i-folder"/></svg>Files</button>
            <button type="button" role="tab" id="stab-terminal" data-stab="terminal" aria-selected="false" aria-controls="spane-terminal"><svg class="i"><use href="#i-terminal"/></svg>Terminal</button>
            <button type="button" role="tab" id="stab-processes" data-stab="processes" aria-selected="false" aria-controls="spane-processes"><svg class="i"><use href="#i-cpu"/></svg>Processes</button>
            <button type="button" role="tab" id="stab-history" data-stab="history" aria-selected="false" aria-controls="spane-history"><svg class="i"><use href="#i-chart"/></svg>History</button>
            <button type="button" role="tab" id="stab-updates" data-stab="updates" aria-selected="false" aria-controls="spane-updates"><svg class="i"><use href="#i-download"/></svg>Updates</button>
            <button type="button" role="tab" id="stab-storage" data-stab="storage" aria-selected="false" aria-controls="spane-storage"><svg class="i"><use href="#i-disk"/></svg>Storage</button>
            <button type="button" role="tab" id="stab-network" data-stab="network" aria-selected="false" aria-controls="spane-network"><svg class="i"><use href="#i-shield"/></svg>Network &amp; security</button>
            <button type="button" role="tab" id="stab-services" data-stab="services" aria-selected="false" aria-controls="spane-services"><svg class="i"><use href="#i-gear"/></svg>Services &amp; jobs</button>
            <button type="button" role="tab" id="stab-logs" data-stab="logs" aria-selected="false" aria-controls="spane-logs"><svg class="i"><use href="#i-doc"/></svg>Logs</button>
          </div>
        </div>
        <div class="srvp-pane" id="spane-overview" role="tabpanel" aria-labelledby="stab-overview">
          <div class="srvp-summary"><p id="srvp-summary">Loading the server's status…</p><p class="srvp-checked" id="srvp-checked"></p></div>
          <ul class="problems" id="srvp-problems"></ul>
          <details class="panel" id="srvp-muted-wrap" hidden>
            <summary><span>Muted problems</span><span class="panel-meta" id="srvp-muted-n"></span></summary>
            <div class="panel-body"><ul class="problems" id="srvp-muted"></ul></div>
          </details>
          <div class="sgrid">
            <div class="sgroup"><div class="sgroup-head"><h3>Apps · pm2</h3><span class="sec-count" id="su-app-n"></span></div><div class="ulist" id="su-app"></div></div>
            <div class="sgroup"><div class="sgroup-head"><h3>Services</h3><span class="sec-count" id="su-service-n"></span></div><div class="ulist" id="su-service"></div></div>
            <div class="sgroup"><div class="sgroup-head"><h3>Containers</h3><span class="sec-count" id="su-container-n"></span></div><div class="ulist" id="su-container"></div></div>
          </div>
          <div class="sgroup">
            <div class="sgroup-head"><h3>Websites</h3><span class="sec-count" id="ss-n"></span><button class="btn btn-sm" id="ss-recheck" type="button" title="Check every website and certificate now"><svg class="i"><use href="#i-reset"/></svg><span>Re-check now</span></button></div>
            <div class="table-wrap"><table class="ptable"><thead><tr><th scope="col">Website</th><th scope="col">Status</th><th scope="col" class="num">Response</th><th scope="col">Certificate</th></tr></thead><tbody id="ss-body"></tbody></table></div>
          </div>
        </div>
        <div class="srvp-pane" id="spane-files" role="tabpanel" aria-labelledby="stab-files" hidden>
          <div class="fbar">
            <button class="icon-btn" id="f-up" type="button" aria-label="Up one folder" title="Up one folder"><svg class="i"><use href="#i-up"/></svg></button>
            <nav class="crumbs" id="f-crumbs" aria-label="Folder path"></nav>
            <button class="btn btn-sm" id="f-mkdir" type="button"><svg class="i"><use href="#i-plus"/></svg><span>New folder</span></button>
            <button class="icon-btn" id="f-refresh" type="button" aria-label="Reload this folder" title="Reload"><svg class="i"><use href="#i-reset"/></svg></button>
          </div>
          <p class="f-msg" id="f-msg" role="status" hidden></p>
          <div class="table-wrap"><table class="ptable ftable"><thead><tr><th scope="col">Name</th><th scope="col" class="num">Size</th><th scope="col" class="c-mod">Modified</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead><tbody id="f-body"></tbody></table></div>
        </div>
        <div class="srvp-pane" id="spane-terminal" role="tabpanel" aria-labelledby="stab-terminal" hidden>
          <div class="term-out" id="term-out" tabindex="0" role="log" aria-label="Terminal output"></div>
          <div class="term-chips" id="term-chips" role="group" aria-label="Common commands">
            <button class="chip" type="button" data-tcmd="df -h">df -h</button>
            <button class="chip" type="button" data-tcmd="free -m">free -m</button>
            <button class="chip" type="button" data-tcmd="uptime">uptime</button>
            <button class="chip" type="button" data-tcmd="pm2 ls">pm2 ls</button>
            <button class="chip" type="button" data-tcmd="docker ps">docker ps</button>
            <button class="chip" type="button" data-tcmd="systemctl --failed">systemctl --failed</button>
          </div>
          <form class="term-form" id="term-form" autocomplete="off">
            <label class="sr-only" for="term-cwd">Working folder</label>
            <input id="term-cwd" type="text" placeholder="~ (home folder)" spellcheck="false" autocapitalize="off" autocorrect="off" maxlength="300">
            <label class="sr-only" for="term-cmd">Command</label>
            <input id="term-cmd" type="text" placeholder="Command, e.g. df -h" spellcheck="false" autocapitalize="off" autocorrect="off" maxlength="2000" enterkeyhint="go">
            <button class="btn btn-primary" id="term-run" type="submit"><svg class="i"><use href="#i-play"/></svg><span>Run</span></button>
            <button class="btn btn-ghost" id="term-clear" type="button">Clear</button>
          </form>
          <p class="term-hint">Runs in a shell on the server as soon as you press Run, and stops after 60 s. <kbd>↑</kbd> <kbd>↓</kbd> recall recent commands.</p>
        </div>
        <div class="srvp-pane" id="spane-processes" role="tabpanel" aria-labelledby="stab-processes" hidden>
          <div class="panel-tools">
            <div class="seg seg-xs" role="group" aria-label="Sort processes by">
              <button type="button" data-psort="memory" aria-pressed="true">Memory</button>
              <button type="button" data-psort="cpu" aria-pressed="false">CPU</button>
            </div>
            <span class="panel-note" id="sp-note"></span>
            <button class="btn btn-sm" id="sp-refresh" type="button"><svg class="i"><use href="#i-reset"/></svg><span>Refresh</span></button>
          </div>
          <div class="table-wrap"><table class="ptable"><thead><tr><th scope="col">Process</th><th scope="col" class="num">PID</th><th scope="col">User</th><th scope="col" class="num">CPU</th><th scope="col" class="num">Memory</th><th scope="col">Command</th></tr></thead><tbody id="sp-body"></tbody></table></div>
        </div>
        <div class="srvp-pane" id="spane-history" role="tabpanel" aria-labelledby="stab-history" hidden>
          <div class="panel-tools">
            <div class="seg seg-xs" role="group" aria-label="Time range">
              <button type="button" data-hrange="1" aria-pressed="true">1 h</button>
              <button type="button" data-hrange="6" aria-pressed="false">6 h</button>
              <button type="button" data-hrange="24" aria-pressed="false">24 h</button>
              <button type="button" data-hrange="168" aria-pressed="false">7 d</button>
            </div>
            <span class="panel-note" id="syh-note"></span>
            <button class="btn btn-sm" id="syh-refresh" type="button"><svg class="i"><use href="#i-reset"/></svg><span>Refresh</span></button>
          </div>
          <div class="sys-grid two" id="syh-charts"></div>
        </div>
        <div class="srvp-pane" id="spane-updates" role="tabpanel" aria-labelledby="stab-updates" hidden>
          <div class="sgroup">
            <div class="sgroup-head"><h3>Updates</h3><span class="sec-count" id="syu-at"></span></div>
            <p class="sys-msg" id="syu-msg">Not checked yet.</p>
            <div class="chips" id="syu-chips"></div>
            <p class="sys-meta" id="syu-meta"></p>
            <details class="panel" id="syu-release" hidden><summary><span>About the new release</span></summary><div class="panel-body"><pre class="sys-pre" id="syu-release-text"></pre></div></details>
          </div>
          <div class="reboot-banner" id="syu-reboot" role="status" hidden><span id="syu-reboot-text"></span><button class="btn btn-sm" id="syu-reboot-cancel" type="button">Cancel the reboot</button></div>
          <div class="sys-acts">
            <button class="btn btn-sm" id="syu-check" type="button" title="Ask dnf what is pending (can take a few minutes)"><svg class="i"><use href="#i-reset"/></svg><span>Check</span></button>
            <button class="btn btn-sm" id="syu-sec" type="button" title="Install only the security updates"><svg class="i"><use href="#i-shield"/></svg><span>Install security</span></button>
            <button class="btn btn-sm" id="syu-all" type="button" title="Install every pending update"><svg class="i"><use href="#i-download"/></svg><span>Install all</span></button>
            <button class="btn btn-sm btn-danger" id="syu-reboot-btn" type="button" title="Reboot the server in 1 minute"><svg class="i"><use href="#i-bolt"/></svg><span>Reboot</span></button>
            <button class="btn btn-sm btn-ghost" id="syu-cancel" type="button" title="Cancel a scheduled reboot"><svg class="i"><use href="#i-x"/></svg><span>Cancel reboot</span></button>
          </div>
          <p class="sys-busy" id="syu-busy" role="status" hidden></p>
          <div class="sgroup" id="syu-out-wrap" hidden>
            <div class="sgroup-head"><h3 id="syu-out-title">Last install</h3></div>
            <pre class="sys-pre" id="syu-out"></pre>
          </div>
          <div class="sgroup">
            <div class="sgroup-head"><h3>Packages</h3><span class="sec-count" id="syu-pkg-n"></span></div>
            <div class="table-wrap"><table class="ptable"><thead><tr><th scope="col">Package</th><th scope="col">Version</th><th scope="col">Repository</th></tr></thead><tbody id="syu-pkgs"></tbody></table></div>
          </div>
        </div>
        <div class="srvp-pane" id="spane-storage" role="tabpanel" aria-labelledby="stab-storage" hidden>
          <div class="panel-tools"><p class="sys-msg" id="sys-msg">Not measured yet.</p><button class="btn btn-sm" id="sys-refresh" type="button"><svg class="i"><use href="#i-reset"/></svg><span>Refresh</span></button></div>
          <div class="sys-grid two">
            <div class="sgroup"><div class="sgroup-head"><h3>Disks &amp; swap</h3><span class="sec-count" id="sys-at"></span></div><div class="mlist" id="sys-mounts"></div></div>
            <div class="sgroup"><div class="sgroup-head"><h3>Clean up</h3></div><div class="ulist" id="sys-clean"></div></div>
          </div>
          <div class="sgroup">
            <div class="sgroup-head"><h3>Biggest folders</h3></div>
            <div class="table-wrap"><table class="ptable"><thead><tr><th scope="col">Folder</th><th scope="col" class="num">Size</th><th scope="col" class="c-bar"><span class="sr-only">Share</span></th></tr></thead><tbody id="sys-big"></tbody></table></div>
          </div>
        </div>
        <div class="srvp-pane" id="spane-network" role="tabpanel" aria-labelledby="stab-network" hidden>
          <div class="panel-tools"><p class="sys-msg" id="syn-msg">Not checked yet.</p><button class="btn btn-sm" id="syn-refresh" type="button"><svg class="i"><use href="#i-reset"/></svg><span>Refresh</span></button></div>
          <div class="sys-grid two">
            <div class="sgroup">
              <div class="sgroup-head"><h3>Network</h3><span class="sec-count" id="syn-at"></span></div>
              <div class="rate-tiles" id="syn-rate"></div>
              <div class="table-wrap"><table class="ptable"><thead><tr><th scope="col">Interface</th><th scope="col">State</th><th scope="col">Addresses</th></tr></thead><tbody id="syn-ifaces"></tbody></table></div>
            </div>
            <div class="sgroup">
              <div class="sgroup-head"><h3>Security</h3></div>
              <p class="sys-msg" id="sysec-msg"></p>
              <dl class="kv" id="sysec-kv"></dl>
              <div class="table-wrap"><table class="ptable"><thead><tr><th scope="col">Failed SSH logins from</th><th scope="col" class="num">Attempts</th></tr></thead><tbody id="sysec-top"></tbody></table></div>
            </div>
          </div>
          <div class="sgroup">
            <div class="sgroup-head"><h3>Listening ports</h3><span class="sec-count" id="syn-ports-n"></span></div>
            <p class="list-note">Public ports answer from the internet unless a firewall blocks them.</p>
            <div class="table-wrap"><table class="ptable"><thead><tr><th scope="col" class="num">Port</th><th scope="col">Protocol</th><th scope="col">Address</th><th scope="col">Process</th><th scope="col">Exposure</th></tr></thead><tbody id="syn-ports"></tbody></table></div>
          </div>
          <div class="sys-grid two">
            <div class="sgroup"><div class="sgroup-head"><h3>Logged in now</h3><span class="sec-count" id="sysec-who-n"></span></div><pre class="sys-pre" id="sysec-who"></pre></div>
            <div class="sgroup"><div class="sgroup-head"><h3>Recent logins</h3></div><pre class="sys-pre" id="sysec-last"></pre></div>
          </div>
        </div>
        <div class="srvp-pane" id="spane-services" role="tabpanel" aria-labelledby="stab-services" hidden>
          <div class="panel-tools">
            <input id="syv-q" class="syv-q" type="search" placeholder="Filter services" aria-label="Filter services by name or description" spellcheck="false" autocapitalize="off" autocorrect="off" maxlength="80">
            <div class="seg seg-xs" role="group" aria-label="Show services">
              <button type="button" data-vfilter="all" aria-pressed="true">All</button>
              <button type="button" data-vfilter="running" aria-pressed="false">Running</button>
              <button type="button" data-vfilter="failed" aria-pressed="false">Failed</button>
              <button type="button" data-vfilter="stopped" aria-pressed="false">Stopped</button>
            </div>
            <button class="btn btn-sm" id="syv-refresh" type="button"><svg class="i"><use href="#i-reset"/></svg><span>Refresh</span></button>
          </div>
          <p class="sys-msg" id="syv-msg"></p>
          <div class="table-wrap svc-wrap"><table class="ptable svc-table"><thead><tr><th scope="col">Service</th><th scope="col">State</th><th scope="col">At boot</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead><tbody id="syv-body"></tbody></table></div>
          <div class="sys-grid two">
            <div class="sgroup"><div class="sgroup-head"><h3>Cron jobs</h3><span class="sec-count" id="syj-cron-n"></span></div><pre class="sys-pre" id="syj-cron"></pre></div>
            <div class="sgroup"><div class="sgroup-head"><h3>Systemd timers</h3><span class="sec-count" id="syj-timers-n"></span></div><div class="table-wrap"><table class="ptable"><thead><tr><th scope="col">Timer</th><th scope="col">Schedule</th></tr></thead><tbody id="syj-timers"></tbody></table></div></div>
          </div>
        </div>
        <div class="srvp-pane" id="spane-logs" role="tabpanel" aria-labelledby="stab-logs" hidden>
          <form class="logs-form" id="syl-form" autocomplete="off">
            <label class="field"><span>Unit</span><input id="syl-unit" type="text" placeholder="all, or e.g. nginx" spellcheck="false" autocapitalize="off" autocorrect="off" maxlength="80"></label>
            <label class="field"><span>Level</span><select id="syl-prio">
              <option value="">Any level</option><option value="err">Errors and worse</option><option value="warning" selected>Warnings and worse</option><option value="notice">Notices and worse</option><option value="info">Info and worse</option><option value="crit">Critical and worse</option><option value="debug">Everything (debug)</option>
            </select></label>
            <label class="field"><span>Since</span><select id="syl-since">
              <option value="">Any time</option><option value="15 min ago">Last 15 min</option><option value="1 hour ago" selected>Last hour</option><option value="6 hours ago">Last 6 hours</option><option value="today">Today</option><option value="yesterday">Since yesterday</option><option value="7 days ago">Last 7 days</option>
            </select></label>
            <label class="field"><span>Search</span><input id="syl-grep" type="text" placeholder="word or regex" spellcheck="false" autocapitalize="off" autocorrect="off" maxlength="80"></label>
            <label class="field"><span>Lines</span><select id="syl-lines"><option value="100">Last 100</option><option value="200" selected>Last 200</option><option value="500">Last 500</option></select></label>
            <button class="btn btn-primary" id="syl-run" type="submit"><svg class="i"><use href="#i-eye"/></svg><span>Show logs</span></button>
          </form>
          <div class="panel-tools"><span class="panel-note" id="syl-note"></span><button class="btn btn-sm btn-ghost" id="syl-copy" type="button" disabled><svg class="i"><use href="#i-clip"/></svg><span>Copy</span></button></div>
          <pre class="sys-pre sys-logs" id="syl-out" tabindex="0">Pick filters and press Show logs.</pre>
        </div>
      </section>
    </div>

    <aside class="col-rail">
      <section class="card activity" aria-labelledby="act-title">
        <div class="card-head"><h2 class="title" id="act-title">Activity</h2></div>
        <div class="stats">
          <div class="stat" title="Commands handled"><span class="k">Total</span><span class="stat-v" id="st-total">—</span></div>
          <div class="stat" title="Share of commands that succeeded"><span class="k">Success</span><span class="stat-v" id="st-success">—</span></div>
          <div class="stat" title="Median latency"><span class="k">p50</span><span class="stat-v" id="st-p50">—</span></div>
          <div class="stat" title="90th percentile latency"><span class="k">p90</span><span class="stat-v" id="st-p90">—</span></div>
          <div class="stat" title="Share of commands answered on the fast path, without the AI round-trip"><span class="k">Fast</span><span class="stat-v" id="st-fast">—</span></div>
        </div>
        <div class="legend" aria-hidden="true">
          <span><i class="rb-stt"></i>Speech</span><span><i class="rb-llm"></i>AI</span><span><i class="rb-tool"></i>Device</span><span><i class="rb-tts"></i>Voice</span>
          <span class="ribbon-scale">bar 0–4 s</span>
        </div>
        <div class="filters" id="filters" role="group" aria-label="Filter by source"></div>
        <ol class="feed" id="feed"></ol>
        <p class="feed-empty" id="feed-empty">No commands yet. Anything you, your phone or your PC asks Willy shows up here live.</p>
      </section>

      <section class="card reminders" aria-labelledby="rem-title">
        <div class="card-head">
          <h2 class="title" id="rem-title">Reminders</h2>
          <button class="btn btn-ghost btn-sm" id="notif-btn" type="button" hidden><svg class="i"><use href="#i-bell"/></svg><span>Turn on desktop alerts</span></button>
        </div>
        <div class="rem-tabs">
          <div class="seg" role="tablist" aria-label="Reminders and alarms">
            <button type="button" role="tab" id="tab-btn-rem" aria-selected="true" aria-controls="tab-rem" data-tab="rem">Reminders <span class="seg-n" id="rem-n"></span></button>
            <button type="button" role="tab" id="tab-btn-alm" aria-selected="false" aria-controls="tab-alm" data-tab="alm">Alarms <span class="seg-n" id="alm-n"></span></button>
          </div>
        </div>
        <div class="tabpanel" id="tab-rem" role="tabpanel" aria-labelledby="tab-btn-rem">
          <ul class="rlist" id="rem-list"></ul>
          <p class="list-empty" id="rem-empty">No reminders. Add one below, or tell Willy “remind me to…”.</p>
          <form class="add-form" id="rem-form" autocomplete="off">
            <label class="sr-only" for="rem-text">Reminder</label>
            <input id="rem-text" type="text" maxlength="200" placeholder="Remind me to…">
            <div class="add-row">
              <label class="sr-only" for="rem-time">Time</label>
              <input id="rem-time" type="time">
              <label class="sr-only" for="rem-day">Day</label>
              <select id="rem-day"><option value="today">Today</option><option value="tomorrow">Tomorrow</option><option value="date">Pick a date…</option></select>
              <label class="sr-only" for="rem-date">Date</label>
              <input id="rem-date" type="date" hidden>
              <button class="btn btn-primary" type="submit"><svg class="i"><use href="#i-plus"/></svg><span>Add</span></button>
            </div>
            <p class="form-err" id="rem-err" role="alert" hidden></p>
          </form>
        </div>
        <div class="tabpanel" id="tab-alm" role="tabpanel" aria-labelledby="tab-btn-alm" hidden>
          <ul class="rlist" id="alm-list"></ul>
          <p class="list-empty" id="alm-empty">No alarms. Alarms ring every day at the set time.</p>
          <form class="add-form" id="alm-form" autocomplete="off">
            <div class="add-row">
              <label class="sr-only" for="alm-time">Alarm time</label>
              <input id="alm-time" type="time">
              <label class="sr-only" for="alm-label">Label</label>
              <input id="alm-label" type="text" maxlength="80" placeholder="Label (optional)">
              <button class="btn btn-primary" type="submit"><svg class="i"><use href="#i-plus"/></svg><span>Add</span></button>
            </div>
            <p class="form-err" id="alm-err" role="alert" hidden></p>
          </form>
        </div>
      </section>

      <section class="card tools" aria-labelledby="tools-title">
        <div class="card-head"><h2 class="title" id="tools-title">Quick tools</h2></div>
        <div class="tool-tabs">
          <div class="seg" role="tablist" aria-label="Quick tools">
            <button type="button" role="tab" aria-selected="true" aria-controls="meet-form" data-tool="meet"><svg class="i"><use href="#i-calendar"/></svg>Schedule meeting</button>
            <button type="button" role="tab" aria-selected="false" aria-controls="msg-form" data-tool="msg"><svg class="i"><use href="#i-message"/></svg>Send message</button>
          </div>
        </div>
        <form class="tool-form" id="meet-form" role="tabpanel" autocomplete="off">
          <label class="field"><span>Title</span><input id="meet-title" type="text" maxlength="120" placeholder="Project sync with Priya"></label>
          <div class="field-row">
            <label class="field"><span>Platform</span><select id="meet-platform"><option>Google Calendar</option><option>Microsoft Teams</option><option>Zoom</option></select></label>
            <label class="field"><span>Duration (min)</span><input id="meet-dur" type="number" min="5" max="480" step="5" value="30"></label>
          </div>
          <div class="field-row">
            <label class="field"><span>Date</span><input id="meet-date" type="date"></label>
            <label class="field"><span>Time</span><input id="meet-time" type="time"></label>
          </div>
          <button class="btn btn-primary btn-block" type="submit"><svg class="i"><use href="#i-calendar"/></svg><span>Schedule meeting</span></button>
          <p class="tool-result" id="meet-result" role="status" hidden></p>
        </form>
        <form class="tool-form" id="msg-form" role="tabpanel" autocomplete="off" hidden>
          <label class="field"><span>Channel</span><select id="msg-channel"><option value="WhatsApp">WhatsApp</option><option value="email">Email</option></select></label>
          <label class="field"><span>To</span><input id="msg-to" type="text" maxlength="120" placeholder="+91 98765 43210, name@example.com or a contact name"></label>
          <label class="field"><span>Message</span><textarea id="msg-body" rows="3" maxlength="1000" placeholder="Running 10 minutes late, see you soon."></textarea></label>
          <button class="btn btn-primary btn-block" type="submit"><svg class="i"><use href="#i-send"/></svg><span>Send message</span></button>
          <p class="tool-result" id="msg-result" role="status" hidden></p>
        </form>
        <p class="tool-note">Willy’s AI opens the right app on your PC with the details filled in.</p>
      </section>
    </aside>
  </div>
</div>

<div class="toasts" id="toasts" aria-live="polite"></div>
<dialog class="dlg" id="dlg"></dialog>

<dialog class="dlg dlg-screen" id="screen" aria-labelledby="scr-title">
  <div class="scr-head">
    <svg class="i"><use href="#i-screen"/></svg>
    <div>
      <h2 class="scr-title" id="scr-title">Live screen · <span id="scr-dev"></span></h2>
      <p class="scr-sub" id="scr-label">Waiting for the first frame…</p>
    </div>
    <div class="scr-stats"><span class="scr-stat" id="scr-fps">— fps</span><span class="scr-stat" id="scr-lat">— ms</span></div>
    <button class="icon-btn" id="scr-close" type="button" aria-label="Close live screen"><svg class="i"><use href="#i-x"/></svg></button>
  </div>
  <div class="scr-stage">
    <img class="scr-img" id="scr-img" alt="Live view of the PC screen. Click to open a full-size capture." hidden>
    <p class="scr-msg" id="scr-msg">Connecting to the screen…</p>
  </div>
  <div class="scr-foot">
    <div class="seg" role="group" aria-label="Quality" id="scr-quality">
      <button type="button" data-q="low" aria-pressed="false">Low</button>
      <button type="button" data-q="med" aria-pressed="true">Medium</button>
      <button type="button" data-q="high" aria-pressed="false">High</button>
    </div>
    <label class="sr-only" for="scr-mon">Monitor</label>
    <select id="scr-mon" hidden></select>
    <span class="spacer"></span>
    <button class="btn btn-sm" id="scr-pause" type="button"><svg class="i"><use href="#i-pause"/></svg><span>Pause</span></button>
    <button class="btn btn-sm" id="scr-full" type="button"><svg class="i"><use href="#i-external"/></svg><span>Open full size</span></button>
  </div>
</dialog>

<template id="tpl-pc">
<article class="card dev dev-pc">
  <header class="dev-head">
    <span class="dev-ico"><svg class="i"><use href="#i-laptop"/></svg></span>
    <div class="dev-ident"><h3 class="dev-name" data-ref="name"></h3><p class="dev-sub" data-ref="sub"></p></div>
    <div class="dev-state"><span class="beat" data-ref="beat" aria-hidden="true"></span><span class="badge" data-ref="badge"></span></div>
  </header>
  <dl class="meta">
    <div><dt>IP</dt><dd data-ref="ip">—</dd></div>
    <div><dt>Wi-Fi</dt><dd data-ref="wifi">—</dd></div>
    <div><dt>Uptime</dt><dd data-ref="uptime">—</dd></div>
    <div><dt>Seen</dt><dd data-ref="seen">—</dd></div>
    <div><dt>Ping</dt><dd data-ref="ping">—</dd></div>
  </dl>
  <div class="dev-body">
    <div class="dev-col">
      <div class="gauges well">
        <div class="gauge" data-g="cpu"><div class="g-dial"><svg class="g-svg" viewBox="0 0 100 100" aria-hidden="true"><path class="g-ticks" d="M17.47 82.53L15.35 84.65M7.5 32.4L4.73 31.25M50 4V1M92.5 32.4L95.27 31.25M82.53 82.53L84.65 84.65"/><circle class="g-track" cx="50" cy="50" r="40" transform="rotate(135 50 50)"/><circle class="g-val" cx="50" cy="50" r="40" transform="rotate(135 50 50)"/></svg><div class="g-read"><span class="g-num">—</span><span class="g-unit">%</span></div><span class="g-k">CPU</span></div><div class="g-sub">&nbsp;</div></div>
        <div class="gauge" data-g="ram"><div class="g-dial"><svg class="g-svg" viewBox="0 0 100 100" aria-hidden="true"><path class="g-ticks" d="M17.47 82.53L15.35 84.65M7.5 32.4L4.73 31.25M50 4V1M92.5 32.4L95.27 31.25M82.53 82.53L84.65 84.65"/><circle class="g-track" cx="50" cy="50" r="40" transform="rotate(135 50 50)"/><circle class="g-val" cx="50" cy="50" r="40" transform="rotate(135 50 50)"/></svg><div class="g-read"><span class="g-num">—</span><span class="g-unit">%</span></div><span class="g-k">RAM</span></div><div class="g-sub">&nbsp;</div></div>
        <div class="gauge" data-g="disk"><div class="g-dial"><svg class="g-svg" viewBox="0 0 100 100" aria-hidden="true"><path class="g-ticks" d="M17.47 82.53L15.35 84.65M7.5 32.4L4.73 31.25M50 4V1M92.5 32.4L95.27 31.25M82.53 82.53L84.65 84.65"/><circle class="g-track" cx="50" cy="50" r="40" transform="rotate(135 50 50)"/><circle class="g-val" cx="50" cy="50" r="40" transform="rotate(135 50 50)"/></svg><div class="g-read"><span class="g-num">—</span><span class="g-unit">%</span></div><span class="g-k">Disk</span></div><div class="g-sub">&nbsp;</div></div>
        <div class="gauge" data-g="bat"><div class="g-dial"><svg class="g-svg" viewBox="0 0 100 100" aria-hidden="true"><path class="g-ticks" d="M17.47 82.53L15.35 84.65M7.5 32.4L4.73 31.25M50 4V1M92.5 32.4L95.27 31.25M82.53 82.53L84.65 84.65"/><circle class="g-track" cx="50" cy="50" r="40" transform="rotate(135 50 50)"/><circle class="g-val" cx="50" cy="50" r="40" transform="rotate(135 50 50)"/></svg><div class="g-read"><svg class="i g-bolt"><use href="#i-bolt"/></svg><span class="g-num">—</span><span class="g-unit">%</span></div><span class="g-k">Battery</span></div><div class="g-sub">&nbsp;</div></div>
      </div>
      <div class="sparks">
        <figure class="spark well">
          <figcaption><span class="k">Load</span><span class="sp-v"><i class="sw sw-cpu"></i>CPU <b data-ref="spCpu">—</b></span><span class="sp-v"><i class="sw sw-ram"></i>RAM <b data-ref="spRam">—</b></span></figcaption>
          <svg class="sp-svg" viewBox="0 0 200 46" preserveAspectRatio="none" aria-hidden="true"><path class="sp-grid" d="M0 12.5H200M0 23H200M0 33.5H200"/><path class="sp-area sp-area-cpu" data-ref="cpuArea" d=""/><path class="sp-line sp-ram" data-ref="ramLine" d=""/><path class="sp-line sp-cpu" data-ref="cpuLine" d=""/></svg>
        </figure>
        <figure class="spark well">
          <figcaption><span class="k">Network</span><span class="sp-v"><i class="sw sw-down"></i>↓ <b data-ref="spDown">—</b></span><span class="sp-v"><i class="sw sw-up"></i>↑ <b data-ref="spUp">—</b></span></figcaption>
          <svg class="sp-svg" viewBox="0 0 200 46" preserveAspectRatio="none" aria-hidden="true"><path class="sp-grid" d="M0 12.5H200M0 23H200M0 33.5H200"/><path class="sp-area sp-area-down" data-ref="downArea" d=""/><path class="sp-line sp-up" data-ref="upLine" d=""/><path class="sp-line sp-down" data-ref="downLine" d=""/></svg>
        </figure>
      </div>
      <div class="focus well">
        <span class="k">Active</span>
        <div class="focus-v"><span class="focus-proc" data-ref="proc" hidden></span><span class="focus-win" data-ref="win">—</span></div>
        <span class="idle" data-ref="idle"></span>
      </div>
    </div>
    <div class="dev-col">
      <div class="ctl ctl-audio well">
        <button class="icon-btn" type="button" data-act="mute" data-ref="mute" aria-label="Mute" aria-pressed="false"><svg class="i"><use data-ref="muteIcon" href="#i-vol"/></svg></button>
        <span class="ctl-k">Volume</span>
        <input class="range" type="range" min="0" max="100" step="1" value="0" data-ref="vol" aria-label="Volume">
        <output class="ctl-v" data-ref="volV">—</output>
        <div class="media" role="group" aria-label="Media controls">
          <button class="icon-btn" type="button" data-act="prev" aria-label="Previous track" title="Previous track"><svg class="i"><use href="#i-prev"/></svg></button>
          <button class="icon-btn" type="button" data-act="playpause" aria-label="Play or pause" title="Play / pause"><svg class="i"><use href="#i-playpause"/></svg></button>
          <button class="icon-btn" type="button" data-act="next" aria-label="Next track" title="Next track"><svg class="i"><use href="#i-next"/></svg></button>
        </div>
      </div>
      <div class="ctl well" data-ref="brightRow" hidden>
        <span class="ctl-ico"><svg class="i"><use href="#i-sun"/></svg></span>
        <span class="ctl-k">Brightness</span>
        <input class="range" type="range" min="0" max="100" step="5" value="70" data-ref="bright" aria-label="Screen brightness">
        <output class="ctl-v" data-ref="brightV">—</output>
      </div>
      <div class="acts">
        <button class="act" type="button" data-act="lock"><svg class="i"><use href="#i-lock"/></svg><span>Lock</span></button>
        <button class="act" type="button" data-act="sleep"><svg class="i"><use href="#i-moon"/></svg><span>Sleep</span></button>
        <button class="act" type="button" data-act="screenshot"><svg class="i"><use href="#i-camera"/></svg><span>Screenshot</span></button>
        <button class="act" type="button" data-act="desktop"><svg class="i"><use href="#i-desktop"/></svg><span>Show desktop</span></button>
        <button class="act act-wide act-accent" type="button" data-act="live"><svg class="i"><use href="#i-screen"/></svg><span>Live screen</span></button>
        <div class="menu-wrap">
          <button class="act" type="button" data-act="folder" aria-haspopup="menu" aria-expanded="false"><svg class="i"><use href="#i-folder"/></svg><span>Open folder</span></button>
          <div class="menu" role="menu" hidden>
            <button type="button" role="menuitem" data-folder="downloads"><svg class="i"><use href="#i-download"/></svg>Downloads</button>
            <button type="button" role="menuitem" data-folder="documents"><svg class="i"><use href="#i-doc"/></svg>Documents</button>
            <button type="button" role="menuitem" data-folder="desktop"><svg class="i"><use href="#i-desktop"/></svg>Desktop</button>
            <button type="button" role="menuitem" data-folder="pictures"><svg class="i"><use href="#i-image"/></svg>Pictures</button>
          </div>
        </div>
        <button class="act" type="button" data-act="note"><svg class="i"><use href="#i-note"/></svg><span>Send note</span></button>
      </div>
    </div>
    <div class="dev-panels">
      <details class="panel" data-ref="procPanel">
        <summary><span>Top processes</span><span class="panel-meta" data-ref="procMeta"></span></summary>
        <div class="panel-body">
          <div class="panel-tools">
            <div class="seg seg-xs" role="group" aria-label="Sort processes by">
              <button type="button" data-sort="cpu" aria-pressed="true">CPU</button>
              <button type="button" data-sort="memory" aria-pressed="false">Memory</button>
            </div>
            <span class="panel-note" data-ref="procNote">Live</span>
          </div>
          <div class="table-wrap"><table class="ptable"><thead><tr><th scope="col">Process</th><th scope="col" class="num c-pid">PID</th><th scope="col" class="num">CPU</th><th scope="col" class="num">Memory</th><th scope="col"><span class="sr-only">End task</span></th></tr></thead><tbody data-ref="procBody"></tbody></table></div>
        </div>
      </details>
      <details class="panel">
        <summary><span>Specs</span><span class="panel-meta" data-ref="specMeta"></span></summary>
        <div class="panel-body"><dl class="specs" data-ref="specs"></dl></div>
      </details>
    </div>
  </div>
</article>
</template>

<template id="tpl-server">
<article class="card dev dev-server">
  <header class="dev-head">
    <span class="dev-ico"><svg class="i"><use href="#i-server"/></svg></span>
    <div class="dev-ident"><h3 class="dev-name" data-ref="name"></h3><p class="dev-sub" data-ref="sub"></p></div>
    <div class="dev-state"><span class="beat" data-ref="beat" aria-hidden="true"></span><span class="badge" data-ref="badge"></span></div>
  </header>
  <dl class="meta">
    <div><dt>Load</dt><dd data-ref="load">—</dd></div>
    <div><dt>Uptime</dt><dd data-ref="uptime">—</dd></div>
    <div><dt>Procs</dt><dd data-ref="procs">—</dd></div>
    <div><dt>Traffic</dt><dd data-ref="net">—</dd></div>
    <div><dt>Seen</dt><dd data-ref="seen">—</dd></div>
  </dl>
  <div class="gauges well" style="margin-top: 14px">
    <div class="gauge" data-g="cpu"><div class="g-dial"><svg class="g-svg" viewBox="0 0 100 100" aria-hidden="true"><path class="g-ticks" d="M17.47 82.53L15.35 84.65M7.5 32.4L4.73 31.25M50 4V1M92.5 32.4L95.27 31.25M82.53 82.53L84.65 84.65"/><circle class="g-track" cx="50" cy="50" r="40" transform="rotate(135 50 50)"/><circle class="g-val" cx="50" cy="50" r="40" transform="rotate(135 50 50)"/></svg><div class="g-read"><span class="g-num">—</span><span class="g-unit">%</span></div><span class="g-k">CPU</span></div><div class="g-sub">&nbsp;</div></div>
    <div class="gauge" data-g="ram"><div class="g-dial"><svg class="g-svg" viewBox="0 0 100 100" aria-hidden="true"><path class="g-ticks" d="M17.47 82.53L15.35 84.65M7.5 32.4L4.73 31.25M50 4V1M92.5 32.4L95.27 31.25M82.53 82.53L84.65 84.65"/><circle class="g-track" cx="50" cy="50" r="40" transform="rotate(135 50 50)"/><circle class="g-val" cx="50" cy="50" r="40" transform="rotate(135 50 50)"/></svg><div class="g-read"><span class="g-num">—</span><span class="g-unit">%</span></div><span class="g-k">RAM</span></div><div class="g-sub">&nbsp;</div></div>
    <div class="gauge" data-g="disk"><div class="g-dial"><svg class="g-svg" viewBox="0 0 100 100" aria-hidden="true"><path class="g-ticks" d="M17.47 82.53L15.35 84.65M7.5 32.4L4.73 31.25M50 4V1M92.5 32.4L95.27 31.25M82.53 82.53L84.65 84.65"/><circle class="g-track" cx="50" cy="50" r="40" transform="rotate(135 50 50)"/><circle class="g-val" cx="50" cy="50" r="40" transform="rotate(135 50 50)"/></svg><div class="g-read"><span class="g-num">—</span><span class="g-unit">%</span></div><span class="g-k">Disk</span></div><div class="g-sub">&nbsp;</div></div>
    <div class="gauge" data-g="swap"><div class="g-dial"><svg class="g-svg" viewBox="0 0 100 100" aria-hidden="true"><path class="g-ticks" d="M17.47 82.53L15.35 84.65M7.5 32.4L4.73 31.25M50 4V1M92.5 32.4L95.27 31.25M82.53 82.53L84.65 84.65"/><circle class="g-track" cx="50" cy="50" r="40" transform="rotate(135 50 50)"/><circle class="g-val" cx="50" cy="50" r="40" transform="rotate(135 50 50)"/></svg><div class="g-read"><span class="g-num">—</span><span class="g-unit">%</span></div><span class="g-k">Swap</span></div><div class="g-sub">&nbsp;</div></div>
  </div>
  <div class="acts acts-server">
    <button class="act" type="button" data-sact="overview" data-keep><svg class="i"><use href="#i-pulse"/></svg><span>Overview</span></button>
    <button class="act" type="button" data-sact="files" data-keep><svg class="i"><use href="#i-folder"/></svg><span>Files</span></button>
    <button class="act" type="button" data-sact="terminal" data-keep><svg class="i"><use href="#i-terminal"/></svg><span>Terminal</span></button>
    <button class="act" type="button" data-sact="processes" data-keep><svg class="i"><use href="#i-cpu"/></svg><span>Processes</span></button>
    <button class="act" type="button" data-sact="history" data-keep><svg class="i"><use href="#i-chart"/></svg><span>History</span></button>
    <button class="act" type="button" data-sact="updates" data-keep><svg class="i"><use href="#i-download"/></svg><span>Updates</span></button>
    <button class="act" type="button" data-sact="storage" data-keep><svg class="i"><use href="#i-disk"/></svg><span>Storage</span></button>
    <button class="act" type="button" data-sact="services" data-keep><svg class="i"><use href="#i-gear"/></svg><span>Services</span></button>
  </div>
</article>
</template>

<template id="tpl-phone">
<article class="card dev dev-phone">
  <header class="dev-head">
    <span class="dev-ico"><svg class="i"><use href="#i-phone"/></svg></span>
    <div class="dev-ident"><h3 class="dev-name" data-ref="name"></h3><p class="dev-sub" data-ref="sub"></p></div>
    <div class="dev-state"><span class="beat" data-ref="beat" aria-hidden="true"></span><span class="badge" data-ref="badge"></span></div>
  </header>
  <dl class="meta">
    <div><dt>Network</dt><dd data-ref="net">—</dd></div>
    <div><dt>IP</dt><dd data-ref="ip">—</dd></div>
    <div><dt>Seen</dt><dd data-ref="seen">—</dd></div>
    <div><dt>Ping</dt><dd data-ref="ping">—</dd></div>
  </dl>
  <div class="phone-main">
    <div class="gauge" data-g="bat"><div class="g-dial"><svg class="g-svg" viewBox="0 0 100 100" aria-hidden="true"><path class="g-ticks" d="M17.47 82.53L15.35 84.65M7.5 32.4L4.73 31.25M50 4V1M92.5 32.4L95.27 31.25M82.53 82.53L84.65 84.65"/><circle class="g-track" cx="50" cy="50" r="40" transform="rotate(135 50 50)"/><circle class="g-val" cx="50" cy="50" r="40" transform="rotate(135 50 50)"/></svg><div class="g-read"><svg class="i g-bolt"><use href="#i-bolt"/></svg><span class="g-num">—</span><span class="g-unit">%</span></div><span class="g-k">Battery</span></div><div class="g-sub">&nbsp;</div></div>
    <ul class="counts well">
      <li><span>Notifications</span><b data-ref="notif">—</b></li>
      <li><span>Missed calls</span><b data-ref="missed">—</b></li>
      <li><span>WhatsApp unread</span><b data-ref="wa">—</b></li>
      <li><span>Free storage</span><b data-ref="storage">—</b></li>
    </ul>
  </div>
  <div class="acts acts-phone">
    <button class="act" type="button" data-act="ring"><svg class="i"><use href="#i-bell"/></svg><span>Ring</span></button>
    <button class="act" type="button" data-act="stopring"><svg class="i"><use href="#i-bell-off"/></svg><span>Stop ring</span></button>
    <button class="act" type="button" data-act="vibrate"><svg class="i"><use href="#i-vibrate"/></svg><span>Vibrate</span></button>
    <button class="act" type="button" data-act="flash" data-ref="flash" aria-pressed="false"><svg class="i"><use href="#i-torch"/></svg><span data-ref="flashLabel">Flashlight</span></button>
    <button class="act" type="button" data-act="drop"><svg class="i"><use href="#i-send"/></svg><span>Send link or note</span></button>
    <button class="act" type="button" data-act="clip"><svg class="i"><use href="#i-clip"/></svg><span>Push clipboard</span></button>
  </div>
  <p class="dev-note" data-ref="note" hidden></p>
</article>
</template>

<script>
(function () {
'use strict';

/* ================================================================ constants */
var BASE = location.pathname.startsWith('/willy') ? '/willy' : '';
var CLIENT = 'dashboard';
var SESSION_ID = 'dashboard';
var KEYS = { token: 'willy_token', hist: 'willy_cmd_history', speak: 'willy_speak', scrq: 'willy_screen_quality', conv: 'willy_dash_conv', banner: 'willy_banner_dismissed', srvHist: 'willy_server_shell_history' };
var RIBBON_MAX_MS = 4000;
var SPARK_N = 40;
var G_ARC = 2 * Math.PI * 40 * 0.75;
var REDUCED = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
var SILENT_WAV = 'data:audio/wav;base64,UklGRhQBAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YfAAAACAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIA=';
var QUALITY = { low: { q: 30, w: 640 }, med: { q: 45, w: 960 }, high: { q: 65, w: 1280 } };
var SEGMENTS = [['stt_ms', 'stt', 'Speech-to-text'], ['llm_ms', 'llm', 'AI'], ['tool_ms', 'tool', 'Device'], ['tts_ms', 'tts', 'Voice']];
var SOURCE_ORDER = ['dashboard', 'mobile', 'pc', 'voice', 'api'];
var SOURCE_LABEL = { dashboard: 'Dashboard', mobile: 'Phone', pc: 'PC', server: 'Server', voice: 'Voice', api: 'API' };
var FOLDER_LABEL = { downloads: 'Downloads', documents: 'Documents', desktop: 'Desktop', pictures: 'Pictures' };

/* ==================================================================== state */
var S = {
  token: null, wired: false,
  devices: new Map(), activity: [], stats: null, server: null, serverAt: 0,
  skew: 0, skewSet: false,
  reminders: [], alarms: [], filter: 'all', sourcesKey: '',
  conv: [], hist: [], histIdx: -1, draft: '', targetKey: '',
  loginRetry: 0, statsTimer: 0, remTimer: 0, ticker: 0, statsPoll: 0
};
var cards = new Map();
var feedEls = new Map();
var dirty = new Set();
var rafId = 0, flushTimer = 0;

/* ================================================================== helpers */
function $(sel, root) { return (root || document).querySelector(sel); }
function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
function isNum(v) { return typeof v === 'number' && isFinite(v); }
function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
function store(area) {
  return {
    get: function (k) { try { return window[area].getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { window[area].setItem(k, v); } catch (e) { /* storage unavailable */ } },
    del: function (k) { try { window[area].removeItem(k); } catch (e) { /* storage unavailable */ } }
  };
}
var local = store('localStorage');
var session = store('sessionStorage');

// DOM builder: strings always become text nodes / attribute values, never HTML.
function h(tag, props) {
  var el = document.createElement(tag);
  if (props) {
    Object.keys(props).forEach(function (k) {
      var v = props[k];
      if (v == null || v === false) return;
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
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
function icon(name, cls) {
  var ns = 'http://www.w3.org/2000/svg';
  var svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('class', 'i' + (cls ? ' ' + cls : ''));
  svg.setAttribute('aria-hidden', 'true');
  var use = document.createElementNS(ns, 'use');
  use.setAttribute('href', '#i-' + name);
  svg.appendChild(use);
  return svg;
}
function setText(el, v) {
  if (!el) return;
  v = v == null ? '' : String(v);
  if (el.textContent !== v) el.textContent = v;
}
function nowSec() { return (Date.now() + S.skew) / 1000; }
function fmtMs(ms) {
  if (!isNum(ms)) return '—';
  if (ms < 1) return '<1 ms';
  if (ms < 1000) return Math.round(ms) + ' ms';
  if (ms < 10000) return (ms / 1000).toFixed(2) + ' s';
  return (ms / 1000).toFixed(1) + ' s';
}
function fmtMsShort(ms) {
  if (!isNum(ms)) return '—';
  if (ms < 1) return '<1ms';
  if (ms < 1000) return Math.round(ms) + 'ms';
  return (ms / 1000).toFixed(ms < 10000 ? 1 : 0) + 's';
}
function fmtRate(kbps) {
  if (!isNum(kbps)) return '—';
  if (kbps < 1) return '0 KB/s';
  if (kbps < 1000) return Math.round(kbps) + ' KB/s';
  return (kbps / 1024).toFixed(kbps < 10240 ? 1 : 0) + ' MB/s';
}
function fmtMem(mb) {
  if (!isNum(mb)) return '—';
  return mb < 1024 ? Math.round(mb) + ' MB' : (mb / 1024).toFixed(1) + ' GB';
}
function fmtDur(sec) {
  if (!isNum(sec) || sec < 0) return '—';
  sec = Math.round(sec);
  var d = Math.floor(sec / 86400), hr = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60);
  if (d) return d + 'd ' + hr + 'h';
  if (hr) return hr + 'h ' + m + 'm';
  if (m) return m + 'm';
  return sec + 's';
}
function ago(ts) {
  if (!isNum(ts) || ts <= 0) return '—';
  var s = Math.max(0, nowSec() - ts);
  if (s < 2) return 'just now';
  if (s < 60) return Math.floor(s) + 's ago';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  if (s < 86400) return Math.floor(s / 3600) + 'h ago';
  return Math.floor(s / 86400) + 'd ago';
}
function agoShort(ts) {
  if (!isNum(ts) || ts <= 0) return '';
  var s = Math.max(0, nowSec() - ts);
  if (s < 5) return 'now';
  if (s < 60) return Math.floor(s) + 's';
  if (s < 3600) return Math.floor(s / 60) + 'm';
  if (s < 86400) return Math.floor(s / 3600) + 'h';
  return Math.floor(s / 86400) + 'd';
}
function pad2(n) { return (n < 10 ? '0' : '') + n; }
function localISO(d) { return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()); }
function to12h(hhmm) {
  var p = String(hhmm || '').split(':'), hr = +p[0], m = +p[1];
  if (!isNum(hr) || !isNum(m)) return hhmm;
  return pad2(hr % 12 || 12) + ':' + pad2(m) + ' ' + (hr >= 12 ? 'PM' : 'AM');
}
function clockMinutes(s) {
  var m = /^(\d{1,2}):(\d{2})\s*([AaPp][Mm])?$/.exec(String(s || '').trim());
  if (!m) return null;
  var hr = +m[1], mi = +m[2], ap = m[3] ? m[3].toUpperCase() : null;
  if (ap === 'PM' && hr < 12) hr += 12;
  if (ap === 'AM' && hr === 12) hr = 0;
  return hr * 60 + mi;
}
function clockLabel(s) {
  var mins = clockMinutes(s);
  if (mins == null) return String(s || '');
  var hr = Math.floor(mins / 60), m = mins % 60;
  return (hr % 12 || 12) + ':' + pad2(m) + ' ' + (hr >= 12 ? 'PM' : 'AM');
}
function dayLabel(iso) {
  if (!iso) return '';
  var today = new Date();
  var tmr = new Date(today); tmr.setDate(today.getDate() + 1);
  var yst = new Date(today); yst.setDate(today.getDate() - 1);
  if (iso === localISO(today)) return 'Today';
  if (iso === localISO(tmr)) return 'Tomorrow';
  if (iso === localISO(yst)) return 'Yesterday';
  var p = String(iso).split('-').map(Number);
  if (p.length !== 3 || !p[0]) return String(iso);
  var dt = new Date(p[0], p[1] - 1, p[2]);
  var opts = { weekday: 'short', month: 'short', day: 'numeric' };
  if (p[0] !== today.getFullYear()) opts.year = 'numeric';
  return dt.toLocaleDateString(undefined, opts);
}
function nextHour() { var d = new Date(); d.setHours(d.getHours() + 1, 0, 0, 0); return pad2(d.getHours()) + ':00'; }
function devName(d) { return (d && (d.name || d.hostname || d.device_id)) || 'device'; }
function kindOf(d) {
  var t = d && d.device_type;
  return t === 'mobile' ? 'mobile' : t === 'server' ? 'server' : 'pc';
}
var KIND_RANK = { pc: 0, server: 1, mobile: 2 };
function humanTool(name) { return String(name || '').replace(/_/g, ' '); }
function sourceLabel(src) { src = String(src || 'api'); return SOURCE_LABEL[src] || src; }
function busy(btn, on) {
  if (!btn) return;
  btn.classList.toggle('is-busy', !!on);
  btn.setAttribute('aria-busy', on ? 'true' : 'false');
}

/* ====================================================================== API */
function ApiError(message, status, data) {
  var e = new Error(message);
  e.status = status || 0;
  e.data = data || null;
  return e;
}
function api(path, opts) {
  opts = opts || {};
  var headers = { 'Authorization': 'Bearer ' + S.token, 'X-Willy-Client': CLIENT };
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
  var ctrl = new AbortController();
  var timedOut = false;
  var timer = setTimeout(function () { timedOut = true; ctrl.abort(); }, opts.timeout || 25000);
  var outer = opts.signal;
  var onAbort = function () { ctrl.abort(); };
  if (outer) { if (outer.aborted) ctrl.abort(); else outer.addEventListener('abort', onAbort); }
  var cleanup = function () { clearTimeout(timer); if (outer) outer.removeEventListener('abort', onAbort); };
  return fetch(BASE + path, {
    method: opts.method || 'GET', headers: headers, cache: 'no-store', signal: ctrl.signal,
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body)
  }).then(function (res) {
    return res.text().then(function (txt) {
      cleanup();
      var data = null;
      try { data = txt ? JSON.parse(txt) : null; } catch (e) { data = null; }
      if (res.status === 401) { onUnauthorized(); throw ApiError('Your access token was rejected.', 401, data); }
      if (!res.ok) {
        var msg = data && (typeof data.detail === 'string' ? data.detail : (data.error || data.reply));
        throw ApiError(msg || ('The hub answered with HTTP ' + res.status + '.'), res.status, data);
      }
      return data;
    });
  }, function (err) {
    cleanup();
    if (outer && outer.aborted) throw err;
    throw ApiError(timedOut ? 'The hub took too long to answer.' : 'Can’t reach the Willy hub.', 0);
  });
}
function resultError(r) {
  if (!r) return 'No answer from the device.';
  if (r.error === 'DEVICE_OFFLINE') return 'The device is offline.';
  if (r.error === 'TIMEOUT') return 'The device didn’t answer in time.';
  return String(r.reply || r.message || r.error || 'Something went wrong.');
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
function showOnly(id) {
  ['boot', 'login', 'app'].forEach(function (x) { $('#' + x).hidden = x !== id; });
}
function showLogin(message, prefill) {
  showOnly('login');
  var err = $('#login-err');
  err.textContent = message || '';
  err.hidden = !message;
  var input = $('#login-token');
  if (prefill) input.value = prefill;
  setTimeout(function () { try { input.focus(); } catch (e) { /* ignore */ } }, 30);
}
function scheduleLoginRetry(token) {
  clearTimeout(S.loginRetry);
  S.loginRetry = setTimeout(function () {
    if (S.token || $('#login').hidden) return;
    if ($('#login-token').value.trim() !== token) return;
    if (document.hidden) { scheduleLoginRetry(token); return; }
    checkToken(token).then(function (r) {
      if (S.token || $('#login').hidden) return;
      if (r.ok) { local.set(KEYS.token, token); $('#login-token').value = ''; startApp(token, r.server); }
      else if (r.reason === 'network' || r.reason === 'http') scheduleLoginRetry(token);
    });
  }, 5000);
}
function loginFailText(r) {
  if (r.reason === 'invalid') return 'That token was rejected. Check WILLY_REMOTE_TOKEN on the hub and try again.';
  if (r.reason === 'network') return 'Can’t reach the Willy hub. Check that it’s running — this page retries every few seconds.';
  return 'The hub answered with HTTP ' + r.status + '. Try again in a moment.';
}
function boot() {
  if (window.WillyGoogle) {
    var pending = false;
    try { pending = sessionStorage.getItem('willy_g_redirect') === '1'; } catch (e) { /* ignore */ }
    if (pending) {
      WillyGoogle.finishRedirect(BASE).then(function (res) {
        if (res) local.set(KEYS.token, res.token);
        bootWithToken();
      }, function (e) { showLogin(e.message); });
      return;
    }
  }
  bootWithToken();
}
function bootWithToken() {
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
  var signedIn = !!S.token;
  if (!signedIn) local.set(KEYS.token, token);
  checkToken(token).then(function (r) {
    if (r.ok) {
      local.set(KEYS.token, token);
      if (S.token) { wsClose(); S.token = null; closeScreen(); resetState(); }
      startApp(token, r.server);
      return;
    }
    if (signedIn) { if (S.token) local.set(KEYS.token, S.token); toast(r.reason === 'invalid' ? 'The token in the link was rejected; you are still signed in.' : 'Couldn’t check the token in the link.', { type: 'warn' }); return; }
    if (r.reason === 'invalid') { local.del(KEYS.token); showLogin('That token was rejected. Enter the current WILLY_REMOTE_TOKEN.'); return; }
    showLogin(loginFailText(r), token);
    scheduleLoginRetry(token);
  });
}
function setTokenArea(show) {
  var area = $('#login-token-area');
  area.style.display = show ? 'contents' : 'none';
  $('#login-token-toggle').textContent = show ? 'Hide token sign-in' : 'Use a hub access token';
}
function googleSignedIn(res) {
  if (!res) return;
  local.set(KEYS.token, res.token);
  checkToken(res.token).then(function (r) {
    if (r.ok) { $('#login-err').hidden = true; startApp(res.token, r.server); }
    else showLogin(loginFailText(r));
  });
}
function emailAuth(path, body) {
  var err = $('#login-err');
  err.hidden = true;
  return fetch(BASE + path, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Willy-Client': CLIENT }, body: JSON.stringify(body) })
    .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) {
      if (!r.ok || !d.token) throw new Error(d.error || (typeof d.detail === 'string' ? d.detail : '') || ('Sign-in failed (HTTP ' + r.status + ').'));
      googleSignedIn(d);
    }); })
    .catch(function (e) { err.textContent = e.message; err.hidden = false; });
}
function wireGoogle() {
  if (!window.WillyGoogle) return;
  WillyGoogle.config(BASE).then(function (cfg) {
    if (!cfg) return;
    $('#login-google').hidden = false;
    var viaGoogle = !!(cfg.google || (cfg.broker && cfg.broker.url));
    $('#login-google-btn').hidden = !viaGoogle;
    $('#login-or-email').hidden = !viaGoogle;
    $('#le-create').hidden = cfg.signup !== 'open';
    setTokenArea(false);
  });
  function emailGo(create) {
    var email = $('#le-email').value.trim(), pass = $('#le-pass').value;
    if (!email || !pass) { var e = $('#login-err'); e.textContent = 'Enter your email and password.'; e.hidden = false; return; }
    emailAuth(create ? '/api/v1/auth/register' : '/api/v1/auth/login', { email: email, password: pass });
  }
  $('#le-signin').addEventListener('click', function () { emailGo(false); });
  $('#le-create').addEventListener('click', function () { emailGo(true); });
  $('#le-pass').addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); emailGo(false); } });
  $('#login-token-toggle').addEventListener('click', function () {
    setTokenArea($('#login-token-area').style.display === 'none');
  });
  $('#login-google-btn').addEventListener('click', function () {
    var b = this, err = $('#login-err');
    b.disabled = true; err.hidden = true;
    WillyGoogle.signIn(BASE).then(function (res) { b.disabled = false; googleSignedIn(res); }, function (e) {
      b.disabled = false; err.textContent = e.message; err.hidden = false;
    });
  });
}
function wireLogin() {
  wireGoogle();
  window.addEventListener('hashchange', onHashToken);
  $('#login-form').addEventListener('submit', function (e) {
    e.preventDefault();
    var input = $('#login-token');
    var token = input.value.trim();
    var err = $('#login-err');
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
function onUnauthorized() {
  if (!S.token) return;
  signOut('Your access token was rejected. Enter the current WILLY_REMOTE_TOKEN.');
}
function signOut(message) {
  var old = S.token || local.get(KEYS.token);
  if (old && old.indexOf('wses_') === 0) {
    try { fetch(BASE + '/api/v1/auth/logout', { method: 'POST', headers: { 'Authorization': 'Bearer ' + old }, keepalive: true }); } catch (e) { /* ignore */ }
  }
  if (window.WillyGoogle) WillyGoogle.signOut();
  local.del(KEYS.token);
  session.del(KEYS.conv);
  local.del(KEYS.srvHist);
  SV.hist = null;
  S.token = null;
  wsClose();
  clearInterval(S.ticker); clearInterval(S.statsPoll); clearTimeout(S.statsTimer); clearTimeout(S.remTimer);
  closeScreen();
  var dlg = $('#dlg'); if (dlg.open) dlg.close();
  resetState();
  showLogin(message || '');
}
function resetState() {
  S.devices.clear(); S.activity = []; S.stats = null; S.server = null; S.reminders = []; S.alarms = [];
  S.conv = []; S.targetKey = ''; S.sourcesKey = ''; S.filter = 'all'; S.skewSet = false; S.loadedMore = false;
  cards.forEach(function (c) { stopProcPoll(c); c.el.remove(); });
  cards.clear(); feedEls.clear(); dirty.clear();
  resetServer();
  $('#feed').replaceChildren(); $('#filters').replaceChildren();
  renderConv(); renderStats(); renderReminders(); renderAlarms(); updateDevMeta();
  $('#feed-empty').hidden = false;
}
function startApp(token, server) {
  clearTimeout(S.loginRetry);
  S.token = token;
  showOnly('app');
  if (!S.wired) { wireApp(); S.wired = true; }
  S.conv = loadConv();
  renderConv();
  if (server) applyServer(server);
  renderStats();
  updateDevMeta();
  wsConnect();
  loadRemindersSoon(0);
  clearInterval(S.ticker);
  S.ticker = setInterval(tick, 1000);
  clearInterval(S.statsPoll);
  S.statsPoll = setInterval(function () { if (S.token && !document.hidden) refreshStats(); }, 30000);
  maybeFirstRunSetup();
}

/* ============================================================ live events */
var WS = { sock: null, attempts: 0, timer: 0, ping: 0, lastMsg: 0, opened: false, nextAt: 0, state: 'connecting', rtt: null };

function wsUrl() {
  return (location.protocol === 'https:' ? 'wss:' : 'ws:') + '//' + location.host + BASE + '/ws/events?token=' + encodeURIComponent(S.token);
}
function wsConnect() {
  clearTimeout(WS.timer); WS.timer = 0;
  if (!S.token || WS.sock) return;
  var sock;
  try { sock = new WebSocket(wsUrl()); } catch (e) { wsRetry(); return; }
  WS.sock = sock; WS.opened = false;
  setConn(WS.attempts ? 'reconnecting' : 'connecting');
  sock.onopen = function () {
    if (WS.sock !== sock) return;
    WS.opened = true; WS.attempts = 0; WS.nextAt = 0; WS.lastMsg = Date.now();
    setConn('live');
    wsPing();
    clearInterval(WS.ping);
    WS.ping = setInterval(wsPingTick, 5000);
  };
  sock.onmessage = function (ev) {
    if (WS.sock !== sock) return;
    WS.lastMsg = Date.now();
    var m;
    try { m = JSON.parse(ev.data); } catch (e) { return; }
    if (!m || typeof m !== 'object') return;
    try { onEvent(m); } catch (err) { if (window.console) console.error('[willy] event handler failed', err); }
  };
  sock.onclose = function (ev) {
    if (WS.sock !== sock) return;
    var opened = WS.opened;
    wsDrop();
    if (!S.token) return;
    if (!opened || ev.code === 4001) wsVerifyThenRetry(); else wsRetry();
  };
  sock.onerror = function () { /* onclose follows */ };
}
function wsDrop() {
  var s = WS.sock;
  WS.sock = null; WS.opened = false;
  clearInterval(WS.ping); WS.ping = 0;
  if (s) {
    s.onopen = s.onmessage = s.onclose = s.onerror = null;
    try { s.close(); } catch (e) { /* ignore */ }
  }
}
function wsClose() { clearTimeout(WS.timer); WS.timer = 0; wsDrop(); WS.attempts = 0; WS.nextAt = 0; WS.rtt = null; }
function wsRetry() {
  clearTimeout(WS.timer);
  var delay = Math.min(15000, 1000 * Math.pow(2, WS.attempts));
  WS.attempts++;
  WS.nextAt = Date.now() + delay;
  WS.rtt = null;
  setConn(WS.attempts > 3 || navigator.onLine === false ? 'offline' : 'reconnecting');
  WS.timer = setTimeout(wsConnect, delay);
}
function wsVerifyThenRetry() {
  checkToken(S.token).then(function (r) {
    if (!S.token) return;
    if (!r.ok && r.reason === 'invalid') { onUnauthorized(); return; }
    wsRetry();
  });
}
function wsSend(obj) {
  if (WS.sock && WS.sock.readyState === 1) { try { WS.sock.send(JSON.stringify(obj)); return true; } catch (e) { /* ignore */ } }
  return false;
}
function wsPing() { wsSend({ type: 'ping', ts: Date.now() }); }
function wsPingTick() {
  if (Date.now() - WS.lastMsg > 16000) { wsDrop(); wsRetry(); return; }
  wsPing();
}
function wsKick() {
  if (!S.token || WS.sock) return;
  WS.attempts = 0;
  wsConnect();
}
function setConn(state) {
  WS.state = state;
  $('#conn').dataset.state = state;
  renderConn();
}
function renderConn() {
  var labels = { connecting: 'Connecting', live: 'Live', reconnecting: 'Reconnecting', offline: 'Offline' };
  var extra = '';
  if (WS.state === 'live') extra = isNum(WS.rtt) ? WS.rtt + ' ms' : '';
  else if ((WS.state === 'reconnecting' || WS.state === 'offline') && WS.nextAt && !WS.sock) {
    var s = Math.max(0, Math.ceil((WS.nextAt - Date.now()) / 1000));
    extra = s ? 'retry in ' + s + 's' : '';
  }
  setText($('#conn-label'), labels[WS.state] || WS.state);
  setText($('#conn-rtt'), extra);
  $('#conn').title = WS.state === 'live'
    ? 'Live updates connected' + (isNum(WS.rtt) ? ' · round trip ' + WS.rtt + ' ms' : '')
    : 'Live updates are disconnected; the page reconnects automatically.';
}
function onPong(m) {
  if (!isNum(m.ts)) return;
  var now = Date.now();
  var rtt = Math.max(0, now - m.ts);
  WS.rtt = rtt;
  if (isNum(m.server_time)) {
    var sk = m.server_time * 1000 - (m.ts + rtt / 2);
    S.skew = S.skewSet ? S.skew * 0.7 + sk * 0.3 : sk;
    S.skewSet = true;
  }
  renderConn();
}
function onEvent(m) {
  switch (m.type) {
    case 'snapshot': applySnapshot(m); break;
    case 'device_discovered':
    case 'device_update':
    case 'device_offline': applyDevice(m); break;
    case 'activity': applyActivity(m.entry); break;
    case 'reminder_due':
    case 'morning_call_due': onDue(m); break;
    case 'reminders_changed': applyReminderLists(m.reminders, m.alarms); break;
    case 'presence_alert': if (m.reply) toast(m.reply, { type: 'info', title: m.title }); break;
    case 'pong': onPong(m); break;
    default: break;
  }
}
var OFFLINE_SHORT = { app_closed: 'app closed', sleep: 'asleep', shutdown: 'shut down', restart: 'restarting',
  logoff: 'signed out', connection_lost: 'connection lost', no_heartbeat: 'not responding', hub_restarted: 'reconnecting' };
function presenceBadge(badge, d) {
  var p = d.presence || {};
  var off = !d.online;
  var short = off ? (OFFLINE_SHORT[p.reason] || '') : '';
  setText(badge, off ? 'Offline' + (short ? ' · ' + short : '') : 'Online');
  badge.title = off && p.reason_text ? 'Offline ' + ago(p.since) + ': ' + p.reason_text : '';
}
function applySnapshot(m) {
  var seen = new Set();
  (m.devices || []).forEach(function (d) {
    if (d && d.device_id) { seen.add(d.device_id); S.devices.set(d.device_id, d); markDirty(d.device_id); }
  });
  Array.from(S.devices.keys()).forEach(function (id) { if (!seen.has(id)) { S.devices.delete(id); markDirty(id); } });
  if (m.server) applyServer(m.server);
  if (!S.skewSet && isNum(m.timestamp)) S.skew = m.timestamp * 1000 - Date.now();
  mergeActivity(m.activity || [], true);
  if (m.stats) { S.stats = m.stats; renderStats(); }
  updateDevMeta();
  loadRemindersSoon(200);
  if (!S.loadedMore) { S.loadedMore = true; loadMoreActivity(); }
}
function applyDevice(m) {
  var d = m.device;
  var id = (d && d.device_id) || m.device_id;
  if (!id) return;
  var prev = S.devices.get(id);
  if (!d) {
    if (!prev) return;
    d = Object.assign({}, prev, { status: 'offline', online: false });
  }
  S.devices.set(id, d);
  if (m.type === 'device_discovered' && (!prev || !prev.online)) {
    var gone = prev && prev.presence && prev.presence.state === 'offline' && isNum(prev.presence.since) ? nowSec() - prev.presence.since : null;
    toast(devName(d) + (gone && gone > 20 ? ' is back online after ' + fmtDur(gone) + '.' : ' is online.'), { type: 'success', title: 'Device connected' });
  }
  if (m.type === 'device_offline' && prev && prev.online) {
    var why = d.presence && d.presence.reason_text;
    toast(devName(d) + ' went offline' + (why ? ': ' + why : '') + '.', { type: 'warn', title: 'Device disconnected' });
  }
  if (m.type === 'device_update' || m.type === 'device_discovered') { var c = cards.get(id); if (c) c.beatPending = true; }
  markDirty(id);
}
function markDirty(id) {
  dirty.add(id);
  if (rafId) return;
  rafId = requestAnimationFrame(flush);
  // Frames can stop while the page still counts as visible (e.g. an occluded window):
  // fall back to a timer so the cards never go stale. Hidden tabs wait for the next frame.
  clearTimeout(flushTimer);
  if (!document.hidden) flushTimer = setTimeout(flush, 350);
}
function flush() {
  if (rafId) { cancelAnimationFrame(rafId); rafId = 0; }
  clearTimeout(flushTimer); flushTimer = 0;
  if (!dirty.size) return;
  var ids = Array.from(dirty);
  dirty.clear();
  renderDevices(ids);
}

/* ================================================================== server */
function applyServer(sv) {
  if (!sv) return;
  S.server = sv; S.serverAt = Date.now();
  var llm = sv.llm || {};
  var label = [llm.provider, llm.model].filter(Boolean).join(' · ') || 'AI brain';
  setText($('#srv-llm-text'), label);
  $('#srv-llm-dot').className = 'dot ' + (llm.ready ? 'ok' : 'bad');
  $('#srv-llm').title = llm.ready ? 'AI brain ready: ' + label : 'AI brain unavailable' + (llm.last_error ? ': ' + String(llm.last_error).slice(0, 200) : '');
  setText($('#srv-ver'), sv.version ? 'v' + sv.version : '');
  renderUptime();
  if (!S.skewSet && isNum(sv.server_time)) S.skew = sv.server_time * 1000 - Date.now();
  $('#sec-banner').hidden = !sv.using_default_token || session.get(KEYS.banner) === '1';
}
function renderUptime() {
  if (!S.server || !isNum(S.server.uptime_sec)) return;
  setText($('#srv-uptime'), 'up ' + fmtDur(S.server.uptime_sec + (Date.now() - S.serverAt) / 1000));
}
function refreshStats() {
  return api('/api/v1/stats').then(function (r) {
    if (!r) return;
    if (r.stats) { S.stats = r.stats; renderStats(); }
    if (r.server) applyServer(r.server);
  }).catch(function () { /* next poll retries */ });
}
function scheduleStats() { clearTimeout(S.statsTimer); S.statsTimer = setTimeout(refreshStats, 1200); }

/* ================================================================== devices */
function sortCards(a, b) {
  if (a.kind !== b.kind) return (KIND_RANK[a.kind] || 0) - (KIND_RANK[b.kind] || 0);
  var da = S.devices.get(a.id), db = S.devices.get(b.id);
  var na = devName(da).toLowerCase(), nb = devName(db).toLowerCase();
  return na < nb ? -1 : na > nb ? 1 : (a.id < b.id ? -1 : 1);
}
function renderDevices(ids) {
  ids.forEach(function (id) {
    var d = S.devices.get(id);
    var card = cards.get(id);
    if (!d) {
      if (card) { stopProcPoll(card); card.el.remove(); cards.delete(id); if (SCR.dev === id) closeScreen(); }
      return;
    }
    if (card && card.kind !== kindOf(d)) { stopProcPoll(card); card.el.remove(); cards.delete(id); card = null; }
    if (!card) { card = createCard(d); cards.set(id, card); }
    if (card.kind === 'pc') updatePc(card, d);
    else if (card.kind === 'server') updateServer(card, d);
    else updatePhone(card, d);
    if (card.beatPending) { card.beatPending = false; beat(card); }
  });
  var grid = $('#dev-grid');
  var sorted = Array.from(cards.values()).sort(sortCards);
  sorted.forEach(function (c, i) {
    var at = grid.children[i];
    if (at !== c.el) grid.insertBefore(c.el, at || null);
  });
  updateDevMeta();
}
function updateDevMeta() {
  var on = 0, off = 0;
  S.devices.forEach(function (d) { if (d.online) on++; else off++; });
  setText($('#dev-count'), S.devices.size ? on + ' online' + (off ? ' · ' + off + ' offline' : '') : '');
  $('#dev-empty').hidden = S.devices.size > 0;
  updateTargetSelect();
  syncServerPanel();
}
function updateTargetSelect() {
  var pcs = [];
  S.devices.forEach(function (d) { if (d.device_type === 'pc' && d.online) pcs.push(d); });
  $('#target-wrap').hidden = pcs.length < 2;
  var key = pcs.map(function (d) { return d.device_id + '\u0001' + devName(d); }).join('\u0002');
  if (key === S.targetKey) return;
  S.targetKey = key;
  var sel = $('#target');
  var cur = sel.value;
  sel.replaceChildren(h('option', { value: '' }, 'Most recent PC'));
  pcs.forEach(function (d) { sel.appendChild(h('option', { value: d.device_id }, devName(d))); });
  sel.value = pcs.some(function (d) { return d.device_id === cur; }) ? cur : '';
}
function beat(card) {
  if (REDUCED || !card.refs.beat.animate) return;
  try {
    card.refs.beat.animate([
      { transform: 'scale(1.9)', opacity: 1, boxShadow: '0 0 0 0 rgba(16,185,129,.6)' },
      { transform: 'scale(1)', opacity: 0.45, boxShadow: '0 0 0 8px rgba(16,185,129,0)' }
    ], { duration: 900, easing: 'ease-out' });
  } catch (e) { /* ignore */ }
}
function createCard(d) {
  var kind = kindOf(d);
  var el = $(kind === 'pc' ? '#tpl-pc' : kind === 'server' ? '#tpl-server' : '#tpl-phone').content.firstElementChild.cloneNode(true);
  var refs = {};
  $$('[data-ref]', el).forEach(function (n) { refs[n.getAttribute('data-ref')] = n; });
  var gauges = {};
  $$('.gauge', el).forEach(function (g) {
    gauges[g.getAttribute('data-g')] = { el: g, val: $('.g-val', g), num: $('.g-num', g), unit: $('.g-unit', g), sub: $('.g-sub', g), cur: null, raf: 0 };
  });
  var card = {
    id: d.device_id, kind: kind, el: el, refs: refs, gauges: gauges,
    volHold: 0, volDrag: false, muted: false, flash: false,
    procSort: 'cpu', procHover: false, procKey: '', procPending: null, procTimer: 0, specKey: '', beatPending: false
  };
  el.setAttribute('data-id', d.device_id);
  if (kind === 'pc') wirePc(card);
  else if (kind === 'server') wireServer(card);
  else wirePhone(card);
  return card;
}
function setGauge(g, v, o) {
  o = o || {};
  var has = isNum(v);
  var pct = has ? Math.max(0, Math.min(100, v)) : 0;
  var off = (G_ARC * (1 - pct / 100)).toFixed(2);
  if (g.val.style.strokeDashoffset !== off) g.val.style.strokeDashoffset = off;
  var level = '';
  if (has && !o.calm) {
    if (o.invert) level = pct <= o.hot ? 'hot' : pct <= o.warm ? 'warm' : '';
    else level = pct >= o.hot ? 'hot' : pct >= o.warm ? 'warm' : '';
  }
  if ((g.el.getAttribute('data-level') || '') !== level) g.el.setAttribute('data-level', level);
  g.el.classList.toggle('is-empty', !has);
  tweenNum(g, has ? pct : null, o.text);
  setText(g.sub, o.sub || ' ');
}
function tweenNum(g, target, text) {
  cancelAnimationFrame(g.raf);
  clearTimeout(g.settle);
  if (text != null) { g.cur = null; setText(g.num, text); g.unit.hidden = true; return; }
  g.unit.hidden = target == null;
  if (target == null) { g.cur = null; setText(g.num, '—'); return; }
  var from = g.cur == null ? target : g.cur;
  if (REDUCED || document.hidden || Math.abs(from - target) < 0.5) { g.cur = target; setText(g.num, Math.round(target)); return; }
  var t0 = performance.now(), dur = 700;
  var step = function (now) {
    var k = Math.min(1, (now - t0) / dur);
    var e = 1 - Math.pow(1 - k, 3);
    g.cur = from + (target - from) * e;
    setText(g.num, Math.round(g.cur));
    if (k < 1) g.raf = requestAnimationFrame(step);
  };
  g.raf = requestAnimationFrame(step);
  // Guarantees the final value even if animation frames are suspended.
  g.settle = setTimeout(function () { cancelAnimationFrame(g.raf); g.cur = target; setText(g.num, Math.round(target)); }, dur + 150);
}
function sparkPaths(arr, max, W, H) {
  W = W || 200; H = H || 46;
  var pad = 3, n = arr.length;
  var step = W / (SPARK_N - 1);
  var x0 = W - (n - 1) * step;
  var line = '', area = '', seg = [];
  var flushSeg = function () {
    if (seg.length > 1) {
      var pts = seg.map(function (p) { return p[0].toFixed(1) + ' ' + p[1].toFixed(1); });
      line += 'M' + pts.join('L');
      area += 'M' + seg[0][0].toFixed(1) + ' ' + H + 'L' + pts.join('L') + 'L' + seg[seg.length - 1][0].toFixed(1) + ' ' + H + 'Z';
    }
    seg = [];
  };
  for (var i = 0; i < n; i++) {
    var v = arr[i];
    if (!isNum(v)) { flushSeg(); continue; }
    var y = H - pad - Math.max(0, Math.min(1, v / max)) * (H - 2 * pad);
    seg.push([x0 + i * step, y]);
  }
  flushSeg();
  return [line, area];
}
function setD(el, d) { if (el && el.getAttribute('d') !== d) el.setAttribute('d', d); }
function setRange(input, value) {
  input.value = value;
  input.style.setProperty('--p', Math.max(0, Math.min(100, +input.value)) + '%');
}
function setOnlineControls(card, enabled) {
  $$('button, input, select', card.el).forEach(function (n) {
    if (n.closest('.panel-tools') || n.hasAttribute('data-keep')) return;
    n.disabled = !enabled;
  });
}
function updatePc(card, d) {
  var t = d.telemetry || {}, sp = d.specs || {}, R = card.refs, G = card.gauges;
  var online = !!d.online;
  card.el.classList.toggle('is-offline', !online);
  if (card.online !== online) { card.online = online; setOnlineControls(card, online); if (!online) closeMenus(); }
  setText(R.name, devName(d));
  var sub = [d.platform || sp.os || 'Windows'];
  if (d.hostname && d.hostname !== d.name) sub.push(d.hostname);
  if (sp.user) sub.push(sp.user);
  setText(R.sub, sub.join(' · '));
  R.badge.className = 'badge ' + (online ? 'on' : 'off');
  presenceBadge(R.badge, d);
  setText(R.ip, t.ip_address || '—');
  setText(R.wifi, t.wifi_ssid || '—');
  R.wifi.title = t.wifi_ssid || '';
  setText(R.uptime, isNum(t.uptime_hours) ? fmtDur(t.uptime_hours * 3600) : '—');
  R.seen.setAttribute('data-ts', isNum(d.last_seen) ? d.last_seen : '');
  setText(R.seen, ago(d.last_seen));
  setText(R.ping, isNum(t.last_ping_ms) ? Math.round(t.last_ping_ms) + ' ms' : '—');

  setGauge(G.cpu, t.cpu_pct, { warm: 75, hot: 90, sub: isNum(t.cpu_freq_mhz) && t.cpu_freq_mhz > 0 ? (t.cpu_freq_mhz / 1000).toFixed(2) + ' GHz' : (isNum(t.process_count) ? t.process_count + ' procs' : '') });
  setGauge(G.ram, t.ram_pct, { warm: 80, hot: 92, sub: isNum(t.ram_used_gb) && isNum(t.ram_total_gb) ? t.ram_used_gb.toFixed(1) + '/' + Math.round(t.ram_total_gb) + ' GB' : '' });
  setGauge(G.disk, t.disk_pct, { warm: 85, hot: 95, sub: isNum(t.disk_free_gb) ? Math.round(t.disk_free_gb) + ' GB free' : '' });
  if (isNum(t.battery_pct)) {
    var bsub = t.is_charging ? 'Charging' : (isNum(t.battery_secs_left) && t.battery_secs_left > 0 ? fmtDur(t.battery_secs_left) + ' left' : 'On battery');
    setGauge(G.bat, t.battery_pct, { invert: true, warm: 30, hot: 15, calm: !!t.is_charging, sub: bsub });
  } else {
    setGauge(G.bat, null, { text: 'AC', sub: sp.has_battery ? 'No reading' : 'Plugged in' });
  }
  G.bat.el.classList.toggle('is-charging', !!t.is_charging && isNum(t.battery_pct));

  var hs = d.history || {};
  var cpu = sparkPaths(hs.cpu || [], 100), ram = sparkPaths(hs.ram || [], 100);
  setD(R.cpuLine, cpu[0]); setD(R.cpuArea, cpu[1]); setD(R.ramLine, ram[0]);
  var downs = hs.net_down || [], ups = hs.net_up || [];
  var peak = 64;
  downs.concat(ups).forEach(function (v) { if (isNum(v) && v > peak) peak = v; });
  var dn = sparkPaths(downs, peak * 1.15), up = sparkPaths(ups, peak * 1.15);
  setD(R.downLine, dn[0]); setD(R.downArea, dn[1]); setD(R.upLine, up[0]);
  setText(R.spCpu, isNum(t.cpu_pct) ? Math.round(t.cpu_pct) + '%' : '—');
  setText(R.spRam, isNum(t.ram_pct) ? Math.round(t.ram_pct) + '%' : '—');
  setText(R.spDown, fmtRate(t.net_down_kbps));
  setText(R.spUp, fmtRate(t.net_up_kbps));

  var proc = t.active_process ? String(t.active_process).replace(/\.exe$/i, '') : '';
  setText(R.proc, proc);
  R.proc.hidden = !proc;
  R.proc.title = t.active_process || '';
  var win = t.active_window ? String(t.active_window) : '';
  setText(R.win, win || '—');
  R.win.title = win;
  var idleText = '', idleCls = 'idle';
  if (online && isNum(t.idle_sec)) {
    if (t.idle_sec < 60) { idleText = 'Active now'; idleCls += ' is-active'; }
    else { idleText = 'Idle ' + fmtDur(t.idle_sec); idleCls += ' is-idle'; }
  }
  setText(R.idle, idleText);
  if (R.idle.className !== idleCls) R.idle.className = idleCls;

  if (!card.volDrag && Date.now() > card.volHold) {
    if (isNum(t.volume_level)) setRange(R.vol, Math.round(t.volume_level));
    card.muted = !!t.is_muted;
    setText(R.volV, card.muted ? 'Muted' : (isNum(t.volume_level) ? Math.round(t.volume_level) + '%' : '—'));
  }
  renderMute(card);
  R.brightRow.hidden = !sp.has_battery;
  if (isNum(t.brightness) && Date.now() > (card.brightHold || 0)) {
    setRange(R.bright, Math.round(t.brightness));
    setText(R.brightV, Math.round(t.brightness) + '%');
  }

  setText(R.procMeta, isNum(t.process_count) ? t.process_count + ' running' : '');
  if (card.procSort === 'cpu') renderProcs(card, Array.isArray(t.top_processes) ? t.top_processes : [], online);
  else if (!online) stopProcPoll(card);
  var specKey = JSON.stringify(sp) + '|' + d.device_id;
  if (specKey !== card.specKey) { card.specKey = specKey; renderSpecs(card, sp, d); }
}
function renderMute(card) {
  var R = card.refs;
  card.el.classList.toggle('is-muted', card.muted);
  R.mute.setAttribute('aria-pressed', String(card.muted));
  R.mute.setAttribute('aria-label', card.muted ? 'Unmute' : 'Mute');
  R.mute.title = card.muted ? 'Unmute' : 'Mute';
  R.muteIcon.setAttribute('href', card.muted ? '#i-mute' : '#i-vol');
}
function renderProcs(card, procs, online) {
  var key = JSON.stringify(procs.map(function (p) { return [p.pid, p.name, p.cpu, p.mem_mb]; })) + (online ? 1 : 0) + card.procSort;
  if (key === card.procKey) return;
  if (card.procHover) { card.procPending = [procs, online]; setText(card.refs.procNote, 'Paused while pointing'); return; }
  card.procKey = key; card.procPending = null;
  setText(card.refs.procNote, card.procSort === 'cpu' ? 'Live' : 'Every 5 s');
  var maxCpu = 0;
  procs.forEach(function (p) { if (isNum(p.cpu) && p.cpu > maxCpu) maxCpu = p.cpu; });
  var rows = procs.map(function (p) {
    var name = String(p.name || 'unknown');
    var bar = h('span', { class: 'p-bar' });
    var share = card.procSort === 'cpu' ? (isNum(p.cpu) && maxCpu > 0 ? p.cpu / maxCpu : 0) : 0;
    bar.style.width = Math.max(2, Math.round(share * 100)) + '%';
    if (card.procSort !== 'cpu') bar.hidden = true;
    var kill = h('button', { class: 'icon-btn sm kill', type: 'button', title: 'End task', 'aria-label': 'End task ' + name }, icon('x'));
    kill.disabled = !online || !isNum(p.pid);
    kill.addEventListener('click', function () { killProcess(card, { pid: p.pid, name: name }); });
    return h('tr', null,
      h('td', { class: 'p-name', title: name }, name, bar),
      h('td', { class: 'num c-pid' }, isNum(p.pid) ? p.pid : '—'),
      h('td', { class: 'num' }, isNum(p.cpu) ? p.cpu.toFixed(1) + '%' : '—'),
      h('td', { class: 'num' }, fmtMem(p.mem_mb)),
      h('td', { class: 'p-act' }, kill));
  });
  if (!rows.length) rows = [h('tr', null, h('td', { colspan: '5', class: 'muted' }, online ? 'Waiting for the process list…' : 'Offline'))];
  card.refs.procBody.replaceChildren.apply(card.refs.procBody, rows);
}
function cpuShort(model) {
  if (!model) return '';
  return String(model).replace(/\((R|TM|C)\)/gi, '').replace(/\bCPU\b/gi, '').replace(/@.*$/, '').replace(/\s+/g, ' ').trim();
}
function renderSpecs(card, sp, d) {
  var rows = [];
  var add = function (k, v) {
    if (v == null || v === '' || (Array.isArray(v) && !v.length)) return;
    if (Array.isArray(v)) v = v.map(String).join('\n');
    else if (typeof v === 'object') v = JSON.stringify(v);
    rows.push(h('div', { class: 'spec' }, h('dt', null, k), h('dd', null, String(v))));
  };
  add('Processor', sp.cpu_model);
  if (sp.cpu_cores || sp.cpu_threads) add('Cores', (sp.cpu_cores != null ? sp.cpu_cores : '?') + ' cores · ' + (sp.cpu_threads != null ? sp.cpu_threads : '?') + ' threads');
  if (isNum(sp.ram_total_gb)) add('Memory', sp.ram_total_gb + ' GB');
  add('Graphics', sp.gpu);
  add('System', [sp.os, sp.os_version ? '(' + sp.os_version + ')' : ''].filter(Boolean).join(' '));
  add('Display', [isNum(sp.monitors) ? sp.monitors + (sp.monitors === 1 ? ' monitor' : ' monitors') : '', sp.primary_resolution].filter(Boolean).join(' · '));
  add('Architecture', sp.machine);
  add('User', sp.user);
  if (isNum(sp.boot_time)) add('Booted', new Date(sp.boot_time * 1000).toLocaleString());
  add('Willy client', [sp.client_version ? 'v' + sp.client_version : '', sp.heartbeat_sec ? 'heartbeat ' + sp.heartbeat_sec + ' s' : ''].filter(Boolean).join(' · '));
  add('Device ID', d.device_id);
  setText(card.refs.specMeta, cpuShort(sp.cpu_model));
  if (rows.length <= 1) rows.unshift(h('p', { class: 'muted' }, 'Hardware details arrive a few seconds after the PC connects.'));
  card.refs.specs.replaceChildren.apply(card.refs.specs, rows);
}
function updatePhone(card, d) {
  var t = d.telemetry || {}, R = card.refs;
  var online = !!d.online;
  var live = online && d.connection !== 'http';
  card.el.classList.toggle('is-offline', !online);
  if (card.online !== live) { card.online = live; setOnlineControls(card, live); }
  setText(R.name, devName(d));
  var sub = [d.platform || 'Android'];
  var model = t.model || (d.hostname && d.hostname !== d.name ? d.hostname : '');
  if (model) sub.push(model);
  if (t.android_version) sub.push('Android ' + t.android_version);
  setText(R.sub, sub.join(' · '));
  R.badge.className = 'badge ' + (online ? 'on' : 'off');
  presenceBadge(R.badge, d);
  var net = '—';
  if (t.network_type) {
    var nt = String(t.network_type).toLowerCase();
    net = nt === 'wifi' || nt === 'wi-fi' ? 'Wi-Fi' + (t.wifi_ssid ? ' · ' + t.wifi_ssid : '') : nt === 'mobile' || nt === 'cellular' ? 'Mobile data' : nt === 'none' ? 'No network' : String(t.network_type);
  }
  setText(R.net, net);
  R.net.title = net;
  setText(R.ip, t.ip_address || '—');
  R.seen.setAttribute('data-ts', isNum(d.last_seen) ? d.last_seen : '');
  setText(R.seen, ago(d.last_seen));
  setText(R.ping, isNum(t.last_ping_ms) ? Math.round(t.last_ping_ms) + ' ms' : '—');
  if (isNum(t.battery_pct)) setGauge(card.gauges.bat, t.battery_pct, { invert: true, warm: 30, hot: 15, calm: !!t.is_charging, sub: t.is_charging ? 'Charging' : 'On battery' });
  else setGauge(card.gauges.bat, null, { sub: 'No reading' });
  card.gauges.bat.el.classList.toggle('is-charging', !!t.is_charging && isNum(t.battery_pct));
  var cnt = function (el, v) { setText(el, isNum(v) ? v : '—'); el.classList.toggle('is-hot', isNum(v) && v > 0); };
  cnt(R.notif, t.unread_notifications); cnt(R.missed, t.missed_calls); cnt(R.wa, t.unread_whatsapp);
  setText(R.storage, isNum(t.storage_free_gb) ? t.storage_free_gb.toFixed(1) + ' GB' : '—');
  if (typeof t.torch_on === 'boolean' && Date.now() > (card.flashHold || 0)) card.flash = t.torch_on;
  R.flash.setAttribute('aria-pressed', String(card.flash));
  setText(R.flashLabel, card.flash ? 'Flashlight off' : 'Flashlight on');
  var note = online && d.connection === 'http' ? 'This phone reports over HTTP only. Open the Willy app to enable remote actions.' : '';
  setText(R.note, note);
  R.note.hidden = !note;
}

/* ------------------------------------------------------------ PC wiring */
function wirePc(card) {
  var R = card.refs, el = card.el;
  el.addEventListener('click', function (e) {
    var item = e.target.closest('[data-folder]');
    if (item && el.contains(item)) { closeMenus(); openFolder(card, item.getAttribute('data-folder')); return; }
    var sortBtn = e.target.closest('[data-sort]');
    if (sortBtn && el.contains(sortBtn)) { setProcSort(card, sortBtn.getAttribute('data-sort')); return; }
    var b = e.target.closest('[data-act]');
    if (!b || !el.contains(b) || b.disabled) return;
    pcAction(card, b.getAttribute('data-act'), b);
  });
  R.vol.addEventListener('pointerdown', function () { card.volDrag = true; });
  R.vol.addEventListener('input', function () {
    setRange(R.vol, R.vol.value);
    setText(R.volV, R.vol.value + '%');
    card.volHold = Date.now() + 6000;
    clearTimeout(card.volTimer);
    card.volTimer = setTimeout(function () { sendVolume(card, +R.vol.value); }, 250);
  });
  var endDrag = function () { card.volDrag = false; card.volHold = Math.max(card.volHold, Date.now() + 4000); };
  R.vol.addEventListener('change', endDrag);
  R.vol.addEventListener('blur', endDrag);
  R.bright.addEventListener('input', function () {
    setRange(R.bright, R.bright.value);
    setText(R.brightV, R.bright.value + '%');
    card.brightHold = Date.now() + 20000; // PC re-reads brightness every ~15 s
    clearTimeout(card.brightTimer);
    card.brightTimer = setTimeout(function () {
      runAction(card.id, 'set_brightness', { level: +R.bright.value, quiet: true }, { label: 'Brightness' });
    }, 250);
  });
  setRange(R.bright, R.bright.value);
  R.procBody.addEventListener('pointerenter', function () { card.procHover = true; });
  R.procBody.addEventListener('pointerleave', function () {
    card.procHover = false;
    if (card.procPending) { var p = card.procPending; card.procPending = null; card.procKey = ''; renderProcs(card, p[0], p[1]); }
  });
  R.procPanel.open = window.innerWidth >= 900;
  R.procPanel.addEventListener('toggle', function () {
    if (card.procSort === 'memory') { if (R.procPanel.open) pollProcs(card); else stopProcPoll(card); }
  });
}
function pcAction(card, act, btn) {
  var d = S.devices.get(card.id), name = devName(d);
  switch (act) {
    case 'lock':
      return runAction(card.id, 'power_action', { action: 'lock' }, { btn: btn, label: 'Lock', ok: 'Locked ' + name + '.' });
    case 'sleep':
      return confirmDlg({ title: 'Put ' + name + ' to sleep?', body: 'It disconnects from Willy until you wake it up.', confirm: 'Sleep' }).then(function (yes) {
        if (yes) runAction(card.id, 'power_action', { action: 'sleep' }, { btn: btn, label: 'Sleep', ok: 'Putting ' + name + ' to sleep.' });
      });
    case 'screenshot':
      return runAction(card.id, 'take_screenshot', {}, { btn: btn, label: 'Screenshot', ok: function (r) { return r.message || ('Screenshot saved on ' + name + '.'); } });
    case 'desktop':
      return runAction(card.id, 'window_action', { action: 'minimize_all' }, { btn: btn, label: 'Show desktop', ok: 'Showing the desktop on ' + name + '.' });
    case 'live':
      return openScreen(card.id);
    case 'folder':
      return toggleMenu(btn);
    case 'note':
      return formDlg({
        title: 'Send a note to ' + name, desc: 'It pops up on the PC screen.', submit: 'Send note',
        fields: [{ name: 'title', label: 'Title', value: 'Willy', max: 80 }, { name: 'message', label: 'Message', type: 'textarea', required: true, max: 500, placeholder: 'Dinner is ready!' }]
      }).then(function (v) {
        if (v) runAction(card.id, 'notify', { title: v.title || 'Willy', message: v.message }, { btn: btn, label: 'Send note', ok: 'Note sent to ' + name + '.' });
      });
    case 'prev':
    case 'next':
    case 'playpause':
      return runAction(card.id, 'media_control', { action: act === 'playpause' ? 'play_pause' : act }, { btn: btn, label: 'Media control' });
    case 'mute':
      return toggleMute(card, btn);
    default:
      return null;
  }
}
function openFolder(card, folder) {
  var d = S.devices.get(card.id);
  var label = FOLDER_LABEL[folder] || folder;
  runAction(card.id, 'open_folder', { path: folder }, { label: 'Open folder', ok: 'Opened ' + label + ' on ' + devName(d) + '.' });
}
function sendVolume(card, level) {
  runAction(card.id, 'volume_control', { action: 'set', level: level, quiet: true }, { label: 'Volume' }).then(function (r) {
    card.volHold = Date.now() + 4000;
    if (r && r.success !== false && typeof r.muted === 'boolean') { card.muted = r.muted; renderMute(card); }
  });
}
function toggleMute(card, btn) {
  var want = !card.muted;
  return runAction(card.id, 'volume_control', { action: want ? 'mute' : 'unmute' }, { btn: btn, label: want ? 'Mute' : 'Unmute' }).then(function (r) {
    if (!r || r.success === false) return;
    card.muted = typeof r.muted === 'boolean' ? r.muted : want;
    card.volHold = Date.now() + 4000;
    if (isNum(r.level)) setRange(card.refs.vol, Math.round(r.level));
    setText(card.refs.volV, card.muted ? 'Muted' : card.refs.vol.value + '%');
    renderMute(card);
  });
}
function killProcess(card, p) {
  var d = S.devices.get(card.id);
  confirmDlg({
    title: 'End ' + p.name + '?',
    body: 'PID ' + p.pid + ' on ' + devName(d) + '. Unsaved work in this app will be lost.',
    confirm: 'End task', danger: true
  }).then(function (yes) {
    if (!yes) return;
    runAction(card.id, 'kill_process', { pid: p.pid }, { label: 'End task', ok: 'Ended ' + p.name + '.' }).then(function (r) {
      if (r && r.success !== false && card.procSort === 'memory') fetchProcs(card);
    });
  });
}
function setProcSort(card, sort) {
  if (sort !== 'cpu' && sort !== 'memory') return;
  card.procSort = sort;
  $$('[data-sort]', card.el).forEach(function (b) { b.setAttribute('aria-pressed', String(b.getAttribute('data-sort') === sort)); });
  card.procKey = '';
  if (sort === 'cpu') {
    stopProcPoll(card);
    var d = S.devices.get(card.id);
    renderProcs(card, (d && d.telemetry && Array.isArray(d.telemetry.top_processes)) ? d.telemetry.top_processes : [], !!(d && d.online));
  } else {
    setText(card.refs.procNote, 'Loading…');
    pollProcs(card);
  }
}
function fetchProcs(card) {
  var d = S.devices.get(card.id);
  if (!d || !d.online) return Promise.resolve();
  return api('/api/v1/devices/' + encodeURIComponent(card.id) + '/processes?sort=memory&limit=8').then(function (r) {
    if (card.procSort !== 'memory') return;
    if (r && r.success !== false && Array.isArray(r.processes)) renderProcs(card, r.processes, true);
    else setText(card.refs.procNote, resultError(r));
  }).catch(function (e) { if (card.procSort === 'memory') setText(card.refs.procNote, e.message); });
}
function pollProcs(card) {
  stopProcPoll(card);
  var loop = function () {
    if (card.procSort !== 'memory' || !card.refs.procPanel.open || !card.el.isConnected || !S.token) return;
    var go = document.hidden ? Promise.resolve() : fetchProcs(card);
    go.then(function () {
      if (card.procSort === 'memory' && card.refs.procPanel.open) card.procTimer = setTimeout(loop, 5000);
    });
  };
  loop();
}
function stopProcPoll(card) { clearTimeout(card.procTimer); card.procTimer = 0; }

/* ---------------------------------------------------------- phone wiring */
function wirePhone(card) {
  card.el.addEventListener('click', function (e) {
    var b = e.target.closest('[data-act]');
    if (!b || !card.el.contains(b) || b.disabled) return;
    phoneAction(card, b.getAttribute('data-act'), b);
  });
}
function looksLikeUrl(s) { return /^(https?:\/\/|www\.)\S+$/i.test(s) || /^[a-z0-9-]+(\.[a-z0-9-]+)+(\/\S*)?$/i.test(s); }
function phoneAction(card, act, btn) {
  var d = S.devices.get(card.id), name = devName(d);
  switch (act) {
    case 'ring':
      return runAction(card.id, 'ring_device', { message: 'Ringing from the Willy dashboard', duration_sec: 20 }, { btn: btn, label: 'Ring', ok: 'Ringing ' + name + '.' });
    case 'stopring':
      return runAction(card.id, 'stop_ring', {}, { btn: btn, label: 'Stop ring', ok: 'Stopped ringing ' + name + '.' });
    case 'vibrate':
      return runAction(card.id, 'vibrate', { ms: 800 }, { btn: btn, label: 'Vibrate', ok: 'Vibrating ' + name + '.' });
    case 'flash':
      var on = !card.flash;
      return runAction(card.id, 'flashlight', { on: on }, { btn: btn, label: 'Flashlight', ok: on ? 'Flashlight on.' : 'Flashlight off.' }).then(function (r) {
        if (r && r.success !== false) {
          card.flash = typeof r.on === 'boolean' ? r.on : on;
          card.flashHold = Date.now() + 8000; // until the phone's next heartbeat reports it
          updatePhone(card, S.devices.get(card.id) || d);
        }
      });
    case 'drop':
      return formDlg({
        title: 'Send to ' + name, desc: 'Links open on the phone. Notes pop up and are copied to its clipboard.', submit: 'Send to phone',
        fields: [{ name: 'content', label: 'Link or note', type: 'textarea', required: true, max: 2000, placeholder: 'https://… or a quick note' }]
      }).then(function (v) {
        if (!v) return;
        var c = v.content;
        var payload = looksLikeUrl(c) ? { url: /^https?:\/\//i.test(c) ? c : 'https://' + c, title: 'From Willy' } : { text: c, title: 'Note from Willy' };
        runAction(card.id, 'quickdrop', payload, { btn: btn, label: 'Send to phone', ok: payload.url ? 'Link sent to ' + name + '.' : 'Note sent to ' + name + '.' });
      });
    case 'clip':
      return formDlg({
        title: 'Copy text to ' + name, desc: 'Replaces the phone’s clipboard.', submit: 'Copy to phone',
        fields: [{ name: 'text', label: 'Text', type: 'textarea', required: true, max: 5000 }]
      }).then(function (v) {
        if (v) runAction(card.id, 'set_clipboard', { text: v.text }, { btn: btn, label: 'Push clipboard', ok: 'Copied to ' + name + '’s clipboard.' });
      });
    default:
      return null;
  }
}

/* --------------------------------------------------------- device actions */
function runAction(id, action, payload, o) {
  o = o || {};
  var btn = o.btn;
  busy(btn, true);
  return api('/api/v1/devices/' + encodeURIComponent(id) + '/action', { method: 'POST', body: { action: action, payload: payload || {}, source: CLIENT }, timeout: o.timeout || 30000 })
    .then(function (r) {
      if (!r || r.success === false) {
        toast(resultError(r), { type: 'error', title: (o.label || 'Action') + ' failed' });
        return r || null;
      }
      if (o.ok) toast(typeof o.ok === 'function' ? o.ok(r) : o.ok, { type: 'success' });
      return r;
    }, function (e) {
      if (e.status !== 401) toast(e.message, { type: 'error', title: (o.label || 'Action') + ' failed' });
      return null;
    })
    .then(function (r) { busy(btn, false); return r; });
}

/* ----------------------------------------------------------------- menus */
function toggleMenu(btn) {
  var menu = btn.parentNode.querySelector('.menu');
  var open = menu.hidden;
  closeMenus();
  if (!open) return;
  menu.hidden = false;
  menu.classList.remove('flip');
  btn.setAttribute('aria-expanded', 'true');
  var r = menu.getBoundingClientRect();
  if (r.right > document.documentElement.clientWidth - 8) menu.classList.add('flip');
  var first = menu.querySelector('button');
  if (first) first.focus();
}
function closeMenus(refocus) {
  $$('.menu').forEach(function (m) {
    if (m.hidden) return;
    var hadFocus = m.contains(document.activeElement);
    m.hidden = true;
    var b = m.parentNode.querySelector('[aria-expanded]');
    if (b) { b.setAttribute('aria-expanded', 'false'); if (refocus && hadFocus) b.focus(); }
  });
}

/* ================================================================ activity */
function mergeActivity(list, replace) {
  if (replace) {
    var incoming = new Map();
    list.forEach(function (e) { if (e && e.id) incoming.set(e.id, e); });
    S.activity.forEach(function (e) { if (!incoming.has(e.id)) incoming.set(e.id, e); });
    S.activity = Array.from(incoming.values());
  } else {
    list.forEach(function (e) {
      if (!e || !e.id) return;
      var i = S.activity.findIndex(function (x) { return x.id === e.id; });
      if (i >= 0) S.activity[i] = e; else S.activity.push(e);
    });
  }
  S.activity.sort(function (a, b) { return (b.ts || 0) - (a.ts || 0); });
  if (S.activity.length > 100) S.activity.length = 100;
  renderFeed();
}
function loadMoreActivity() {
  api('/api/v1/activity?limit=50').then(function (r) {
    if (r && Array.isArray(r.activity)) mergeActivity(r.activity, false);
    if (r && r.stats) { S.stats = r.stats; renderStats(); }
  }).catch(function () { /* the live stream still works */ });
}
function applyActivity(e) {
  if (!e || !e.id) return;
  var i = S.activity.findIndex(function (x) { return x.id === e.id; });
  if (i >= 0) S.activity[i] = e;
  else { S.activity.unshift(e); if (S.activity.length > 100) S.activity.length = 100; }
  var li = feedEls.get(e.id);
  if (li) fillAct(li, e);
  else {
    li = h('li', { class: 'act-item' + (REDUCED ? '' : ' is-new') });
    fillAct(li, e);
    feedEls.set(e.id, li);
    $('#feed').prepend(li);
    while (feedEls.size > 100) {
      var last = $('#feed').lastElementChild;
      if (!last) break;
      feedEls.forEach(function (v, k) { if (v === last) feedEls.delete(k); });
      last.remove();
    }
  }
  li.hidden = !passes(e);
  renderFilters();
  updateFeedEmpty();
  if (e.status !== 'running') scheduleStats();
}
function passes(e) { return S.filter === 'all' || String(e.source || 'api') === S.filter; }
function renderFeed() {
  var feed = $('#feed');
  feedEls.clear();
  var items = S.activity.map(function (e) {
    var li = h('li', { class: 'act-item' });
    fillAct(li, e);
    li.hidden = !passes(e);
    feedEls.set(e.id, li);
    return li;
  });
  feed.replaceChildren.apply(feed, items);
  renderFilters();
  updateFeedEmpty();
}
function updateFeedEmpty() {
  var any = S.activity.some(passes);
  var el = $('#feed-empty');
  el.hidden = any;
  if (!any) setText(el, S.filter === 'all' ? 'No commands yet. Anything you, your phone or your PC asks Willy shows up here live.' : 'Nothing from ' + sourceLabel(S.filter) + ' yet.');
}
function fillAct(li, e) {
  var status = e.status === 'running' ? 'running' : (e.status === 'error' || e.success === false ? 'error' : 'done');
  li.setAttribute('data-status', status);
  var ico = h('span', { class: 'ai-ico', title: status === 'running' ? 'Running' : status === 'error' ? 'Failed' : 'Done' },
    status === 'running' ? h('span', { class: 'spinner' }) : icon(status === 'error' ? 'x' : 'check'));
  var top = h('div', { class: 'ai-top' },
    h('span', { class: 'src' }, sourceLabel(e.source)),
    h('span', { class: 'ai-q', title: e.query || '' }, e.query || '—'),
    h('time', { class: 'ai-t', 'data-ts': isNum(e.ts) ? e.ts : '', 'data-fmt': 'short', title: isNum(e.ts) ? new Date(e.ts * 1000).toLocaleString() : '' }, agoShort(e.ts)));
  var body = h('div', { class: 'ai-body' }, top);
  if (e.reply) body.appendChild(h('p', { class: 'ai-reply', title: String(e.reply).length > 120 ? String(e.reply) : null }, e.reply));
  else if (status === 'running') body.appendChild(h('p', { class: 'ai-reply is-pending' }, 'Working…'));
  var meta = h('div', { class: 'ai-meta' });
  if (e.fast_path) meta.appendChild(fastBadge());
  var q = String(e.query || '').trim().toLowerCase();
  (e.tools || []).forEach(function (t) {
    t = typeof t === 'string' ? { name: t } : t;
    if (humanTool(t && t.name).toLowerCase() !== q) meta.appendChild(toolChip(t));  // direct actions already read as the query
  });
  if (status !== 'running') { meta.appendChild(ribbon(e.timings, e.latency_ms)); meta.appendChild(h('span', { class: 'lat' }, fmtMs(e.latency_ms))); }
  if (meta.childNodes.length) body.appendChild(meta);
  li.replaceChildren(ico, body);
}
function renderFilters() {
  var set = new Set();
  S.activity.forEach(function (e) { set.add(String(e.source || 'api')); });
  if (S.stats && S.stats.by_source) Object.keys(S.stats.by_source).forEach(function (k) { set.add(String(k)); });
  var list = Array.from(set).sort(function (a, b) {
    var ia = SOURCE_ORDER.indexOf(a), ib = SOURCE_ORDER.indexOf(b);
    if (ia < 0) ia = 99; if (ib < 0) ib = 99;
    return ia - ib || (a < b ? -1 : 1);
  });
  if (S.filter !== 'all' && list.indexOf(S.filter) < 0) list.push(S.filter);
  var key = list.join('|') + '#' + S.filter;
  if (key === S.sourcesKey) return;
  S.sourcesKey = key;
  var box = $('#filters');
  var focused = document.activeElement && box.contains(document.activeElement) ? document.activeElement.getAttribute('data-src') : null;
  var chips = ['all'].concat(list).map(function (src) {
    return h('button', { class: 'chip', type: 'button', 'data-src': src, 'aria-pressed': String(S.filter === src) }, src === 'all' ? 'All' : sourceLabel(src));
  });
  box.replaceChildren.apply(box, list.length ? chips : []);
  if (focused) { var f = box.querySelector('[data-src="' + (window.CSS && CSS.escape ? CSS.escape(focused) : focused) + '"]'); if (f) f.focus(); }
}
function setFilter(src) {
  S.filter = src;
  S.activity.forEach(function (e) { var li = feedEls.get(e.id); if (li) li.hidden = !passes(e); });
  S.sourcesKey = '';
  renderFilters();
  updateFeedEmpty();
}
function renderStats() {
  var s = S.stats || {};
  setText($('#st-total'), isNum(s.total) ? s.total : '—');
  setText($('#st-success'), s.total ? Math.round(100 * (s.succeeded || 0) / s.total) + '%' : '—');
  setText($('#st-p50'), fmtMsShort(s.latency_p50_ms));
  setText($('#st-p90'), fmtMsShort(s.latency_p90_ms));
  setText($('#st-fast'), s.total ? Math.round(100 * (s.fast_path || 0) / s.total) + '%' : '—');
  $('#st-fast').title = isNum(s.fast_path_p50_ms) ? 'Fast-path median ' + fmtMs(s.fast_path_p50_ms) : '';
  $('#st-p50').title = 'Median command latency';
  $('#st-p90').title = '90th percentile command latency';
}

/* -------------------------------------------------- shared reply widgets */
function fastBadge() {
  return h('span', { class: 'fast', title: 'Fast path: answered without the AI round-trip' }, icon('bolt'), 'Fast path');
}
function toolChip(t) {
  var failed = t && t.success === false;
  var title = null;
  if (t && t.args && typeof t.args === 'object') { try { title = JSON.stringify(t.args); } catch (e) { title = null; } }
  return h('span', { class: 'tool' + (failed ? ' is-fail' : ''), title: title ? (failed ? 'Failed · ' : '') + title : (failed ? 'Failed' : null) }, humanTool(t && t.name));
}
function breakdownText(tm, total) {
  tm = tm || {};
  var parts = SEGMENTS.filter(function (s) { return isNum(tm[s[0]]); }).map(function (s) { return s[2] + ' ' + fmtMs(tm[s[0]]); });
  if (isNum(total)) parts.push('Total ' + fmtMs(total));
  return parts.length ? parts.join(' · ') : 'No timing data';
}
function ribbon(tm, total) {
  tm = tm || {};
  if (!isNum(total)) total = isNum(tm.total_ms) ? tm.total_ms : null;
  var wrap = h('span', { class: 'ribbon', role: 'img', 'aria-label': 'Latency: ' + breakdownText(tm, total), title: breakdownText(tm, total) });
  var fill = h('span', { class: 'rb-fill' });
  wrap.appendChild(fill);
  if (!isNum(total)) { wrap.classList.add('is-empty'); fill.style.width = '0'; return wrap; }
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

/* ================================================================= console */
function loadConv() {
  try { var v = JSON.parse(session.get(KEYS.conv) || '[]'); return Array.isArray(v) ? v.slice(-40) : []; } catch (e) { return []; }
}
function saveConv() {
  var slim = S.conv.filter(function (m) { return m.role !== 'pending'; }).slice(-40).map(function (m) {
    var c = Object.assign({}, m); delete c.audio; delete c.el; return c;
  });
  session.set(KEYS.conv, JSON.stringify(slim));
}
function renderConv() {
  var box = $('#conv');
  var empty = $('#conv-empty');
  var nodes = S.conv.map(function (m) { return m.el = msgEl(m); });
  box.replaceChildren.apply(box, [empty].concat(nodes));
  empty.hidden = S.conv.length > 0;
  box.scrollTop = box.scrollHeight;
}
function addMsg(m) {
  var box = $('#conv');
  var nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  S.conv.push(m);
  if (S.conv.length > 80) { var old = S.conv.shift(); if (old.el) old.el.remove(); }
  m.el = msgEl(m);
  box.appendChild(m.el);
  $('#conv-empty').hidden = true;
  if (nearBottom || m.role !== 'willy') box.scrollTop = box.scrollHeight;
  if (m.role !== 'pending') saveConv();
  return m;
}
function removeMsg(m) {
  var i = S.conv.indexOf(m);
  if (i >= 0) S.conv.splice(i, 1);
  if (m.el) m.el.remove();
}
function avatar() {
  var ns = 'http://www.w3.org/2000/svg';
  var svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('class', 'avatar'); svg.setAttribute('aria-hidden', 'true');
  var use = document.createElementNS(ns, 'use'); use.setAttribute('href', '#i-logo'); svg.appendChild(use);
  return svg;
}
function msgEl(m) {
  if (m.role === 'user') return h('div', { class: 'msg msg-user' }, h('div', { class: 'bubble' }, m.text));
  if (m.role === 'pending') {
    return h('div', { class: 'msg msg-willy' }, avatar(), h('div', { class: 'msg-col' },
      h('div', { class: 'bubble' }, h('span', { class: 'typing', 'aria-hidden': 'true' }, h('i'), h('i'), h('i')), h('span', { class: 'sr-only' }, 'Willy is working…'))));
  }
  var meta = h('div', { class: 'msg-meta' });
  if (m.fast) meta.appendChild(fastBadge());
  (m.tools || []).forEach(function (t) { meta.appendChild(toolChip(t)); });
  if (isNum(m.latency) || m.timings) { meta.appendChild(ribbon(m.timings, m.latency)); meta.appendChild(h('span', { class: 'lat', title: breakdownText(m.timings, m.latency) }, fmtMs(m.latency))); }
  if (m.audio) {
    var play = h('button', { class: 'icon-btn sm', type: 'button', title: 'Play reply', 'aria-label': 'Play reply' }, icon('vol'));
    play.addEventListener('click', function () { playAudio(m.audio, true); });
    meta.appendChild(play);
  }
  return h('div', { class: 'msg msg-willy' + (m.ok === false ? ' is-error' : '') }, avatar(),
    h('div', { class: 'msg-col' }, h('div', { class: 'bubble' }, m.text), meta.childNodes.length ? meta : null));
}
function pushHistory(text) {
  S.hist = S.hist.filter(function (x) { return x !== text; });
  S.hist.push(text);
  if (S.hist.length > 30) S.hist = S.hist.slice(-30);
  local.set(KEYS.hist, JSON.stringify(S.hist));
  S.histIdx = -1; S.draft = '';
}
function sendCommand(text) {
  text = String(text || '').trim();
  if (!text || !S.token) return Promise.resolve(null);
  pushHistory(text);
  addMsg({ role: 'user', text: text, ts: Date.now() });
  var pending = addMsg({ role: 'pending' });
  var speak = $('#speak').checked;
  if (speak) unlockAudio();
  var body = { query: text, return_audio: speak, session_id: SESSION_ID, source: CLIENT };
  var target = $('#target').value;
  if (target) body.target_device_id = target;
  return api('/api/v1/command', { method: 'POST', body: body, timeout: 120000 }).then(function (r) {
    removeMsg(pending);
    r = r || {};
    var tm = r.timings || {};
    var m = {
      role: 'willy', text: r.reply || 'Done.', ok: r.success !== false, fast: !!r.fast_path,
      tools: Array.isArray(r.tools) ? r.tools : [], timings: tm,
      latency: isNum(tm.total_ms) ? tm.total_ms : (isNum(r.duration_sec) ? r.duration_sec * 1000 : null), ts: Date.now()
    };
    if (r.audio_base64) { m.audio = r.audio_base64; playAudio(r.audio_base64); }
    addMsg(m);
    return r;
  }, function (e) {
    removeMsg(pending);
    if (e.status === 401) return null;
    addMsg({ role: 'willy', text: e.message, ok: false, ts: Date.now() });
    return { success: false, reply: e.message };
  });
}
var player = null, audioUnlocked = false;
function getPlayer() { if (!player) { player = new Audio(); player.preload = 'auto'; } return player; }
function unlockAudio() {
  if (audioUnlocked) return;
  audioUnlocked = true;
  try { var p = getPlayer(); p.src = SILENT_WAV; var pr = p.play(); if (pr && pr.catch) pr.catch(function () { /* ignore */ }); } catch (e) { /* ignore */ }
}
function playAudio(b64, fromClick) {
  try {
    var p = getPlayer();
    p.pause();
    p.src = 'data:audio/mpeg;base64,' + b64;
    var pr = p.play();
    if (pr && pr.catch) pr.catch(function () { if (!fromClick) toast('The browser blocked audio. Use the speaker button on the reply to play it.', { type: 'warn' }); });
  } catch (e) { /* ignore */ }
}
function wireConsole() {
  try { var hist = JSON.parse(local.get(KEYS.hist) || '[]'); S.hist = Array.isArray(hist) ? hist.filter(function (x) { return typeof x === 'string'; }).slice(-30) : []; } catch (e) { S.hist = []; }
  var speak = $('#speak');
  speak.checked = local.get(KEYS.speak) === '1';
  speak.addEventListener('change', function () { local.set(KEYS.speak, speak.checked ? '1' : '0'); if (speak.checked) unlockAudio(); });
  var input = $('#prompt');
  // Keep the conversation pinned to the newest message across resizes / rotations.
  var conv = $('#conv');
  var pinned = true;
  conv.addEventListener('scroll', function () { pinned = conv.scrollHeight - conv.scrollTop - conv.clientHeight < 40; }, { passive: true });
  if (window.ResizeObserver) new ResizeObserver(function () { if (pinned) conv.scrollTop = conv.scrollHeight; }).observe(conv);
  $('#prompt-form').addEventListener('submit', function (e) {
    e.preventDefault();
    var v = input.value;
    if (!v.trim()) return;
    input.value = '';
    sendCommand(v);
  });
  input.addEventListener('keydown', function (e) {
    if (e.key === 'ArrowUp') {
      if (!S.hist.length) return;
      e.preventDefault();
      if (S.histIdx === -1) { S.draft = input.value; S.histIdx = S.hist.length - 1; }
      else if (S.histIdx > 0) S.histIdx--;
      input.value = S.hist[S.histIdx];
      setTimeout(function () { input.setSelectionRange(input.value.length, input.value.length); }, 0);
    } else if (e.key === 'ArrowDown') {
      if (S.histIdx === -1) return;
      e.preventDefault();
      if (S.histIdx < S.hist.length - 1) { S.histIdx++; input.value = S.hist[S.histIdx]; }
      else { S.histIdx = -1; input.value = S.draft; }
    } else if (e.key === 'Escape') {
      S.histIdx = -1; input.value = '';
    }
  });
  $('#chips').addEventListener('click', function (e) {
    var chip = e.target.closest('[data-cmd]');
    if (chip) sendCommand(chip.getAttribute('data-cmd'));
  });
  $('#new-conv').addEventListener('click', function () {
    var btn = this;
    busy(btn, true);
    api('/api/v1/session/reset', { method: 'POST', body: { session_id: SESSION_ID } }).then(function () {
      S.conv = []; saveConv(); renderConv();
      toast('Willy has forgotten this conversation.', { type: 'success', title: 'New conversation' });
    }, function (e) {
      if (e.status !== 401) toast(e.message, { type: 'error', title: 'Couldn’t start a new conversation' });
    }).then(function () { busy(btn, false); });
  });
}

/* =============================================================== reminders */
function loadRemindersSoon(delay) {
  clearTimeout(S.remTimer);
  S.remTimer = setTimeout(loadReminders, delay || 0);
}
function loadReminders() {
  if (!S.token) return;
  Promise.all([api('/api/v1/reminders'), api('/api/v1/alarms')]).then(function (res) {
    applyReminderLists(res[0] && res[0].reminders, res[1] && res[1].alarms);
  }).catch(function (e) { if (e.status !== 401) setText($('#rem-empty'), 'Couldn’t load reminders: ' + e.message); });
}
function applyReminderLists(rems, alarms) {
  if (Array.isArray(rems)) { S.reminders = rems; renderReminders(); }
  if (Array.isArray(alarms)) { S.alarms = alarms; renderAlarms(); }
}
function remSortKey(r) {
  var mins = clockMinutes(r.time);
  return (r.completed ? '1' : '0') + (r.date || '9999-99-99') + pad2(Math.floor((mins == null ? 1439 : mins) / 60)) + pad2((mins == null ? 1439 : mins) % 60);
}
function keepFocus(container, fn) {
  var a = document.activeElement;
  var key = a && container.contains(a) ? a.getAttribute('data-key') : null;
  fn();
  if (key) { var n = container.querySelector('[data-key="' + (window.CSS && CSS.escape ? CSS.escape(key) : key) + '"]'); if (n) n.focus(); }
}
function renderReminders() {
  var list = $('#rem-list');
  var items = S.reminders.slice().sort(function (a, b) { var ka = remSortKey(a), kb = remSortKey(b); return ka < kb ? -1 : ka > kb ? 1 : 0; });
  keepFocus(list, function () {
    list.replaceChildren.apply(list, items.map(function (r) {
      var id = String(r.id);
      var cb = h('input', { type: 'checkbox', 'data-key': 'done:' + id, 'aria-label': 'Done: ' + (r.text || 'reminder') });
      cb.checked = !!r.completed;
      cb.addEventListener('change', function () { setReminderDone(id, cb.checked); });
      var when = h('p', { class: 'rwhen' }, [dayLabel(r.date), clockLabel(r.time)].filter(Boolean).join(' · '));
      if (r.missed && !r.completed) when.appendChild(h('span', { class: 'tag tag-missed' }, 'Missed'));
      else if (r.fired_at && !r.completed) when.appendChild(h('span', { class: 'tag tag-rang' }, 'Rang'));
      var del = h('button', { class: 'icon-btn sm', type: 'button', 'data-key': 'del:' + id, title: 'Delete reminder', 'aria-label': 'Delete reminder: ' + (r.text || '') }, icon('trash'));
      del.addEventListener('click', function () { deleteReminder(id); });
      return h('li', { class: 'ritem' + (r.completed ? ' done' : '') },
        h('label', { class: 'check' }, cb, h('span', { class: 'check-box' }, icon('check'))),
        h('div', null, h('p', { class: 'rtext' }, r.text || 'Reminder'), when), del);
    }));
  });
  var open = S.reminders.filter(function (r) { return !r.completed; }).length;
  setText($('#rem-n'), open || '');
  $('#rem-empty').hidden = S.reminders.length > 0;
  setText($('#rem-empty'), 'No reminders. Add one below, or tell Willy “remind me to…”.');
}
function renderAlarms() {
  var list = $('#alm-list');
  var items = S.alarms.slice().sort(function (a, b) { return (clockMinutes(a.time) || 0) - (clockMinutes(b.time) || 0); });
  keepFocus(list, function () {
    list.replaceChildren.apply(list, items.map(function (a) {
      var id = String(a.id);
      var on = a.enabled !== false;
      var sw = h('input', { type: 'checkbox', 'data-key': 'on:' + id, 'aria-label': 'Alarm ' + clockLabel(a.time) + ' enabled' });
      sw.checked = on;
      sw.addEventListener('change', function () { setAlarmEnabled(id, sw.checked); });
      var del = h('button', { class: 'icon-btn sm', type: 'button', 'data-key': 'del:' + id, title: 'Delete alarm', 'aria-label': 'Delete alarm ' + clockLabel(a.time) }, icon('trash'));
      del.addEventListener('click', function () { deleteAlarm(id); });
      return h('li', { class: 'ritem aitem' + (on ? '' : ' is-off') },
        h('div', null, h('p', { class: 'atime' }, clockLabel(a.time)), h('p', { class: 'alabel' }, (a.label || 'Alarm') + ' · every day')),
        h('label', { class: 'switch', title: on ? 'On' : 'Off' }, sw, h('span', { class: 'switch-track' })), del);
    }));
  });
  var n = S.alarms.filter(function (a) { return a.enabled !== false; }).length;
  setText($('#alm-n'), n || '');
  $('#alm-empty').hidden = S.alarms.length > 0;
}
function setReminderDone(id, done) {
  var r = S.reminders.find(function (x) { return String(x.id) === id; });
  if (r) { r.completed = done; renderReminders(); }
  api('/api/v1/reminders/' + encodeURIComponent(id) + '/complete', { method: 'POST', body: { completed: done } }).catch(function (e) {
    if (r) { r.completed = !done; renderReminders(); }
    if (e.status !== 401) toast(e.message, { type: 'error', title: 'Couldn’t update the reminder' });
  });
}
function deleteReminder(id) {
  var i = S.reminders.findIndex(function (x) { return String(x.id) === id; });
  var item = i >= 0 ? S.reminders.splice(i, 1)[0] : null;
  renderReminders();
  api('/api/v1/reminders/' + encodeURIComponent(id), { method: 'DELETE' }).catch(function (e) {
    if (e.status === 404) return;
    if (item) { S.reminders.splice(i, 0, item); renderReminders(); }
    if (e.status !== 401) toast(e.message, { type: 'error', title: 'Couldn’t delete the reminder' });
  });
}
function setAlarmEnabled(id, on) {
  var a = S.alarms.find(function (x) { return String(x.id) === id; });
  if (a) { a.enabled = on; renderAlarms(); }
  api('/api/v1/alarms/' + encodeURIComponent(id) + '/toggle', { method: 'POST', body: { enabled: on } }).catch(function (e) {
    if (a) { a.enabled = !on; renderAlarms(); }
    if (e.status !== 401) toast(e.message, { type: 'error', title: 'Couldn’t update the alarm' });
  });
}
function deleteAlarm(id) {
  var i = S.alarms.findIndex(function (x) { return String(x.id) === id; });
  var item = i >= 0 ? S.alarms.splice(i, 1)[0] : null;
  renderAlarms();
  api('/api/v1/alarms/' + encodeURIComponent(id), { method: 'DELETE' }).catch(function (e) {
    if (e.status === 404) return;
    if (item) { S.alarms.splice(i, 0, item); renderAlarms(); }
    if (e.status !== 401) toast(e.message, { type: 'error', title: 'Couldn’t delete the alarm' });
  });
}
function wireReminders() {
  $$('[data-tab]').forEach(function (b) {
    b.addEventListener('click', function () {
      var tab = b.getAttribute('data-tab');
      $$('[data-tab]').forEach(function (x) { x.setAttribute('aria-selected', String(x === b)); });
      $('#tab-rem').hidden = tab !== 'rem';
      $('#tab-alm').hidden = tab !== 'alm';
    });
  });
  $('#rem-time').value = nextHour();
  $('#alm-time').value = '07:00';
  $('#rem-day').addEventListener('change', function () {
    var pick = this.value === 'date';
    $('#rem-date').hidden = !pick;
    if (pick) { var tmr = new Date(); tmr.setDate(tmr.getDate() + 1); if (!$('#rem-date').value) $('#rem-date').value = localISO(tmr); $('#rem-date').min = localISO(new Date()); }
  });
  $('#rem-form').addEventListener('submit', function (e) {
    e.preventDefault();
    var err = $('#rem-err');
    err.hidden = true;
    var text = $('#rem-text').value.trim(), time = $('#rem-time').value, day = $('#rem-day').value;
    var fail = function (msg) { err.textContent = msg; err.hidden = false; };
    if (!text) { fail('Write what Willy should remind you about.'); $('#rem-text').focus(); return; }
    if (!time) { fail('Pick a time.'); return; }
    var date, iso;
    if (day === 'tomorrow') { date = 'tomorrow'; var t = new Date(); t.setDate(t.getDate() + 1); iso = localISO(t); }
    else if (day === 'date') { date = iso = $('#rem-date').value; if (!date) { fail('Pick a date.'); return; } }
    else { date = iso = localISO(new Date()); }
    var p = iso.split('-').map(Number), tp = time.split(':').map(Number);
    if (new Date(p[0], p[1] - 1, p[2], tp[0], tp[1]).getTime() <= Date.now()) { fail('That time has already passed. Pick a later time or another day.'); return; }
    var btn = $('#rem-form button[type=submit]');
    busy(btn, true);
    api('/api/v1/reminders', { method: 'POST', body: { text: text, time: to12h(time), date: date } }).then(function (r) {
      if (r && r.reminder && !S.reminders.some(function (x) { return x.id === r.reminder.id; })) { S.reminders.push(r.reminder); renderReminders(); }
      $('#rem-text').value = '';
      toast(text + ' — ' + [dayLabel(r && r.reminder ? r.reminder.date : iso), clockLabel(to12h(time))].join(', ') + '.', { type: 'success', title: 'Reminder added' });
    }, function (e2) { if (e2.status !== 401) fail(e2.message); }).then(function () { busy(btn, false); });
  });
  $('#alm-form').addEventListener('submit', function (e) {
    e.preventDefault();
    var err = $('#alm-err');
    err.hidden = true;
    var time = $('#alm-time').value;
    if (!time) { err.textContent = 'Pick a time.'; err.hidden = false; return; }
    var label = $('#alm-label').value.trim() || 'Alarm';
    var btn = $('#alm-form button[type=submit]');
    busy(btn, true);
    api('/api/v1/alarms', { method: 'POST', body: { time: to12h(time), label: label } }).then(function (r) {
      if (r && r.alarm && !S.alarms.some(function (x) { return x.id === r.alarm.id; })) { S.alarms.push(r.alarm); renderAlarms(); }
      $('#alm-label').value = '';
      toast(label + ' rings every day at ' + clockLabel(to12h(time)) + '.', { type: 'success', title: 'Alarm set' });
    }, function (e2) { if (e2.status !== 401) { err.textContent = e2.message; err.hidden = false; } }).then(function () { busy(btn, false); });
  });
  updateNotifBtn();
  $('#notif-btn').addEventListener('click', function () {
    if (!('Notification' in window)) return;
    var done = function (p) {
      updateNotifBtn();
      if (p === 'granted') toast('You’ll get a desktop alert when a reminder or alarm is due.', { type: 'success', title: 'Desktop alerts on' });
      else if (p === 'denied') toast('Alerts are blocked. Allow notifications for this site in your browser settings.', { type: 'warn', title: 'Desktop alerts off' });
    };
    try {
      var pr = Notification.requestPermission(done);
      if (pr && pr.then) pr.then(done);
    } catch (e) { done(Notification.permission); }
  });
}
function updateNotifBtn() {
  $('#notif-btn').hidden = !('Notification' in window) || Notification.permission !== 'default';
}
var actx = null;
function unlockBeep() {
  if (actx) { if (actx.state === 'suspended') actx.resume().catch(function () { /* ignore */ }); return; }
  var AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return;
  try { actx = new AC(); } catch (e) { actx = null; }
}
function beepAlert() {
  if (!actx) return;
  try {
    if (actx.state === 'suspended') actx.resume();
    var t = actx.currentTime + 0.03;
    [0, 0.2, 0.4].forEach(function (d, i) {
      var o = actx.createOscillator(), g = actx.createGain();
      o.type = 'sine';
      o.frequency.value = i === 2 ? 1175 : 880;
      g.gain.setValueAtTime(0.0001, t + d);
      g.gain.exponentialRampToValueAtTime(0.2, t + d + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t + d + 0.16);
      o.connect(g); g.connect(actx.destination);
      o.start(t + d); o.stop(t + d + 0.18);
    });
  } catch (e) { /* ignore */ }
}
function onDue(m) {
  var kind = m.type === 'morning_call_due' ? 'Morning call' : (m.kind === 'alarm' ? 'Alarm' : 'Reminder');
  var title = String(m.title || kind);
  var msg = String(m.message || '');
  toast(msg, { type: 'alert', title: title, timeout: 20000, icon: 'bell' });
  beepAlert();
  if ('Notification' in window && Notification.permission === 'granted') {
    try {
      var ref = m.reminder || m.alarm || {};
      new Notification('Willy · ' + title, { body: msg, tag: 'willy-' + (ref.id || kind), renotify: true });
    } catch (e) { /* not supported here (e.g. Android Chrome) */ }
  }
  loadRemindersSoon(600);
}

/* ============================================================= quick tools */
function wireTools() {
  $$('[data-tool]').forEach(function (b) {
    b.addEventListener('click', function () {
      var tool = b.getAttribute('data-tool');
      $$('[data-tool]').forEach(function (x) { x.setAttribute('aria-selected', String(x === b)); });
      $('#meet-form').hidden = tool !== 'meet';
      $('#msg-form').hidden = tool !== 'msg';
    });
  });
  $('#meet-date').value = localISO(new Date());
  $('#meet-time').value = nextHour();
  var result = function (el, r) {
    if (!r) { el.hidden = true; return; }
    el.textContent = r.reply || (r.success === false ? 'Something went wrong.' : 'Done.');
    el.classList.toggle('is-error', r.success === false);
    el.hidden = false;
  };
  $('#meet-form').addEventListener('submit', function (e) {
    e.preventDefault();
    var title = $('#meet-title').value.trim() || 'Team sync';
    var platform = $('#meet-platform').value;
    var date = $('#meet-date').value || localISO(new Date());
    var time = $('#meet-time').value || nextHour();
    var dur = Math.max(5, Math.min(480, parseInt($('#meet-dur').value, 10) || 30));
    var p = date.split('-').map(Number);
    var weekday = new Date(p[0], p[1] - 1, p[2]).toLocaleDateString('en-US', { weekday: 'long' });
    var prompt = 'Schedule a ' + platform + ' meeting titled "' + title + '" on ' + weekday + ' ' + date + ' at ' + clockLabel(to12h(time)) + ' for ' + dur + ' minutes.';
    var btn = $('#meet-form button[type=submit]');
    busy(btn, true);
    $('#meet-result').hidden = true;
    sendCommand(prompt).then(function (r) { result($('#meet-result'), r); busy(btn, false); });
  });
  $('#msg-form').addEventListener('submit', function (e) {
    e.preventDefault();
    var channel = $('#msg-channel').value;
    var to = $('#msg-to').value.trim(), body = $('#msg-body').value.trim();
    var out = $('#msg-result');
    if (!to || !body) { result(out, { success: false, reply: 'Add who it’s for and what to say.' }); return; }
    var prompt = 'Send a ' + channel + ' message to "' + to + '" saying: "' + body + '"';
    var btn = $('#msg-form button[type=submit]');
    busy(btn, true);
    out.hidden = true;
    sendCommand(prompt).then(function (r) { result(out, r); busy(btn, false); });
  });
}

/* ============================================================ live screen */
var SCR = { dev: null, gen: 0, paused: false, autoPaused: false, q: 'med', mon: 'active', times: [], url: null, ctrl: null };
function openScreen(id) {
  var d = S.devices.get(id);
  if (!d) return;
  SCR.dev = id; SCR.paused = false; SCR.autoPaused = false; SCR.times = []; SCR.mon = 'active';
  var q = local.get(KEYS.scrq);
  SCR.q = QUALITY[q] ? q : 'med';
  setText($('#scr-dev'), devName(d));
  setText($('#scr-label'), 'Waiting for the first frame…');
  setText($('#scr-fps'), '— fps');
  setText($('#scr-lat'), '— ms');
  var n = d.specs && isNum(d.specs.monitors) ? d.specs.monitors : 0;
  var sel = $('#scr-mon');
  sel.hidden = !(n > 1);
  if (n > 1) {
    var opts = [h('option', { value: 'active' }, 'Active monitor'), h('option', { value: 'all' }, 'All monitors')];
    for (var i = 1; i <= n; i++) opts.push(h('option', { value: String(i) }, 'Monitor ' + i));
    sel.replaceChildren.apply(sel, opts);
    sel.value = 'active';
  }
  renderScreenControls();
  var img = $('#scr-img');
  img.hidden = true;
  img.removeAttribute('src');
  showScreenMsg('Connecting to the screen…');
  var dlg = $('#screen');
  if (!dlg.open) { if (typeof dlg.showModal === 'function') dlg.showModal(); else dlg.setAttribute('open', ''); }
  startScreenLoop();
}
function renderScreenControls() {
  $$('#scr-quality [data-q]').forEach(function (b) { b.setAttribute('aria-pressed', String(b.getAttribute('data-q') === SCR.q)); });
  var pause = $('#scr-pause');
  pause.querySelector('use').setAttribute('href', SCR.paused ? '#i-play' : '#i-pause');
  setText(pause.querySelector('span'), SCR.paused ? 'Resume' : 'Pause');
}
function showScreenMsg(text) { var m = $('#scr-msg'); setText(m, text); m.hidden = !text; }
function startScreenLoop() {
  SCR.gen++;
  if (SCR.ctrl) SCR.ctrl.abort();
  SCR.times = [];
  screenLoop(SCR.gen);
}
function screenUrl(q, w) {
  return '/api/v1/devices/' + encodeURIComponent(SCR.dev) + '/screen?quality=' + q + '&max_width=' + w + '&monitor=' + encodeURIComponent(SCR.mon);
}
function screenLoop(gen) {
  var fails = 0;
  var next = function () {
    if (gen !== SCR.gen || !$('#screen').open || SCR.paused || !S.token) return;
    if (document.hidden) { SCR.autoPaused = true; return; }
    var Q = QUALITY[SCR.q] || QUALITY.med;
    var t0 = performance.now();
    var ctrl = new AbortController();
    SCR.ctrl = ctrl;
    api(screenUrl(Q.q, Q.w), { signal: ctrl.signal, timeout: 15000 }).then(function (r) {
      if (gen !== SCR.gen) return null;
      if (!r || r.success === false || !r.image_base64) throw ApiError(resultError(r), 0, r);
      return showFrame(r, gen).then(function () {
        if (gen !== SCR.gen) return;
        fails = 0;
        var now = performance.now();
        SCR.times.push(now);
        if (SCR.times.length > 8) SCR.times.shift();
        var fps = SCR.times.length > 1 ? (SCR.times.length - 1) / ((now - SCR.times[0]) / 1000) : 0;
        setText($('#scr-fps'), fps ? fps.toFixed(1) + ' fps' : '— fps');
        setText($('#scr-lat'), Math.round(now - t0) + ' ms');
        var res = isNum(r.width) && isNum(r.height) ? r.width + '×' + r.height : '';
        setText($('#scr-label'), [r.label, res, (QUALITY[SCR.q] ? { low: 'Low', med: 'Medium', high: 'High' }[SCR.q] : '') + ' quality'].filter(Boolean).join(' · '));
        next();
      });
    }).catch(function (e) {
      if (gen !== SCR.gen || (e && e.name === 'AbortError') || (e && e.status === 401)) return;
      fails++;
      showScreenMsg((e && e.message) || 'The frame didn’t arrive.');
      setTimeout(next, Math.min(4000, 500 * Math.pow(2, Math.min(fails, 3))));
    });
  };
  next();
}
function b64ToBytes(b64) {
  var bin = atob(b64), n = bin.length, out = new Uint8Array(n);
  for (var i = 0; i < n; i++) out[i] = bin.charCodeAt(i);
  return out;
}
function showFrame(r, gen) {
  var url;
  try { url = URL.createObjectURL(new Blob([b64ToBytes(r.image_base64)], { type: 'image/jpeg' })); } catch (e) { return Promise.reject(ApiError('The frame was damaged.', 0)); }
  var pre = new Image();
  pre.src = url;
  var ready = pre.decode ? pre.decode().catch(function () { /* show anyway */ }) : Promise.resolve();
  return ready.then(function () {
    if (gen !== SCR.gen) { URL.revokeObjectURL(url); return; }
    var old = SCR.url;
    SCR.url = url;
    var img = $('#scr-img');
    img.src = url;
    img.hidden = false;
    showScreenMsg('');
    if (old) setTimeout(function () { URL.revokeObjectURL(old); }, 1500);
  });
}
function closeScreen() {
  SCR.gen++;
  if (SCR.ctrl) SCR.ctrl.abort();
  var dlg = $('#screen');
  if (dlg.open) { if (typeof dlg.close === 'function') dlg.close(); else dlg.removeAttribute('open'); }
  if (SCR.url) { var u = SCR.url; SCR.url = null; setTimeout(function () { URL.revokeObjectURL(u); }, 500); }
}
function openFullSize() {
  if (!SCR.dev) return;
  var w = window.open('', '_blank');
  if (!w) { toast('Allow pop-ups for this site to open the full-size capture.', { type: 'warn' }); return; }
  try {
    w.document.title = 'Willy · full-size screen';
    w.document.body.style.cssText = 'margin:0;min-height:100vh;display:grid;place-items:center;background:#070913;color:#94A3B8;font:14px system-ui,sans-serif';
    w.document.body.textContent = 'Capturing the full-size screen…';
  } catch (e) { /* ignore */ }
  api(screenUrl(85, 1920), { timeout: 20000 }).then(function (r) {
    if (!r || r.success === false || !r.image_base64) throw ApiError(resultError(r), 0);
    var url = URL.createObjectURL(new Blob([b64ToBytes(r.image_base64)], { type: 'image/jpeg' }));
    w.location.href = url;
    setTimeout(function () { URL.revokeObjectURL(url); }, 120000);
  }).catch(function (e) {
    try { w.close(); } catch (x) { /* ignore */ }
    if (e.status !== 401) toast(e.message || 'Couldn’t capture the screen.', { type: 'error', title: 'Full-size capture failed' });
  });
}
function wireScreen() {
  var dlg = $('#screen');
  dlg.addEventListener('close', function () { SCR.gen++; if (SCR.ctrl) SCR.ctrl.abort(); });
  dlg.addEventListener('click', function (e) { if (e.target === dlg) closeScreen(); });
  $('#scr-close').addEventListener('click', closeScreen);
  $('#scr-img').addEventListener('click', openFullSize);
  $('#scr-full').addEventListener('click', openFullSize);
  $('#scr-pause').addEventListener('click', function () {
    SCR.paused = !SCR.paused;
    renderScreenControls();
    if (SCR.paused) { SCR.gen++; if (SCR.ctrl) SCR.ctrl.abort(); setText($('#scr-fps'), 'paused'); }
    else startScreenLoop();
  });
  $('#scr-quality').addEventListener('click', function (e) {
    var b = e.target.closest('[data-q]');
    if (!b) return;
    SCR.q = b.getAttribute('data-q');
    local.set(KEYS.scrq, SCR.q);
    renderScreenControls();
    if (!SCR.paused) startScreenLoop();
  });
  $('#scr-mon').addEventListener('change', function () {
    SCR.mon = this.value || 'active';
    if (!SCR.paused) startScreenLoop();
  });
}

/* =================================================================== server */
// The Willy server (device_type "server"): a Linux machine whose agent connects like the PC.
// Its card shows live stats. The Server panel shows the hub's health check (problems,
// websites, services) with apps and containers, plus files, a terminal and processes; every
// action goes through the same device-action route as the PC.
var SRV_TABS = ['overview', 'files', 'terminal', 'processes', 'history', 'updates', 'storage', 'network', 'services', 'logs'];
var UNIT_VERBS = { restart: ['Restart', 'Restarted'], stop: ['Stop', 'Stopped'], start: ['Start', 'Started'] };
var UNIT_EMPTY = { app: 'No pm2 apps.', service: 'No services watched.', container: 'No Docker containers.' };
function newServerState() {
  return {
    id: null, online: false, name: '', tab: 'overview',
    health: null, healthErr: '', healthAt: 0, agent: null, agentErr: '', loading: null, recheck: false, poll: 0,
    busy: new Set(),
    files: { path: '', parent: null, entries: [], drives: [], loaded: false, loading: false, err: '', gen: 0, want: '', truncated: false },
    procSort: 'memory', procs: [], procErr: '', procAt: 0, procLoaded: false, procLoading: false, procGen: 0,
    runs: [], running: false, hist: null, histIdx: -1, draft: '',
    sys: newSysState()
  };
}
var SV = newServerState();

function serverDevice() {
  var best = null;
  S.devices.forEach(function (d) {
    if (!d || d.device_type !== 'server') return;
    if (!best || (d.online && !best.online) || (!!d.online === !!best.online && (d.last_seen || 0) > (best.last_seen || 0))) best = d;
  });
  return best;
}
function devAct(id, action, payload, timeout) {
  return api('/api/v1/devices/' + encodeURIComponent(id) + '/action', { method: 'POST', body: { action: action, payload: payload || {}, source: CLIENT }, timeout: timeout || 30000 });
}
function unitHealth(state) {
  var s = String(state || '').toLowerCase();
  if (/^(online|active|running|up)/.test(s)) return 'ok';
  if (/error|fail|crash|restarting/.test(s) || s === 'dead') return 'bad';
  if (/launch|start|activating|reload|paused/.test(s)) return 'warn';
  return 'idle';
}
function hchip(text, level, title) { return h('span', { class: 'hchip ' + level, title: title || null }, text); }
function certLevel(days) { if (!isNum(days)) return 'idle'; if (days <= 7) return 'bad'; if (days <= 21) return 'warn'; return 'ok'; }
function certText(days) {
  if (!isNum(days)) return 'No cert info';
  if (days < 0) return 'Expired';
  if (days === 0) return 'Expires today';
  return days + (days === 1 ? ' day left' : ' days left');
}
function problemLabel(key) {
  var i = key.indexOf(':'), kind = i < 0 ? key : key.slice(0, i), what = i < 0 ? '' : key.slice(i + 1);
  var map = { site: 'Website ' + what, cert: 'Certificate for ' + what, app: 'App ' + what, service: 'Service ' + what, container: 'Container ' + what,
    cpu: 'High CPU use', ram: 'High memory use', disk: 'Disk almost full', swap: 'Heavy swap use', load: 'High load' };
  return map[kind] || key;
}
function serverProblems(hl) {
  return (hl && Array.isArray(hl.problems) ? hl.problems : []).filter(function (p) { return p && typeof p.key === 'string' && p.key; })
    .map(function (p) { return { key: p.key, message: String(p.message || problemLabel(p.key)) }; });
}
function isSiteProblem(p) { return /^site:/.test(p.key); }
function fmtBytes(n) {
  if (!isNum(n) || n < 0) return '—';
  if (n < 1024) return n + ' B';
  if (n < 1048576) return Math.round(n / 1024) + ' KB';
  if (n < 1073741824) return (n / 1048576).toFixed(1) + ' MB';
  return (n / 1073741824).toFixed(2) + ' GB';
}
function fmtLoad(t) {
  var f = function (v) { return isNum(v) ? v.toFixed(2) : '—'; };
  return isNum(t.load_1) || isNum(t.load_5) || isNum(t.load_15) ? f(t.load_1) + ' · ' + f(t.load_5) + ' · ' + f(t.load_15) : '—';
}
function fmtWhen(ts) {
  if (!isNum(ts) || ts <= 0) return '';
  if (ts > 1e12) ts = ts / 1000;
  var d = new Date(ts * 1000), now = new Date();
  if (d.toDateString() === now.toDateString()) return 'Today ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  var opts = { day: 'numeric', month: 'short' };
  if (d.getFullYear() !== now.getFullYear()) opts.year = 'numeric';
  return d.toLocaleDateString([], opts);
}
function baseNameOf(p) {
  var t = String(p || '').replace(/\/+$/, '');
  var i = t.lastIndexOf('/');
  return (i < 0 ? t : t.slice(i + 1)) || '/';
}
function joinPath(dir, name) { return dir ? dir.replace(/\/+$/, '') + '/' + name : name; }
function crumbsOf(path) {
  var out = [], cur = '';
  if (!path) return out;
  if (path.charAt(0) === '/') out.push({ label: '/', path: '/' });
  path.split('/').forEach(function (part) {
    if (!part) return;
    cur = path.charAt(0) === '/' || cur ? cur + '/' + part : part;
    out.push({ label: part, path: cur });
  });
  return out;
}

/* ---------------------------------------------------------- server card */
function wireServer(card) {
  card.el.addEventListener('click', function (e) {
    var b = e.target.closest('[data-sact]');
    if (b && card.el.contains(b)) openServerPanel(b.getAttribute('data-sact'));
  });
}
function updateServer(card, d) {
  var t = d.telemetry || {}, R = card.refs, G = card.gauges;
  var online = !!d.online;
  card.el.classList.toggle('is-offline', !online);
  if (card.online !== online) { card.online = online; setOnlineControls(card, online); }
  setText(R.name, devName(d));
  var host = t.hostname || d.hostname;
  var sub = [d.platform || 'Linux'];
  if (host && host !== d.name) sub.push(host);
  setText(R.sub, sub.join(' · '));
  R.badge.className = 'badge ' + (online ? 'on' : 'off');
  presenceBadge(R.badge, d);
  setText(R.load, fmtLoad(t));
  R.load.title = 'Load average over 1, 5 and 15 minutes' + (isNum(t.cores) ? ' on ' + t.cores + ' cores' : '');
  var perCore = isNum(t.load_5) && isNum(t.cores) && t.cores > 0 ? t.load_5 / t.cores : null;
  R.load.classList.toggle('is-stale', perCore != null && perCore >= 1);
  setText(R.uptime, isNum(t.uptime_hours) ? fmtDur(t.uptime_hours * 3600) : '—');
  setText(R.procs, isNum(t.process_count) ? t.process_count : '—');
  setText(R.net, isNum(t.net_sent_mb) || isNum(t.net_recv_mb) ? '↑ ' + fmtMem(t.net_sent_mb) + ' · ↓ ' + fmtMem(t.net_recv_mb) : '—');
  R.net.title = 'Sent and received since the server booted';
  R.seen.setAttribute('data-ts', isNum(d.last_seen) ? d.last_seen : '');
  setText(R.seen, ago(d.last_seen));
  setGauge(G.cpu, t.cpu_pct, { warm: 75, hot: 90, sub: isNum(t.cores) ? t.cores + ' cores' : '' });
  setGauge(G.ram, t.ram_pct, { warm: 80, hot: 92, sub: isNum(t.ram_used_gb) && isNum(t.ram_total_gb) ? t.ram_used_gb.toFixed(1) + '/' + t.ram_total_gb.toFixed(1) + ' GB' : '' });
  setGauge(G.disk, t.disk_pct, { warm: 85, hot: 95, sub: isNum(t.disk_free_gb) ? Math.round(t.disk_free_gb) + ' GB free' : '' });
  setGauge(G.swap, t.swap_pct, { warm: 40, hot: 75, sub: isNum(t.swap_pct) ? 'swap in use' : 'no swap data' });
}

/* --------------------------------------------------------- server panel */
function syncServerPanel() {
  var d = serverDevice();
  var panel = $('#srvp');
  if (!d) {
    if (!panel.hidden || SV.id) resetServer();
    return;
  }
  var changed = SV.id !== d.device_id;
  var wasOnline = SV.online;
  if (changed) {
    var keepTab = SV.tab, hist = SV.hist;
    clearInterval(SV.poll);
    SV = newServerState();
    SV.tab = keepTab; SV.hist = hist;
  }
  SV.id = d.device_id; SV.online = !!d.online; SV.name = devName(d);
  panel.hidden = false;
  setText($('#srvp-name'), 'Server · ' + SV.name);
  var note = $('#srvp-agent');
  note.hidden = SV.online;
  setText(note, SV.online ? '' : 'The server agent is offline, so apps, files, the terminal, processes and the system tools are unavailable until it reconnects. Website checks and History still come from the hub.');
  if (changed) {
    var st = SV;
    st.poll = setInterval(function () { if (S.token && SV === st && !document.hidden) loadServer(false, false); }, 60000);
    showServerTab(SV.tab);
    renderServerAll();
    loadServer(false, false);
  } else if (wasOnline !== SV.online) {
    renderServerAll();
    if (SV.online && SV.tab === 'files' && !SV.files.loaded) loadFiles('');
    if (SV.online && SV.tab === 'processes' && !SV.procLoaded) loadServerProcs();
    if (SV.online && SYS_TABS[SV.tab]) sysShow(SV.tab);
  }
}
function resetServer() {
  clearInterval(SV.poll);
  var hist = SV.hist;
  SV = newServerState();
  SV.hist = hist;
  var panel = $('#srvp');
  if (panel) panel.hidden = true;
}
function renderServerAll() { renderServerOverview(); renderFiles(); renderTerm(); renderServerProcs(); renderSysAll(); }
// Hub health check (no device round trip); `live` also asks the server agent for its pm2 apps
// and Docker containers (that call shows in the activity feed, so only on demand).
function loadServer(recheck, live) {
  if (!SV.id || !S.token) return Promise.resolve();
  if (SV.loading) return SV.loading;
  var st = SV, id = SV.id;
  st.recheck = !!recheck;
  var health = api('/api/v1/server/status' + (recheck ? '?refresh=true' : ''), { timeout: recheck ? 120000 : 30000 }).then(function (r) {
    st.health = r || {}; st.healthErr = ''; st.healthAt = Date.now();
  }, function (e) { if (e.status !== 401) st.healthErr = e.message; st.healthAt = Date.now(); });
  var agent = live && st.online ? devAct(id, 'server_status', {}, 45000).then(function (r) {
    if (r && r.success !== false) { st.agent = r; st.agentErr = ''; } else st.agentErr = resultError(r);
  }, function (e) { if (e.status !== 401) st.agentErr = e.message; }) : Promise.resolve();
  st.loading = Promise.all([health, agent]).then(function () {
    st.loading = null; st.recheck = false;
    if (SV === st) renderServerOverview();
  });
  renderServerHead();
  return st.loading;
}
function renderServerHead() {
  busy($('#srvp-refresh'), !!SV.loading && !SV.recheck);
  busy($('#ss-recheck'), !!SV.loading && SV.recheck);
  var chip = $('#srvp-health');
  if (!SV.health) { chip.hidden = true; return; }
  var probs = serverProblems(SV.health), n = probs.length;
  chip.hidden = false;
  chip.className = 'hchip ' + (n === 0 ? 'ok' : probs.some(isSiteProblem) ? 'bad' : 'warn');
  setText(chip, n === 0 ? 'Healthy' : n === 1 ? '1 problem' : n + ' problems');
}
function unitList(kind, raw) {
  var out = [];
  if (raw && !Array.isArray(raw) && typeof raw === 'object') {
    Object.keys(raw).sort(function (a, b) { return a.toLowerCase() < b.toLowerCase() ? -1 : 1; }).forEach(function (k) {
      var v = raw[k];
      out.push({ kind: kind, name: k, state: String(v && typeof v === 'object' ? (v.state || v.status || 'unknown') : (v || 'unknown')) });
    });
    return out;
  }
  (Array.isArray(raw) ? raw : []).forEach(function (u) {
    if (!u || !u.name) return;
    var state = kind === 'container' ? (u.state || u.status) : (u.status || u.state);
    var detail = kind === 'container' && u.status && u.status !== u.state ? String(u.status) : '';
    out.push({ kind: kind, name: String(u.name), state: String(state || 'unknown'), restarts: u.restarts, memory: u.memory_mb, detail: detail });
  });
  return out;
}
function renderServerOverview() {
  renderServerHead();
  var hl = SV.health, snap = (hl && hl.snapshot) || {};
  var probs = serverProblems(hl);
  var sum = $('#srvp-summary');
  if (hl) setText(sum, hl.summary || (probs.length ? 'Something on the server needs a look.' : 'Everything on the server looks fine.'));
  else setText(sum, SV.healthErr ? 'Couldn’t load the server’s health: ' + SV.healthErr : 'Loading the server’s status…');
  var bits = [];
  if (isNum(snap.checked_at)) bits.push('Checked ', h('time', { 'data-ts': snap.checked_at }, ago(snap.checked_at)));
  if (isNum(snap.sites_checked_at)) bits.push(' · websites ', h('time', { 'data-ts': snap.sites_checked_at }, ago(snap.sites_checked_at)));
  if (SV.healthErr && hl) bits.push(' · last refresh failed: ' + SV.healthErr);
  if (SV.agentErr) bits.push((bits.length ? ' · ' : '') + 'apps: ' + SV.agentErr);
  var chk = $('#srvp-checked');
  chk.replaceChildren.apply(chk, bits);
  var plist = $('#srvp-problems');
  plist.replaceChildren.apply(plist, probs.map(function (p) { return problemEl(p, false); }));
  var muted = hl && Array.isArray(hl.ignored) ? hl.ignored.filter(function (k) { return typeof k === 'string' && k; }) : [];
  $('#srvp-muted-wrap').hidden = !muted.length;
  setText($('#srvp-muted-n'), muted.length ? String(muted.length) : '');
  var mlist = $('#srvp-muted');
  mlist.replaceChildren.apply(mlist, muted.map(function (k) { return problemEl({ key: k, message: problemLabel(k) }, true); }));
  var agent = SV.agent || {};
  renderUnits('app', unitList('app', Array.isArray(agent.apps) ? agent.apps : snap.apps));
  renderUnits('service', unitList('service', snap.services));
  renderUnits('container', unitList('container', Array.isArray(agent.containers) ? agent.containers : snap.containers));
  renderSites(snap);
}
function problemEl(p, muted) {
  var st = SV;
  var btn = h('button', { class: 'btn btn-ghost btn-sm', type: 'button', title: muted ? 'Alert about this again' : 'Stop alerting about this' }, muted ? 'Unmute' : 'Mute');
  btn.addEventListener('click', function () {
    busy(btn, true);
    api('/api/v1/server/ignore', { method: 'POST', body: { key: p.key, ignore: !muted } }).then(function () {
      toast(muted ? 'You’ll be alerted about it again.' : 'No more alerts for it.', { type: 'success', title: muted ? 'Unmuted' : 'Muted' });
      if (SV !== st) return null;
      return (st.loading || Promise.resolve()).then(function () { return loadServer(false, false); });
    }, function (e) { if (e.status !== 401) toast(e.message, { type: 'error', title: 'Couldn’t change that' }); })
      .then(function () { busy(btn, false); });
  });
  return h('li', { class: muted ? 'is-muted' : (isSiteProblem(p) ? 'is-bad' : '') }, icon(muted ? 'bell-off' : 'alert'), h('span', null, p.message), btn);
}
function renderUnits(kind, units) {
  var box = $('#su-' + kind);
  var bad = units.filter(function (u) { return unitHealth(u.state) === 'bad'; }).length;
  var n = $('#su-' + kind + '-n');
  setText(n, units.length ? (bad ? bad + ' of ' + units.length + ' failing' : String(units.length)) : '');
  n.style.color = bad ? '#FCA5A5' : '';
  if (!units.length) { box.replaceChildren(h('p', { class: 'list-note' }, SV.health || SV.agent ? UNIT_EMPTY[kind] : 'Loading…')); return; }
  box.replaceChildren.apply(box, units.map(unitEl));
}
function unitEl(u) {
  var lvl = unitHealth(u.state);
  var running = lvl === 'ok' || lvl === 'warn';
  var isBusy = SV.busy.has(u.kind + ':' + u.name);
  var meta = [];
  if (isNum(u.restarts)) meta.push(u.restarts + (u.restarts === 1 ? ' restart' : ' restarts'));
  if (isNum(u.memory) && u.memory > 0) meta.push(fmtMem(u.memory));
  if (u.detail) meta.push(u.detail);
  var b = function (act, label, iconName, disabled, cls) {
    var btn = h('button', { class: 'btn btn-sm' + (cls ? ' ' + cls : ''), type: 'button', title: label + ' ' + u.name, 'aria-label': label + ' ' + u.kind + ' ' + u.name }, icon(iconName), h('span', null, label));
    btn.disabled = !SV.online || isBusy || !!disabled;
    btn.addEventListener('click', function () { if (act === 'logs') unitLogs(u, btn); else controlUnit(u, act); });
    return btn;
  };
  return h('div', { class: 'unit' + (lvl === 'bad' ? ' is-bad' : '') },
    h('div', { class: 'unit-top' }, h('span', { class: 'unit-name', title: u.name }, u.name), isBusy ? h('span', { class: 'spinner', title: 'Working…' }) : null, hchip(u.state, lvl)),
    meta.length ? h('p', { class: 'unit-meta' }, meta.join(' · ')) : null,
    h('div', { class: 'unit-acts' },
      b('logs', 'Logs', 'doc'),
      b('restart', 'Restart', 'reset'),
      b('stop', 'Stop', 'x', !running, 'is-stop'),
      b('start', 'Start', 'play', running)));
}
function unitLogs(u, btn) {
  var st = SV;
  if (!st.id) return;
  busy(btn, true);
  devAct(st.id, 'control', { kind: u.kind, name: u.name, action: 'logs', lines: 200 }, 45000).then(function (r) {
    var ok = !!r && r.success !== false;
    var text = ok ? String(r.stdout || '') : [resultError(r), r && r.stderr ? String(r.stderr) : ''].filter(Boolean).join('\n\n');
    textDlg({ title: 'Logs · ' + u.name, sub: 'Last 200 lines of the ' + u.kind + ' on ' + st.name, text: text, error: !ok, scrollEnd: true });
  }, function (e) { if (e.status !== 401) toast(e.message, { type: 'error', title: 'Couldn’t read the logs' }); })
    .then(function () { busy(btn, false); });
}
function controlUnit(u, action) {
  var v = UNIT_VERBS[action], st = SV;
  if (!v || !st.id) return;
  var what = 'the ' + u.kind + ' “' + u.name + '”';
  var body = action === 'restart' ? 'This restarts ' + what + ' on ' + st.name + '. It is briefly unavailable while it comes back.'
    : action === 'stop' ? 'This stops ' + what + ' on ' + st.name + '. Whatever it serves stays down until it is started again.'
    : 'This starts ' + what + ' on ' + st.name + '.';
  confirmDlg({ title: v[0] + ' ' + u.name + '?', body: body, confirm: v[0] + ' ' + u.kind, danger: action !== 'start' }).then(function (yes) {
    if (!yes || SV !== st) return;
    var key = u.kind + ':' + u.name;
    st.busy.add(key);
    renderServerOverview();
    runAction(st.id, 'control', { kind: u.kind, name: u.name, action: action }, {
      label: v[0] + ' ' + u.name, timeout: 120000, ok: function (r) { return r.message || (v[1] + ' ' + u.name + '.'); }
    }).then(function (r) {
      st.busy.delete(key);
      if (SV !== st) return;
      renderServerOverview();
      if (r && r.success !== false) setTimeout(function () { if (SV === st) loadServer(false, true); }, 1500);
    });
  });
}
function renderSites(snap) {
  var sites = (Array.isArray(snap.sites) ? snap.sites : []).filter(function (x) { return x && x.domain; }).slice();
  sites.sort(function (a, b) {
    if (!!a.up !== !!b.up) return a.up ? 1 : -1;
    var ca = isNum(a.cert_days) ? a.cert_days : 1e9, cb = isNum(b.cert_days) ? b.cert_days : 1e9;
    if (ca !== cb) return ca - cb;
    return String(a.domain).localeCompare(String(b.domain));
  });
  var down = sites.filter(function (x) { return !x.up; }).length;
  var n = $('#ss-n');
  setText(n, sites.length ? (down ? down + ' of ' + sites.length + ' down' : 'all ' + sites.length + ' up') : '');
  n.style.color = down ? '#FCA5A5' : '';
  var body = $('#ss-body');
  if (!sites.length) {
    body.replaceChildren(h('tr', null, h('td', { colspan: '4', class: 'muted' }, SV.health ? 'No websites watched.' : 'Loading…')));
    return;
  }
  body.replaceChildren.apply(body, sites.map(function (x) {
    var code = isNum(x.status) ? ' · ' + x.status : '';
    return h('tr', null,
      h('td', null, h('span', { class: 'site-dom', title: x.domain }, x.domain), !x.up && x.error ? h('span', { class: 'site-err', title: String(x.error) }, String(x.error)) : null),
      h('td', null, x.up ? hchip('Up' + code, 'ok') : hchip('Down' + code, 'bad')),
      h('td', { class: 'num' }, isNum(x.ms) ? fmtMs(x.ms) : '—'),
      h('td', null, hchip(certText(x.cert_days), certLevel(x.cert_days))));
  }));
}
function showServerTab(tab) {
  if (SRV_TABS.indexOf(tab) < 0) tab = 'overview';
  SV.tab = tab;
  $$('[data-stab]').forEach(function (b) { b.setAttribute('aria-selected', String(b.getAttribute('data-stab') === tab)); });
  SRV_TABS.forEach(function (t) { $('#spane-' + t).hidden = t !== tab; });
  if (tab === 'files') { if (!SV.files.loaded && !SV.files.loading && SV.online) loadFiles(''); else renderFiles(); }
  if (tab === 'processes') { if (!SV.procLoaded && !SV.procLoading && SV.online) loadServerProcs(); else renderServerProcs(); }
  if (tab === 'terminal') renderTerm();
  if (SYS_TABS[tab]) sysShow(tab);
}
function openServerPanel(tab) {
  showServerTab(tab);
  var panel = $('#srvp');
  if (panel.hidden) return;
  try { panel.scrollIntoView({ behavior: REDUCED ? 'auto' : 'smooth', block: 'start' }); } catch (e) { panel.scrollIntoView(); }
  if (tab === 'terminal') setTimeout(function () { var c = $('#term-cmd'); if (c && !c.disabled) { try { c.focus({ preventScroll: true }); } catch (x) { c.focus(); } } }, 400);
}

/* ---------------------------------------------------------------- files */
function sortFileEntries(list) {
  return (Array.isArray(list) ? list : []).filter(function (e) { return e && e.path; }).slice().sort(function (a, b) {
    if (!!a.folder !== !!b.folder) return a.folder ? -1 : 1;
    var na = String(a.name || '').toLowerCase(), nb = String(b.name || '').toLowerCase();
    return na < nb ? -1 : na > nb ? 1 : 0;
  });
}
function loadFiles(path) {
  var st = SV, F = SV.files;
  if (!st.id) return;
  var gen = ++F.gen;
  F.loading = true; F.want = path || ''; F.err = '';
  renderFiles();
  devAct(st.id, 'list_dir', { path: path || '' }, 30000).then(function (r) {
    if (gen !== F.gen) return;
    if (!r || r.success === false) { F.err = resultError(r); return; }
    F.path = r.path || ''; F.parent = r.parent || null; F.entries = sortFileEntries(r.entries); F.truncated = !!r.truncated; F.loaded = true;
    if (Array.isArray(r.drives) && r.drives.length) F.drives = r.drives;
  }, function (e) { if (gen === F.gen && e.status !== 401) F.err = e.message; }).then(function () {
    if (gen !== F.gen) return;
    F.loading = false;
    if (SV === st) renderFiles();
  });
}
function renderFiles() {
  var F = SV.files, online = SV.online, root = !F.path;
  $('#f-up').disabled = root || !online;
  $('#f-mkdir').disabled = root || !online;
  $('#f-refresh').disabled = !online;
  busy($('#f-refresh'), F.loading);
  var nav = $('#f-crumbs');
  var parts = [h('button', { type: 'button', 'data-fpath': '', 'aria-current': root ? 'page' : null, title: 'Top-level folders and disks' }, SV.name || 'Server')];
  var crumbs = crumbsOf(F.path);
  crumbs.forEach(function (c, i) {
    parts.push(h('span', { class: 'sep', 'aria-hidden': 'true' }, '›'));
    parts.push(h('button', { type: 'button', 'data-fpath': c.path, 'aria-current': i === crumbs.length - 1 ? 'page' : null, title: c.path }, c.label));
  });
  nav.replaceChildren.apply(nav, parts);
  nav.scrollLeft = nav.scrollWidth;
  var msg = $('#f-msg');
  var text = !online ? 'The server agent is offline.' : F.err ? F.err : (F.loading && !F.loaded ? 'Loading…' : (F.truncated ? 'Showing the first 1000 items.' : ''));
  setText(msg, text);
  msg.hidden = !text;
  msg.classList.toggle('is-error', !!F.err && online);
  var rows = [];
  if (root) {
    F.drives.forEach(function (d) {
      if (!d || !d.name) return;
      var used = isNum(d.total_gb) && isNum(d.free_gb) && d.total_gb > 0 ? ' · ' + Math.round(100 * (d.total_gb - d.free_gb) / d.total_gb) + '% used' : '';
      rows.push(h('tr', { class: 'is-drive' },
        h('td', null, h('div', { class: 'f-name' }, icon('disk', 'is-disk'), h('button', { type: 'button', 'data-fpath': d.name, title: 'Open ' + d.name }, d.name), d.label ? h('span', { class: 'muted' }, String(d.label)) : null)),
        h('td', { class: 'num' }, isNum(d.free_gb) ? d.free_gb.toFixed(1) + ' GB free' : '—'),
        h('td', { class: 'c-mod muted' }, isNum(d.total_gb) ? 'of ' + Math.round(d.total_gb) + ' GB' + used : ''),
        h('td', null)));
    });
  }
  F.entries.forEach(function (e, i) { rows.push(fileRow(e, i, online)); });
  if (!rows.length) {
    var empty = F.loading ? 'Loading…' : F.loaded ? 'This folder is empty.' : (online ? '' : '—');
    rows.push(h('tr', null, h('td', { colspan: '4', class: 'muted' }, empty)));
  }
  var body = $('#f-body');
  body.replaceChildren.apply(body, rows);
}
function fileRow(e, i, online) {
  var name = String(e.name || baseNameOf(e.path));
  var nameEl = e.folder ? h('button', { type: 'button', 'data-fpath': e.path, title: e.path }, name) : h('span', { title: e.path }, name);
  var dl = null;
  if (!e.folder) {
    dl = h('button', { class: 'icon-btn sm', type: 'button', 'data-fact': 'download', 'data-i': i, title: 'Download', 'aria-label': 'Download ' + name }, icon('download'));
    dl.disabled = !online;
  }
  var more = h('button', { class: 'icon-btn sm', type: 'button', 'data-fact': 'menu', 'data-i': i, 'aria-haspopup': 'dialog', title: 'More actions', 'aria-label': 'More actions for ' + name }, icon('more'));
  more.disabled = !online;
  return h('tr', null,
    h('td', null, h('div', { class: 'f-name' }, icon(e.folder ? 'folder' : 'doc', e.folder ? 'is-dir' : ''), nameEl)),
    h('td', { class: 'num' }, e.folder ? '—' : fmtBytes(e.size)),
    h('td', { class: 'c-mod muted' }, fmtWhen(e.modified)),
    h('td', null, h('div', { class: 'f-acts' }, dl, more)));
}
function fileMenu(e) {
  var name = String(e.name || baseNameOf(e.path));
  var acts = e.folder ? [['open', 'Open', 'folder']] : [['view', 'View / edit', 'doc'], ['download', 'Download', 'download']];
  acts = acts.concat([['rename', 'Rename', 'note'], ['move', 'Move to…', 'arrow'], ['copy', 'Copy to…', 'clip'], ['zip', 'Zip', 'plus']]);
  if (!e.folder && /\.zip$/i.test(name)) acts.push(['unzip', 'Unzip here', 'folder']);
  acts.push(['delete', 'Delete', 'trash']);
  var list = h('div', { class: 'act-list' }, acts.map(function (a) {
    return h('button', { class: 'btn' + (a[0] === 'delete' ? ' btn-danger' : ''), type: 'submit', value: a[0] }, icon(a[2]), h('span', null, a[1]));
  }));
  var form = h('form', { method: 'dialog', class: 'dlg-form' },
    h('h2', { class: 'dlg-title' }, name), h('p', { class: 'dlg-sub' }, e.path), list,
    h('div', { class: 'dlg-actions' }, h('button', { class: 'btn btn-ghost', type: 'submit', value: 'cancel' }, 'Cancel')));
  openDialog(form, function (v) { if (v && v !== 'cancel') fileAction(e, v); });
}
function validName(n) { return !!n && n !== '.' && n !== '..' && !/[\/\0]/.test(n); }
function fileAction(e, act) {
  var st = SV, F = SV.files, id = SV.id;
  if (!id) return null;
  var name = String(e.name || baseNameOf(e.path));
  var after = function (r) { if (r && r.success !== false && SV === st) loadFiles(F.path); return r; };
  var okMsg = function (fallback) { return function (r) { return r.message || fallback; }; };
  switch (act) {
    case 'open': return loadFiles(e.path);
    case 'download': return downloadServerFile(e, name);
    case 'view': return viewServerFile(e, name);
    case 'rename':
      return formDlg({ title: 'Rename ' + name, submit: 'Rename', fields: [{ name: 'to', label: 'New name', value: name, required: true, max: 255 }] }).then(function (v) {
        if (!v || v.to === name) return null;
        if (!validName(v.to)) { toast('A name can’t contain “/”.', { type: 'error', title: 'Rename' }); return null; }
        return runAction(id, 'manage_file', { op: 'rename', path: e.path, destination: v.to }, { label: 'Rename', ok: okMsg('Renamed to ' + v.to + '.') }).then(after);
      });
    case 'move':
    case 'copy':
      var verb = act === 'move' ? 'Move' : 'Copy';
      return formDlg({
        title: verb + ' ' + name, desc: 'Into which folder on ' + st.name + '? Give the full path, e.g. /var/www/backups (it is created if needed).', submit: verb,
        fields: [{ name: 'to', label: 'Folder', value: F.path || '', required: true, max: 500 }]
      }).then(function (v) {
        if (!v) return null;
        return runAction(id, 'manage_file', { op: act, path: e.path, destination: v.to }, { label: verb, timeout: 180000, ok: okMsg((act === 'move' ? 'Moved ' : 'Copied ') + name + '.') }).then(after);
      });
    case 'zip':
      return runAction(id, 'manage_file', { op: 'zip', path: e.path }, { label: 'Zip', timeout: 180000, ok: okMsg('Zipped ' + name + '.') }).then(after);
    case 'unzip':
      return runAction(id, 'manage_file', { op: 'unzip', path: e.path }, { label: 'Unzip', timeout: 180000, ok: okMsg('Extracted ' + name + '.') }).then(after);
    case 'delete':
      return confirmDlg({
        title: 'Delete ' + name + '?',
        body: (e.folder ? 'This folder and everything in it' : 'It') + ' moves to ~/.willy-trash on ' + st.name + ', where you can restore it.',
        confirm: 'Delete', danger: true
      }).then(function (yes) {
        if (!yes) return null;
        return runAction(id, 'manage_file', { op: 'delete', path: e.path }, { label: 'Delete', ok: okMsg('Deleted ' + name + '.') }).then(after);
      });
    default: return null;
  }
}
function newServerFolder() {
  var st = SV, F = SV.files;
  if (!st.id || !F.path) return;
  formDlg({ title: 'New folder in ' + baseNameOf(F.path), submit: 'Create', fields: [{ name: 'name', label: 'Folder name', required: true, max: 255, placeholder: 'backups' }] }).then(function (v) {
    if (!v) return;
    if (!validName(v.name)) { toast('A name can’t contain “/”.', { type: 'error', title: 'New folder' }); return; }
    runAction(st.id, 'manage_file', { op: 'mkdir', path: joinPath(F.path, v.name) }, { label: 'New folder', ok: 'Created ' + v.name + '.' }).then(function (r) {
      if (r && r.success !== false && SV === st) loadFiles(F.path);
    });
  });
}
function viewServerFile(e, name) {
  var st = SV;
  return devAct(st.id, 'manage_file', { op: 'read', path: e.path }, 30000).then(function (r) {
    if (!r || r.success === false || typeof r.content !== 'string') { toast(resultError(r), { type: 'error', title: 'Couldn’t open ' + name }); return null; }
    var text = r.content;
    return textDlg({
      title: name, text: text, editable: !r.truncated,
      sub: e.path + (r.truncated ? ' · only the start is shown, so it can’t be edited here' : '')
    }).then(function (edited) {
      if (edited == null || edited === text) return null;
      return runAction(st.id, 'manage_file', { op: 'write', path: e.path, content: edited, overwrite: true }, { label: 'Save', ok: 'Saved ' + name + '.' }).then(function (r2) {
        if (r2 && r2.success !== false && SV === st) loadFiles(st.files.path);
      });
    });
  }, function (x) { if (x.status !== 401) toast(x.message, { type: 'error', title: 'Couldn’t open ' + name }); });
}
// The server uploads the file to the hub; the browser then fetches it with the Bearer header
// (the token never goes into a URL) and saves it.
function downloadServerFile(e, name) {
  var st = SV;
  var close = toast('Getting ' + name + ' from ' + st.name + '…', { type: 'info', timeout: 120000 });
  return devAct(st.id, 'file_to_hub', { path: e.path }, 180000).then(function (r) {
    if (!r || r.success === false || !r.file || !r.file.url) throw ApiError(resultError(r), 0);
    return downloadHubFile(String(r.file.url), String(r.file.name || name));
  }).then(function () {
    close();
    toast('Downloaded ' + name + '.', { type: 'success' });
  }, function (x) {
    close();
    if (x.status !== 401) toast(x.message, { type: 'error', title: 'Download failed' });
  });
}
function downloadHubFile(url, name) {
  if (url.indexOf('/api/v1/files/') !== 0) return Promise.reject(ApiError('The hub sent an unexpected file link.', 0));
  return fetch(BASE + url, { headers: { 'Authorization': 'Bearer ' + S.token, 'X-Willy-Client': CLIENT }, cache: 'no-store' }).then(function (res) {
    if (res.status === 401) { onUnauthorized(); throw ApiError('Your access token was rejected.', 401); }
    if (!res.ok) throw ApiError('The hub answered with HTTP ' + res.status + '.', res.status);
    return res.blob();
  }, function () { throw ApiError('Can’t reach the Willy hub.', 0); }).then(function (blob) {
    var href = URL.createObjectURL(blob);
    var a = h('a', { href: href, download: name || 'file' });
    a.hidden = true;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(href); }, 60000);
  });
}

/* ------------------------------------------------------------- terminal */
function loadShellHist() {
  if (SV.hist) return SV.hist;
  try {
    var v = JSON.parse(local.get(KEYS.srvHist) || '[]');
    SV.hist = Array.isArray(v) ? v.filter(function (x) { return typeof x === 'string'; }).slice(0, 30) : [];
  } catch (e) { SV.hist = []; }
  return SV.hist;
}
function pushShellHist(cmd) {
  var list = loadShellHist().filter(function (x) { return x !== cmd; });
  list.unshift(cmd);
  if (list.length > 30) list.length = 30;
  SV.hist = list;
  local.set(KEYS.srvHist, JSON.stringify(list));
  SV.histIdx = -1; SV.draft = '';
}
// The user typed the command, so pressing Run is the confirmation.
function runShell(cmd) {
  cmd = String(cmd || '').trim();
  var st = SV;
  if (!cmd || st.running || !st.id) return;
  if (!st.online) { toast('The server agent is offline.', { type: 'warn' }); return; }
  pushShellHist(cmd);
  var cwd = $('#term-cwd').value.trim();
  var run = { cmd: cmd, cwd: cwd, running: true };
  st.runs.push(run);
  if (st.runs.length > 30) st.runs.shift();
  st.running = true;
  $('#term-cmd').value = '';
  renderTerm();
  var payload = { command: cmd, timeout: 60 };
  if (cwd) payload.cwd = cwd;
  devAct(st.id, 'run_shell', payload, 90000).then(function (r) {
    r = r || {};
    run.exit = isNum(r.exit_code) ? r.exit_code : null;
    run.sec = isNum(r.duration_sec) ? r.duration_sec : null;
    run.out = r.stdout ? String(r.stdout) : '';
    run.err = r.stderr ? String(r.stderr) : '';
    if (run.exit == null && r.success === false) run.fail = resultError(r);
  }, function (e) { run.fail = e.status === 401 ? 'Signed out.' : e.message; }).then(function () {
    run.running = false; st.running = false;
    if (SV === st) renderTerm();
  });
}
function renderTerm() {
  var out = $('#term-out'), st = SV;
  var nearBottom = out.scrollHeight - out.scrollTop - out.clientHeight < 60;
  if (!st.runs.length) {
    out.replaceChildren(h('p', { class: 'term-empty' }, st.online
      ? 'Run a command on ' + (st.name || 'the server') + '. It runs as the server user, from the home folder unless you set a working folder.'
      : 'The server agent is offline.'));
  } else {
    out.replaceChildren.apply(out, st.runs.map(termRunEl));
  }
  if (nearBottom || st.running) out.scrollTop = out.scrollHeight;
  $('#term-cmd').disabled = !st.online;
  $('#term-run').disabled = !st.online || st.running;
  busy($('#term-run'), st.running);
}
function termRunEl(r) {
  var meta = r.running ? 'running…' : [r.exit != null ? 'exit ' + r.exit : 'failed', r.sec != null ? r.sec.toFixed(r.sec < 10 ? 2 : 1) + ' s' : '', r.cwd ? 'in ' + r.cwd : ''].filter(Boolean).join(' · ');
  var metaEl = h('span', { class: 'term-meta' }, meta);
  if (!r.running) metaEl.style.color = r.exit === 0 ? '#6EE7B7' : '#FCA5A5';
  var again = h('button', { class: 'icon-btn sm', type: 'button', title: 'Put this command back in the box', 'aria-label': 'Edit and run again: ' + r.cmd }, icon('reset'));
  again.addEventListener('click', function () { var c = $('#term-cmd'); c.value = r.cmd; c.focus(); });
  var kids = [h('div', { class: 'term-head' }, h('span', { class: 'term-cmd' }, r.cmd), metaEl, again)];
  if (r.running) kids.push(h('p', { class: 'term-pre dim' }, h('span', { class: 'typing', 'aria-hidden': 'true' }, h('i'), h('i'), h('i'))));
  if (r.out) kids.push(h('pre', { class: 'term-pre' }, r.out.replace(/\s+$/, '')));
  if (r.err) kids.push(h('pre', { class: 'term-pre err' }, r.err.replace(/\s+$/, '')));
  if (r.fail) kids.push(h('pre', { class: 'term-pre err' }, r.fail));
  if (!r.running && !r.out && !r.err && !r.fail) kids.push(h('p', { class: 'term-pre dim' }, '(no output)'));
  return h('div', { class: 'term-run' }, kids);
}

/* ------------------------------------------------------------ processes */
function loadServerProcs() {
  var st = SV;
  if (!st.id) return;
  var gen = ++st.procGen;
  st.procLoading = true; st.procErr = '';
  renderServerProcs();
  devAct(st.id, 'list_processes', { limit: 15, sort: st.procSort }, 30000).then(function (r) {
    if (gen !== st.procGen) return;
    if (r && r.success !== false && Array.isArray(r.processes)) { st.procs = r.processes; st.procAt = Date.now(); st.procLoaded = true; }
    else st.procErr = resultError(r);
  }, function (e) { if (gen === st.procGen && e.status !== 401) st.procErr = e.message; }).then(function () {
    if (gen !== st.procGen) return;
    st.procLoading = false;
    if (SV === st) renderServerProcs();
  });
}
function renderServerProcs() {
  var st = SV;
  $$('[data-psort]').forEach(function (b) { b.setAttribute('aria-pressed', String(b.getAttribute('data-psort') === st.procSort)); });
  busy($('#sp-refresh'), st.procLoading);
  $('#sp-refresh').disabled = !st.online;
  var note = !st.online ? 'Agent offline' : st.procErr ? st.procErr : st.procLoading ? 'Loading…'
    : st.procAt ? 'Top 15 by ' + (st.procSort === 'cpu' ? 'CPU' : 'memory') + ' · ' + new Date(st.procAt).toLocaleTimeString() : '';
  setText($('#sp-note'), note);
  var body = $('#sp-body');
  if (!st.procs.length) {
    body.replaceChildren(h('tr', null, h('td', { colspan: '6', class: 'muted' }, st.procLoading ? 'Loading…' : (st.online ? 'No processes yet.' : 'Offline'))));
    return;
  }
  body.replaceChildren.apply(body, st.procs.map(function (p) {
    var name = String(p.name || '?'), cmd = String(p.command || '');
    return h('tr', null,
      h('td', { class: 'p-name', title: name }, name),
      h('td', { class: 'num' }, isNum(p.pid) ? p.pid : '—'),
      h('td', { class: 'muted' }, p.user ? String(p.user) : '—'),
      h('td', { class: 'num' }, isNum(p.cpu) ? p.cpu.toFixed(1) + '%' : '—'),
      h('td', { class: 'num' }, fmtMem(isNum(p.memory_mb) ? p.memory_mb : p.mem_mb)),
      h('td', null, h('div', { class: 'pcmd', title: cmd }, cmd || '—')));
  }));
}

/* -------------------------------------------------------- text + copy */
function copyText(text) {
  var done = function () { toast('Copied to the clipboard.', { type: 'success' }); };
  if (navigator.clipboard && navigator.clipboard.writeText && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(done, function () { toast('The browser blocked the clipboard.', { type: 'warn' }); });
    return;
  }
  try {
    var ta = h('textarea', { readonly: true });
    ta.value = text;
    ta.style.position = 'fixed'; ta.style.top = '0'; ta.style.opacity = '0';
    var host = $('#dlg').open ? $('#dlg') : document.body; // a modal dialog makes the rest of the page inert
    host.appendChild(ta);
    ta.select();
    var ok = document.execCommand('copy');
    ta.remove();
    if (ok) done(); else toast('Couldn’t copy. Select the text and copy it.', { type: 'warn' });
  } catch (e) { toast('Couldn’t copy. Select the text and copy it.', { type: 'warn' }); }
}
// Logs, command output and server files in a wide dialog. With `editable`, resolves to the
// edited text when the user presses Save (else null).
function textDlg(o) {
  return new Promise(function (resolve) {
    var area = null, body;
    if (o.editable) {
      area = h('textarea', { class: 'dlg-edit', spellcheck: 'false', autocapitalize: 'off', autocorrect: 'off', 'aria-label': 'Contents of ' + o.title });
      area.value = o.text || '';
      body = area;
    } else {
      body = h('pre', { class: 'dlg-pre' + (o.error ? ' is-error' : ''), tabindex: '0' }, o.text && String(o.text).trim() ? String(o.text) : '(no output)');
    }
    var copy = h('button', { class: 'btn btn-ghost', type: 'button' }, icon('clip'), h('span', null, 'Copy'));
    copy.addEventListener('click', function () { copyText(area ? area.value : String(o.text || '')); });
    var acts = [copy, h('button', { class: 'btn', type: 'submit', value: 'cancel' }, o.editable ? 'Cancel' : 'Close')];
    if (o.editable) acts.push(h('button', { class: 'btn btn-primary', type: 'submit', value: 'save' }, 'Save'));
    var form = h('form', { method: 'dialog', class: 'dlg-form' },
      h('h2', { class: 'dlg-title' }, o.title), o.sub ? h('p', { class: 'dlg-sub' }, o.sub) : null, body, h('div', { class: 'dlg-actions' }, acts));
    openDialog(form, function (v) { resolve(v === 'save' && area ? area.value : null); }, function () { return 'cancel'; }, true);
    if (o.scrollEnd && !area) body.scrollTop = body.scrollHeight;
  });
}

/* ------------------------------------------------- server system tabs */
// History (hub samples), Updates, Storage, Network & security, Services & jobs and Logs.
// Each tab loads the first time it is shown; slow agent actions get long timeouts.
var SYS_TABS = { history: 1, updates: 1, storage: 1, network: 1, services: 1, logs: 1 };
var SYS_TIMEOUT = {
  sys_updates: 260000, apply_updates: 1920000, storage: 130000, cleanup: 330000, network: 40000, security: 85000,
  services_list: 40000, service_boot: 40000, timers: 40000, journal: 55000, reboot: 30000, cancel_reboot: 30000, control: 125000
};
var CLEANUP_WHAT = {
  journal: 'Trims the system logs (journald) to the last 14 days and at most 300 MB.',
  docker: 'Runs “docker system prune”: removes stopped containers, unused networks, dangling images and the build cache. It never removes volumes, so app data stays.',
  pm2_logs: 'Empties the pm2 app log files (“pm2 flush”). Running apps keep running.',
  dnf_cache: 'Deletes downloaded package files (“dnf clean packages”). They are fetched again when needed.',
  trash: 'Permanently deletes everything in Willy’s trash (~/.willy-trash). This can’t be undone.'
};
var CLEANUP_ORDER = ['journal', 'docker', 'pm2_logs', 'dnf_cache', 'trash'];
var CHART_C = { cpu: '#00F2FE', ram: '#A78BFA', disk: '#F59E0B', swap: '#F472B6', rx: '#10B981', tx: '#38BDF8', load: '#FCD34D' };
function newSysState() {
  var box = function () { return { data: null, err: '', loading: false, loaded: false, at: 0, gen: 0 }; };
  return {
    hours: 1, hist: box(), upd: box(), sto: box(), net: box(), sec: box(), svc: box(), jobs: box(), logs: box(),
    installing: '', installOut: null, installOk: true, rebootAt: 0, rebootBusy: false,
    cleaning: new Set(), svcBusy: new Set(), svcFilter: 'all', svcQuery: '', logsText: ''
  };
}
function fmtGb(gb) {
  if (!isNum(gb)) return '—';
  if (gb <= 0) return '0 GB';
  if (gb < 1) return Math.round(gb * 1024) + ' MB';
  return gb < 100 ? gb.toFixed(1) + ' GB' : Math.round(gb) + ' GB';
}
// Network rates arrive in kilobits per second.
function fmtKbit(v) {
  if (!isNum(v)) return '—';
  if (v < 1) return '0 kbit/s';
  if (v < 1000) return Math.round(v) + ' kbit/s';
  if (v < 1e6) return (v / 1000).toFixed(v < 10000 ? 1 : 0) + ' Mbit/s';
  return (v / 1e6).toFixed(1) + ' Gbit/s';
}
function fmtKbitShort(v) {
  if (v < 1000) return Math.round(v) + 'k';
  if (v < 1e6) return (v / 1000).toFixed(v < 10000 && v % 1000 ? 1 : 0) + 'M';
  return (v / 1e6).toFixed(1) + 'G';
}
function pctColor(p) { return !isNum(p) ? '#64748B' : p < 70 ? '#10B981' : p < 90 ? '#F59E0B' : '#EF4444'; }
function niceCeil(v) {
  if (!(v > 1)) return 1;
  var p = Math.pow(10, Math.floor(Math.log(v) / Math.LN10));
  var steps = [1, 2, 2.5, 5, 10];
  for (var i = 0; i < steps.length; i++) if (v <= steps[i] * p) return steps[i] * p;
  return 10 * p;
}
function seriesStats(vals) {
  var n = 0, sum = 0, max = null, last = null;
  vals.forEach(function (v) { if (!isNum(v)) return; n++; sum += v; if (max == null || v > max) max = v; last = v; });
  return { avg: n ? sum / n : null, max: max, last: last };
}
function sysBar(pct) {
  var fill = h('i');
  fill.style.width = Math.max(0, Math.min(100, isNum(pct) ? pct : 0)) + '%';
  fill.style.background = pctColor(pct);
  return h('div', { class: 'bar', role: 'presentation' }, fill);
}
function sysErr(r) { return r && r.error === 'TIMEOUT' ? 'The server didn’t answer in time.' : resultError(r); }
// Runs a read-only server action into SV.sys[key]; stale answers (after a newer request or
// another server) are dropped.
function sysLoad(key, action, payload, render) {
  var st = SV, b = st.sys[key];
  if (!st.id) return Promise.resolve();
  if (!st.online) { b.err = 'The server agent is offline.'; render(); return Promise.resolve(); }
  var gen = ++b.gen;
  b.loading = true; b.err = '';
  render();
  return devAct(st.id, action, payload || {}, SYS_TIMEOUT[action]).then(function (r) {
    if (gen !== b.gen) return;
    if (r && r.success !== false) { b.data = r; b.at = Date.now(); } else b.err = sysErr(r);
  }, function (e) { if (gen === b.gen && e.status !== 401) b.err = e.message; }).then(function () {
    if (gen !== b.gen) return;
    b.loading = false; b.loaded = true;
    if (SV === st) render();
  });
}
function sysShow(tab) {
  var y = SV.sys, on = SV.online;
  var need = function (b) { return !b.loaded && !b.loading && on; };
  if (tab === 'history') { if (!y.hist.loaded && !y.hist.loading) loadHistory(); else renderHistory(); }
  else if (tab === 'updates') { if (need(y.upd)) loadUpdates(); else renderUpdates(); }
  else if (tab === 'storage') { if (need(y.sto)) loadStorage(); else renderStorage(); }
  else if (tab === 'network') { if (need(y.net)) loadNetwork(); else renderNetwork(); if (need(y.sec)) loadSecurity(); else renderSecurity(); }
  else if (tab === 'services') { if (need(y.svc)) loadServices(); else renderServices(); if (need(y.jobs)) loadJobs(); else renderJobs(); }
  else if (tab === 'logs') { if (need(y.logs)) loadLogs(); else renderLogs(); }
}
function renderSysAll() {
  renderHistory(); renderUpdates(); renderStorage(); renderNetwork(); renderSecurity(); renderServices(); renderJobs(); renderLogs();
}
function offlineNote(b) { return !SV.online && !b.data ? 'The server agent is offline.' : ''; }

/* history */
function loadHistory() {
  var st = SV, b = st.sys.hist;
  if (!st.id || !S.token) return;
  var gen = ++b.gen;
  b.loading = true; b.err = '';
  renderHistory();
  api('/api/v1/server/history?hours=' + st.sys.hours + '&points=240', { timeout: 30000 }).then(function (r) {
    if (gen !== b.gen) return;
    b.data = r || {}; b.at = Date.now();
  }, function (e) { if (gen === b.gen && e.status !== 401) b.err = e.message; }).then(function () {
    if (gen !== b.gen) return;
    b.loading = false; b.loaded = true;
    if (SV === st) renderHistory();
  });
}
function renderHistory() {
  var y = SV.sys, b = y.hist;
  $$('[data-hrange]').forEach(function (x) { x.setAttribute('aria-pressed', String(+x.getAttribute('data-hrange') === y.hours)); });
  busy($('#syh-refresh'), b.loading);
  var pts = (b.data && Array.isArray(b.data.points) ? b.data.points : []).filter(function (p) { return p && isNum(p.t); })
    .slice().sort(function (p, q) { return p.t - q.t; });
  setText($('#syh-note'), b.err ? 'Couldn’t load the history: ' + b.err
    : b.loading && !b.data ? 'Loading…'
    : b.at ? pts.length + ' samples · updated ' + new Date(b.at).toLocaleTimeString() + ' · hover a chart to read a moment' : '');
  var wrap = $('#syh-charts');
  if (!b.data) { wrap.replaceChildren(); return; }
  var times = pts.map(function (p) { return p.t; });
  var col = function (k) { return pts.map(function (p) { return isNum(p[k]) ? p[k] : null; }); };
  var pct = function (v) { return Math.round(v) + '%'; };
  var cards = [
    chartCard('CPU & memory', times, [{ label: 'CPU', color: CHART_C.cpu, values: col('cpu') }, { label: 'RAM', color: CHART_C.ram, values: col('ram') }], { max: 100, format: pct }),
    chartCard('Disk & swap', times, [{ label: 'Disk', color: CHART_C.disk, values: col('disk') }, { label: 'Swap', color: CHART_C.swap, values: col('swap') }], { max: 100, format: pct }),
    chartCard('Network', times, [{ label: 'In', color: CHART_C.rx, values: col('rx_kbps') }, { label: 'Out', color: CHART_C.tx, values: col('tx_kbps') }], { format: fmtKbit, axis: fmtKbitShort }),
    chartCard('Load average', times, [{ label: 'Load', color: CHART_C.load, values: col('load') }], { format: function (v) { return v < 10 ? v.toFixed(2) : String(Math.round(v)); } })
  ];
  wrap.replaceChildren.apply(wrap, cards);
  drawHistoryCharts();
}
function drawHistoryCharts() {
  if (SV.tab !== 'history') return;
  $$('#syh-charts .chart-card').forEach(function (c) { if (c._draw) c._draw(); });
}
function chartCard(title, times, series, o) {
  var fmt = o.format, axis = o.axis || fmt;
  var top = o.max;
  if (!top) {
    var m = 0;
    series.forEach(function (s) { s.values.forEach(function (v) { if (isNum(v) && v > m) m = v; }); });
    top = niceCeil(m * 1.1);
  }
  var legend = h('div', { class: 'chart-legend' }, series.map(function (s) {
    var st = seriesStats(s.values), sw = h('i');
    sw.style.background = s.color;
    return h('span', null, sw, s.label + ' ' + (st.last == null ? '—' : fmt(st.last)) + (st.avg == null ? '' : ' · avg ' + fmt(st.avg) + ' · peak ' + fmt(st.max)));
  }));
  var box = h('div', { class: 'chart-box' });
  var card = h('div', { class: 'chart-card' }, h('div', { class: 'chart-head' }, h('h3', null, title)), legend, box);
  card._draw = function () { drawChart(box, times, series, top, axis, fmt, title); };
  return card;
}
function chartTime(ts, span) {
  var d = new Date(ts * 1000);
  if (span > 2 * 86400) return d.toLocaleDateString([], { weekday: 'short', day: 'numeric' });
  return pad2(d.getHours()) + ':' + pad2(d.getMinutes());
}
// Inline SVG line chart: grid, value and time labels, one path per series (gaps where data
// is missing or the hub was down), and a hover / touch cursor with a tooltip.
function drawChart(box, times, series, top, axis, fmt, title) {
  var W = Math.max(220, Math.round(box.clientWidth)), H = Math.max(100, Math.round(box.clientHeight || 170));
  if (!box.clientWidth) return;
  var L = 46, R = 8, T = 6, B = 20, pw = W - L - R, ph = H - T - B, base = T + ph;
  var NS = 'http://www.w3.org/2000/svg';
  var svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', title + ' chart');
  var mk = function (tag, attrs, text) {
    var e = document.createElementNS(NS, tag);
    Object.keys(attrs).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (text != null) e.textContent = text;
    svg.appendChild(e);
    return e;
  };
  for (var g = 0; g <= 4; g++) {
    var gy = (base - ph * g / 4).toFixed(1);
    mk('line', { class: 'grid', x1: L, x2: L + pw, y1: gy, y2: gy });
    mk('text', { class: 'lbl', x: L - 6, y: (+gy + 3).toFixed(1), 'text-anchor': 'end' }, axis(top * g / 4));
  }
  if (times.length < 2) {
    mk('text', { class: 'empty', x: L + pw / 2, y: T + ph / 2, 'text-anchor': 'middle' }, times.length ? 'Only one sample so far' : 'No samples yet');
    box.replaceChildren(svg);
    return;
  }
  var t0 = times[0], span = (times[times.length - 1] - t0) || 1;
  var xOf = function (i) { return L + pw * (times[i] - t0) / span; };
  var yOf = function (v) { return base - ph * Math.max(0, Math.min(1, v / top)); };
  [0, 0.5, 1].forEach(function (f, k) {
    mk('text', { class: 'lbl', x: (L + pw * f).toFixed(1), y: H - 5, 'text-anchor': ['start', 'middle', 'end'][k] }, chartTime(t0 + span * f, span));
  });
  var maxGap = span / Math.max(1, times.length - 1) * 5;
  series.forEach(function (s) {
    var d = '', area = '', open = false, lastX = 0, prevT = null;
    for (var i = 0; i < times.length; i++) {
      var v = s.values[i];
      var gap = prevT != null && times[i] - prevT > maxGap;
      if (!isNum(v) || gap) {
        if (open) area += 'L' + lastX + ' ' + base + 'Z';
        open = false;
        if (!isNum(v)) { prevT = times[i]; continue; }
      }
      var px = xOf(i).toFixed(1), py = yOf(v).toFixed(1);
      if (!open) { d += 'M' + px + ' ' + py; area += 'M' + px + ' ' + base + 'L' + px + ' ' + py; open = true; }
      else { d += 'L' + px + ' ' + py; area += 'L' + px + ' ' + py; }
      lastX = px; prevT = times[i];
    }
    if (open) area += 'L' + lastX + ' ' + base + 'Z';
    if (area) mk('path', { d: area, fill: s.color, 'fill-opacity': '0.09', stroke: 'none' });
    if (d) mk('path', { d: d, fill: 'none', stroke: s.color, 'stroke-width': '1.8', 'stroke-linejoin': 'round', 'stroke-linecap': 'round' });
  });
  var cursor = mk('line', { class: 'cursor', x1: 0, x2: 0, y1: T, y2: base, visibility: 'hidden' });
  var dots = series.map(function (s) { return mk('circle', { r: '3.5', fill: s.color, stroke: '#070913', 'stroke-width': '1.2', visibility: 'hidden' }); });
  var tip = h('div', { class: 'chart-tip' });
  tip.hidden = true;
  var pick = function (clientX) {
    var r = svg.getBoundingClientRect();
    if (!r.width) return;
    var px = (clientX - r.left) * (W / r.width);
    var target = t0 + span * Math.max(0, Math.min(1, (px - L) / pw));
    var lo = 0, hi = times.length - 1;
    while (hi - lo > 1) { var mid = (lo + hi) >> 1; if (times[mid] < target) lo = mid; else hi = mid; }
    var i = Math.abs(times[lo] - target) <= Math.abs(times[hi] - target) ? lo : hi;
    var cx = xOf(i);
    cursor.setAttribute('x1', cx); cursor.setAttribute('x2', cx); cursor.setAttribute('visibility', 'visible');
    var when = new Date(times[i] * 1000);
    var rows = [h('b', null, when.toLocaleDateString([], { weekday: 'short' }) + ' ' + pad2(when.getHours()) + ':' + pad2(when.getMinutes()))];
    series.forEach(function (s, k) {
      var v = s.values[i];
      if (!isNum(v)) dots[k].setAttribute('visibility', 'hidden');
      else { dots[k].setAttribute('cx', cx); dots[k].setAttribute('cy', yOf(v)); dots[k].setAttribute('visibility', 'visible'); }
      var sw = h('i');
      sw.style.background = s.color;
      rows.push(h('div', null, sw, s.label + ' ' + (isNum(v) ? fmt(v) : '—')));
    });
    tip.replaceChildren.apply(tip, rows);
    tip.hidden = false;
    var scale = r.width / W, left = cx * scale + 12;
    if (left + tip.offsetWidth > box.clientWidth) left = cx * scale - tip.offsetWidth - 12;
    tip.style.left = Math.max(0, left) + 'px';
  };
  var hide = function () {
    cursor.setAttribute('visibility', 'hidden');
    dots.forEach(function (dt) { dt.setAttribute('visibility', 'hidden'); });
    tip.hidden = true;
  };
  svg.addEventListener('pointermove', function (e) { pick(e.clientX); });
  svg.addEventListener('pointerdown', function (e) { pick(e.clientX); });
  svg.addEventListener('pointerleave', function (e) { if (e.pointerType === 'mouse') hide(); });
  box.replaceChildren(svg, tip);
}

/* updates */
function loadUpdates() { return sysLoad('upd', 'sys_updates', {}, renderUpdates); }
function renderUpdates() {
  var y = SV.sys, b = y.upd, d = b.data, online = SV.online;
  var pkgs = d && Array.isArray(d.packages) ? d.packages.filter(function (p) { return p && p.name; }) : [];
  var sec = d && isNum(d.security_count) ? d.security_count : 0;
  var msg = $('#syu-msg');
  msg.classList.toggle('is-error', !!b.err);
  setText(msg, b.err ? 'Couldn’t check for updates: ' + b.err
    : b.loading ? 'Checking for updates… dnf can take a few minutes.'
    : d ? (d.message || (pkgs.length ? pkgs.length + ' updates available.' : 'Everything is up to date.'))
    : offlineNote(b) || 'Not checked yet.');
  setText($('#syu-at'), b.at ? 'checked ' + new Date(b.at).toLocaleTimeString() : '');
  var chips = $('#syu-chips');
  if (d) {
    var list = [hchip(pkgs.length ? pkgs.length + (pkgs.length === 1 ? ' update' : ' updates') : 'Up to date', pkgs.length ? 'warn' : 'ok'),
      hchip(sec + ' security', sec ? 'bad' : 'ok')];
    if (d.reboot_needed) list.push(hchip('Reboot needed', 'warn'));
    if (d.newer_release) list.push(hchip('New release ' + d.newer_release, 'idle'));
    chips.replaceChildren.apply(chips, list);
  } else chips.replaceChildren();
  setText($('#syu-meta'), d && d.kernel ? 'Kernel ' + d.kernel : '');
  var rel = $('#syu-release');
  rel.hidden = !(d && d.newer_release && d.release_note);
  setText($('#syu-release-text'), d && d.release_note ? String(d.release_note) : '');
  var working = b.loading || !!y.installing;
  busy($('#syu-check'), b.loading);
  busy($('#syu-sec'), y.installing === 'security');
  busy($('#syu-all'), y.installing === 'all');
  busy($('#syu-reboot-btn'), y.rebootBusy);
  $('#syu-check').disabled = !online || working;
  $('#syu-sec').disabled = !online || working || !sec;
  $('#syu-all').disabled = !online || working || !pkgs.length;
  $('#syu-reboot-btn').disabled = !online || !!y.installing || y.rebootBusy;
  $('#syu-cancel').disabled = !online || y.rebootBusy;
  var bz = $('#syu-busy');
  bz.hidden = !y.installing;
  setText(bz, y.installing ? 'Installing ' + (y.installing === 'security' ? 'security' : 'all') + ' updates… this can take up to 30 minutes and keeps running on the server if you close this page.' : '');
  var banner = $('#syu-reboot');
  banner.hidden = !y.rebootAt;
  setText($('#syu-reboot-text'), y.rebootAt ? 'Reboot scheduled for ' + new Date(y.rebootAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) + '. Every website and the Willy hub go down for a few minutes.' : '');
  $('#syu-reboot-cancel').disabled = !online || y.rebootBusy;
  $('#syu-out-wrap').hidden = y.installOut == null;
  setText($('#syu-out-title'), y.installOk ? 'Last install' : 'Last install failed');
  var out = $('#syu-out');
  out.classList.toggle('is-error', !y.installOk);
  setText(out, y.installOut == null ? '' : (String(y.installOut).trim() || '(no output)'));
  setText($('#syu-pkg-n'), d ? String(pkgs.length) : '');
  var body = $('#syu-pkgs');
  if (!pkgs.length) {
    body.replaceChildren(h('tr', null, h('td', { colspan: '3', class: 'muted' }, d ? 'Nothing to update.' : (b.loading ? 'Checking…' : '—'))));
    return;
  }
  body.replaceChildren.apply(body, pkgs.map(function (p) {
    return h('tr', null, h('td', null, h('span', { class: 'p-name', title: String(p.name) }, String(p.name))), h('td', { class: 'mono' }, String(p.version || '')), h('td', { class: 'muted' }, String(p.repo || '')));
  }));
}
function installUpdates(securityOnly) {
  var st = SV, y = st.sys, d = y.upd.data || {};
  var n = securityOnly ? (d.security_count || 0) : (Array.isArray(d.packages) ? d.packages.length : 0);
  confirmDlg({
    title: securityOnly ? 'Install security updates?' : 'Install all updates?',
    body: (securityOnly ? 'Installs the ' + n + ' security update' + (n === 1 ? '' : 's') : 'Installs all ' + n + ' pending update' + (n === 1 ? '' : 's'))
      + ' on ' + st.name + ' with dnf. It can take up to 30 minutes and keeps running on the server if you close this page. Services may restart briefly; the server does not reboot by itself.',
    confirm: 'Install'
  }).then(function (yes) {
    if (!yes || SV !== st || !st.id) return;
    y.installing = securityOnly ? 'security' : 'all';
    y.installOut = null;
    renderUpdates();
    devAct(st.id, 'apply_updates', { security_only: !!securityOnly }, SYS_TIMEOUT.apply_updates).then(function (r) {
      var ok = !!r && r.success !== false;
      y.installOk = ok;
      y.installOut = [r && r.output ? String(r.output) : '', ok ? '' : sysErr(r)].filter(Boolean).join('\n\n');
      if (r && r.reboot_needed && y.upd.data) y.upd.data.reboot_needed = true;
      toast(ok ? (r.message || 'Updates installed.') : sysErr(r), { type: ok ? 'success' : 'error', title: ok ? 'Updates installed' : 'The update failed' });
      return ok;
    }, function (e) {
      if (e.status === 401) return false;
      y.installOk = false;
      y.installOut = e.message + '\n\nThe page lost the answer, but dnf may still be running on the server. Press Check in a few minutes to see what is left.';
      toast('The update may still be running on the server.', { type: 'warn', title: 'Lost the answer' });
      return false;
    }).then(function (ok) {
      y.installing = '';
      if (SV !== st) return;
      renderUpdates();
      if (ok) loadUpdates();
    });
  });
}
function rebootServer() {
  var st = SV, y = st.sys;
  confirmDlg({
    title: 'Reboot ' + st.name + '?',
    body: 'The server restarts 1 minute from now. Every website, app, container and the Willy hub on it go offline for a few minutes, and anything unsaved on the server is lost. You can cancel the reboot until then.',
    ack: 'I understand the websites go down while it restarts',
    confirm: 'Reboot in 1 min', danger: true
  }).then(function (yes) {
    if (!yes || SV !== st || !st.id) return;
    y.rebootBusy = true;
    renderUpdates();
    runAction(st.id, 'reboot', {}, { label: 'Reboot', timeout: SYS_TIMEOUT.reboot, ok: function (r) { return r.message || 'The server reboots in 1 minute.'; } }).then(function (r) {
      y.rebootBusy = false;
      if (r && r.success !== false) y.rebootAt = Date.now() + 60000;
      if (SV === st) renderUpdates();
    });
  });
}
function cancelReboot() {
  var st = SV, y = st.sys;
  if (!st.id) return;
  y.rebootBusy = true;
  renderUpdates();
  runAction(st.id, 'cancel_reboot', {}, { label: 'Cancel reboot', timeout: SYS_TIMEOUT.cancel_reboot, ok: function (r) { return r.message || 'Reboot cancelled.'; } }).then(function (r) {
    y.rebootBusy = false;
    if (r && r.success !== false) y.rebootAt = 0;
    if (SV === st) renderUpdates();
  });
}

/* storage */
function loadStorage() { return sysLoad('sto', 'storage', {}, renderStorage); }
function renderStorage() {
  var y = SV.sys, b = y.sto, d = b.data;
  busy($('#sys-refresh'), b.loading);
  $('#sys-refresh').disabled = !SV.online;
  var msg = $('#sys-msg');
  msg.classList.toggle('is-error', !!b.err);
  setText(msg, b.err ? 'Couldn’t read the disks: ' + b.err
    : b.loading && !d ? 'Measuring the disks and the biggest folders… this takes up to a minute.'
    : d ? (d.message || '') : offlineNote(b) || 'Not measured yet.');
  setText($('#sys-at'), b.at ? new Date(b.at).toLocaleTimeString() : '');
  var mounts = $('#sys-mounts'), clean = $('#sys-clean'), big = $('#sys-big');
  if (!d) {
    mounts.replaceChildren(); clean.replaceChildren();
    big.replaceChildren(h('tr', null, h('td', { colspan: '3', class: 'muted' }, b.loading ? 'Measuring…' : '—')));
    return;
  }
  var mrow = function (name, pct, meta) {
    var p = h('span', { class: 'pct' }, isNum(pct) ? Math.round(pct) + '%' : '—');
    p.style.color = pctColor(pct);
    return h('div', { class: 'mrow' }, h('div', { class: 'mrow-top' }, h('b', { title: name }, name), h('span', { class: 'meta', title: meta }, meta), p), sysBar(pct));
  };
  var rows = (Array.isArray(d.mounts) ? d.mounts : []).filter(function (m) { return m && m.mount; }).map(function (m) {
    return mrow(String(m.mount), m.pct, fmtGb(m.used_gb) + ' of ' + fmtGb(m.total_gb) + ' · ' + fmtGb(m.free_gb) + ' free' + (m.fs ? ' · ' + m.fs : ''));
  });
  var sw = d.swap || {};
  rows.push(mrow('Swap', sw.pct, isNum(sw.total_gb) && sw.total_gb > 0 ? fmtGb(sw.used_gb) + ' of ' + fmtGb(sw.total_gb) : 'no swap'));
  mounts.replaceChildren.apply(mounts, rows);
  var raw = d.cleanable && typeof d.cleanable === 'object' ? d.cleanable : {}, labels = d.cleanable_labels || {};
  var keys = CLEANUP_ORDER.filter(function (k) { return k in raw; }).concat(Object.keys(raw).filter(function (k) { return CLEANUP_ORDER.indexOf(k) < 0; }));
  if (!keys.length) clean.replaceChildren(h('p', { class: 'list-note' }, 'Nothing to clean.'));
  else clean.replaceChildren.apply(clean, keys.map(function (k) {
    var v = raw[k], size = isNum(v) ? fmtGb(v) : (String(v || '').trim() || 'nothing reported');
    var label = String(labels[k] || k);
    var btn = h('button', { class: 'btn btn-sm' + (k === 'trash' ? ' btn-danger' : ''), type: 'button', 'aria-label': 'Clean ' + label }, icon('trash'), h('span', null, 'Clean'));
    btn.disabled = !SV.online || y.cleaning.has(k) || (isNum(v) && v <= 0);
    busy(btn, y.cleaning.has(k));
    btn.addEventListener('click', function () { sysCleanup(k, label, size); });
    return h('div', { class: 'clean-row' }, h('div', null, h('b', null, label), h('span', null, size)), btn);
  }));
  var list = (Array.isArray(d.biggest) ? d.biggest : []).filter(function (f) { return f && f.path; }).slice().sort(function (a, c) { return (c.gb || 0) - (a.gb || 0); });
  var maxGb = list.reduce(function (m, f) { return Math.max(m, f.gb || 0); }, 0.001);
  if (!list.length) { big.replaceChildren(h('tr', null, h('td', { colspan: '3', class: 'muted' }, 'No folder sizes reported.'))); return; }
  big.replaceChildren.apply(big, list.map(function (f) {
    var fill = h('i');
    fill.style.width = Math.round((f.gb || 0) / maxGb * 100) + '%';
    fill.style.background = CHART_C.disk;
    return h('tr', null, h('td', null, h('span', { class: 'big-path', title: String(f.path) }, String(f.path))), h('td', { class: 'num' }, fmtGb(f.gb)), h('td', null, h('div', { class: 'bar thin' }, fill)));
  }));
}
function sysCleanup(key, label, size) {
  var st = SV, y = st.sys;
  confirmDlg({
    title: 'Clean ' + label.toLowerCase() + '?',
    body: (CLEANUP_WHAT[key] || 'Clears ' + label + '.') + ' Now: ' + size + '.',
    confirm: 'Clean', danger: key === 'trash'
  }).then(function (yes) {
    if (!yes || SV !== st || !st.id) return;
    y.cleaning.add(key);
    renderStorage();
    runAction(st.id, 'cleanup', { what: key }, { label: 'Cleanup', timeout: SYS_TIMEOUT.cleanup, ok: function (r) { return r.message || 'Cleaned up.'; } }).then(function (r) {
      y.cleaning.delete(key);
      if (SV !== st) return;
      renderStorage();
      if (r && r.success !== false) loadStorage();
      else if (r && r.output) textDlg({ title: 'Cleanup · ' + key, text: String(r.output), error: true });
    });
  });
}

/* network & security */
function loadNetwork() { return sysLoad('net', 'network', {}, renderNetwork); }
function loadSecurity() { return sysLoad('sec', 'security', {}, renderSecurity); }
function renderNetwork() {
  var y = SV.sys, b = y.net, d = b.data;
  busy($('#syn-refresh'), b.loading || y.sec.loading);
  $('#syn-refresh').disabled = !SV.online;
  var msg = $('#syn-msg');
  msg.classList.toggle('is-error', !!b.err);
  setText(msg, b.err ? 'Couldn’t read the network: ' + b.err : b.loading && !d ? 'Reading interfaces and ports…' : d ? (d.message || '') : offlineNote(b) || 'Not checked yet.');
  setText($('#syn-at'), b.at ? 'rate at ' + new Date(b.at).toLocaleTimeString() + ' · every 20 s' : '');
  var rate = (d && d.rate) || {};
  var tile = function (k, v) { return h('div', { class: 'rate-tile' }, h('small', null, k), h('strong', null, v)); };
  var tiles = $('#syn-rate');
  if (d) tiles.replaceChildren(tile('Down', fmtKbit(rate.down_kbps)), tile('Up', fmtKbit(rate.up_kbps)), tile('Connections', isNum(d.established) ? String(d.established) : '—'));
  else tiles.replaceChildren();
  var ifb = $('#syn-ifaces');
  var ifaces = d && Array.isArray(d.interfaces) ? d.interfaces.filter(function (i) { return i && i.interface; }) : [];
  if (!ifaces.length) ifb.replaceChildren(h('tr', null, h('td', { colspan: '3', class: 'muted' }, d ? 'No interfaces reported.' : '—')));
  else ifb.replaceChildren.apply(ifb, ifaces.map(function (i) {
    var addrs = (Array.isArray(i.addresses) ? i.addresses : []).map(function (a) { return h('span', { class: 'addr' }, String(a)); });
    return h('tr', null, h('td', null, h('b', null, String(i.interface))), h('td', null, i.state ? hchip(String(i.state), i.state === 'UP' ? 'ok' : 'idle') : '—'), h('td', { class: 'wrap' }, addrs.length ? addrs : '—'));
  }));
  var ports = d && Array.isArray(d.listening) ? d.listening.filter(Boolean).slice() : [];
  ports.sort(function (p, q) { if (!!p.public !== !!q.public) return p.public ? -1 : 1; return (parseInt(p.port, 10) || 0) - (parseInt(q.port, 10) || 0); });
  var pub = ports.filter(function (p) { return p.public; }).length;
  var pn = $('#syn-ports-n');
  setText(pn, d ? pub + ' public · ' + (ports.length - pub) + ' local' : '');
  pn.style.color = pub ? '#FCD34D' : '';
  var pb = $('#syn-ports');
  if (!ports.length) { pb.replaceChildren(h('tr', null, h('td', { colspan: '5', class: 'muted' }, d ? 'Nothing is listening.' : '—'))); return; }
  pb.replaceChildren.apply(pb, ports.map(function (p) {
    return h('tr', { class: p.public ? 'is-public' : null },
      h('td', { class: 'num' }, h('b', null, String(p.port || '?'))),
      h('td', { class: 'muted' }, String(p.proto || '')),
      h('td', { class: 'mono' }, String(p.address || '*')),
      h('td', null, p.process ? String(p.process) + (isNum(p.pid) ? ' · ' + p.pid : '') : '—'),
      h('td', null, p.public ? hchip('Public', 'warn', 'Reachable from the internet unless a firewall blocks it') : hchip('Local only', 'idle')));
  }));
}
function renderSecurity() {
  var y = SV.sys, b = y.sec, d = b.data;
  busy($('#syn-refresh'), b.loading || y.net.loading);
  var msg = $('#sysec-msg');
  msg.classList.toggle('is-error', !!b.err);
  setText(msg, b.err ? 'Couldn’t read the security status: ' + b.err : b.loading && !d ? 'Reading SSH logs and settings…' : d ? (d.message || '') : offlineNote(b));
  var kv = $('#sysec-kv'), top = $('#sysec-top');
  if (!d) {
    kv.replaceChildren(); top.replaceChildren();
    setText($('#sysec-who'), ''); setText($('#sysec-last'), ''); setText($('#sysec-who-n'), '');
    return;
  }
  var failed = isNum(d.failed_ssh_24h) ? d.failed_ssh_24h : 0;
  var pw = String(d.ssh_password_login || 'unknown'), root = String(d.root_login || 'unknown'), f2b = String(d.fail2ban || 'unknown');
  var item = function (k, chip) { return [h('dt', null, k), h('dd', null, chip)]; };
  kv.replaceChildren.apply(kv, [].concat(
    item('Failed SSH logins (24 h)', hchip(String(failed), failed === 0 ? 'ok' : failed > 100 ? 'bad' : 'warn')),
    item('SSH password login', hchip(pw === 'yes' ? 'ON' : pw, pw === 'yes' ? 'bad' : pw === 'no' ? 'ok' : 'idle', pw === 'yes' ? 'Password logins invite brute-force attempts; keys only is safer' : null)),
    item('Root login', hchip(root, root === 'no' ? 'ok' : root === 'yes' ? 'bad' : root === 'unknown' ? 'idle' : 'warn')),
    item('fail2ban', hchip(f2b, f2b === 'active' ? 'ok' : 'warn'))));
  var att = Array.isArray(d.top_attackers) ? d.top_attackers.filter(function (a) { return a && a.ip; }) : [];
  if (!att.length) top.replaceChildren(h('tr', null, h('td', { colspan: '2', class: 'muted' }, 'No failed logins in the last 24 hours.')));
  else top.replaceChildren.apply(top, att.map(function (a) { return h('tr', null, h('td', { class: 'mono' }, String(a.ip)), h('td', { class: 'num' }, isNum(a.attempts) ? a.attempts : '—')); }));
  var who = Array.isArray(d.logged_in) ? d.logged_in.filter(Boolean) : [];
  var last = Array.isArray(d.recent_logins) ? d.recent_logins.filter(Boolean) : [];
  setText($('#sysec-who-n'), String(who.length));
  setText($('#sysec-who'), who.length ? who.join('\n') : 'Nobody is logged in.');
  setText($('#sysec-last'), last.length ? last.join('\n') : 'No recent logins recorded.');
}

/* services & jobs */
function loadServices() { return sysLoad('svc', 'services_list', {}, renderServices); }
function loadJobs() { return sysLoad('jobs', 'timers', {}, renderJobs); }
function svcFailed(s) { return s.active === 'failed' || s.sub === 'failed'; }
function svcRunning(s) { return s.active === 'active' || s.active === 'activating' || s.active === 'reloading'; }
function svcList() {
  var d = SV.sys.svc.data;
  var list = d && Array.isArray(d.services) ? d.services.filter(function (s) { return s && s.name; }) : [];
  var rank = function (s) { return svcFailed(s) ? 0 : svcRunning(s) ? 1 : 2; };
  return list.slice().sort(function (a, c) { return rank(a) - rank(c) || String(a.name).localeCompare(String(c.name)); });
}
function renderServices() {
  var y = SV.sys, b = y.svc, d = b.data;
  busy($('#syv-refresh'), b.loading || y.jobs.loading);
  $('#syv-refresh').disabled = !SV.online;
  $$('[data-vfilter]').forEach(function (x) { x.setAttribute('aria-pressed', String(x.getAttribute('data-vfilter') === y.svcFilter)); });
  var all = svcList();
  var nFailed = all.filter(svcFailed).length;
  var fb = $('[data-vfilter="failed"]');
  if (fb) setText(fb, nFailed ? 'Failed ' + nFailed : 'Failed');
  var msg = $('#syv-msg');
  msg.classList.toggle('is-error', !!b.err || nFailed > 0);
  setText(msg, b.err ? 'Couldn’t list the services: ' + b.err : b.loading && !d ? 'Listing services…' : d ? (d.message || '') : offlineNote(b));
  var q = y.svcQuery.trim().toLowerCase();
  var list = all.filter(function (s) {
    var ok = y.svcFilter === 'all' || (y.svcFilter === 'running' && svcRunning(s)) || (y.svcFilter === 'failed' && svcFailed(s))
      || (y.svcFilter === 'stopped' && !svcRunning(s) && !svcFailed(s));
    return ok && (!q || String(s.name).toLowerCase().indexOf(q) >= 0 || String(s.description || '').toLowerCase().indexOf(q) >= 0);
  });
  var body = $('#syv-body');
  if (!list.length) {
    body.replaceChildren(h('tr', null, h('td', { colspan: '4', class: 'muted' }, d ? 'No services match.' : (b.loading ? 'Loading…' : '—'))));
    return;
  }
  body.replaceChildren.apply(body, list.slice(0, 400).map(svcRow));
}
function svcRow(s) {
  var y = SV.sys, name = String(s.name), isBusy = y.svcBusy.has(name), failed = svcFailed(s), running = svcRunning(s);
  var state = s.sub && s.sub !== s.active ? s.active + ' (' + s.sub + ')' : String(s.active || 'unknown');
  var boot = String(s.boot || '');
  var btn = function (label, iconName, fn, cls, disabled) {
    var x = h('button', { class: 'btn btn-sm' + (cls ? ' ' + cls : ''), type: 'button', title: label + ' ' + name, 'aria-label': label + ' service ' + name }, icon(iconName), h('span', null, label));
    x.disabled = !SV.online || isBusy || !!disabled;
    x.addEventListener('click', function () { fn(x); });
    return x;
  };
  var acts = [
    btn('Logs', 'doc', function (x) { unitLogs({ kind: 'service', name: name }, x); }),
    btn('Restart', 'reset', function () { svcControl(s, 'restart'); }),
    running ? btn('Stop', 'x', function () { svcControl(s, 'stop'); }, 'btn-danger') : btn('Start', 'play', function () { svcControl(s, 'start'); })
  ];
  if (boot === 'enabled' || boot === 'disabled') {
    acts.push(btn(boot === 'enabled' ? 'Not at boot' : 'At boot', boot === 'enabled' ? 'pause' : 'check', function () { svcBoot(s, boot !== 'enabled'); }));
  }
  return h('tr', { class: failed ? 'is-bad' : null },
    h('td', null, h('span', { class: 'svc-name', title: name }, name), s.description ? h('span', { class: 'svc-desc', title: String(s.description) }, String(s.description)) : null),
    h('td', null, isBusy ? h('span', { class: 'spinner', title: 'Working…' }) : hchip(state, failed ? 'bad' : unitHealth(s.active))),
    h('td', null, boot ? hchip(boot === 'enabled' ? 'Starts at boot' : boot === 'disabled' ? 'Not at boot' : boot, boot === 'enabled' ? 'ok' : 'idle') : '—'),
    h('td', null, h('div', { class: 'svc-acts' }, acts)));
}
function svcControl(s, action) {
  var v = UNIT_VERBS[action], st = SV, name = String(s.name);
  if (!v || !st.id) return;
  var body = action === 'restart' ? 'This restarts the service “' + name + '” on ' + st.name + '. It is briefly unavailable while it comes back.'
    : action === 'stop' ? 'This stops the service “' + name + '” on ' + st.name + '. Whatever it serves stays down until it is started again.'
    : 'This starts the service “' + name + '” on ' + st.name + '.';
  confirmDlg({ title: v[0] + ' ' + name + '?', body: body, confirm: v[0] + ' service', danger: action !== 'start' }).then(function (yes) {
    if (!yes || SV !== st) return;
    st.sys.svcBusy.add(name);
    renderServices();
    runAction(st.id, 'control', { kind: 'service', name: name, action: action }, {
      label: v[0] + ' ' + name, timeout: SYS_TIMEOUT.control, ok: function (r) { return r.message || (v[1] + ' ' + name + '.'); }
    }).then(function (r) {
      st.sys.svcBusy.delete(name);
      if (SV !== st) return;
      renderServices();
      if (r && r.success !== false) loadServices();
    });
  });
}
function svcBoot(s, enable) {
  var st = SV, name = String(s.name);
  confirmDlg({
    title: enable ? 'Start ' + name + ' at boot?' : 'Stop starting ' + name + ' at boot?',
    body: enable ? 'The service “' + name + '” will start by itself whenever ' + st.name + ' boots. Nothing changes right now.'
      : 'The service “' + name + '” will no longer start when ' + st.name + ' boots. It keeps running now; after the next reboot it stays off until started by hand.',
    confirm: enable ? 'Enable at boot' : 'Disable at boot', danger: !enable
  }).then(function (yes) {
    if (!yes || SV !== st || !st.id) return;
    st.sys.svcBusy.add(name);
    renderServices();
    runAction(st.id, 'service_boot', { name: name, enable: !!enable }, { label: 'Boot setting', timeout: SYS_TIMEOUT.service_boot, ok: function (r) { return r.message || 'Changed.'; } }).then(function (r) {
      st.sys.svcBusy.delete(name);
      if (r && r.success !== false) s.boot = enable ? 'enabled' : 'disabled';
      if (SV === st) renderServices();
    });
  });
}
function renderJobs() {
  var b = SV.sys.jobs, d = b.data;
  busy($('#syv-refresh'), b.loading || SV.sys.svc.loading);
  var cron = d && Array.isArray(d.cron) ? d.cron.filter(function (l) { return l && String(l).trim(); }) : [];
  var timers = d && Array.isArray(d.timers) ? d.timers.filter(function (t) { return t && t.timer; }) : [];
  setText($('#syj-cron-n'), d ? String(cron.length) : '');
  setText($('#syj-timers-n'), d ? String(timers.length) : '');
  var pre = $('#syj-cron');
  pre.classList.toggle('is-error', !!b.err);
  setText(pre, b.err ? 'Couldn’t read the scheduled jobs: ' + b.err : d ? (cron.length ? cron.join('\n') : 'No cron jobs for the server user.') : (b.loading ? 'Loading…' : offlineNote(b) || '—'));
  var tb = $('#syj-timers');
  if (!timers.length) { tb.replaceChildren(h('tr', null, h('td', { colspan: '2', class: 'muted' }, d ? 'No timers.' : (b.loading ? 'Loading…' : '—')))); return; }
  tb.replaceChildren.apply(tb, timers.map(function (t) {
    return h('tr', null, h('td', null, h('b', null, String(t.timer))), h('td', null, h('span', { class: 'timer-line', title: String(t.line || '') }, String(t.line || ''))));
  }));
}

/* logs */
function loadLogs() {
  var unit = $('#syl-unit').value.trim(), grep = $('#syl-grep').value.trim();
  if (!/^[A-Za-z0-9_.@-]*$/.test(unit)) {
    SV.sys.logs.err = 'A unit name has only letters, digits and . _ @ -';
    renderLogs();
    return Promise.resolve();
  }
  var payload = { lines: +$('#syl-lines').value || 200 };
  if (unit) payload.unit = unit;
  if ($('#syl-prio').value) payload.priority = $('#syl-prio').value;
  if ($('#syl-since').value) payload.since = $('#syl-since').value;
  if (grep) payload.grep = grep;
  return sysLoad('logs', 'journal', payload, renderLogs);
}
function renderLogs() {
  var b = SV.sys.logs, d = b.data;
  busy($('#syl-run'), b.loading);
  $('#syl-run').disabled = !SV.online;
  var text = d ? String(d.logs || '') : '';
  SV.sys.logsText = text;
  var lines = text.trim() ? text.replace(/\s+$/, '').split('\n').length : 0;
  setText($('#syl-note'), b.err ? '' : b.loading ? 'Reading the journal…' : d ? (lines ? lines + ' lines' : 'No matching lines') + ' · ' + new Date(b.at).toLocaleTimeString() : offlineNote(b));
  $('#syl-copy').disabled = !text;
  var out = $('#syl-out');
  out.classList.toggle('is-error', !!b.err);
  setText(out, b.err ? 'Couldn’t read the logs: ' + b.err : d ? (lines ? text : 'Nothing logged for these filters. Try a lower level or a longer time.') : (b.loading ? 'Reading the journal…' : 'Pick filters and press Show logs.'));
  if (d && !b.err) out.scrollTop = out.scrollHeight;
}

function wireSysTabs() {
  $$('[data-hrange]').forEach(function (x) {
    x.addEventListener('click', function () {
      var hrs = +x.getAttribute('data-hrange');
      if (hrs === SV.sys.hours && SV.sys.hist.data) return;
      SV.sys.hours = hrs;
      loadHistory();
    });
  });
  $('#syh-refresh').addEventListener('click', loadHistory);
  $('#syu-check').addEventListener('click', function () { if (!SV.sys.upd.loading) loadUpdates(); });
  $('#syu-sec').addEventListener('click', function () { installUpdates(true); });
  $('#syu-all').addEventListener('click', function () { installUpdates(false); });
  $('#syu-reboot-btn').addEventListener('click', rebootServer);
  $('#syu-cancel').addEventListener('click', cancelReboot);
  $('#syu-reboot-cancel').addEventListener('click', cancelReboot);
  $('#sys-refresh').addEventListener('click', function () { if (!SV.sys.sto.loading) loadStorage(); });
  $('#syn-refresh').addEventListener('click', function () { loadNetwork(); if (!SV.sys.sec.loading) loadSecurity(); });
  $('#syv-refresh').addEventListener('click', function () { if (!SV.sys.svc.loading) loadServices(); if (!SV.sys.jobs.loading) loadJobs(); });
  $('#syv-q').addEventListener('input', function () { SV.sys.svcQuery = this.value; renderServices(); });
  $$('[data-vfilter]').forEach(function (x) {
    x.addEventListener('click', function () { SV.sys.svcFilter = x.getAttribute('data-vfilter'); renderServices(); });
  });
  $('#syl-form').addEventListener('submit', function (e) { e.preventDefault(); if (!SV.sys.logs.loading) loadLogs(); });
  $('#syl-copy').addEventListener('click', function () { if (SV.sys.logsText) copyText(SV.sys.logsText); });
  var rt = 0;
  window.addEventListener('resize', function () { clearTimeout(rt); rt = setTimeout(drawHistoryCharts, 150); });
  // While on screen: the live network rate every 20 s, the history charts every minute.
  var ticks = 0;
  setInterval(function () {
    ticks++;
    if (!S.token || !SV.id || document.hidden || $('#srvp').hidden) return;
    var y = SV.sys;
    if (SV.tab === 'network' && SV.online && y.net.loaded && !y.net.loading) loadNetwork();
    if (SV.tab === 'history' && ticks % 3 === 0 && !y.hist.loading) loadHistory();
  }, 20000);
}
function sysReload(tab) {
  var y = SV.sys;
  if (tab === 'history') loadHistory();
  else if (!SV.online) return;
  else if (tab === 'storage' && !y.sto.loading) loadStorage();
  else if (tab === 'network') { loadNetwork(); if (!y.sec.loading) loadSecurity(); }
  else if (tab === 'services') { if (!y.svc.loading) loadServices(); if (!y.jobs.loading) loadJobs(); }
  else if (tab === 'logs' && !y.logs.loading) loadLogs();
}

function wireServerPanel() {
  $$('[data-stab]').forEach(function (b) { b.addEventListener('click', function () { showServerTab(b.getAttribute('data-stab')); }); });
  $('#srvp-refresh').addEventListener('click', function () {
    loadServer(false, true);
    if (SV.tab === 'files' && SV.files.loaded) loadFiles(SV.files.path);
    if (SV.tab === 'processes' && SV.online) loadServerProcs();
    if (SYS_TABS[SV.tab] && SV.tab !== 'updates') sysReload(SV.tab);
  });
  $('#ss-recheck').addEventListener('click', function () {
    if (SV.loading) return;
    loadServer(true, false);
  });
  $('#f-up').addEventListener('click', function () {
    var F = SV.files;
    if (F.path) loadFiles(F.parent && F.parent !== F.path ? F.parent : '');
  });
  $('#f-refresh').addEventListener('click', function () { var F = SV.files; loadFiles(F.err ? F.want : F.path); });
  $('#f-mkdir').addEventListener('click', newServerFolder);
  $('#f-crumbs').addEventListener('click', function (e) {
    var b = e.target.closest('[data-fpath]');
    if (b && b.getAttribute('aria-current') !== 'page') loadFiles(b.getAttribute('data-fpath'));
  });
  $('#f-body').addEventListener('click', function (e) {
    var b = e.target.closest('button');
    if (!b || b.disabled) return;
    if (b.hasAttribute('data-fpath')) { loadFiles(b.getAttribute('data-fpath')); return; }
    var entry = SV.files.entries[+b.getAttribute('data-i')];
    if (!entry) return;
    var act = b.getAttribute('data-fact');
    if (act === 'menu') fileMenu(entry); else if (act) fileAction(entry, act);
  });
  $('#term-form').addEventListener('submit', function (e) { e.preventDefault(); runShell($('#term-cmd').value); });
  $('#term-clear').addEventListener('click', function () { if (SV.running) return; SV.runs = []; renderTerm(); });
  $('#term-chips').addEventListener('click', function (e) {
    var c = e.target.closest('[data-tcmd]');
    if (!c) return;
    var input = $('#term-cmd');
    input.value = c.getAttribute('data-tcmd');
    if (!input.disabled) input.focus();
  });
  $('#term-cmd').addEventListener('keydown', function (e) {
    var hist = loadShellHist(), input = this;
    if (e.key === 'ArrowUp') {
      if (!hist.length) return;
      e.preventDefault();
      if (SV.histIdx === -1) SV.draft = input.value;
      if (SV.histIdx < hist.length - 1) SV.histIdx++;
      input.value = hist[SV.histIdx];
    } else if (e.key === 'ArrowDown') {
      if (SV.histIdx === -1) return;
      e.preventDefault();
      SV.histIdx--;
      input.value = SV.histIdx === -1 ? SV.draft : hist[SV.histIdx];
    } else if (e.key === 'Escape') {
      SV.histIdx = -1;
    }
  });
  $$('[data-psort]').forEach(function (b) {
    b.addEventListener('click', function () {
      var sort = b.getAttribute('data-psort');
      if (sort === SV.procSort) return;
      SV.procSort = sort; SV.procs = [];
      if (SV.online) loadServerProcs(); else renderServerProcs();
    });
  });
  $('#sp-refresh').addEventListener('click', loadServerProcs);
  wireSysTabs();
}

/* ========================================================= toasts + dialogs */
function toast(msg, o) {
  o = o || {};
  var type = o.type || 'info';
  var iconName = o.icon || { success: 'check', error: 'alert', warn: 'alert', alert: 'bell', info: 'info' }[type] || 'info';
  var el = h('div', { class: 'toast toast-' + type, role: type === 'error' || type === 'alert' ? 'alert' : 'status' },
    h('span', { class: 'toast-ico' }, icon(iconName)),
    h('div', null, o.title ? h('p', { class: 'toast-title' }, o.title) : null, msg ? h('p', { class: 'toast-msg' }, msg) : null));
  var x = h('button', { class: 'toast-x', type: 'button', 'aria-label': 'Dismiss' }, icon('x'));
  el.appendChild(x);
  var box = $('#toasts');
  box.appendChild(el);
  while (box.children.length > 5) box.firstElementChild.remove();
  var timer = 0;
  var close = function () {
    clearTimeout(timer);
    if (!el.isConnected || el.classList.contains('is-out')) return;
    el.classList.add('is-out');
    setTimeout(function () { el.remove(); }, REDUCED ? 0 : 200);
  };
  var arm = function (ms) { clearTimeout(timer); timer = setTimeout(close, ms); };
  arm(o.timeout || (type === 'error' ? 7000 : type === 'alert' ? 20000 : 4500));
  x.addEventListener('click', close);
  el.addEventListener('mouseenter', function () { clearTimeout(timer); });
  el.addEventListener('mouseleave', function () { arm(2500); });
  return close;
}
/* "Set up Willy": the AI brain, the owner's own login, and adding devices (one scrolling dialog). */
function setupDialog(focus) {
  Promise.all([api('/api/v1/ai').catch(function () { return null; }), api('/api/v1/auth/me').catch(function () { return null; })]).then(function (r) {
    var ai = r[0], me = r[1] && r[1].account;
    var hubUrl = location.origin + BASE;
    var form = h('form', { method: 'dialog', class: 'dlg-form', onsubmit: function (e) { e.preventDefault(); } });
    form.appendChild(h('h2', { class: 'dlg-title' }, 'Set up Willy'));

    function section(title, intro) { var el = h('section', { class: 'setup-sec', id: 'setup-s' + title.charAt(0) }, h('h3', { class: 'setup-h' }, title), intro ? h('p', { class: 'dlg-body' }, intro) : null); form.appendChild(el); return el; }
    function field(label, input) { return h('label', { class: 'field' }, h('span', null, label), input); }
    function status() { var p2 = h('p', { class: 'setup-status', role: 'status' }); return p2; }
    function say(el, text, bad) { el.textContent = text || ''; el.className = 'setup-status' + (bad ? ' is-bad' : text ? ' is-ok' : ''); }

    /* 1. AI brain (owner only) */
    if (ai) {
      var sec = section('1. AI brain', 'Willy thinks with an AI service you choose. Paste your own key; it stays on this hub and is never shown again.');
      var prov = h('select', null, ai.providers.map(function (p2) { return h('option', { value: p2.id }, p2.label); }));
      prov.value = ai.provider;
      var model = h('input', { type: 'text', maxlength: 120, autocomplete: 'off' });
      var base = h('input', { type: 'url', maxlength: 200, autocomplete: 'off', placeholder: 'http://localhost:11434/v1' });
      var key = h('input', { type: 'password', maxlength: 300, autocomplete: 'off', spellcheck: 'false' });
      var getKey = h('a', { href: '#', target: '_blank', rel: 'noopener noreferrer', class: 'setup-link' }, 'Get a key');
      var baseField = field('Address of the service (OpenAI-compatible)', base);
      var out1 = status();
      function sync() {
        var spec = ai.providers.filter(function (p2) { return p2.id === prov.value; })[0];
        var current = prov.value === ai.provider;
        model.placeholder = spec.default_model || 'model name, e.g. llama3.1';
        model.value = current ? ai.model : '';
        base.value = current ? ai.base_url : '';
        baseField.hidden = prov.value !== 'custom';
        key.placeholder = spec.key ? 'Saved key ' + spec.key + ' (leave empty to keep it)' : (spec.needs_key ? 'Paste your API key' : 'Not needed for local models');
        key.value = '';
        getKey.hidden = !spec.key_url; getKey.href = spec.key_url || '#';
      }
      prov.addEventListener('change', sync); sync();
      var save = h('button', { class: 'btn btn-primary', type: 'button' }, 'Test and save');
      save.addEventListener('click', function () {
        save.disabled = true; say(out1, 'Testing with your provider…');
        api('/api/v1/ai', { method: 'POST', timeout: 60000, body: { provider: prov.value, model: model.value.trim(), api_key: key.value.trim(), base_url: base.value.trim() } })
          .then(function (res) {
            save.disabled = false; ai = res; sync();
            say(out1, 'Saved. Willy now uses ' + res.provider + ' (' + res.model + ').');
            refreshStats();
          }, function (err) { save.disabled = false; say(out1, err.message, true); });
      });
      sec.appendChild(field('Provider', prov));
      sec.appendChild(field('Model (optional)', model));
      sec.appendChild(baseField);
      sec.appendChild(field('API key', key));
      sec.appendChild(h('div', { class: 'setup-row' }, save, getKey));
      sec.appendChild(out1);

      /* Speech-to-text needs a Groq or OpenAI key, whichever AI does the thinking. */
      var vprov = h('select', null, h('option', { value: 'groq' }, 'Groq'), h('option', { value: 'openai' }, 'OpenAI'));
      var vkey = h('input', { type: 'password', maxlength: 300, autocomplete: 'off', spellcheck: 'false' });
      var vout = status();
      var vsave = h('button', { class: 'btn btn-ghost btn-sm', type: 'button' }, 'Test and save voice key');
      function vsync() {
        var spec = ai.providers.filter(function (p2) { return p2.id === vprov.value; })[0];
        vkey.placeholder = spec && spec.key ? 'Saved key ' + spec.key + ' (leave empty to keep it)' : 'Paste a ' + vprov.options[vprov.selectedIndex].text + ' key';
      }
      vprov.addEventListener('change', vsync); vsync();
      vsave.addEventListener('click', function () {
        if (!vkey.value.trim()) { say(vout, 'Paste a key first.', true); return; }
        vsave.disabled = true; say(vout, 'Testing…');
        api('/api/v1/ai/voice', { method: 'POST', timeout: 60000, body: { provider: vprov.value, api_key: vkey.value.trim() } })
          .then(function (res) { vsave.disabled = false; ai = res; vkey.value = ''; vsync(); say(vout, 'Saved. Voice input will use it.'); },
                function (err) { vsave.disabled = false; say(vout, err.message, true); });
      });
      sec.appendChild(h('p', { class: 'dlg-body' }, 'Talking to Willy (voice calls, \u201cHey Willy\u201d) needs a Groq or OpenAI key for speech-to-text. If your AI above is Gemini or another service, add one here too.'));
      sec.appendChild(h('div', { class: 'setup-row' }, vprov, vkey));
      sec.appendChild(h('div', { class: 'setup-row' }, vsave));
      sec.appendChild(vout);
    }

    /* 2. The owner's login */
    if (me && me.via !== 'device_key') {
      var sec2 = section('2. Your login', 'You are signed in as ' + (me.email && me.email.indexOf('@local.willy') < 0 ? me.email : 'the hub owner') +
        '. Add an email and password to sign in later without the private link' + (ai ? ' (Google sign-in is on the login page too)' : '') + '.');
      var em = h('input', { type: 'text', maxlength: 200, autocomplete: 'username', placeholder: 'you@example.com' });
      if (me.email && me.email.indexOf('@local.willy') < 0) { em.value = me.email; em.readOnly = true; }
      var pw = h('input', { type: 'password', maxlength: 200, autocomplete: 'new-password', placeholder: 'At least 10 characters' });
      var out2 = status();
      var save2 = h('button', { class: 'btn', type: 'button' }, 'Save login');
      save2.addEventListener('click', function () {
        save2.disabled = true; say(out2, 'Saving…');
        api('/api/v1/account/password', { method: 'POST', body: { password: pw.value, email: em.readOnly ? null : em.value.trim() } })
          .then(function () { save2.disabled = false; pw.value = ''; say(out2, 'Saved. You can now sign in with this email and password.'); },
                function (err) { save2.disabled = false; say(out2, err.message, true); });
      });
      sec2.appendChild(field('Email', em));
      sec2.appendChild(field('Password', pw));
      sec2.appendChild(h('div', { class: 'setup-row' }, save2));
      sec2.appendChild(out2);
    }

    /* 3. Devices: a one-time code for the phone app and the Windows app */
    var sec3 = section('3. Add your phone and PC', 'Enter this code in the Willy app on your phone (Settings \u2192 Sign in with a code) and in WillyPC on Windows, together with the hub address.');
    var code = h('div', { class: 'setup-code' }, '\u2026');
    var out3 = status();
    var again = h('button', { class: 'btn btn-ghost btn-sm', type: 'button' }, 'New code');
    function newCode() {
      say(out3, '');
      api('/api/v1/pair/link', { method: 'POST', body: {} }).then(function (res) {
        code.textContent = res.code; hubLine.textContent = res.hub_url || hubUrl;
        say(out3, 'The code works once and expires in 10 minutes.');
      }, function (err) { code.textContent = '\u2014'; say(out3, err.message, true); });
    }
    var hubLine = h('div', { class: 'setup-hub' }, hubUrl);
    again.addEventListener('click', newCode);
    sec3.appendChild(code);
    sec3.appendChild(h('div', { class: 'setup-row' }, h('span', { class: 'dlg-body' }, 'Hub address: '), hubLine, again));
    sec3.appendChild(out3);
    sec3.appendChild(h('p', { class: 'dlg-body' }, 'On a server, run: python -m server_agent.pair --hub ' + hubUrl + '  (the installer already did this for the server it ran on).'));
    newCode();

    form.appendChild(h('div', { class: 'dlg-actions' }, h('button', { class: 'btn btn-primary', type: 'submit', value: 'ok' }, 'Done')));
    openDialog(form, function () {}, function () { window.alert('Hub: ' + hubUrl); return 'ok'; });
    if (focus) { var target = form.querySelector('#setup-' + focus); if (target && target.scrollIntoView) target.scrollIntoView({ block: 'start' }); }
  });
}
function maybeFirstRunSetup() {
  var seen = false; try { seen = sessionStorage.getItem('willy_setup_prompted') === '1'; } catch (e) { /* ignore */ }
  if (seen) return;
  api('/api/v1/ai').then(function (ai) {
    if (ai && !ai.ready) { try { sessionStorage.setItem('willy_setup_prompted', '1'); } catch (e) { /* ignore */ } setupDialog('1'); }
  }, function () { /* not the owner: nothing to set up */ });
}
function openDialog(form, onClose, fallback, wide) {
  var dlg = $('#dlg');
  // Browsers without <dialog> (Safari < 15.4) fall back to native prompts.
  if (typeof dlg.showModal !== 'function') { onClose(fallback ? fallback() : 'cancel'); return; }
  if (dlg.open) dlg.close();
  dlg.classList.toggle('dlg-wide', !!wide);
  dlg.returnValue = '';
  dlg.replaceChildren(form);
  var done = function () { dlg.removeEventListener('close', done); onClose(dlg.returnValue); };
  dlg.addEventListener('close', done);
  dlg.showModal();
}
// With `ack`, the confirm button stays disabled until the user ticks that sentence.
function confirmDlg(o) {
  return new Promise(function (resolve) {
    var cancel = h('button', { class: 'btn btn-ghost', type: 'submit', value: 'cancel' }, 'Cancel');
    var ok = h('button', { class: 'btn ' + (o.danger ? 'btn-danger' : 'btn-primary'), type: 'submit', value: 'ok' }, o.confirm || 'Confirm');
    var ack = null;
    if (o.ack) {
      var tick = h('input', { type: 'checkbox' });
      ok.disabled = true;
      tick.addEventListener('change', function () { ok.disabled = !tick.checked; });
      ack = h('label', { class: 'dlg-ack' }, tick, h('span', null, o.ack));
    }
    var form = h('form', { method: 'dialog', class: 'dlg-form' },
      h('h2', { class: 'dlg-title' }, o.title),
      o.body ? h('p', { class: 'dlg-body' }, o.body) : null,
      ack,
      h('div', { class: 'dlg-actions' }, cancel, ok));
    openDialog(form, function (v) { resolve(v === 'ok'); }, function () {
      return window.confirm(o.title + (o.body ? '\n\n' + o.body : '') + (o.ack ? '\n\n' + o.ack + '?' : '')) ? 'ok' : 'cancel';
    });
    if ($('#dlg').open) cancel.focus();
  });
}
function formDlg(o) {
  return new Promise(function (resolve) {
    var inputs = {}, result = null;
    var fields = o.fields.map(function (f) {
      var input = f.type === 'textarea'
        ? h('textarea', { rows: f.rows || 3, maxlength: f.max || 1000, placeholder: f.placeholder || null })
        : h('input', { type: f.type || 'text', maxlength: f.max || 200, placeholder: f.placeholder || null });
      if (f.value) input.value = f.value;
      inputs[f.name] = input;
      return h('label', { class: 'field' }, h('span', null, f.label), input);
    });
    var err = h('p', { class: 'form-err', role: 'alert' });
    err.hidden = true;
    var dlg = $('#dlg');
    var form = h('form', { class: 'dlg-form', novalidate: true },
      h('h2', { class: 'dlg-title' }, o.title),
      o.desc ? h('p', { class: 'dlg-body' }, o.desc) : null,
      fields, err,
      h('div', { class: 'dlg-actions' },
        h('button', { class: 'btn btn-ghost', type: 'button', onclick: function () { dlg.close('cancel'); } }, 'Cancel'),
        h('button', { class: 'btn btn-primary', type: 'submit' }, o.submit || 'Send')));
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var out = {};
      Object.keys(inputs).forEach(function (k) { out[k] = inputs[k].value.trim(); });
      var missing = o.fields.filter(function (f) { return f.required && !out[f.name]; })[0];
      if (missing) { err.textContent = missing.label + ' can’t be empty.'; err.hidden = false; inputs[missing.name].focus(); return; }
      result = out;
      dlg.close('ok');
    });
    openDialog(form, function (v) { resolve(v === 'ok' ? result : null); }, function () {
      var out = {};
      for (var i = 0; i < o.fields.length; i++) {
        var f = o.fields[i];
        var v = window.prompt(f.label, f.value || '');
        if (v === null) return 'cancel';
        out[f.name] = v.trim();
        if (f.required && !out[f.name]) return 'cancel';
      }
      result = out;
      return 'ok';
    });
    var first = fields.length ? inputs[o.fields.filter(function (f) { return !f.value; }).concat(o.fields)[0].name] : null;
    if (first && $('#dlg').open) first.focus();
  });
}

/* ============================================================ ticker, wiring */
function tick() {
  $$('[data-ts]').forEach(function (el) {
    var ts = parseFloat(el.getAttribute('data-ts'));
    if (!isNum(ts)) return;
    setText(el, el.getAttribute('data-fmt') === 'short' ? agoShort(ts) : ago(ts));
  });
  cards.forEach(function (c) {
    var d = S.devices.get(c.id);
    if (d && c.refs.seen) c.refs.seen.classList.toggle('is-stale', !!d.online && isNum(d.last_seen) && nowSec() - d.last_seen > 10);
  });
  renderUptime();
  renderConn();
}
function wireApp() {
  wireConsole();
  wireReminders();
  wireTools();
  wireScreen();
  wireServerPanel();
  $('#call-link').setAttribute('href', BASE + '/call');
  var wsu = (location.protocol === 'https:' ? 'wss:' : 'ws:') + '//' + location.host + BASE + '/ws/devices';
  setText($('#ws-url'), wsu);
  $('#signout').addEventListener('click', function () { signOut(''); });
  $('#add-device').addEventListener('click', function () { setupDialog('3'); });
  $('#sec-dismiss').addEventListener('click', function () { session.set(KEYS.banner, '1'); $('#sec-banner').hidden = true; });
  $('#filters').addEventListener('click', function (e) {
    var b = e.target.closest('[data-src]');
    if (b) setFilter(b.getAttribute('data-src'));
  });
  document.addEventListener('click', function (e) { if (!e.target.closest('.menu-wrap')) closeMenus(); });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closeMenus(true);
    if (e.key === '/' && !e.ctrlKey && !e.metaKey && !e.altKey) {
      var a = document.activeElement;
      var typing = a && (a.tagName === 'INPUT' || a.tagName === 'TEXTAREA' || a.tagName === 'SELECT' || a.isContentEditable);
      if (!typing && !$('#dlg').open && !$('#screen').open && S.token) { e.preventDefault(); $('#prompt').focus(); }
    }
  });
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) return;
    wsKick();
    if (S.token) { refreshStats(); if (dirty.size) flush(); }
    if (SCR.autoPaused && $('#screen').open && !SCR.paused) { SCR.autoPaused = false; startScreenLoop(); }
    if (S.token && SV.id && Date.now() - SV.healthAt > 60000) loadServer(false, false);
  });
  window.addEventListener('online', wsKick);
  window.addEventListener('offline', function () { if (!WS.sock && S.token) setConn('offline'); });
  var unlock = function () { unlockBeep(); };
  document.addEventListener('pointerdown', unlock, true);
  document.addEventListener('keydown', unlock, true);
}

wireLogin();
boot();
})();
</script>
</body>
</html>
"""


def get_dashboard_html() -> str:
    """Returns the dashboard page. It contains no secrets; auth happens in the browser."""
    from server.google_signin_js import GOOGLE_ICON_SVG, GOOGLE_SIGNIN_JS

    return (DASHBOARD_HTML.replace("__GOOGLE_ICON__", GOOGLE_ICON_SVG)
            .replace("<script>\n(function () {\n'use strict';", "<script>" + GOOGLE_SIGNIN_JS + "</script>\n<script>\n(function () {\n'use strict';", 1))
