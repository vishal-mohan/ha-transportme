"""HTTP views for TransportMe config-flow authentication.

Two endpoints are registered on the HA HTTP server:

  GET  /api/transportme/auth?flow_id=<id>&flow_type=<config|options>
       Serves the sign-in page (no HA auth required – the page IS the auth).

  POST /api/transportme/callback
       Receives {flow_id, flow_type, id_token, refresh_token} from the sign-in page,
       verifies the token against TransportMe, then advances the config/options flow.
"""
from __future__ import annotations

import logging

import aiohttp
from aiohttp import web

from homeassistant.components.http import HomeAssistantView

_LOGGER = logging.getLogger(__name__)

GRAPHQL_URL = "https://production.api2.transportme.com.au/"

# ---------------------------------------------------------------------------
# Token verification (shared with config_flow – kept here to avoid circular import)
# ---------------------------------------------------------------------------

async def verify_transportme_token(id_token: str) -> dict | None:
    """Return paxUser dict if the token is valid, else None."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                GRAPHQL_URL,
                json={"query": "query { paxUser { id email fav_operator_id } }"},
                headers={
                    "Authorization": f"Bearer {id_token}",
                    "Content-Type":  "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status in (401, 403):
                    return None
                body = await resp.json(content_type=None)
                return body.get("data", {}).get("paxUser")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Sign-in page HTML (email/password via Firebase REST + Google token paste)
# ---------------------------------------------------------------------------
# Email/password uses the Firebase REST API directly (fetch call to Google's
# endpoint) so it works from ANY origin — no Firebase JS SDK domain authorisation
# needed.  Google sign-in via signInWithPopup only works when HA is served from
# an authorised Firebase domain (usually localhost), so for self-hosted installs
# we offer a straightforward token-paste path instead.

_AUTH_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TransportMe – Sign In</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: #f0f2f5;
      min-height: 100vh;
      display: flex; align-items: center; justify-content: center;
      padding: 24px 16px;
    }
    .card {
      background: #fff;
      border-radius: 14px;
      box-shadow: 0 4px 28px rgba(0,0,0,.10);
      padding: 36px 32px;
      width: 100%; max-width: 420px;
    }
    .logo { font-size: 28px; margin-bottom: 4px; }
    h1 { color: #e65100; font-size: 20px; font-weight: 700; margin-bottom: 6px; }
    .subtitle { color: #666; font-size: 14px; margin-bottom: 24px; line-height: 1.5; }
    .tabs { display: flex; border-bottom: 2px solid #eee; margin-bottom: 24px; }
    .tab {
      flex: 1; padding: 10px 0; border: none; background: none;
      font-size: 14px; font-weight: 600; color: #999; cursor: pointer;
      border-bottom: 2px solid transparent; margin-bottom: -2px;
      transition: color .15s, border-color .15s;
    }
    .tab.active { color: #e65100; border-color: #e65100; }
    label { display: block; font-size: 13px; font-weight: 600; color: #444; margin-bottom: 6px; }
    input, textarea {
      width: 100%; padding: 11px 13px; border: 1.5px solid #ddd;
      border-radius: 8px; font-size: 14px; margin-bottom: 14px;
      font-family: inherit; transition: border-color .2s; resize: vertical;
    }
    input:focus, textarea:focus { outline: none; border-color: #e65100; }
    .btn {
      width: 100%; padding: 13px; border: none; border-radius: 8px;
      font-size: 15px; font-weight: 600; cursor: pointer;
      transition: opacity .2s; background: #e65100; color: #fff;
    }
    .btn:hover { opacity: .88; }
    .btn:disabled { opacity: .5; cursor: not-allowed; }
    .error {
      background: #fff0ed; border: 1px solid #ffccbc; color: #bf360c;
      border-radius: 8px; padding: 11px 14px; font-size: 14px; margin-bottom: 16px;
    }
    .hint {
      background: #fffde7; border: 1px solid #fff176; color: #555;
      border-radius: 8px; padding: 12px 14px; font-size: 13px; margin-bottom: 16px;
      line-height: 1.55;
    }
    .hint ol { padding-left: 18px; margin-top: 6px; }
    .hint li { margin-bottom: 4px; }
    code {
      background: #f5f5f5; padding: 1px 5px; border-radius: 4px;
      font-size: 12px; font-family: monospace;
    }
    .success {
      text-align: center; padding: 24px 8px;
    }
    .success .icon { font-size: 52px; margin-bottom: 14px; }
    .success h2 { color: #2e7d32; font-size: 18px; margin-bottom: 8px; }
    .success p { color: #666; font-size: 14px; line-height: 1.5; }
  </style>
</head>
<body>
<div class="card" id="card">
  <div class="logo">🚌</div>
  <h1>TransportMe</h1>
  <p class="subtitle">Sign in to link your TransportMe account to Home Assistant.</p>

  <div id="err" class="error" style="display:none"></div>

  <div class="tabs">
    <button class="tab active" onclick="showTab('ep')">Email / Password</button>
    <button class="tab"        onclick="showTab('goog')">Google Account</button>
  </div>

  <!-- ── Email / Password ── -->
  <div id="tab-ep">
    <label for="email">Email</label>
    <input type="email" id="email" placeholder="your@email.com" autocomplete="username">

    <label for="pw">Password</label>
    <input type="password" id="pw" placeholder="Password" autocomplete="current-password">

    <button class="btn" id="btn-ep" onclick="signInEmail()">Sign in</button>
  </div>

  <!-- ── Google token paste ── -->
  <div id="tab-goog" style="display:none">
    <div class="hint">
      Google sign-in via the TransportMe app requires a one-time token helper:
      <ol>
        <li>Open a terminal and run:<br><code>python3 -m http.server 8080</code><br>from the folder containing <code>get_token.html</code></li>
        <li>Open <a href="http://localhost:8080/get_token.html" target="_blank">http://localhost:8080/get_token.html</a></li>
        <li>Click <strong>Sign in with Google</strong></li>
        <li>Copy both tokens and paste them below</li>
      </ol>
    </div>

    <label for="id-tok">ID Token</label>
    <textarea id="id-tok" rows="3" placeholder="eyJhbGciOi…"></textarea>

    <label for="ref-tok">Refresh Token <span style="font-weight:400;color:#888">(recommended)</span></label>
    <input type="text" id="ref-tok" placeholder="AMf-vBw…">

    <button class="btn" id="btn-goog" onclick="submitToken()">Verify &amp; Connect</button>
  </div>
</div>

<script>
  const FIREBASE_KEY = "AIzaSyD9xVRwjC0V-FHj5D97pwD8oGUNCufs9vI";
  const FLOW_ID      = "__FLOW_ID__";
  const FLOW_TYPE    = "__FLOW_TYPE__";

  function showTab(t) {
    document.getElementById("tab-ep").style.display   = t === "ep"   ? "" : "none";
    document.getElementById("tab-goog").style.display = t === "goog" ? "" : "none";
    document.querySelectorAll(".tab").forEach((el, i) =>
      el.classList.toggle("active", i === (t === "ep" ? 0 : 1)));
    clearErr();
  }

  function showErr(msg) {
    const el = document.getElementById("err");
    el.textContent = msg; el.style.display = "";
  }
  function clearErr() { document.getElementById("err").style.display = "none"; }

  function setLoading(btnId, on) {
    document.getElementById(btnId).disabled = on;
    document.getElementById(btnId).textContent = on ? "Please wait…" :
      (btnId === "btn-ep" ? "Sign in" : "Verify & Connect");
  }

  async function signInEmail() {
    clearErr();
    const email = document.getElementById("email").value.trim();
    const pw    = document.getElementById("pw").value;
    if (!email || !pw) { showErr("Please enter your email and password."); return; }
    setLoading("btn-ep", true);
    try {
      const r = await fetch(
        "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=" + FIREBASE_KEY,
        { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email, password: pw, returnSecureToken: true }) }
      );
      const d = await r.json();
      if (!r.ok) {
        const code = d.error && d.error.message;
        throw { message: ({
          EMAIL_NOT_FOUND:              "No account found with that email.",
          INVALID_PASSWORD:             "Incorrect password. Please try again.",
          INVALID_LOGIN_CREDENTIALS:    "Incorrect email or password. Please try again.",
          INVALID_EMAIL:                "Invalid email address.",
          USER_DISABLED:                "This account has been disabled.",
          TOO_MANY_ATTEMPTS_TRY_LATER:  "Too many failed attempts. Please wait and try again.",
        }[code] || ("Sign-in failed: " + code)) };
      }
      await sendToHA(d.idToken, d.refreshToken);
    } catch (e) { showErr(e.message || "Sign-in failed. Please try again."); }
    finally { setLoading("btn-ep", false); }
  }

  async function submitToken() {
    clearErr();
    const idToken  = document.getElementById("id-tok").value.trim();
    const refToken = document.getElementById("ref-tok").value.trim();
    if (!idToken) { showErr("Please paste your ID Token."); return; }
    setLoading("btn-goog", true);
    try { await sendToHA(idToken, refToken); }
    catch (e) { showErr(e.message || "Failed. Please try again."); }
    finally { setLoading("btn-goog", false); }
  }

  async function sendToHA(idToken, refreshToken) {
    const r = await fetch("/api/transportme/callback", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        flow_id: FLOW_ID, flow_type: FLOW_TYPE,
        id_token: idToken, refresh_token: refreshToken
      })
    });
    const d = await r.json();
    if (!r.ok) throw { message: d.error || "Sign-in failed. Please try again." };
    document.getElementById("card").innerHTML = `
      <div class="success">
        <div class="icon">✅</div>
        <h2>Signed in successfully!</h2>
        <p>You can close this tab and return to Home Assistant.<br>
           The setup will continue automatically.</p>
      </div>`;
  }

  // Allow Enter key in password field
  document.addEventListener("DOMContentLoaded", () => {
    document.getElementById("pw").addEventListener("keydown", e => {
      if (e.key === "Enter") signInEmail();
    });
  });
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

class TransportMeAuthView(HomeAssistantView):
    """Serves the sign-in page used during the config / options flow."""

    url = "/api/transportme/auth"
    name = "api:transportme:auth"
    requires_auth = False  # The page itself is the authentication step

    async def get(self, request: web.Request) -> web.Response:
        flow_id   = request.query.get("flow_id", "")
        flow_type = request.query.get("flow_type", "config")
        html = (
            _AUTH_HTML
            .replace("__FLOW_ID__", flow_id)
            .replace("__FLOW_TYPE__", flow_type)
        )
        return web.Response(text=html, content_type="text/html")


class TransportMeCallbackView(HomeAssistantView):
    """Receives tokens from the sign-in page and advances the HA flow."""

    url = "/api/transportme/callback"
    name = "api:transportme:callback"
    requires_auth = False

    async def post(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]

        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON body"}, status=400)

        flow_id       = (data.get("flow_id") or "").strip()
        flow_type     = (data.get("flow_type") or "config").strip()
        id_token      = (data.get("id_token") or "").strip()
        refresh_token = (data.get("refresh_token") or "").strip()

        if not flow_id or not id_token:
            return web.json_response({"error": "Missing flow_id or id_token."}, status=400)

        # Verify token against TransportMe API before advancing the flow
        pax_user = await verify_transportme_token(id_token)
        if not pax_user:
            return web.json_response(
                {
                    "error": (
                        "TransportMe account not recognised. "
                        "Make sure you are signing in with your TransportMe account "
                        "(not a different Google or email account)."
                    )
                },
                status=401,
            )

        # Advance the correct flow manager
        flow_mgr = hass.config_entries.options if flow_type == "options" else hass.config_entries.flow
        try:
            await flow_mgr.async_configure(
                flow_id,
                {
                    "id_token":      id_token,
                    "refresh_token": refresh_token,
                    "email":         pax_user.get("email", ""),
                },
            )
        except Exception as exc:
            _LOGGER.warning("TransportMe callback: could not advance flow %s: %s", flow_id, exc)
            return web.json_response(
                {"error": "Sign-in session expired or not found. Please restart the setup."},
                status=404,
            )

        return web.json_response({"success": True, "email": pax_user.get("email", "")})
