import os
import json
import time
import urllib.parse
import uuid
import logging
import sys
import boto3
import requests
from functools import wraps
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from werkzeug.middleware.proxy_fix import ProxyFix
from bedrock_agentcore import BedrockAgentCoreApp



# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.getenv("APP_SECRET_KEY", "your-secret-key-here")
app.config['SESSION_COOKIE_SAMESITE'] = "Lax"
# Render serves only HTTPS; Secure cookies persist correctly behind the proxy
if (os.getenv("RENDER") or "").lower() == "true":
    app.config["SESSION_COOKIE_SECURE"] = True
# Render / other reverse proxies: trust X-Forwarded-* so url_for(..., _external=True) is https + public host
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# Okta Configuration (OAuth + optional client credentials)
OKTA_CLIENT_ID = os.getenv("OKTA_CLIENT_ID")
OKTA_CLIENT_SECRET = os.getenv("OKTA_CLIENT_SECRET")
OKTA_ISSUER = os.getenv("OKTA_ISSUER_RESOURCE")
OKTA_SCOPE = os.getenv("OKTA_SCOPE", "openid profile email")


def get_okta_callback_url():
    """OAuth redirect_uri sent to Okta. If OKTA_CALLBACK_URL is unset, derive from the incoming request (deployed HTTPS)."""
    explicit = (os.getenv("OKTA_CALLBACK_URL") or "").strip()
    if explicit:
        return explicit
    return url_for("callback", _external=True)


# Agent Core Configuration
AGENT_RUNTIME_ARN = os.getenv("AGENT_RUNTIME_ARN")

# Okta OAuth Configuration
oauth = OAuth(app)
okta = oauth.register(
    'okta',
    client_id=OKTA_CLIENT_ID,
    client_secret=OKTA_CLIENT_SECRET,
    client_kwargs={
        'scope': OKTA_SCOPE,
    },
    server_metadata_url=f'{OKTA_ISSUER}/.well-known/openid-configuration'
)

