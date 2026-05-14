"""Router — read-only trending posts feed + draft/skip/send status transitions.

``GET /api/v1/trending`` — list trending posts (any auth role).
``POST /api/v1/trending/{post_id}/draft`` — admin-only, render the active
template against the post and flip status to ``DRAFTED``. Returns the
rendered draft text for the UI to show in an editable textarea.
``POST /api/v1/trending/{post_id}/skip`` — admin-only, set status to
``SKIPPED``. Skip is a purely local action; FB is not touched.
``POST /api/v1/trending/{post_id}/comment`` — admin-only, call the
Playwright :func:`send_comment` with the active FB account's cookies.
Rate-limited to 5 comments / 6 hours via :class:`RateLimitService`.
On success records ``CommentHistory(status='SENT')`` which auto-flips
the post to ``COMMENTED``; on sender error records ``FAILED`` without
mutating post status. Cookie expired / checkpoint errors also mark the
FB account accordingly.

Status transition rules for ``/draft``:
- ``NEW`` / ``DRAFTED`` / ``SKIPPED`` may be drafted (or re-drafted).
- ``COMMENTED`` is terminal; trying to draft it returns 409.
- Missing active template → 400.
- Missing post row → 404.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from bot.modules.comment_sender import (
    CheckpointRequiredError,
    CommentSendError,
    CookieExpiredError,
    send_comment,
)
from server.auth import Role, get_current_user, require_role
from server.crypto import decrypt_cookies
from server.database import get_db
from server.models import FBAccount, TrendingPost, Source
from server.services.rate_limit_service import (
    RateLimitExceededError,
    RateLimitService,
)
from server.services.template_service import TemplateService, render_template
from server.services.ai_draft_service import (
    AIDraftConfigError,
    AIDraftEmptyResponseError,
    AIDraftNotFoundError,
    AIDraftRateLimitError,
    AIDraftService,
    AIDraftUpstreamError,
)
from server.utils.fb_url import classify_unsupported_post_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/trending", tags=["trending"])

_VALID_SORTS = {"score", "velocity", "recent"}
_VALID_STATUSES = {"NEW", "DRAFTED", "SKIPPED", "COMMENTED"}
_MAX_LIMIT = 200
_DEFAULT_LIMIT = 50

_admin_only = require_role(Role.ADMIN)


# Path fragments that cannot host a comment composer live in
# ``server.utils.fb_url``. We alias the classifier here so existing call
# sites keep the short name without changing signatures.
_classify_unsupported_post_url = classify_unsupported_post_url


def _serialize(post: TrendingPost, source: Source | None) -> dict:
    return {
        "id": post.id,
        "fb_post_id": post.fb_post_id,
        "author_name": post.author_name,
        "author_fb_id": post.author_fb_id,
        "text_snippet": post.text_snippet,
        "post_url": post.post_url,
        "unsupported_kind": _classify_unsupported_post_url(post.post_url or ""),
        "thumbnail_url": post.thumbnail_url,
        "likes": post.likes,
        "comments": post.comments,
        "shares": post.shares,
        "reactions_total": post.reactions_total,
        "score": post.score,
        "velocity": post.velocity,
        "post_timestamp": (
            post.post_timestamp.isoformat() if post.post_timestamp else None
        ),
        "collected_at": (
            post.collected_at.isoformat() if post.collected_at else None
        ),
        "status": post.status,
        "source": (
            {
                "id": source.id,
                "type": source.type,
                "label": source.label,
            }
            if source is not None
            else None
        ),
    }


@router.get("")
def list_trending(
    status: str | None = None,
    source_id: int | None = None,
    sort: str = "score",
    limit: int = _DEFAULT_LIMIT,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if sort not in _VALID_SORTS:
        raise HTTPException(
            status_code=400,
            detail=f"sort harus salah satu dari {sorted(_VALID_SORTS)}",
        )
    if status is not None and status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"status harus salah satu dari {sorted(_VALID_STATUSES)}",
        )

    # Clamp limit to a sane upper bound. Values < 1 fall back to default
    # so the UI can't accidentally request zero rows.
    try:
        limit_int = int(limit)
    except (TypeError, ValueError):
        limit_int = _DEFAULT_LIMIT
    if limit_int < 1:
        limit_int = _DEFAULT_LIMIT
    if limit_int > _MAX_LIMIT:
        limit_int = _MAX_LIMIT

    query = db.query(TrendingPost)
    if status is not None:
        query = query.filter(TrendingPost.status == status)
    if source_id is not None:
        query = query.filter(TrendingPost.source_id == source_id)

    total = query.with_entities(func.count(TrendingPost.id)).scalar() or 0

    if sort == "velocity":
        query = query.order_by(desc(TrendingPost.velocity), desc(TrendingPost.id))
    elif sort == "recent":
        query = query.order_by(
            desc(TrendingPost.collected_at), desc(TrendingPost.id)
        )
    else:  # "score"
        query = query.order_by(desc(TrendingPost.score), desc(TrendingPost.id))

    rows = query.limit(limit_int).all()

    source_ids = {row.source_id for row in rows}
    sources_by_id: dict[int, Source] = {}
    if source_ids:
        for src in db.query(Source).filter(Source.id.in_(source_ids)).all():
            sources_by_id[src.id] = src

    posts = [
        _serialize(row, sources_by_id.get(row.source_id)) for row in rows
    ]
    return {"posts": posts, "total": int(total)}


def _load_post_or_404(db: Session, post_id: int) -> TrendingPost:
    post = db.query(TrendingPost).filter(TrendingPost.id == post_id).first()
    if post is None:
        raise HTTPException(status_code=404, detail="Post gak ketemu")
    return post


@router.post("/{post_id}/draft")
def generate_draft(
    post_id: int,
    user=Depends(_admin_only),
    db: Session = Depends(get_db),
):
    """Render the active template and flip post to ``DRAFTED``.

    Allows re-drafting from ``NEW`` / ``DRAFTED`` / ``SKIPPED``, but
    rejects ``COMMENTED`` as terminal.
    """
    post = _load_post_or_404(db, post_id)
    if post.status == "COMMENTED":
        raise HTTPException(
            status_code=409,
            detail="Post udah COMMENTED, gak bisa di-draft ulang.",
        )

    template = TemplateService(db).get_active()
    if template is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Belum ada template aktif — isi dulu di halaman Template."
            ),
        )

    draft_text = render_template(
        template.template_text,
        author_name=post.author_name,
        text_snippet=post.text_snippet,
    )

    post.status = "DRAFTED"
    db.commit()
    db.refresh(post)

    source = (
        db.query(Source).filter(Source.id == post.source_id).first()
        if post.source_id is not None
        else None
    )
    return {"draft_text": draft_text, "post": _serialize(post, source)}


@router.post("/{post_id}/ai-draft")
def generate_ai_draft(
    post_id: int,
    user=Depends(_admin_only),
    db: Session = Depends(get_db),
):
    """Generate a contextual draft via LLM (sumopod.com).

    Uses the post's context (author, text, engagement) + all active
    comment templates as style references. Does NOT change post status
    — the user still has to review + click Generate Draft or Send to
    flip it to DRAFTED/COMMENTED. This endpoint is pure text generation.

    Rate-limited: 15s cooldown per user, 429 on cooldown hit.
    """
    post = _load_post_or_404(db, post_id)
    if post.status == "COMMENTED":
        raise HTTPException(
            status_code=409,
            detail="Post udah COMMENTED, gak perlu generate draft lagi.",
        )

    # Refuse preflight for unsupported URL kinds (Stories/Reel/Watch).
    kind = _classify_unsupported_post_url(post.post_url or "")
    if kind:
        raise HTTPException(
            status_code=415,
            detail=f"Tipe post '{kind}' tidak didukung untuk komentar.",
        )

    user_id = int(user.get("sub", 0))

    try:
        svc = AIDraftService(db)
    except AIDraftConfigError as exc:
        logger.error("AI draft service not configured: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="AI draft belum dikonfigurasi di server (SUMOPOD_API_KEY).",
        )

    try:
        text = svc.generate(post_id=post.id, user_id=user_id)
    except AIDraftNotFoundError:
        raise HTTPException(status_code=404, detail="Post gak ketemu")
    except AIDraftRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    except AIDraftEmptyResponseError:
        raise HTTPException(
            status_code=502,
            detail="LLM balikin respons kosong, coba lagi.",
        )
    except AIDraftUpstreamError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return {"draft_text": text, "post_id": post.id}


@router.post("/{post_id}/skip")
def skip_post(
    post_id: int,
    user=Depends(_admin_only),
    db: Session = Depends(get_db),
):
    """Mark post as ``SKIPPED`` locally (no FB interaction)."""
    post = _load_post_or_404(db, post_id)
    if post.status == "COMMENTED":
        raise HTTPException(
            status_code=409,
            detail="Post udah COMMENTED, gak bisa di-skip.",
        )
    post.status = "SKIPPED"
    db.commit()
    db.refresh(post)

    source = (
        db.query(Source).filter(Source.id == post.source_id).first()
        if post.source_id is not None
        else None
    )
    return {"post": _serialize(post, source)}


# --- Send Comment (F5) ------------------------------------------------------


class SendCommentRequest(BaseModel):
    comment_text: str = Field(..., min_length=1, max_length=5_000)


def _quota_dict(svc: RateLimitService) -> dict:
    stats = svc.window_stats()
    resets = stats.get("resets_at")
    return {
        "allowed": stats["allowed"],
        "used": stats["used"],
        "remaining": stats["remaining"],
        "limit": stats["limit"],
        "window_hours": stats["window_hours"],
        "resets_at": resets.isoformat() if resets else None,
    }


def _pick_active_fb_account(db: Session) -> FBAccount | None:
    return (
        db.query(FBAccount)
        .filter(FBAccount.status == "ACTIVE")
        .filter(FBAccount.cookies_encrypted.isnot(None))
        .order_by(FBAccount.id.asc())
        .first()
    )


@router.post("/{post_id}/comment")
async def send_post_comment(
    post_id: int,
    payload: SendCommentRequest,
    user=Depends(_admin_only),
    db: Session = Depends(get_db),
):
    """Post ``payload.comment_text`` as a real FB comment under this post.

    Wiring order:
      1. Validate inputs + load post + 409 if already COMMENTED.
      2. Rate-limit preflight via :class:`RateLimitService.check_allowed`.
      3. Load active FB account cookies; 503 kalau gak ada.
      4. Invoke Playwright ``send_comment``.
      5. On success → ``record_send(status='SENT')`` (auto-flips post
         status). On sender error → ``record_send(status='FAILED')``
         (no quota burn, no status flip) and return 502.
      6. CookieExpired / Checkpoint → mark FB account + 503.
    """
    stripped = (payload.comment_text or "").strip()
    if not stripped:
        raise HTTPException(
            status_code=400,
            detail="comment_text kosong — minimal 1 karakter bukan whitespace.",
        )

    post = _load_post_or_404(db, post_id)
    if post.status == "COMMENTED":
        raise HTTPException(
            status_code=409,
            detail="Post udah COMMENTED, gak bisa dikomen lagi.",
        )
    if not post.post_url:
        raise HTTPException(
            status_code=400,
            detail="Post gak punya post_url — gak bisa buka post di FB.",
        )

    unsupported_kind = _classify_unsupported_post_url(post.post_url)
    if unsupported_kind is not None:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Tipe post '{unsupported_kind}' tidak didukung untuk komen "
                f"via bot — FB tidak merender composer di halaman "
                f"{unsupported_kind.lower()}. Skip post ini."
            ),
        )

    rate_svc = RateLimitService(db)
    pre = rate_svc.check_allowed()
    if not pre.allowed:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Quota habis bro — {pre.used}/{pre.limit} komen dalam "
                f"{pre.window_hours} jam. Tunggu sampe "
                f"{pre.resets_at.isoformat() if pre.resets_at else 'nanti'}."
            ),
        )

    account = _pick_active_fb_account(db)
    if account is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Belum ada FB account ACTIVE dengan cookies — "
                "login cookie dulu di halaman Accounts."
            ),
        )

    try:
        cookies = decrypt_cookies(account.cookies_encrypted or "")
    except Exception as exc:
        logger.exception("decrypt cookies failed for account=%s", account.id)
        raise HTTPException(
            status_code=503,
            detail=f"Decrypt cookies gagal: {exc}",
        ) from exc

    display_name = account.fb_name or account.label or "me"

    # Phase I-A-3 — pin browser fingerprint (UA + viewport) per-account so
    # FB sees a stable device for this session cookie.
    from server.services.fb_account_service import FBAccountService

    fp_svc = FBAccountService(db)
    pinned_ua, pinned_w, pinned_h = fp_svc.ensure_fingerprint(account.id)
    pinned_viewport = {"width": pinned_w, "height": pinned_h}

    # Phase I-B-3 — capture rotated cookies after the send succeeds and
    # persist them back silently so the next tick uses FB's newest session
    # blob (FB rotates ``xs`` mid-session).
    async def _refresh_cookies(new_cookies: dict[str, str]) -> None:
        FBAccountService(db).refresh_cookies_silent(
            account.id, cookies=new_cookies
        )

    # Actually post to Facebook via Playwright.
    try:
        result = await send_comment(
            post_url=post.post_url,
            comment_text=stripped,
            cookies=cookies,
            display_name=display_name,
            user_agent=pinned_ua,
            viewport=pinned_viewport,
            on_cookies_refresh=_refresh_cookies,
            account_id=account.id,
        )
    except CookieExpiredError as exc:
        logger.warning(
            "cookie expired for account=%s while commenting post=%s: %s",
            account.id,
            post.id,
            exc,
        )
        account.status = "EXPIRED"
        db.commit()
        rate_svc.record_send(
            trending_post_id=post.id,
            comment_text=stripped,
            user_id=getattr(user, "id", None),
            status="FAILED",
            error_message=f"cookie_expired: {exc}",
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "Cookie FB expired — login ulang di halaman Accounts."
            ),
        ) from exc
    except CheckpointRequiredError as exc:
        logger.warning(
            "checkpoint required for account=%s post=%s: %s",
            account.id,
            post.id,
            exc,
        )
        account.status = "CHECKPOINT"
        db.commit()
        rate_svc.record_send(
            trending_post_id=post.id,
            comment_text=stripped,
            user_id=getattr(user, "id", None),
            status="FAILED",
            error_message=f"checkpoint: {exc}",
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "FB minta checkpoint/verifikasi — buka akun manual dulu."
            ),
        ) from exc
    except CommentSendError as exc:
        logger.exception(
            "comment sender error for post=%s: %s", post.id, exc
        )
        rate_svc.record_send(
            trending_post_id=post.id,
            comment_text=stripped,
            user_id=getattr(user, "id", None),
            status="FAILED",
            error_message=str(exc),
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not result.success:
        rate_svc.record_send(
            trending_post_id=post.id,
            comment_text=stripped,
            user_id=getattr(user, "id", None),
            status="FAILED",
            error_message=result.error or "unknown sender error",
        )
        raise HTTPException(
            status_code=502,
            detail=result.error or "Kirim komen gagal tanpa pesan.",
        )

    # Success — record SENT (auto-flip post to COMMENTED).
    try:
        rate_svc.record_send(
            trending_post_id=post.id,
            comment_text=stripped,
            user_id=getattr(user, "id", None),
            fb_comment_id=result.fb_comment_id,
            status="SENT",
        )
    except RateLimitExceededError as exc:
        # Very rare race if two admins click Send at once. Log + 429
        # (but komen udah kelanjur ter-post, so just warn loud).
        logger.warning(
            "rate limit race after send succeeded post=%s: %s",
            post.id,
            exc,
        )
        raise HTTPException(
            status_code=429,
            detail=(
                "Komen udah kelanjur ke-post tapi rate limit full — "
                "next send diblok."
            ),
        ) from exc

    db.refresh(post)
    source = (
        db.query(Source).filter(Source.id == post.source_id).first()
        if post.source_id is not None
        else None
    )
    return {
        "result": {
            "success": True,
            "comment_text": result.comment_text,
            "post_url": result.post_url,
            "fb_comment_id": result.fb_comment_id,
            "error": None,
        },
        "post": _serialize(post, source),
        "quota": _quota_dict(rate_svc),
    }
