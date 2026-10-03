"""
Google sign-in for the hub's web pages (dashboard, voice call, device pairing).

The page loads Firebase Auth from Google's CDN only when the hub has Google sign-in set up
(GET /api/v1/auth/config), signs in with a popup (redirect when popups are blocked), and
trades the Firebase ID token for a Willy session token at /api/v1/auth/google. The session
token then works exactly like the old access token (Bearer header / ?token= on sockets).
"""

FIREBASE_SDK = "https://www.gstatic.com/firebasejs/10.12.2"

GOOGLE_SIGNIN_JS = r"""
window.WillyGoogle = (function () {
  'use strict';
  var SDK = '__SDK__';
  var cfgPromise = null, sdkPromise = null;

  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      var s = document.createElement('script');
      s.src = src; s.async = true;
      s.onload = resolve;
      s.onerror = function () { reject(new Error('Couldn’t load Google sign-in. Check your connection.')); };
      document.head.appendChild(s);
    });
  }
  function config(base) {
    if (!cfgPromise) {
      cfgPromise = fetch((base || '') + '/api/v1/auth/config', { cache: 'no-store' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) { return d && d.success ? d : null; })
        .catch(function () { return null; });
    }
    return cfgPromise;
  }
  function sdk(cfg) {
    if (!sdkPromise) {
      sdkPromise = loadScript(SDK + '/firebase-app-compat.js')
        .then(function () { return loadScript(SDK + '/firebase-auth-compat.js'); })
        .then(function () {
          if (!firebase.apps.length) firebase.initializeApp(cfg.google);
          return firebase.auth();
        });
    }
    return sdkPromise;
  }
  function exchange(base, user) {
    return user.getIdToken().then(function (idToken) {
      return fetch((base || '') + '/api/v1/auth/google', {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Willy-Client': 'web' },
        body: JSON.stringify({ id_token: idToken, client: 'web' })
      });
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok || !d.success) throw new Error(d.error || d.detail || ('Sign-in failed (HTTP ' + r.status + ').'));
        return d;
      });
    });
  }
  function friendly(err) {
    var code = err && err.code || '';
    if (code === 'auth/popup-closed-by-user' || code === 'auth/cancelled-popup-request') return null;
    if (code === 'auth/unauthorized-domain') return 'This address isn’t allowed for Google sign-in yet (Firebase → Authentication → Settings → Authorized domains).';
    if (code === 'auth/network-request-failed') return 'No connection to Google. Try again.';
    return (err && err.message) || 'Google sign-in failed.';
  }
  function randomState() {
    var a = new Uint8Array(16); (window.crypto || window.msCrypto).getRandomValues(a);
    return Array.prototype.map.call(a, function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
  }
  /* No Firebase on this hub: send the person to the master's sign-in page and back. */
  function viaMaster(cfg) {
    var state = randomState();
    try {
      sessionStorage.setItem('willy_broker_state', state);
      sessionStorage.setItem('willy_after_login', location.pathname + location.search);
    } catch (e) { throw new Error('Your browser blocks the storage Google sign-in needs. Allow it for this site.'); }
    location.href = cfg.broker.url.replace(/\/$/, '') + '/broker/login?hub=' + encodeURIComponent(location.origin) + '&state=' + state;
    return new Promise(function () {});   // the page is leaving
  }
  function popup(auth, base) {
    var provider = new firebase.auth.GoogleAuthProvider();
    provider.setCustomParameters({ prompt: 'select_account' });
    return auth.signInWithPopup(provider).then(function (res) { return exchange(base, res.user); }, function (err) {
      if (err && (err.code === 'auth/popup-blocked' || err.code === 'auth/operation-not-supported-in-this-environment')) {
        try { sessionStorage.setItem('willy_g_redirect', '1'); } catch (e) {}
        return auth.signInWithRedirect(provider).then(function () { return null; });
      }
      var msg = friendly(err);
      if (msg === null) return null;
      throw new Error(msg);
    });
  }
  /* Resolves {token, user} after a completed sign-in, or null when the person closed the popup. */
  function signIn(base) {
    return config(base).then(function (cfg) {
      if (!cfg || !(cfg.google || (cfg.broker && cfg.broker.url))) throw new Error('Google sign-in isn’t set up on this hub.');
      if (!cfg.google) return viaMaster(cfg);
      return sdk(cfg).then(function (auth) { return popup(auth, base); });
    });
  }
  /* The Firebase ID token after a popup sign-in (null when closed): used by the master's /broker/login page. */
  function idToken(base) {
    return config(base).then(function (cfg) {
      if (!cfg || !cfg.google) throw new Error('Google sign-in isn’t set up on this hub.');
      return sdk(cfg);
    }).then(function (auth) {
      var provider = new firebase.auth.GoogleAuthProvider();
      provider.setCustomParameters({ prompt: 'select_account' });
      return auth.signInWithPopup(provider).then(function (res) { return res.user.getIdToken(); }, function (err) {
        if (err && err.code === 'auth/popup-blocked') throw new Error('Your browser blocked the Google window. Allow pop-ups for this page and try again.');
        var msg = friendly(err);
        if (msg === null) return null;
        throw new Error(msg);
      });
    });
  }
  /* After a redirect sign-in the page reloads: finish it here (null when there was none). */
  function finishRedirect(base) {
    var pending = false;
    try { pending = sessionStorage.getItem('willy_g_redirect') === '1'; sessionStorage.removeItem('willy_g_redirect'); } catch (e) {}
    if (!pending) return Promise.resolve(null);
    return config(base).then(function (cfg) {
      if (!cfg) return null;
      return sdk(cfg).then(function (auth) { return auth.getRedirectResult(); })
        .then(function (res) { return res && res.user ? exchange(base, res.user) : null; });
    }).catch(function (err) { var m = friendly(err); if (m) throw new Error(m); return null; });
  }
  function signOutGoogle() {
    try { if (window.firebase && firebase.apps.length) firebase.auth().signOut(); } catch (e) {}
  }
  return { config: config, signIn: signIn, idToken: idToken, finishRedirect: finishRedirect, signOut: signOutGoogle };
})();
""".replace("__SDK__", FIREBASE_SDK)

GOOGLE_ICON_SVG = (
    '<svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true">'
    '<path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3 0 5.8 1.1 7.9 3l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z"/>'
    '<path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3 0 5.8 1.1 7.9 3l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/>'
    '<path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-7.9l-6.5 5C9.5 39.6 16.2 44 24 44z"/>'
    '<path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 38.2 44 33 44 24c0-1.3-.1-2.4-.4-3.5z"/>'
    '</svg>'
)
