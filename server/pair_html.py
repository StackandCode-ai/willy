"""
/pair: approve a new device (PC app, server agent) for your Willy account.

The device shows a code and opens this page with ?code=...; you sign in with Google (or are
already signed in from the dashboard, same browser storage) and press Approve. The device,
which is polling, then receives its own key.
"""

from server.google_signin_js import GOOGLE_ICON_SVG, GOOGLE_SIGNIN_JS

_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Add a device · Willy</title>
<style>
:root { --bg:#070913; --card:#10142a; --line:#232a4a; --text:#e8ebff; --muted:#9aa3c7; --accent:#7c8cff; --ok:#3ddc97; --bad:#ff6b81; }
* { box-sizing:border-box; }
body { margin:0; min-height:100vh; display:grid; place-items:center; padding:16px; background:var(--bg); color:var(--text);
       font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
.card { width:100%; max-width:420px; background:var(--card); border:1px solid var(--line); border-radius:16px; padding:28px 24px; }
h1 { font-size:20px; margin:0 0 6px; }
p { margin:0 0 14px; color:var(--muted); }
.code { font:600 26px/1.2 ui-monospace,Consolas,monospace; letter-spacing:.12em; text-align:center; padding:14px; margin:6px 0 16px;
        border:1px dashed var(--line); border-radius:12px; color:var(--text); }
.dev { display:flex; gap:12px; align-items:center; padding:12px 14px; border:1px solid var(--line); border-radius:12px; margin-bottom:16px; }
.dev b { display:block; color:var(--text); }
.dev span { color:var(--muted); font-size:13px; }
button { width:100%; display:flex; gap:10px; align-items:center; justify-content:center; padding:12px 16px; border-radius:10px;
         font:600 15px system-ui,sans-serif; cursor:pointer; border:1px solid var(--line); background:#1a2040; color:var(--text); }
button.primary { background:var(--accent); border-color:var(--accent); color:#0b0e22; }
button.google { background:#fff; color:#1f1f1f; border-color:#dadce0; }
button:disabled { opacity:.6; cursor:default; }
input { width:100%; padding:12px; border-radius:10px; border:1px solid var(--line); background:#0b0f22; color:var(--text);
        font:600 18px ui-monospace,Consolas,monospace; letter-spacing:.1em; text-align:center; text-transform:uppercase; margin-bottom:12px; }
.msg { margin-top:14px; font-size:14px; }
.msg.ok { color:var(--ok); } .msg.bad { color:var(--bad); }
.who { font-size:13px; color:var(--muted); margin-top:14px; text-align:center; }
[hidden] { display:none !important; }
</style>
</head>
<body>
<main class="card">
  <h1>Add a device to Willy</h1>
  <p id="lead">Check the code matches the one on your device, then approve it.</p>

  <section id="enter" hidden>
    <input id="code-in" maxlength="9" placeholder="ABCD-EFGH" autocomplete="off" spellcheck="false" aria-label="Pairing code">
    <button id="code-go" type="button">Continue</button>
  </section>

  <div class="code" id="code" hidden></div>
  <div class="dev" id="dev" hidden><div><b id="dev-name"></b><span id="dev-type"></span></div></div>

  <button class="google" id="signin" type="button" hidden>__GOOGLE__<span>Sign in with Google to approve</span></button>
  <button class="primary" id="approve" type="button" hidden>Approve this device</button>
  <p class="msg" id="msg" role="status"></p>
  <p class="who" id="who" hidden></p>
</main>
<script>__GOOGLE_JS__</script>
<script>
(function () {
  'use strict';
  var BASE = location.pathname.replace(/\/pair\/?$/, '');
  var TOKEN_KEY = 'willy_token';
  var $ = function (id) { return document.getElementById(id); };
  var code = (new URLSearchParams(location.search).get('code') || '').toUpperCase().trim();

  function token() { try { return localStorage.getItem(TOKEN_KEY); } catch (e) { return null; } }
  function setToken(t) { try { localStorage.setItem(TOKEN_KEY, t); } catch (e) {} }
  function say(text, kind) { var m = $('msg'); m.textContent = text || ''; m.className = 'msg' + (kind ? ' ' + kind : ''); }
  function api(path, opts) {
    opts = opts || {};
    opts.headers = Object.assign({ 'Content-Type': 'application/json', 'X-Willy-Client': 'web' }, opts.headers || {});
    var t = token(); if (t) opts.headers.Authorization = 'Bearer ' + t;
    return fetch(BASE + path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) { d._status = r.status; return d; });
    });
  }
  function showCode() {
    $('enter').hidden = true;
    $('code').hidden = false; $('code').textContent = code;
  }
  function needSignIn() {
    $('approve').hidden = true;
    WillyGoogle.config(BASE).then(function (cfg) {
      if (cfg && (cfg.google || (cfg.broker && cfg.broker.url))) { $('signin').hidden = false; say(''); }
      else say('Sign in on the Willy dashboard of this hub first, then reopen this link.', 'bad');
    });
  }
  function load() {
    showCode();
    if (!token()) { needSignIn(); return; }
    api('/api/v1/pair/info?code=' + encodeURIComponent(code)).then(function (d) {
      if (d._status === 401) { try { localStorage.removeItem(TOKEN_KEY); } catch (e) {} needSignIn(); return; }
      if (!d.success) { say(d.error || 'That code has expired. Start pairing again on the device.', 'bad'); return; }
      $('dev').hidden = false;
      $('dev-name').textContent = d.name || d.device_id || 'Device';
      $('dev-type').textContent = ({ pc: 'Windows PC', server: 'Server', mobile: 'Phone' })[d.device_type] || d.device_type || '';
      if (d.approved) { done(); return; }
      $('signin').hidden = true; $('approve').hidden = false;
      api('/api/v1/auth/me').then(function (me) {
        if (me.account && me.account.email) { $('who').hidden = false; $('who').textContent = 'Signed in as ' + me.account.email; }
      });
    });
  }
  function done() {
    $('approve').hidden = true; $('signin').hidden = true;
    $('lead').textContent = 'All set.';
    say('Approved. The device connects to your Willy in a few seconds; you can close this page.', 'ok');
  }
  function signedIn(res) {
    if (!res) return;
    setToken(res.token);
    load();
  }

  $('signin').addEventListener('click', function () {
    var b = this; b.disabled = true; say('Opening Google sign-in…');
    WillyGoogle.signIn(BASE).then(function (res) { b.disabled = false; if (!res) { say(''); return; } signedIn(res); },
      function (err) { b.disabled = false; say(err.message, 'bad'); });
  });
  $('approve').addEventListener('click', function () {
    var b = this; b.disabled = true; say('Approving…');
    api('/api/v1/pair/approve', { method: 'POST', body: JSON.stringify({ code: code }) }).then(function (d) {
      b.disabled = false;
      if (d.success) done(); else say(d.error || d.detail || 'Couldn’t approve that code.', 'bad');
    }, function () { b.disabled = false; say('No connection to the hub. Try again.', 'bad'); });
  });
  $('code-go').addEventListener('click', function () {
    var v = $('code-in').value.toUpperCase().replace(/[^A-Z0-9]/g, '');
    if (v.length !== 8) { say('The code has 8 letters and numbers, like ABCD-EFGH.', 'bad'); return; }
    code = v.slice(0, 4) + '-' + v.slice(4);
    history.replaceState(null, '', location.pathname + '?code=' + code);
    load();
  });

  WillyGoogle.finishRedirect(BASE).then(function (res) {
    if (res) setToken(res.token);
    if (code) load(); else { $('enter').hidden = false; $('lead').textContent = 'Enter the code shown on your device.'; }
  }, function (err) { say(err.message, 'bad'); if (code) load(); });
})();
</script>
</body>
</html>
"""


def get_pair_html() -> str:
    return _PAGE.replace("__GOOGLE_JS__", GOOGLE_SIGNIN_JS).replace("__GOOGLE__", GOOGLE_ICON_SVG)
