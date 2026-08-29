from __future__ import annotations

from abc import ABC, abstractmethod

from domain.enums import ChannelType
from services.ports.providers import OutboundMessage, SendResult


class PushProvider(ABC):
    name: str = "push"
    channel: ChannelType = ChannelType.PUSH

    @abstractmethod
    async def send(self, message: OutboundMessage) -> SendResult: ...
