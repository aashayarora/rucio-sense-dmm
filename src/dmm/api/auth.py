"""
Browser login for the frontend, through SENSE-O's Keycloak (OIDC).

Off unless [auth] client_id is configured. When on, every route needs a
logged-in session except the ones machines call without a browser: Rucio's
/query routes, the health probes, Prometheus' /metrics, and the static files.
Any account the realm logs in is let in; who has one is SENSE-O's call.

/query matters most. Rucio's SENSE transfertool reads any non-200 there as
"DMM unavailable" and submits the rest of its cycle without SENSE routing, so a
login wall in front of it would not break anything visibly -- transfers would
just stop using the circuits DMM goes on provisioning.
"""
import logging
import secrets
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional
from urllib.parse import quote, urlencode, urlsplit

import httpx
from authlib.integrations.starlette_client import OAuth, OAuthError
from authlib.jose import JsonWebKey, jwt as jose_jwt
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from dmm.core.config import config_get, config_get_int

LOGIN_PATH = "/login"
CALLBACK_PATH = "/auth/callback"
LOGOUT_PATH = "/logout"

# Exact paths, plus prefixes ending in "/" so that "/query" does not also open
# some later "/query_admin".
PUBLIC_PATHS = frozenset({
    "/query", "/health", "/health/live", "/metrics",
    LOGIN_PATH, CALLBACK_PATH, LOGOUT_PATH,
})
PUBLIC_PREFIXES = ("/query/", "/static/")

SESSION_COOKIE = "dmm_session"
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

MACHINE_USER_KEY = "dmm_machine_user"


def is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


@dataclass(frozen=True)
class LoginSettings:
    metadata_url: str
    client_id: str
    client_secret: Optional[str]
    redirect_uri: str
    session_secret: str
    session_seconds: int

    @property
    def origin(self) -> str:
        parts = urlsplit(self.redirect_uri)
        return f"{parts.scheme}://{parts.netloc}"


@lru_cache(maxsize=1)
def login_settings() -> Optional[LoginSettings]:
    """The [auth] section, or None when login is off.

    A half-configured section raises rather than quietly leaving the frontend
    open: someone who set client_id meant to turn login on.
    """
    client_id = config_get("auth", "client_id", default=None)
    if not client_id:
        return None

    if not config_get("auth", "redirect_uri", default=None):
        raise RuntimeError("[auth] client_id is set but redirect_uri is not")

    session_secret = config_get("auth", "session_secret", default=None)
    if not session_secret:
        # Only costs a fresh login after a restart, and that is a silent round
        # trip while the Keycloak session lasts. DMM runs one frontend process.
        session_secret = secrets.token_urlsafe(32)

    return LoginSettings(
        metadata_url=config_get(
            "auth", "metadata_url",
            default="https://sense-o.es.net:8543/realms/StackV/.well-known/openid-configuration",
        ),
        client_id=client_id,
        # None for a public client (Client authentication off in Keycloak),
        # which has no secret; PKCE then carries the code exchange alone.
        client_secret=config_get("auth", "client_secret", default=None) or None,
        redirect_uri=config_get("auth", "redirect_uri"),
        session_secret=session_secret,
        session_seconds=config_get_int("auth", "session_hours", default=12) * 3600,
    )


def login_enabled() -> bool:
    return login_settings() is not None


@lru_cache(maxsize=1)
def _oauth_client():
    settings = login_settings()
    oauth = OAuth()
    oauth.register(
        name="sense",
        server_metadata_url=settings.metadata_url,
        client_id=settings.client_id,
        client_secret=settings.client_secret,
        client_kwargs={
            "scope": "openid profile email",
            "code_challenge_method": "S256",
            "token_endpoint_auth_method": "client_secret_basic" if settings.client_secret else "none",
        },
    )
    return oauth.create_client("sense")


def current_user(request: Request) -> Optional[dict]:
    """The logged-in user, or None (always None while login is off).

    Also covers the token caller the gate put on the scope, which has no
    session -- otherwise the audit line in frontend.py would raise on it.
    """
    session = request.scope.get("session")
    user = session.get("user") if session else None
    return user or request.scope.get(MACHINE_USER_KEY)


@lru_cache(maxsize=1)
def _realm_keys():
    """The realm's issuer and signing keys. Cached: a fetch per request would
    put the realm in the path of every API call. A rotation needs a restart."""
    metadata = httpx.get(login_settings().metadata_url).json()
    keys = httpx.get(metadata["jwks_uri"]).json()
    return metadata["issuer"], JsonWebKey.import_key_set(keys)


def machine_user(request: Request) -> Optional[dict]:
    """The service account behind an Authorization header, or None.

    Every failure is None, so a bad token lands on the same "not logged in"
    path as no token rather than a 500.
    """
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        return None
    try:
        issuer, keys = _realm_keys()
    except Exception as exc:
        logging.warning(f"Could not load realm keys, refusing token: {exc}")
        return None
    try:
        claims = jose_jwt.decode(header[len("Bearer "):], keys)
        claims.validate()  # exp, nbf, iat
    except Exception as exc:
        logging.warning(f"Rejected bearer token: {type(exc).__name__}")
        return None
    # A signature from another realm is still a valid signature.
    if claims.get("iss") != issuer:
        logging.warning(f"Rejected bearer token from issuer {claims.get('iss')!r}")
        return None
    # Service accounts carry azp and no preferred_username, so a log line can
    # say "sense-iop" instead of a uuid.
    name = (claims.get("preferred_username") or claims.get("azp")
            or claims.get("client_id") or claims.get("sub"))
    return {"username": name, "name": name, "machine": True}


