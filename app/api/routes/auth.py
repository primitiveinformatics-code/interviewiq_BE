import secrets
from urllib.parse import urlencode
from datetime import timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import settings
from app.core.security import create_access_token, create_refresh_token, verify_token, pwd_context, get_current_user
from app.db.database import get_db
from app.db.models import User

router = APIRouter()


async def _get_redis():
    from app.main import redis_client
    return redis_client

# ── OAuth provider constants ───────────────────────────────────────────────────

GOOGLE_AUTH_URL    = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL   = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

GITHUB_AUTH_URL    = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL   = "https://github.com/login/oauth/access_token"
GITHUB_EMAILS_URL  = "https://api.github.com/user/emails"

# Short-lived in-memory CSRF state store (fine for single-process dev;
# swap for Redis in multi-replica production).
_oauth_states: dict[str, str] = {}   # state_token -> provider


def _callback_uri(provider: str) -> str:
    return f"{settings.BACKEND_URL}/auth/callback/{provider}"


# ── Login redirects ────────────────────────────────────────────────────────────

@router.get("/login/{provider}")
async def oauth_login(provider: str):
    """Redirect the browser to the OAuth provider's consent page."""
    state = secrets.token_urlsafe(16)
    _oauth_states[state] = provider

    if provider == "google":
        if not settings.GOOGLE_CLIENT_ID:
            raise HTTPException(501, "Google OAuth not configured — add GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET to .env")
        url = GOOGLE_AUTH_URL + "?" + urlencode({
            "client_id":     settings.GOOGLE_CLIENT_ID,
            "redirect_uri":  _callback_uri("google"),
            "response_type": "code",
            "scope":         "openid email profile",
            "state":         state,
        })
        return RedirectResponse(url=url)

    elif provider == "github":
        if not settings.GITHUB_CLIENT_ID:
            raise HTTPException(501, "GitHub OAuth not configured — add GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET to .env")
        url = GITHUB_AUTH_URL + "?" + urlencode({
            "client_id":    settings.GITHUB_CLIENT_ID,
            "redirect_uri": _callback_uri("github"),
            "scope":        "user:email",
            "state":        state,
        })
        return RedirectResponse(url=url)

    else:
        raise HTTPException(400, f"Unsupported provider: {provider}. Supported: google, github")


# ── OAuth callbacks ────────────────────────────────────────────────────────────

