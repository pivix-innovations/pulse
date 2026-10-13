"""
The catalog of delivery providers Pulse supports.

Each entry describes what a client sees (name, logo, which fields to fill in)
and how to build the driver from a saved provider's config + secrets. Clients
configure an entry once as a saved provider, then attach it to workflows.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from config.email import EmailSettings
from domain.enums import ChannelType
from providers.email.console import ConsoleEmailProvider
from providers.email.smtp import SmtpEmailProvider
from providers.push.console import ConsolePushProvider
from providers.push.firebase import FcmPushProvider
from providers.sms.console import ConsoleSmsProvider
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


Builder = Callable[[dict[str, Any], dict[str, Any], BuildContext], NotificationProvider]


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
        return clean_config, clean_secrets

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "key": self.key,
            "channel": self.channel.value,
            "name": self.name,
            "description": self.description,
            "builtin": self.builtin,
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
