from __future__ import annotations

from html import escape

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from api.dependencies import get_uow
from domain.entities.suppression import Suppression, SuppressionReason
from domain.value_objects.ids import SuppressionId
from repositories.uow import SqlAlchemyUnitOfWork

router = APIRouter(prefix="/unsubscribe", tags=["unsubscribe"])

_PAGE = (
    "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
    "<title>{title}</title><body style='font:16px system-ui;max-width:28rem;margin:4rem auto;padding:0 1rem'>"
    "<h1 style='font-size:1.3rem'>{title}</h1>{body}"
)


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(_PAGE.format(title=escape(title), body=body), status_code=status)


def _links(request: Request):
    return getattr(request.app.state, "unsubscribe_links", None)


@router.get("", response_class=HTMLResponse)
async def confirm_unsubscribe(request: Request, token: str = Query(min_length=1, max_length=2000)) -> HTMLResponse:
    """Shows a confirm button; the POST does the work, so mail scanners that open links cannot unsubscribe anyone."""
    links = _links(request)
    parsed = links.verify(token) if links else None
    if parsed is None:
        return _page("Link not valid", "<p>This unsubscribe link is invalid or has expired.</p>", 404)
    _, channel, address = parsed
    return _page(
        "Unsubscribe",
        f"<p>Stop {escape(channel.value)} messages to <b>{escape(address)}</b>?</p>"
        f"<form method=post><input type=hidden name=token value='{escape(token)}'>"
        "<button style='font:inherit;padding:.5rem 1rem'>Unsubscribe</button></form>",
    )


@router.post("", response_class=HTMLResponse)
async def unsubscribe(
    request: Request,
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> HTMLResponse:
    """Also the RFC 8058 one-click target (`List-Unsubscribe-Post`). Idempotent."""
    # The token is in the form body (our confirm page) or the URL (RFC 8058 one-click, body "List-Unsubscribe=One-Click").
    form = parse_qs((await request.body()).decode("utf-8", "replace"))
    token = (form.get("token") or [request.query_params.get("token", "")])[0][:2000]
    links = _links(request)
    parsed = links.verify(token) if links and token else None
    if parsed is None:
        return _page("Link not valid", "<p>This unsubscribe link is invalid or has expired.</p>", 404)
    tenant_id, channel, address = parsed
    async with uow:
        await uow.suppressions.add(Suppression(
            id=SuppressionId.new(), tenant_id=tenant_id, channel=channel, address=address,
            reason=SuppressionReason.UNSUBSCRIBE, detail="Unsubscribe link",
        ))
        await uow.commit()
    return _page("You are unsubscribed", f"<p>{escape(address)} will no longer receive {escape(channel.value)} messages.</p>")