@router.get("/callback/{provider}")
async def oauth_callback(
    provider: str,
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Exchange the auth code for a user email, upsert the user, issue JWTs,
    then redirect the browser to the frontend with the tokens in the query string."""

    if _oauth_states.pop(state, None) != provider:
        raise HTTPException(400, "Invalid or expired OAuth state — please try logging in again")

    email: str | None = None

    async with httpx.AsyncClient(timeout=10) as client:
        if provider == "google":
            tok = await client.post(GOOGLE_TOKEN_URL, data={
                "code":          code,
                "client_id":     settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "redirect_uri":  _callback_uri("google"),
                "grant_type":    "authorization_code",
            })
            tok.raise_for_status()
            info = await client.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {tok.json()['access_token']}"},
            )
            info.raise_for_status()
            email = info.json().get("email")

        elif provider == "github":
            tok = await client.post(
                GITHUB_TOKEN_URL,
                data={
                    "code":          code,
                    "client_id":     settings.GITHUB_CLIENT_ID,
                    "client_secret": settings.GITHUB_CLIENT_SECRET,
                    "redirect_uri":  _callback_uri("github"),
                },
                headers={"Accept": "application/json"},
            )
            tok.raise_for_status()
            gh_token = tok.json().get("access_token")
            emails_resp = await client.get(
                GITHUB_EMAILS_URL,
                headers={"Authorization": f"Bearer {gh_token}", "Accept": "application/json"},
            )
            emails_resp.raise_for_status()
            emails = emails_resp.json()
            email = next(
                (e["email"] for e in emails if e.get("primary") and e.get("verified")),
                emails[0]["email"] if emails else None,
            )

    if not email:
        raise HTTPException(400, "Could not retrieve email from OAuth provider")

    # Upsert user
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if not user:
        user = User(email=email, oauth_provider=provider)
        db.add(user)
        await db.flush()
        await db.refresh(user)

    jwt_payload = {"sub": str(user.user_id), "email": email}
    qs = urlencode({
        "token":         create_access_token(jwt_payload),
        "refresh_token": create_refresh_token(jwt_payload),
        "email":         email,
    })
    return RedirectResponse(url=f"{settings.FRONTEND_URL}/auth/callback?{qs}")


# ── Email / password auth ──────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str

@router.post("/register", status_code=201)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """Create a new local (email + password) account."""
    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(409, "An account with that email already exists")

    user = User(
        email=body.email,
        oauth_provider="local",
        password_hash=pwd_context.hash(body.password),
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)

    jwt_payload = {"sub": str(user.user_id), "email": user.email}
    return {
        "access_token":  create_access_token(jwt_payload),
        "refresh_token": create_refresh_token(jwt_payload),
        "token_type":    "bearer",
    }


@router.post("/token")
async def login_password(
    form: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    """OAuth2-compatible password login (username field = email)."""
    result = await db.execute(select(User).where(User.email == form.username))
    user = result.scalar_one_or_none()

    if not user or not user.password_hash:
        raise HTTPException(401, "Invalid email or password")
    if not pwd_context.verify(form.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")

    jwt_payload = {"sub": str(user.user_id), "email": user.email}
    flags = user.feature_flags or {}
    return {
        "access_token":        create_access_token(jwt_payload),
        "refresh_token":       create_refresh_token(jwt_payload),
        "token_type":          "bearer",
        "must_change_password": bool(flags.get("must_change_password")),
    }


# ── Token refresh ──────────────────────────────────────────────────────────────

class TokenRefreshRequest(BaseModel):
    refresh_token: str

@router.post("/refresh")
async def refresh_token(body: TokenRefreshRequest):
    """Rotate JWT access + refresh tokens."""
    payload = verify_token(body.refresh_token, token_type="refresh")
    sub = {"sub": payload["sub"]}
    return {
        "access_token":  create_access_token(sub),
        "refresh_token": create_refresh_token(sub),
    }


# ── Forgot / reset password (self-service via Redis token) ─────────────────────

class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class ResetPasswordTokenRequest(BaseModel):
    token: str
    new_password: str

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


PWD_RESET_TTL = 900  # 15 minutes


@router.post("/forgot-password")
async def forgot_password(body: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)):
    """Generate a password-reset token (stored in Redis for 15 min).
    Dev: returns token directly. Prod: returns generic message only."""
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    generic_ok = {"message": "If that email exists, a reset link was sent."}

    if not user or user.oauth_provider != "local":
        # Prevent email enumeration in prod; show detail in dev
        if settings.APP_ENV != "production":
            return {"message": "No local account found with that email.", "token": None}
        return generic_ok

    token = secrets.token_hex(32)
    redis = await _get_redis()
    await redis.set(f"pwd_reset:{token}", str(user.user_id).encode(), ex=PWD_RESET_TTL)

    if settings.APP_ENV != "production":
        return {"token": token, "expires_in": PWD_RESET_TTL}
    return generic_ok


@router.post("/reset-password")
async def reset_password_via_token(
    body: ResetPasswordTokenRequest,
    db: AsyncSession = Depends(get_db),
):
    """Exchange a valid reset token for a new password."""
    if len(body.new_password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    redis = await _get_redis()
    raw = await redis.get(f"pwd_reset:{body.token}")
    if not raw:
        raise HTTPException(400, "Invalid or expired reset token")
    user_id = raw.decode() if isinstance(raw, bytes) else raw

    result = await db.execute(select(User).where(User.user_id == __import__("uuid").UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(400, "Invalid reset token")

    user.password_hash = pwd_context.hash(body.new_password)
    await db.commit()
    await redis.delete(f"pwd_reset:{body.token}")
    return {"success": True}


@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Authenticated: change password (also clears must_change_password flag)."""
    if len(body.new_password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    result = await db.execute(select(User).where(User.user_id == __import__("uuid").UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")
    if not user.password_hash or not pwd_context.verify(body.current_password, user.password_hash):
        raise HTTPException(401, "Current password is incorrect")

    user.password_hash = pwd_context.hash(body.new_password)
    flags = dict(user.feature_flags or {})
    flags.pop("must_change_password", None)
    user.feature_flags = flags
    await db.commit()
    return {"success": True}
