from __future__ import annotations

import pytest

from domain.enums import ChannelType
from providers.catalog import BuildContext, get_spec, list_specs


def test_every_entry_has_a_logo_and_unique_id():
    specs = list_specs()
    assert len({s.id for s in specs}) == len(specs)
    for s in specs:
        assert s.logo_svg().lstrip().startswith("<svg")


def test_every_channel_has_at_least_one_catalog_entry():
    assert {s.channel for s in list_specs()} == set(ChannelType)


def test_smtp_validation_splits_config_from_secrets_and_applies_defaults():
    spec = get_spec(ChannelType.EMAIL, "smtp")
    config, secrets = spec.validate(
        {"host": "smtp.acme.io", "from_address": "hi@acme.io"},
        {"username": "u", "password": "p"},
    )
    assert config == {"host": "smtp.acme.io", "from_address": "hi@acme.io", "port": 587, "use_tls": True}
    assert secrets == {"username": "u", "password": "p"}


def test_validation_lists_every_problem():
    spec = get_spec(ChannelType.EMAIL, "smtp")
    with pytest.raises(ValueError) as err:
        spec.validate({"port": "abc", "password": "p", "bogus": 1}, {"host": "x"})
    msg = str(err.value)
    assert "'password' is not a config field (send it under secrets)" in msg
    assert "'bogus' is not a config field" in msg
    assert "'host' is not a secret field" in msg


def test_required_secret_may_already_be_stored():
    spec = get_spec(ChannelType.PUSH, "fcm")
    with pytest.raises(ValueError, match="service_account_json"):
        spec.validate({}, {})
    spec.validate({}, {}, existing_secret_keys=frozenset({"service_account_json"}))


def test_public_description_never_carries_values():
    public = get_spec(ChannelType.EMAIL, "smtp").public()
    assert public["logo_url"].endswith("/email/smtp/logo")
    assert any(f["key"] == "password" and f["secret"] for f in public["fields"])


def test_drivers_build_from_config_and_secrets():
    smtp = get_spec(ChannelType.EMAIL, "smtp").build(
        {"host": "h", "port": 25, "from_address": "a@b.c"}, {"password": "p"}, BuildContext()
    )
    assert smtp.name == "smtp"
    assert get_spec(ChannelType.SMS, "console").build({}, {}, BuildContext()).name == "console"


def test_twilio_needs_a_sender():
    spec = get_spec(ChannelType.SMS, "twilio")
    base = {"account_sid": "AC1"}
    with pytest.raises(ValueError, match="from_number"):
        spec.validate(base, {"auth_token": "t"})
    spec.validate({**base, "from_number": "+15550001111"}, {"auth_token": "t"})
    spec.validate({**base, "messaging_service_sid": "MG1"}, {"auth_token": "t"})


def test_real_providers_are_testable_and_log_only_ones_are_not():
    testable = {s.id for s in list_specs() if s.check is not None}
    assert {"email/smtp", "email/resend", "email/sendgrid", "sms/twilio", "push/fcm"} <= testable
    assert "email/console" not in testable


def test_new_drivers_build_from_saved_values():
    ctx = BuildContext()
    assert get_spec(ChannelType.EMAIL, "resend").build({"from_address": "a@b.c"}, {"api_key": "k"}, ctx).name == "resend"
    assert get_spec(ChannelType.EMAIL, "sendgrid").build({"from_address": "a@b.c"}, {"api_key": "k"}, ctx).name == "sendgrid"
    assert get_spec(ChannelType.SMS, "twilio").build(
        {"account_sid": "AC", "from_number": "+1"}, {"auth_token": "t"}, ctx
    ).name == "twilio"