def requires_auth(f):
    """
    Decorator to require authentication for protected routes.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'profile' not in session:
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated

def get_bearer_token():
    """
    Get bearer token via Okta client credentials for machine-to-machine authentication.
    Falls back to BEARER_TOKEN env var if configured.
    """
    try:
        issuer = OKTA_ISSUER
        client_id = OKTA_CLIENT_ID
        client_secret = OKTA_CLIENT_SECRET
        scope = OKTA_SCOPE
        if scope:
            scope = " ".join([s for s in scope.split() if s and s.lower() != "offline_access"])

        if not all([issuer, client_id, client_secret, scope]):
            raise ValueError("Missing required Okta environment variables for client credentials flow")

        token_url = f"{issuer}/v1/token"
        headers = {"Accept": "application/json"}
        data = {
            "grant_type": "client_credentials",
            "scope": scope
        }
        response = requests.post(token_url, data=data, headers=headers, auth=(client_id, client_secret), timeout=15)
        response.raise_for_status()
        token_response = response.json()
        access_token = token_response.get("access_token")
        print('access_tokenaccess_token',access_token)
        if not access_token:
            raise ValueError("No access token received from Okta")
        return access_token

    except Exception as e:
        print(f"Error getting Okta bearer token: {e}")
        # Fallback to environment variable if available
        fallback_token = os.getenv("BEARER_TOKEN")
        if fallback_token:
            return fallback_token
        raise

@app.route("/")
def index():
    """
    Home page - redirect to chat if authenticated, otherwise show login
    """
    if 'profile' in session:
        return redirect('/chat')
    else:
        return render_template(
            "login.html",
            oauth_error=request.args.get("oauth_error"),
        )

@app.route("/login")
def login():
    """
    Initiate Okta login flow
    """
    print('=== LOGIN REQUEST ===')
    print('Issuer:', OKTA_ISSUER)
    print('Callback URL:', get_okta_callback_url())
    print('===================')
    session.clear()

    redirect_response = okta.authorize_redirect(redirect_uri=get_okta_callback_url())
    print('Redirect URL:', redirect_response.headers.get('Location'))
    return redirect_response

@app.route("/callback")
def callback():
    """
    Handle Okta callback after successful authentication
    """
    try:

        token = okta.authorize_access_token()
        print('token', token)

        # Decode and check claims
        if token.get('access_token'):
            try:
                import jwt
                decoded_access = jwt.decode(token['access_token'], options={"verify_signature": False})
                print('\n--- DECODED ACCESS TOKEN CLAIMS ---')
                print('Audience (aud):', decoded_access.get('aud', 'NOT FOUND'))
                print('Issuer (iss):', decoded_access.get('iss', 'NOT FOUND'))
                print('Subject (sub):', decoded_access.get('sub', 'NOT FOUND'))
                print('Scope (scp):', decoded_access.get('scp', 'NOT FOUND'))
                print('Expiration (exp):', decoded_access.get('exp', 'NOT FOUND'))
            except Exception as e:
                print(f'Could not decode access token: {e}')
        
        if token.get('id_token'):
            try:
                import jwt
                decoded_id = jwt.decode(token['id_token'], options={"verify_signature": False})
                print('\n--- DECODED ID TOKEN CLAIMS ---')
                print('Audience (aud):', decoded_id.get('aud', 'NOT FOUND'))
                print('Issuer (iss):', decoded_id.get('iss', 'NOT FOUND'))
                print('Subject (sub):', decoded_id.get('sub', 'NOT FOUND'))
            except Exception as e:
                print(f'Could not decode ID token: {e}')
        print('===================')

        print('Userinfo:')
        print(token['userinfo'])
        userinfo = token['userinfo']

        user_profile = {
            'user_id': userinfo['sub'],
            'name': userinfo['name'],
            'email': userinfo['email']
        }
        session['profile'] = user_profile
        session_id = str(uuid.uuid4())
        session['session_id'] = session_id

        # Store only compact strings — the full OAuth dict + duplicate "user" blob exceeds browser cookie limits (~4KB)
        # and the session silently fails to persist → /chat → /login → Okta → "too many redirects".
        access_token_str = token.get("access_token") or ""
        id_token_str = token.get("id_token") or ""
        session["access_token"] = access_token_str
        session["id_token"] = id_token_str
        if access_token_str:
            print("Stored access token in session (string)")
        else:
            print("No access token in OAuth response")
        if id_token_str:
            print("Stored ID token in session (string)")
        else:
            print("No ID token received")
        if token.get("refresh_token"):
            session["refresh_token"] = token["refresh_token"]
            print("Stored refresh token in session")
        else:
            print("No refresh token received")

        return redirect('/chat')

    except Exception as e:
        print(f"Error in callback: {str(e)}")
        session.clear()
        # Redirect to home, not /login — /login immediately sends users to Okta and can loop on repeated failures
        return redirect("/?oauth_error=1")

@app.route("/logout")
def logout():
    """
    Handle user logout
    """
    id_token = session.get("id_token")
    session.clear()
    if OKTA_ISSUER and id_token:
        end_session_endpoint = None
        try:
            meta = okta.load_server_metadata()
            end_session_endpoint = meta.get("end_session_endpoint")
        except Exception:
            pass
        if not end_session_endpoint:
            end_session_endpoint = f"{OKTA_ISSUER}/oauth2/v1/logout"
        params = urllib.parse.urlencode({
            "id_token_hint": id_token,
            "post_logout_redirect_uri": url_for("index", _external=True),
        })
        return redirect(f"{end_session_endpoint}?{params}")
    return redirect(url_for('index'))

def _decode_jwt_claims(token_str):
    """Decode a JWT payload without verification using base64."""
    import base64
    try:
        parts = token_str.split(".")
        if len(parts) != 3:
            return None
        payload = parts[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding
        decoded_bytes = base64.urlsafe_b64decode(payload)
        return json.loads(decoded_bytes)
    except Exception:
        return None


def _build_flow_trace(step_label, status="success", detail=None, children=None):
    """Helper to build a flow trace node."""
    node = {"label": step_label, "status": status}
    if detail:
        node["detail"] = detail
    if children:
        node["children"] = children
    return node


@app.route("/chat", methods=["GET", "POST"])
@requires_auth
def chat_page():
    """
    Main chat interface page - handles both displaying chat and processing messages
    """
    messages = session.get('chat_messages', [])
    flow_trace = session.get('flow_trace')

    if request.method == "POST":
        user_message = request.form.get("message", "").strip()

        if user_message:
            messages.append({
                "sender": "user",
                "message": user_message,
                "timestamp": "Just now"
            })

            flow_steps = []

            try:
                bearer_token_obj = session.get("id_token")
                bearer_token = bearer_token_obj.get("id_token") if isinstance(bearer_token_obj, dict) else bearer_token_obj
                if not isinstance(bearer_token, str) or not bearer_token:
                    raise ValueError("Missing access token for Agent Core request")

                # --- Token debug info ---
                id_token_raw = session.get("id_token", "")
                id_token_decoded = _decode_jwt_claims(id_token_raw)

                flow_steps.append(_build_flow_trace(
                    "User sends prompt",
                    detail=user_message[:80]
                ))

                if not isinstance(AGENT_RUNTIME_ARN, str) or not AGENT_RUNTIME_ARN.strip():
                    raise ValueError("AGENT_RUNTIME_ARN environment variable is not set")

                session_id = session.get('profile', {}).get('user_id', 'default-session')
                agent_runtime_arn_encoded = urllib.parse.quote(AGENT_RUNTIME_ARN, safe="")
                api_endpoint = f"https://bedrock-agentcore.us-east-2.amazonaws.com/runtimes/{agent_runtime_arn_encoded}/invocations?qualifier=DEFAULT"

                request_body = {
                    "prompt": user_message,
                    "id_token": session['id_token'],
                    "access_token": session['access_token'],
                    "sessionId": session_id
                }

                headers = {
                    "Authorization": f"Bearer {bearer_token}",
                    "Content-Type": "application/json"
                }

                # Build ID token decoded claims as drill-down under Authorization
                id_token_children = []
                if id_token_decoded:
                    highlight_keys = ['iss', 'sub', 'aud', 'email', 'name', 'exp', 'iat', 'nonce', 'amr', 'idp', 'auth_time']
                    for k in highlight_keys:
                        if k in id_token_decoded:
                            id_token_children.append(_build_flow_trace(k, detail=str(id_token_decoded[k])))
                    for k, v in id_token_decoded.items():
                        if k not in highlight_keys:
                            id_token_children.append(_build_flow_trace(k, detail=str(v)))
                else:
                    id_token_children.append(_build_flow_trace("Could not decode", status="error"))

                flow_steps.append(_build_flow_trace(
                    "Call AWS Bedrock AgentCore",
                    detail=api_endpoint[:90] + "...",
                    children=[
                        _build_flow_trace("Endpoint", detail=api_endpoint),
                        _build_flow_trace("Authorization: Bearer <id_token>",
                            detail=f"JWT ({len(id_token_raw)} chars)",
                            children=[
                                _build_flow_trace("Raw JWT", detail=id_token_raw if id_token_raw else "N/A"),
                                _build_flow_trace("Decoded Claims", children=id_token_children),
                            ]
                        ),
                        _build_flow_trace("Body: sessionId", detail=session_id),
                    ]
                ))

                req_start = time.time()
                response = requests.post(
                    api_endpoint,
                    headers=headers,
                    json=request_body,
                    timeout=120
                )
                req_duration_ms = int((time.time() - req_start) * 1000)

                response.raise_for_status()

                try:
                    agent_response = response.json()
                    response_text = extract_response_text(agent_response)
                except Exception:
                    agent_response = response.text
                    response_text = response.text

                flow_steps.append(_build_flow_trace(
                    "AgentCore Response",
                    detail=f"HTTP {response.status_code} in {req_duration_ms}ms",
                    children=[
                        _build_flow_trace("Status", detail=str(response.status_code)),
                        _build_flow_trace("Latency", detail=f"{req_duration_ms}ms"),
                        _build_flow_trace("Response preview", detail=response_text[:120] if response_text else "empty"),
                    ]
                ))

                messages.append({
                    "sender": "agent",
                    "message": response_text,
                    "timestamp": "Just now"
                })

            except requests.exceptions.HTTPError as e:
                error_msg = f"Agent Core API error: {str(e)}"
                if hasattr(e, 'response') and e.response is not None:
                    try:
                        error_detail = e.response.json()
                        error_msg += f" - {error_detail}"
                    except Exception:
                        error_msg += f" - {e.response.text}"
                flow_steps.append(_build_flow_trace("AgentCore Response", status="error", detail=error_msg))
                messages.append({"sender": "system", "message": error_msg, "timestamp": "Just now"})

            except Exception as e:
                error_msg = f"Internal error: {str(e)}"
                flow_steps.append(_build_flow_trace("Error", status="error", detail=error_msg))
                messages.append({"sender": "system", "message": error_msg, "timestamp": "Just now"})

            flow_trace = flow_steps
            session['chat_messages'] = messages
            session['flow_trace'] = flow_trace

    return render_template(
        "chat.html",
        user=session['profile'],
        messages=messages,
        flow_trace=flow_trace,
    )

@app.route("/clear-chat", methods=["POST"])
@requires_auth
def clear_chat():
    """
    Clear chat history
    """
    session['chat_messages'] = []
    return redirect('/chat')

def extract_response_text(response):
    """
    Extract response text from Agent Core response
    """
    if isinstance(response, str):
        return response
    elif isinstance(response, dict):
        # Look for common response fields
        return response.get('text') or response.get('message') or response.get('content') or str(response)
    else:
        return "Received response from agent."

if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=8000)