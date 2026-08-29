from __future__ import annotations

from abc import ABC, abstractmethod

from domain.enums import ChannelType
from services.ports.providers import OutboundMessage, SendResult


class EmailProvider(ABC):
    """Base class for email drivers; implements the NotificationProvider port."""

    name: str = "email"
    channel: ChannelType = ChannelType.EMAIL

    @abstractmethod
    async def send(self, message: OutboundMessage) -> SendResult: ...
