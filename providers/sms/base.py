from __future__ import annotations

from abc import ABC, abstractmethod

from domain.enums import ChannelType
from services.ports.providers import OutboundMessage, SendResult


class SmsProvider(ABC):
    name: str = "sms"
    channel: ChannelType = ChannelType.SMS

    @abstractmethod
    async def send(self, message: OutboundMessage) -> SendResult: ...
