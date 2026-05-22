"""Unit tests for ``core.crypto`` — the pure-Python Argon2id KDF, no Qt.

Most assertions use cheap Argon2 cost params so the suite stays fast (security
is irrelevant to a determinism check); the shipped ``DEFAULT_PARAMS`` are
exercised once on their own to prove they actually derive a valid key.
"""

import pytest

from core.crypto import (
    DEFAULT_PARAMS,
    KEY_BYTES,
    SALT_BYTES,
    KdfParams,
    derive_key,
    generate_salt,
)

# Minimal valid Argon2 params (memory_cost floor is 8 * parallelism) — fast.
FAST = KdfParams(time_cost=1, memory_cost=8, parallelism=1)
SALT = b"0123456789abcdef"  # 16 bytes


def test_key_is_256_bits():
    key = derive_key("correct horse", SALT, FAST)
    assert isinstance(key, (bytes, bytearray))
    assert len(key) == KEY_BYTES == 32


def test_derivation_is_deterministic():
    a = derive_key("master-pass", SALT, FAST)
    b = derive_key("master-pass", SALT, FAST)
    assert a == b


def test_wrong_password_yields_different_key():
    right = derive_key("correct-pass", SALT, FAST)
    wrong = derive_key("wrong-pass", SALT, FAST)
    assert right != wrong


def test_different_salt_yields_different_key():
    a = derive_key("same-pass", b"saltsaltsaltsalt", FAST)
    b = derive_key("same-pass", b"DIFFERENTSALT_16", FAST)
    assert a != b


def test_params_are_tunable_and_change_the_key():
    base = derive_key("p", SALT, KdfParams(time_cost=1, memory_cost=8, parallelism=1))
    more_time = derive_key("p", SALT, KdfParams(time_cost=2, memory_cost=8, parallelism=1))
    more_memory = derive_key("p", SALT, KdfParams(time_cost=1, memory_cost=16, parallelism=1))
    assert base != more_time
    assert base != more_memory


def test_generate_salt_length_and_randomness():
    s1 = generate_salt()
    s2 = generate_salt()
    assert len(s1) == SALT_BYTES == 16
    assert s1 != s2  # collision is astronomically unlikely


def test_short_salt_raises():
    with pytest.raises(ValueError):
        derive_key("pw", b"short", FAST)  # 5 bytes < MIN_SALT_BYTES


def test_default_params_meet_security_floor():
    # Guard against accidentally weakening the shipped KDF cost below the OWASP
    # Argon2id floor — that would be a real security regression, not editorial.
    assert DEFAULT_PARAMS.memory_cost >= 19 * 1024  # >= 19 MiB
    assert DEFAULT_PARAMS.time_cost >= 2
    assert DEFAULT_PARAMS.parallelism >= 1


def test_default_params_derive_a_valid_key():
    # Exercise the real (heavier) defaults once to ensure the combo is valid.
    key = derive_key("desktop-default", SALT)
    assert len(key) == KEY_BYTES
