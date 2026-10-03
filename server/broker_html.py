"""
Pages for hub-to-master Google sign-in:
- /broker/login    (master only) "Sign in to <hub>" with Google, then back to the hub with a signed token
- /auth/callback   (any hub)     receives that token in the URL fragment and signs the person in
- /master          (master only) the owner's overview of sign-ins, hubs and usage
"""

from server.google_signin_js import GOOGLE_ICON_SVG, GOOGLE_SIGNIN_JS

_BASE_CSS = """
:root { --bg:#EDF1F3; --card:#F8FAFB; --ink:#0B1F33; --muted:#44566A; --line:#C9D4DA; --accent:#0C7C7E; --bad:#B42318; }
* { box-sizing:border-box; }
body { margin:0; min-height:100vh; display:grid; place-items:center; padding:16px; background:var(--bg); color:var(--ink);
       font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
.card { width:100%; max-width:440px; background:var(--card); border:1px solid var(--line); border-radius:16px; padding:28px 24px; }
h1 { font-size:21px; margin:0 0 8px; letter-spacing:-.01em; } p { margin:0 0 14px; color:var(--muted); }
.hub { font:500 14px ui-monospace,Consolas,monospace; background:#E2E9ED; border-radius:8px; padding:8px 10px; word-break:break-all; color:var(--ink); }
button { width:100%; display:flex; gap:10px; align-items:center; justify-content:center; padding:12px 16px; border-radius:10px;
         font:600 15px system-ui,sans-serif; cursor:pointer; border:1px solid #dadce0; background:#fff; color:#1f1f1f; }
button:disabled { opacity:.6; cursor:default; }
.msg { margin-top:14px; font-size:14px; min-height:1.4em; } .msg.bad { color:var(--bad); }
:focus-visible { outline:3px solid var(--accent); outline-offset:3px; }
"""

_LOGIN = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in · Willy</title><style>__CSS__</style></head><body>
<main class="card">
  <h1>Sign in with Google</h1>
  <p>You are signing in to this Willy hub:</p>
  <div class="hub" id="hub">…</div>
  <p style="margin-top:14px">Only continue if you set up or were invited to this hub. truewilly.com shares your name and email with it and keeps a record of the sign-in.</p>
  <button id="go" type="button" disabled>__GOOGLE__<span>Continue with Google</span></button>
  <p class="msg" id="msg" role="status"></p>
</main>
<script>__GOOGLE_JS__</script>
<script>
(function () {
  'use strict';
  var BASE = location.pathname.replace(/\/broker\/login\/?$/, '');
  var q = new URLSearchParams(location.search);
  var hub = q.get('hub') || '', state = q.get('state') || '';
  var $ = function (id) { return document.getElementById(id); };
  function say(t, bad) { $('msg').textContent = t || ''; $('msg').className = 'msg' + (bad ? ' bad' : ''); }
  $('hub').textContent = hub || '(missing hub address)';
  if (!/^https?:\/\/[^\/\s]+$/.test(hub) || !state) { say('This sign-in link is incomplete. Start again from your hub.', true); return; }
  $('go').disabled = false;
  $('go').addEventListener('click', function () {
    var b = this; b.disabled = true; say('Opening Google…');
    WillyGoogle.idToken(BASE).then(function (idToken) {
      if (!idToken) { b.disabled = false; say(''); return null; }
      say('Signing you in to the hub…');
      return fetch(BASE + '/api/v1/broker/token', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id_token: idToken, hub: hub, state: state }) })
        .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) {
          if (!r.ok || !d.token) throw new Error(d.error || d.detail || ('Sign-in failed (HTTP ' + r.status + ').'));
          WillyGoogle.signOut();
          location.href = hub + '/auth/callback#token=' + encodeURIComponent(d.token) + '&state=' + encodeURIComponent(state);
        }); });
    }).catch(function (e) { b.disabled = false; say(e.message, true); });
  });
})();
</script></body></html>"""

_CALLBACK = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Signing in · Willy</title><style>__CSS__</style></head><body>
<main class="card"><h1>Signing you in…</h1><p class="msg" id="msg" role="status">One moment.</p></main>
<script>
(function () {
  'use strict';
  var BASE = location.pathname.replace(/\/auth\/callback\/?$/, '');
  var frag = new URLSearchParams(location.hash.replace(/^#/, ''));
  var token = frag.get('token') || '', state = frag.get('state') || '';
  history.replaceState(null, '', location.pathname);   // the token never stays in the address bar
  function fail(t) { document.querySelector('h1').textContent = 'Sign-in didn’t finish'; var m = document.getElementById('msg'); m.textContent = t; m.className = 'msg bad';
    m.insertAdjacentHTML('afterend', '<p><a href="' + (BASE || '/') + '">Back to the hub</a></p>'); }
  var expected = ''; try { expected = sessionStorage.getItem('willy_broker_state') || ''; sessionStorage.removeItem('willy_broker_state'); } catch (e) {}
  if (!token || !state) return fail('No sign-in came back. Start again from the hub.');
  if (!expected || expected !== state) return fail('This sign-in was not started from this browser. Start again from the hub.');
  fetch(BASE + '/api/v1/auth/broker', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Willy-Client': 'web' },
    body: JSON.stringify({ token: token, state: state }) })
    .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) {
      if (!r.ok || !d.token) throw new Error(d.error || d.detail || ('Sign-in failed (HTTP ' + r.status + ').'));
      try { localStorage.setItem('willy_token', d.token); } catch (e) {}
      var next = ''; try { next = sessionStorage.getItem('willy_after_login') || ''; sessionStorage.removeItem('willy_after_login'); } catch (e) {}
      location.replace(next && next.charAt(0) === '/' && next.charAt(1) !== '/' ? next : (BASE || '/'));
    }); }).catch(function (e) { fail(e.message); });
})();
</script></body></html>"""

