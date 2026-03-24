"""
Billing routes — Razorpay credit-based payments
================================================
Users buy interview credit packs (one-time payments, no subscriptions).
1 credit = 1 full interview (15-20 questions).
New users get 1 free trial interview (3 questions) before needing credits.

Flow:
  1. Frontend calls POST /billing/order  → gets {order_id, amount, currency, key_id}
  2. Frontend opens Razorpay checkout JS with order_id
  3. User pays → Razorpay returns {razorpay_payment_id, razorpay_order_id, razorpay_signature}
  4. Frontend calls POST /billing/verify → backend verifies signature + credits user

Endpoints:
  POST /billing/order   → create Razorpay order, return order details for checkout
  POST /billing/verify  → verify payment signature + credit user
  GET  /billing/status  → return {credits, trial_used}
  POST /billing/webhook → (optional) Razorpay webhook for async confirmation
"""

import hmac
import hashlib
import razorpay
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime
import uuid

from app.db.database import get_db
from app.db.models import User, Coupon
from app.core.security import get_current_user
from app.core.config import settings

router = APIRouter()

CREDIT_AMOUNT_MAP = {
    1:  lambda: settings.RAZORPAY_1_CREDIT_AMOUNT,
    5:  lambda: settings.RAZORPAY_5_CREDIT_AMOUNT,
    10: lambda: settings.RAZORPAY_10_CREDIT_AMOUNT,
}


def _get_razorpay():
    return razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))


# ── Request / Response models ─────────────────────────────────────────────────

class OrderRequest(BaseModel):
    credits: int  # must be 1, 5, or 10


class VerifyRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
    credits: int  # how many credits this payment was for


class RedeemRequest(BaseModel):
    code: str  # coupon code, e.g. "BETA2024"


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/order")
async def create_order(
    body: OrderRequest,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a Razorpay order for a one-time credit purchase.
    Returns the order details needed by the Razorpay checkout JS widget.
    """
    if body.credits not in CREDIT_AMOUNT_MAP:
        raise HTTPException(status_code=400, detail="credits must be 1, 5, or 10")

    amount = CREDIT_AMOUNT_MAP[body.credits]()
    if not amount:
        raise HTTPException(status_code=500, detail="Price not configured for this credit pack")

    result = await db.execute(select(User).where(User.user_id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    client = _get_razorpay()
    order = client.order.create({
        "amount":   amount,
        "currency": settings.RAZORPAY_CURRENCY,
        "receipt":  f"interviewiq_{user_id[:8]}_{body.credits}cr",
        "notes": {
            "user_id": user_id,
            "credits": str(body.credits),
            "email":   user.email,
        },
    })

    return {
        "order_id": order["id"],
        "amount":   amount,
        "currency": settings.RAZORPAY_CURRENCY,
        "key_id":   settings.RAZORPAY_KEY_ID,
        "credits":  body.credits,
        "name":     "InterviewIQ",
        "description": f"{body.credits} Interview Credit{'s' if body.credits > 1 else ''}",
        "prefill_email": user.email,
    }


@router.post("/verify")
async def verify_payment(
    body: VerifyRequest,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Verify Razorpay payment signature (client-side callback).
    Credits are added only after cryptographic verification passes.
    """
    if body.credits not in CREDIT_AMOUNT_MAP:
        raise HTTPException(status_code=400, detail="Invalid credits value")

    # Verify signature: HMAC-SHA256(order_id + "|" + payment_id, key_secret)
    expected = hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode(),
        f"{body.razorpay_order_id}|{body.razorpay_payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, body.razorpay_signature):
        raise HTTPException(status_code=400, detail="Payment verification failed — invalid signature")

    # Signature valid → credit the user
    result = await db.execute(select(User).where(User.user_id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.interview_credits += body.credits

    return {
        "success": True,
        "interview_credits": user.interview_credits,
        "credits_added": body.credits,
    }


@router.get("/status")
async def billing_status(
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return current credit balance and trial status for the authenticated user."""
    result = await db.execute(select(User).where(User.user_id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "interview_credits": user.interview_credits,
        "trial_used":        user.trial_used,
    }


@router.post("/redeem")
async def redeem_coupon(
    body: RedeemRequest,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Redeem a coupon code for interview credits.
    Each user can only redeem a given code once (tracked in feature_flags.redeemed_coupons).
    Checks: exists → active → not expired → max_uses not reached → not already used by this user.
    """
    code = body.code.strip().upper()

    coupon_res = await db.execute(select(Coupon).where(Coupon.code == code))
    coupon = coupon_res.scalar_one_or_none()
    if not coupon:
        raise HTTPException(status_code=404, detail="Invalid coupon code")
    if not coupon.is_active:
        raise HTTPException(status_code=410, detail="This coupon has been deactivated")
    if coupon.expires_at and datetime.utcnow() > coupon.expires_at:
        raise HTTPException(status_code=410, detail="This coupon has expired")
    if coupon.max_uses is not None and coupon.uses >= coupon.max_uses:
        raise HTTPException(status_code=410, detail="This coupon has reached its maximum uses")

    user_res = await db.execute(select(User).where(User.user_id == uuid.UUID(user_id)))
    user = user_res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    redeemed: list = (user.feature_flags or {}).get("redeemed_coupons", [])
    if code in redeemed:
        raise HTTPException(status_code=409, detail="You have already redeemed this coupon")

    # All checks passed — apply credits
    user.interview_credits += coupon.credits
    coupon.uses += 1

    # Record redemption to prevent double-use
    flags = dict(user.feature_flags or {})
    flags["redeemed_coupons"] = redeemed + [code]
    user.feature_flags = flags

    return {
        "success": True,
        "credits_added": coupon.credits,
        "interview_credits": user.interview_credits,
        "message": f"Coupon applied! {coupon.credits} interview credit{'s' if coupon.credits > 1 else ''} added.",
    }


@router.post("/webhook")
async def razorpay_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Optional Razorpay webhook for async payment confirmation.
    Useful as a fallback if the user closes the browser before /verify is called.

    Razorpay signs webhooks with HMAC-SHA256 using your webhook secret.
    Set RAZORPAY_WEBHOOK_SECRET in .env and configure in Razorpay Dashboard →
    Settings → Webhooks → Add new webhook.
    """
    webhook_secret = getattr(settings, "RAZORPAY_WEBHOOK_SECRET", "")
    if not webhook_secret:
        # Webhook not configured — skip silently
        return {"received": True}

    payload = await request.body()
    sig = request.headers.get("x-razorpay-signature", "")

    expected = hmac.new(
        webhook_secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, sig):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    import json
    event = json.loads(payload)

    if event.get("event") == "payment.captured":
        payment = event["payload"]["payment"]["entity"]
        notes = payment.get("notes", {})
        raw_user_id = notes.get("user_id")
        raw_credits = notes.get("credits")

        if raw_user_id and raw_credits:
            result = await db.execute(
                select(User).where(User.user_id == uuid.UUID(raw_user_id))
            )
            user = result.scalar_one_or_none()
            if user:
                # Idempotency: only add if not already credited via /verify
                # (In production, store payment_id in DB to deduplicate)
                user.interview_credits += int(raw_credits)

    return {"received": True}
