"""
The catalog of delivery providers Pulse supports.

Each entry describes what a client sees (name, logo, which fields to fill in)
and how to build the driver from a saved provider's config + secrets. Clients
configure an entry once as a saved provider, then attach it to workflows.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

from config.email import EmailSettings
from domain.enums import ChannelType
from providers.email.console import ConsoleEmailProvider
from providers.email.resend import ResendEmailProvider, check_resend
from providers.email.ses import SesEmailProvider, check_ses
from providers.email.sendgrid import SendGridEmailProvider, check_sendgrid
from providers.email.smtp import SmtpEmailProvider
from providers.receipts import supports_receipts
from providers.push.console import ConsolePushProvider
from providers.push.firebase import FcmPushProvider, check_fcm
from providers.sms.console import ConsoleSmsProvider
from providers.sms.twilio import TwilioSmsProvider, check_twilio
from services.ports.providers import NotificationProvider

ASSETS = Path(__file__).parent / "assets"

FieldKind = Literal["string", "number", "boolean", "select", "textarea"]


@dataclass(frozen=True, slots=True)
class ConfigField:
    key: str
    label: str
    kind: FieldKind = "string"
    secret: bool = False
    required: bool = False
    default: Any = None
    help: str = ""
    options: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BuildContext:
    """Process-level dependencies some drivers need (e.g. SSE's Redis broadcaster)."""

    redis: Any = None
    # Env-configured FCM credentials, used by saved FCM providers that
    # predate per-provider secrets.
    fcm_credentials_path: str | None = None
    # This saved provider's public webhook URL, when Pulse's public URL is set.
    webhook_url: str | None = None


Builder = Callable[[dict[str, Any], dict[str, Any], BuildContext], NotificationProvider]
# Live credential check: (config, secrets) -> (ok, human-readable detail).
Checker = Callable[[dict[str, Any], dict[str, Any]], Awaitable[tuple[bool, str]]]
# Rules spanning several fields; raises ValueError with the problem.
CrossValidator = Callable[[dict[str, Any], dict[str, Any]], None]


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    key: str
    channel: ChannelType
    name: str
    description: str
    logo: str  # filename under providers/assets
    build: Builder
    fields: tuple[ConfigField, ...] = ()
    # Needs nothing from the client; a single saved instance is enough.
    builtin: bool = False
    docs_url: str | None = None
    check: Checker | None = None
    cross_validate: CrossValidator | None = None

    @property
    def id(self) -> str:
        return f"{self.channel.value}/{self.key}"

    def logo_svg(self) -> str:
        return (ASSETS / self.logo).read_text()

    def validate(
        self,
        config: dict[str, Any],
        secrets: dict[str, Any],
        *,
        existing_secret_keys: frozenset[str] = frozenset(),
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """
        Splits and type-checks input against the field schema. Secrets
        already stored (`existing_secret_keys`) satisfy `required`. Raises
        ValueError listing every problem.
        """
        by_key = {f.key: f for f in self.fields}
        problems: list[str] = []
        clean_config: dict[str, Any] = {}
        clean_secrets: dict[str, Any] = {}

        for key in config:
            f = by_key.get(key)
            if f is None or f.secret:
                problems.append(
                    f"'{key}' is not a config field"
                    + (" (send it under secrets)" if f is not None else "")
                )
        for key in secrets:
            f = by_key.get(key)
            if f is None or not f.secret:
                problems.append(f"'{key}' is not a secret field")
        if problems:
            raise ValueError("; ".join(problems))

        for f in self.fields:
            source = secrets if f.secret else config
            present = f.key in source and source[f.key] not in (None, "")
            if not present:
                if f.secret:
                    if f.required and f.key not in existing_secret_keys:
                        problems.append(f"'{f.key}' is required")
                elif f.default is not None:
                    clean_config[f.key] = f.default
                elif f.required:
                    problems.append(f"'{f.key}' is required")
                continue
            try:
                value = _coerce(f, source[f.key])
            except ValueError as exc:
                problems.append(f"'{f.key}': {exc}")
                continue
            (clean_secrets if f.secret else clean_config)[f.key] = value
        if problems:
            raise ValueError("; ".join(problems))
        if self.cross_validate is not None:
            self.cross_validate(clean_config, {**{k: None for k in existing_secret_keys}, **clean_secrets})
        return clean_config, clean_secrets

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "key": self.key,
            "channel": self.channel.value,
            "name": self.name,
            "description": self.description,
            "builtin": self.builtin,
            "testable": self.check is not None,
            "receipts": supports_receipts(self.id),
            "docs_url": self.docs_url,
            "logo_url": f"/api/v1/provider-catalog/{self.channel.value}/{self.key}/logo",
            "fields": [
                {
                    "key": f.key,
                    "label": f.label,
                    "kind": f.kind,
                    "secret": f.secret,
                    "required": f.required,
                    "default": f.default,
                    "help": f.help,
                    "options": list(f.options),
                }
                for f in self.fields
            ],
        }


def _coerce(f: ConfigField, value: Any) -> Any:
    if f.kind == "number":
        if isinstance(value, bool):
            raise ValueError("must be a number")
        try:
            return int(value) if float(value).is_integer() else float(value)
        except (TypeError, ValueError):
            raise ValueError("must be a number") from None
    if f.kind == "boolean":
        if isinstance(value, bool):
            return value
        if str(value).lower() in ("true", "false"):
            return str(value).lower() == "true"
        raise ValueError("must be true or false")
    if f.kind == "select":
        if value not in f.options:
            raise ValueError(f"must be one of {', '.join(f.options)}")
        return value
    if not isinstance(value, str):
        raise ValueError("must be text")
    return value


# ── Builders ───────────────────────────────────────────────────────────


def _build_smtp(config: dict[str, Any], secrets: dict[str, Any], _: BuildContext):
    # `smtp_*` keys: rows saved before the catalog existed.
    return SmtpEmailProvider(
        EmailSettings(
            driver="smtp",
            from_address=config.get("from_address", "pulse@localhost"),
            smtp_host=config.get("host") or config.get("smtp_host", "localhost"),
            smtp_port=config.get("port") or config.get("smtp_port", 587),
            smtp_use_tls=config.get("use_tls", config.get("smtp_use_tls", True)),
            smtp_username=secrets.get("username"),
            smtp_password=secrets.get("password"),
        )
    )


def _build_twilio(config: dict[str, Any], secrets: dict[str, Any], ctx: BuildContext):
    return TwilioSmsProvider(
        account_sid=config["account_sid"],
        auth_token=secrets["auth_token"],
        from_number=config.get("from_number"),
        messaging_service_sid=config.get("messaging_service_sid"),
        status_callback=ctx.webhook_url,
    )


def _twilio_cross(config: dict[str, Any], secrets: dict[str, Any]) -> None:
    if not config.get("from_number") and not config.get("messaging_service_sid"):
        raise ValueError("Provide a 'from_number' or a 'messaging_service_sid'")


def _build_resend(config: dict[str, Any], secrets: dict[str, Any], _: BuildContext):
    return ResendEmailProvider(
        api_key=secrets["api_key"],
        from_address=config["from_address"],
        receipts=bool(secrets.get("webhook_secret")),
    )


def _build_ses(config: dict[str, Any], secrets: dict[str, Any], _: BuildContext):
    return SesEmailProvider(
        region=config.get("region", "us-east-1"),
        access_key_id=secrets["access_key_id"],
        secret_access_key=secrets["secret_access_key"],
        from_address=config["from_address"],
        configuration_set=config.get("configuration_set"),
    )


def _build_sendgrid(config: dict[str, Any], secrets: dict[str, Any], _: BuildContext):
    return SendGridEmailProvider(
        api_key=secrets["api_key"],
        from_address=config["from_address"],
        receipts=bool(secrets.get("webhook_public_key")),
    )


async def _check_smtp(config: dict[str, Any], secrets: dict[str, Any]) -> tuple[bool, str]:
    import aiosmtplib

    client = aiosmtplib.SMTP(
        hostname=config.get("host"),
        port=config.get("port", 587),
        start_tls=config.get("use_tls", True),
        timeout=10,
    )
    try:
        await client.connect()
        if secrets.get("username"):
            await client.login(secrets["username"], secrets.get("password") or "")
        await client.quit()
    except aiosmtplib.SMTPAuthenticationError:
        return False, "Connected, but the server rejected the username/password"
    except (aiosmtplib.SMTPException, OSError) as exc:
        return False, f"Could not connect: {exc}"
    return True, "Connected and authenticated" if secrets.get("username") else "Connected"


async def _check_twilio(config: dict[str, Any], secrets: dict[str, Any]) -> tuple[bool, str]:
    return await check_twilio(config["account_sid"], secrets["auth_token"])


async def _check_ses(config: dict[str, Any], secrets: dict[str, Any]) -> tuple[bool, str]:
    return await check_ses(
        config.get("region", "us-east-1"), secrets["access_key_id"], secrets["secret_access_key"]
    )


async def _check_resend(config: dict[str, Any], secrets: dict[str, Any]) -> tuple[bool, str]:
    return await check_resend(secrets["api_key"])


async def _check_sendgrid(config: dict[str, Any], secrets: dict[str, Any]) -> tuple[bool, str]:
    return await check_sendgrid(secrets["api_key"])


async def _check_fcm(config: dict[str, Any], secrets: dict[str, Any]) -> tuple[bool, str]:
    return await check_fcm(secrets["service_account_json"])


def _build_fcm(config: dict[str, Any], secrets: dict[str, Any], ctx: BuildContext):
    return FcmPushProvider(
        credentials_json=secrets.get("service_account_json"),
        credentials_path=None if secrets.get("service_account_json") else ctx.fcm_credentials_path,
    )


def _build_sse(config: dict[str, Any], secrets: dict[str, Any], ctx: BuildContext):
    from providers.in_app.sse import SseInAppProvider
    from sse.broadcaster import SseBroadcaster

    if ctx.redis is None:
        raise LookupError("in_app provider requires a Redis connection")
    return SseInAppProvider(SseBroadcaster(ctx.redis))


_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        key="smtp",
        channel=ChannelType.EMAIL,
        name="SMTP",
        description="Send email through any SMTP server or relay.",
        logo="email-smtp.svg",
        build=_build_smtp,
        fields=(
            ConfigField("host", "Host", required=True, help="e.g. smtp.example.com"),
            ConfigField("port", "Port", "number", default=587),
            ConfigField("use_tls", "Use STARTTLS", "boolean", default=True),
            ConfigField("from_address", "From address", required=True),
            ConfigField("username", "Username", secret=True),
            ConfigField("password", "Password", secret=True),
        ),
        check=_check_smtp,
    ),
    ProviderSpec(
        key="ses",
        channel=ChannelType.EMAIL,
        name="Amazon SES",
        description="Low-cost, high-volume email through AWS Simple Email Service.",
        logo="email-ses.svg",
        build=_build_ses,
        fields=(
            ConfigField("region", "AWS region", default="us-east-1", required=True),
            ConfigField("from_address", "From address", required=True, help="Must be a verified identity in SES."),
            ConfigField("configuration_set", "Configuration set", help="Optional, for event publishing."),
            ConfigField("access_key_id", "Access key ID", secret=True, required=True),
            ConfigField("secret_access_key", "Secret access key", secret=True, required=True,
                        help="Grant the IAM user only ses:SendEmail (and ses:GetAccount to test)."),
        ),
        check=_check_ses,
        docs_url="https://docs.aws.amazon.com/ses/latest/APIReference-V2/API_SendEmail.html",
    ),
    ProviderSpec(
        key="resend",
        channel=ChannelType.EMAIL,
        name="Resend",
        description="Developer-friendly transactional email API.",
        logo="email-resend.svg",
        build=_build_resend,
        fields=(
            ConfigField("from_address", "From address", required=True, help="Must use a domain verified in Resend."),
            ConfigField("api_key", "API key", secret=True, required=True),
            ConfigField(
                "webhook_secret", "Webhook signing secret", secret=True,
                help="Optional. In Resend add a webhook to this provider's URL (shown after saving), "
                "then paste its signing secret (whsec_…) to confirm delivery and catch bounces.",
            ),
        ),
        check=_check_resend,
        docs_url="https://resend.com/docs/api-reference/emails/send-email",
    ),
    ProviderSpec(
        key="sendgrid",
        channel=ChannelType.EMAIL,
        name="SendGrid",
        description="Twilio SendGrid transactional email API.",
        logo="email-sendgrid.svg",
        build=_build_sendgrid,
        fields=(
            ConfigField("from_address", "From address", required=True, help="Must be a verified sender or domain."),
            ConfigField("api_key", "API key", secret=True, required=True, help="Needs the Mail Send permission."),
            ConfigField(
                "webhook_public_key", "Event Webhook verification key", secret=True,
                help="Optional. Enable a signed Event Webhook in SendGrid pointing at this provider's "
                "URL (shown after saving) and paste its verification key to confirm delivery and catch bounces.",
            ),
        ),
        check=_check_sendgrid,
        docs_url="https://www.twilio.com/docs/sendgrid/api-reference/mail-send/mail-send",
    ),
    ProviderSpec(
        key="console",
        channel=ChannelType.EMAIL,
        name="Email (log only)",
        description="Logs emails instead of sending them. For development.",
        logo="email-console.svg",
        build=lambda c, s, ctx: ConsoleEmailProvider(),
        builtin=True,
    ),
    ProviderSpec(
        key="fcm",
        channel=ChannelType.PUSH,
        name="Firebase Cloud Messaging",
        description="Push to Android, iOS and web through FCM HTTP v1.",
        logo="push-fcm.svg",
        build=_build_fcm,
        fields=(
            ConfigField(
                "service_account_json",
                "Service account JSON",
                "textarea",
                secret=True,
                required=True,
                help="Firebase console → Project settings → Service accounts → Generate key.",
            ),
        ),
        docs_url="https://firebase.google.com/docs/cloud-messaging",
        check=_check_fcm,
    ),
    ProviderSpec(
        key="console",
        channel=ChannelType.PUSH,
        name="Push (log only)",
        description="Logs push payloads instead of sending them. For development.",
        logo="push-console.svg",
        build=lambda c, s, ctx: ConsolePushProvider(),
        builtin=True,
    ),
    ProviderSpec(
        key="twilio",
        channel=ChannelType.SMS,
        name="Twilio",
        description="Send SMS worldwide through Twilio Programmable Messaging.",
        logo="sms-twilio.svg",
        build=_build_twilio,
        fields=(
            ConfigField("account_sid", "Account SID", required=True, help="Starts with AC."),
            ConfigField("auth_token", "Auth token", secret=True, required=True),
            ConfigField("from_number", "From number", help="E.164 format, e.g. +14155550100."),
            ConfigField("messaging_service_sid", "Messaging Service SID", help="Use instead of a from number."),
        ),
        cross_validate=_twilio_cross,
        check=_check_twilio,
        docs_url="https://www.twilio.com/docs/messaging/api/message-resource",
    ),
    ProviderSpec(
        key="console",
        channel=ChannelType.SMS,
        name="SMS (log only)",
        description="Logs SMS messages instead of sending them. For development.",
        logo="sms-console.svg",
        build=lambda c, s, ctx: ConsoleSmsProvider(),
        builtin=True,
    ),
    ProviderSpec(
        key="sse",
        channel=ChannelType.IN_APP,
        name="In-app (live stream)",
        description="Real-time in-app notifications over server-sent events. No setup needed.",
        logo="in_app-sse.svg",
        build=_build_sse,
        builtin=True,
    ),
)

_BY_ID = {s.id: s for s in _SPECS}


def list_specs() -> list[ProviderSpec]:
    return list(_SPECS)


def get_spec(channel: ChannelType, key: str) -> ProviderSpec | None:
    return _BY_ID.get(f"{channel.value}/{key}")
