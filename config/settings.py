from __future__ import annotations

from config.base import EnvironmentSettings
from config.breaker import BreakerSettings
from config.app import AppSettings
from config.api import ApiSettings
from config.email import EmailSettings
from config.logging import LoggingSettings
from config.database import DatabaseSettings
from config.outbox import OutboxSettings
from config.push import PushSettings
from config.reconciliation import ReconciliationSettings
from config.redis import RedisSettings
from config.retry import RetrySettings
from config.security import SecuritySettings
from config.webhook import WebhookSettings


class Settings(EnvironmentSettings):
    app: AppSettings = AppSettings()
    # No default: DatabaseSettings has required fields (credentials come from
    # the environment), so it must be built by the settings loader itself.
    database: DatabaseSettings
    api: ApiSettings = ApiSettings()
    logging: LoggingSettings = LoggingSettings()
    redis: RedisSettings = RedisSettings()
    security: SecuritySettings = SecuritySettings()
    email: EmailSettings = EmailSettings()
    outbox: OutboxSettings = OutboxSettings()
    push: PushSettings = PushSettings()
    retry: RetrySettings = RetrySettings()
    breaker: BreakerSettings = BreakerSettings()
    reconciliation: ReconciliationSettings = ReconciliationSettings()
    webhook: WebhookSettings = WebhookSettings()
