from __future__ import annotations

import pytest

from services.secrets import SecretBox, SecretsDecryptionError, SecretsNotConfigured, generate_key


def test_roundtrip_and_ciphertext_hides_values():
    box = SecretBox(generate_key())
    token = box.encrypt({"password": "hunter2"})
    assert "hunter2" not in token
    assert box.decrypt(token) == {"password": "hunter2"}


def test_empty_secrets_store_nothing():
    assert SecretBox(generate_key()).encrypt({}) is None
    assert SecretBox().decrypt(None) == {}


def test_unconfigured_box_refuses_secrets():
    with pytest.raises(SecretsNotConfigured):
        SecretBox().encrypt({"a": "b"})


def test_key_rotation_new_key_encrypts_old_key_still_decrypts():
    old, new = generate_key(), generate_key()
    token = SecretBox(old).encrypt({"k": "v"})
    rotated = SecretBox(f"{new},{old}")
    assert rotated.decrypt(token) == {"k": "v"}
    # Encrypts with the first key: the old key alone can no longer read it.
    with pytest.raises(SecretsDecryptionError):
        SecretBox(old).decrypt(rotated.encrypt({"k": "v"}))