_MASTER = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Network · Willy master</title><style>
:root { --bg:#EDF1F3; --card:#F8FAFB; --ink:#0B1F33; --muted:#44566A; --line:#C9D4DA; --accent:#0C7C7E; }
* { box-sizing:border-box; } body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
.wrap { width:min(1100px,100% - 32px); margin:24px auto 60px; } h1 { margin:0 0 4px; font-size:26px; } .sub { color:var(--muted); margin:0 0 22px; }
.tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin-bottom:26px; }
.tile { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px 16px; } .tile b { display:block; font-size:28px; letter-spacing:-.02em; } .tile span { color:var(--muted); font-size:13px; }
h2 { font-size:18px; margin:26px 0 10px; } .scroll { overflow-x:auto; background:var(--card); border:1px solid var(--line); border-radius:12px; }
table { width:100%; border-collapse:collapse; min-width:640px; } th,td { text-align:left; padding:9px 12px; border-bottom:1px solid var(--line); white-space:nowrap; }
th { font-size:12px; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); } tr:last-child td { border-bottom:0; }
.tools span { display:inline-block; margin:2px 6px 2px 0; padding:2px 8px; border-radius:99px; background:#E2E9ED; font-size:13px; }
.note { color:var(--muted); font-size:13px; margin-top:8px; } .err { color:#B42318; }
</style></head><body><div class="wrap">
<h1>Willy network</h1><p class="sub">People who signed in through this master, and the hubs that check in. Counts only: never commands, files or conversations.</p>
<div id="out"><p>Loading…</p></div></div>
<script>
(function () {
  'use strict';
  var BASE = location.pathname.replace(/\/master\/?$/, '');
  var token = ''; try { token = localStorage.getItem('willy_token') || ''; } catch (e) {}
  var out = document.getElementById('out');
  function get(p) { return fetch(BASE + p, { headers: { Authorization: 'Bearer ' + token } }).then(function (r) {
    if (r.status === 401 || r.status === 403 || r.status === 404) throw new Error('Sign in as the owner of this master hub on the dashboard first, then reopen this page.');
    if (!r.ok) throw new Error('The hub answered HTTP ' + r.status); return r.json(); }); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function when(ts) { if (!ts) return ''; var d = new Date(ts * 1000); return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); }
  Promise.all([get('/api/v1/master/summary'), get('/api/v1/master/users'), get('/api/v1/master/hubs')]).then(function (r) {
    var s = r[0].summary, users = r[1].users, hubs = r[2].hubs;
    var tools = Object.keys(s.top_tools_7d).map(function (k) { return '<span>' + esc(k) + ' · ' + s.top_tools_7d[k] + '</span>'; }).join('') || '<span>none yet</span>';
    var html = '<div class="tiles">' +
      '<div class="tile"><b>' + s.users + '</b><span>people signed in (' + s.users_active_7d + ' active this week)</span></div>' +
      '<div class="tile"><b>' + s.hubs + '</b><span>hubs registered (' + s.hubs_active_7d + ' seen this week)</span></div>' +
      '<div class="tile"><b>' + (s.devices.pc + s.devices.mobile + s.devices.server) + '</b><span>devices: ' + s.devices.pc + ' PC, ' + s.devices.mobile + ' phone, ' + s.devices.server + ' server</span></div>' +
      '<div class="tile"><b>' + s.commands_7d + '</b><span>commands in 7 days (' + s.failures_7d + ' failed)</span></div></div>' +
      '<h2>What people use (7 days)</h2><div class="tools">' + tools + '</div>' +
      '<h2>Hubs</h2><div class="scroll"><table><thead><tr><th>Address</th><th>Owner</th><th>Version</th><th>PC</th><th>Phone</th><th>Server</th><th>AI</th><th>Last seen</th></tr></thead><tbody>' +
      hubs.map(function (h) { return '<tr><td>' + esc(h.origin || '(not set)') + '</td><td>' + esc(h.owner_email || '') + '</td><td>' + esc(h.version) + '</td><td>' + (h.pcs || 0) + '</td><td>' + (h.phones || 0) + '</td><td>' + (h.servers || 0) + '</td><td>' + esc(h.ai_provider) + '</td><td>' + when(h.last_seen) + '</td></tr>'; }).join('') +
      '</tbody></table></div><p class="note">Check-ins are self-reported by each hub and can be spoofed.</p>' +
      '<h2>People</h2><div class="scroll"><table><thead><tr><th>Email</th><th>Name</th><th>Sign-ins</th><th>First seen</th><th>Last seen</th><th>Last hub</th></tr></thead><tbody>' +
      users.map(function (u) { return '<tr><td>' + esc(u.email) + '</td><td>' + esc(u.name) + '</td><td>' + Math.round(u.logins) + '</td><td>' + when(u.first_seen) + '</td><td>' + when(u.last_seen) + '</td><td>' + esc(u.last_hub) + '</td></tr>'; }).join('') +
      '</tbody></table></div>';
    out.innerHTML = html;
  }).catch(function (e) { out.innerHTML = '<p class="err">' + esc(e.message) + '</p>'; });
})();
</script></body></html>"""


def get_broker_login_html() -> str:
    return _LOGIN.replace("__CSS__", _BASE_CSS).replace("__GOOGLE_JS__", GOOGLE_SIGNIN_JS).replace("__GOOGLE__", GOOGLE_ICON_SVG)


def get_auth_callback_html() -> str:
    return _CALLBACK.replace("__CSS__", _BASE_CSS)


def get_master_html() -> str:
    return _MASTER
