from __future__ import annotations

from abc import ABC, abstractmethod

from domain.enums import ChannelType
from services.ports.providers import OutboundMessage, SendResult


class InAppProvider(ABC):
    name: str = "in_app"
    channel: ChannelType = ChannelType.IN_APP

    @abstractmethod
    async def send(self, message: OutboundMessage) -> SendResult: ...
