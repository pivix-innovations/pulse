from __future__ import annotations

from html import escape
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from api.dependencies import get_uow
from domain.entities.category_preference import ALL_CHANNELS, CategoryPreference
from domain.entities.recipient import Recipient
from domain.value_objects.ids import CategoryPreferenceId, RecipientId, TenantId
from repositories.uow import SqlAlchemyUnitOfWork
from workflows.registry import WorkflowDefinition

router = APIRouter(prefix="/preference-center", tags=["unsubscribe"])

_STYLE = "font:16px system-ui;max-width:30rem;margin:3rem auto;padding:0 1rem;line-height:1.5"


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{escape(title)}</title><body style='{_STYLE}'><h1 style='font-size:1.3rem'>{escape(title)}</h1>{body}",
        status_code=status,
    )


_BAD = ("Link not valid", "<p>This link is invalid or has expired.</p>")


async def _categories(uow: SqlAlchemyUnitOfWork, tenant_id: TenantId) -> list[str]:
    """The kinds of message this tenant sends: every category declared by one of its workflows."""
    found: set[str] = set()
    for wf in await uow.workflows.list(tenant_id):
        try:
            category = WorkflowDefinition.from_dict(wf.name, wf.event_type, wf.definition).category
        except (KeyError, ValueError):
            continue
        if category:
            found.add(category)
    return sorted(found)


def _links(request: Request):
    return getattr(request.app.state, "unsubscribe_links", None)


@router.get("", response_class=HTMLResponse)
async def show_preferences(
    request: Request,
    token: str = Query(min_length=1, max_length=2000),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> HTMLResponse:
    """Hosted page where a recipient picks which kinds of message they want. Linked from `{{ preferences_url }}`."""
    links = _links(request)
    parsed = links.verify_preferences(token) if links else None
    if parsed is None:
        return _page(*_BAD, status=404)
    tenant_id, ref = parsed
    async with uow:
        categories = await _categories(uow, tenant_id)
        recipient = await uow.recipients.get_by_external_ref(tenant_id, ref)
        chosen = {
            p.category: p.opted_in
            for p in (await uow.category_preferences.list_for_recipient(tenant_id, recipient.id) if recipient else [])
            if p.channel == ALL_CHANNELS
        }
    if not categories:
        return _page("Your preferences", "<p>There is nothing to choose here yet.</p>")
    rows = "".join(
        f"<label style='display:block;margin:.6rem 0'><input type=checkbox name='cat_{escape(c)}'"
        f"{' checked' if chosen.get(c, True) else ''}> {escape(c.replace('-', ' ').replace('_', ' ').capitalize())}</label>"
        for c in categories
    )
    return _page(
        "Your preferences",
        "<p>Choose the messages you want to receive.</p>"
        f"<form method=post><input type=hidden name=token value='{escape(token)}'>{rows}"
        "<button style='font:inherit;padding:.5rem 1rem;margin-top:.8rem'>Save</button></form>",
    )


@router.post("", response_class=HTMLResponse)
async def save_preferences(request: Request, uow: SqlAlchemyUnitOfWork = Depends(get_uow)) -> HTMLResponse:
    form = parse_qs((await request.body()).decode("utf-8", "replace"))
    token = (form.get("token") or [request.query_params.get("token", "")])[0][:2000]
    links = _links(request)
    parsed = links.verify_preferences(token) if links and token else None
    if parsed is None:
        return _page(*_BAD, status=404)
    tenant_id, ref = parsed
    async with uow:
        recipient = await uow.recipients.get_by_external_ref(tenant_id, ref)
        if recipient is None:
            recipient = await uow.recipients.upsert_by_external_ref(
                Recipient(id=RecipientId.new(), tenant_id=tenant_id, external_ref=ref)
            )
        # Only categories the tenant really has are accepted; a checkbox that is absent means "off".
        for category in await _categories(uow, tenant_id):
            await uow.category_preferences.upsert(CategoryPreference(
                id=CategoryPreferenceId.new(), tenant_id=tenant_id, recipient_id=recipient.id,
                category=category, channel=ALL_CHANNELS, opted_in=f"cat_{category}" in form,
            ))
        await uow.commit()
    return _page("Preferences saved", "<p>Your choices will apply to future messages.</p>")