def _safe_next(target: Optional[str]) -> str:
    # Only same-site paths: "//evil.example" is a URL to another host.
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return target


def _message_page(templates, request, status_code, title, message, link=None):
    return templates.TemplateResponse(
        request, "auth_message.html",
        {"title": title, "message": message, "link": link},
        status_code=status_code,
    )


def build_router(templates) -> APIRouter:
    router = APIRouter()

    @router.get(LOGIN_PATH, include_in_schema=False)
    async def login(request: Request, next: Optional[str] = None):
        settings = login_settings()
        if settings is None:
            return RedirectResponse("/", status_code=303)
        request.session["next"] = _safe_next(next)
        return await _oauth_client().authorize_redirect(request, settings.redirect_uri)

    @router.get(CALLBACK_PATH, include_in_schema=False)
    async def callback(request: Request):
        settings = login_settings()
        if settings is None:
            return RedirectResponse("/", status_code=303)
        try:
            token = await _oauth_client().authorize_access_token(request)
        except OAuthError as e:
            logging.warning(f"Login failed: {e.error}: {e.description}")
            return _message_page(
                templates, request, 400, "Login failed",
                "The login could not be completed. It may have expired or been opened twice.",
                link=(LOGIN_PATH, "Try again"),
            )

        claims = token.get("userinfo") or {}
        username = claims.get("preferred_username") or claims.get("email") or claims.get("sub")
        next_url = request.session.get("next", "/")
        # A fresh session on login, so nothing set before it carries over.
        request.session.clear()
        request.session["user"] = {
            "username": username,
            "name": claims.get("name") or username,
        }
        logging.info(f"Login: {username}")
        return RedirectResponse(next_url, status_code=303)

    @router.get(LOGOUT_PATH, include_in_schema=False)
    async def logout(request: Request):
        settings = login_settings()
        if settings is None:
            return RedirectResponse("/", status_code=303)
        user = current_user(request)
        request.session.clear()
        if user:
            logging.info(f"Logout: {user['username']}")
        # Ending only DMM's session would sign straight back in through the
        # still-live Keycloak one. This also signs out of the SENSE-O portal.
        metadata = await _oauth_client().load_server_metadata()
        end_session = metadata.get("end_session_endpoint")
        if not end_session:
            return RedirectResponse("/", status_code=303)
        return RedirectResponse(
            f"{end_session}?{urlencode({'client_id': settings.client_id})}", status_code=303
        )

    return router


class LoginRequired:
    """ASGI middleware: the session cookie, and the login wall behind it.

    Reads [auth] on the first request rather than at import, so importing the
    frontend needs no config and an unconfigured DMM passes straight through.
    """

    def __init__(self, app):
        self.app = app
        self._inner = None

    async def __call__(self, scope, receive, send):
        if self._inner is None:
            self._inner = self._build()
        await self._inner(scope, receive, send)

    def _build(self):
        settings = login_settings()
        if settings is None:
            return self.app
        return SessionMiddleware(
            self._gate,
            secret_key=settings.session_secret,
            session_cookie=SESSION_COOKIE,
            max_age=settings.session_seconds,
            same_site="lax",
            https_only=settings.redirect_uri.startswith("https://"),
        )

    async def _gate(self, scope, receive, send):
        if scope["type"] != "http" or is_public(scope["path"]):
            return await self.app(scope, receive, send)

        request = Request(scope)
        user = current_user(request)
        if user is None:
            # Checked second, so a session wins: a signed-in human who also
            # sends a header stays a human.
            user = machine_user(request)
            if user is not None:
                scope[MACHINE_USER_KEY] = user

        if user is None:
            response = self._login_required(request)
        elif user.get("machine") and request.method in _UNSAFE_METHODS:
            # These cancel circuits and reset requests. A token in a
            # dashboard's config should not be able to.
            response = JSONResponse(
                {"detail": "Token is read-only; operator actions need a login"},
                status_code=403,
            )
        elif request.method in _UNSAFE_METHODS and not self._same_origin(request):
            response = JSONResponse({"detail": "Cross-origin request refused"}, status_code=403)
        else:
            return await self.app(scope, receive, send)
        await response(scope, receive, send)

    @staticmethod
    def _login_required(request):
        if request.method in ("GET", "HEAD") and "text/html" in request.headers.get("accept", ""):
            target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
            return RedirectResponse(f"{LOGIN_PATH}?next={quote(target)}", status_code=303)
        return JSONResponse({"detail": "Login required"}, status_code=401)

    @staticmethod
    def _same_origin(request):
        # SameSite=Lax already keeps the cookie off cross-site POSTs; this
        # covers sibling subdomains, which count as same-site. Browsers send
        # Origin on every POST.
        origin = request.headers.get("origin")
        return origin is None or origin == login_settings().origin
