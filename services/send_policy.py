"""Whether a message may go to a recipient on a channel: the one place every send path asks."""
from __future__ import annotations

from typing import Sequence

from domain.entities.preference import Preference
from domain.entities.recipient import Recipient
from domain.enums import ChannelType
from domain.value_objects.ids import TenantId
from services.ports.uow import UnitOfWork
from telemetry.metrics import get_metrics


def address_for(recipient: Recipient, channel: ChannelType) -> str | None:
    if channel is ChannelType.EMAIL:
        return recipient.email
    if channel is ChannelType.SMS:
        return recipient.phone
    return None


async def blocked_reason(
    uow: UnitOfWork,
    tenant_id: TenantId,
    recipient: Recipient,
    channel: ChannelType,
    category: str | None,
    *,
    prefs: Sequence[Preference] | None = None,
) -> str | None:
    """
    Why this send must not happen, or None when it may. In order: the recipient turned the channel off;
    the recipient turned the workflow's category off (a channel-specific choice beats an "all channels" one);
    the address is on the suppression list. Workflows without a category are never category-filtered.
    """
    if prefs is None:
        prefs = await uow.preferences.list_for_recipient(tenant_id, recipient.id)
    if any(p.channel is channel and not p.opted_in for p in prefs):
        return f"recipient opted out of {channel.value}"
    if category:
        chosen = {
            p.channel: p.opted_in
            for p in await uow.category_preferences.list_for_recipient(tenant_id, recipient.id)
            if p.category == category
        }
        if not chosen.get(channel.value, chosen.get("all", True)):
            get_metrics().category_optouts.add(1, {"channel": channel.value})
            return f"recipient opted out of '{category}' messages"
    address = address_for(recipient, channel)
    if address and await uow.suppressions.is_suppressed(tenant_id, channel, address):
        get_metrics().suppressed_sends.add(1, {"channel": channel.value})
        return f"{channel.value} address is suppressed"
    return None
