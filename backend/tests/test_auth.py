"""Offline JWT/JWKS and identity tests for the fail-closed Supabase authenticator.

No network calls and no live Supabase project are involved. Signing keys are generated
locally and the JWKS document is served by an in-memory ``PyJWKClient`` subclass, so PyJWT
performs real signature and claim verification against real cryptography keys. Only the
transport (JWKS fetch) is replaced; cryptographic verification is never bypassed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from types import SimpleNamespace
from urllib.error import URLError

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi import HTTPException
from jwt import PyJWKClient
from starlette.requests import Request

from app.api import auth
from app.core.config import Settings
from app.models.enums import UserStatus

SUPABASE_URL = "https://project.supabase.co"
ISSUER = f"{SUPABASE_URL}/auth/v1"
JWKS_URL = f"{ISSUER}/.well-known/jwks.json"
AUDIENCE = "authenticated"
SUBJECT = "0f4b1c2e-8f3a-4c55-9d1e-2b6a7c8d9e01"
RSA_KID = "rsa-key-1"
EC_KID = "ec-key-1"


# ── key material ─────────────────────────────────────────────────────────────


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _ec_key():
    return ec.generate_private_key(ec.SECP256R1())


def _rsa_jwk(key, kid: str = RSA_KID) -> dict[str, str]:
    numbers = key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": _b64u(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
        "e": _b64u(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
    }


def _ec_jwk(key, kid: str = EC_KID) -> dict[str, str]:
    numbers = key.public_key().public_numbers()
    return {
        "kty": "EC",
        "kid": kid,
        "use": "sig",
        "alg": "ES256",
        "crv": "P-256",
        "x": _b64u(numbers.x.to_bytes(32, "big")),
        "y": _b64u(numbers.y.to_bytes(32, "big")),
    }


def _claims(**overrides) -> dict[str, object]:
    now = int(time.time())
    values: dict[str, object | None] = {
        "sub": SUBJECT,
        "aud": AUDIENCE,
        "iss": ISSUER,
        "iat": now,
        "exp": now + 300,
        "role": "authenticated",
    }
    values.update(overrides)
    return {key: value for key, value in values.items() if value is not None}


def _token(key, algorithm: str = "RS256", kid: str | None = RSA_KID, **overrides) -> str:
    headers = {"kid": kid} if kid else {}
    return jwt.encode(_claims(**overrides), key, algorithm=algorithm, headers=headers)


def _forge_hs256(secret: bytes, **overrides) -> str:
    """Hand-built HS256 token; PyJWT's encoder refuses PEM material as an HMAC secret."""
    header = _b64u(json.dumps({"alg": "HS256", "typ": "JWT", "kid": RSA_KID}).encode())
    payload = _b64u(json.dumps(_claims(**overrides)).encode())
    signing_input = f"{header}.{payload}".encode()
    signature = _b64u(hmac.new(secret, signing_input, hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"


# ── offline transport ────────────────────────────────────────────────────────


class OfflineJWKSClient(PyJWKClient):
    """Real PyJWKClient (kid lookup, caching, JWK parsing) with an in-memory document."""

    def __init__(self, jwks: object, error: Exception | None = None) -> None:
        super().__init__(JWKS_URL, cache_keys=True, lifespan=600)
        self._jwks = jwks
        self._error = error
        self.fetches = 0

    def fetch_data(self):
        self.fetches += 1
        if self._error is not None:
            raise self._error
        return self._jwks


@pytest.fixture(autouse=True)
def _clear_jwks_cache():
    # Clearing at setup is enough: each test starts from an empty client cache, and cached
    # clients are re-created per URL anyway (see the cache tests below).
    auth._jwks_client.cache_clear()
    yield


@pytest.fixture
def rsa_key():
    return _rsa_key()


@pytest.fixture
def ec_key():
    return _ec_key()


def use_jwks(monkeypatch, jwks: object, error: Exception | None = None) -> OfflineJWKSClient:
    client = OfflineJWKSClient(jwks, error)
    monkeypatch.setattr(auth, "_jwks_client", lambda _url: client)
    return client


def jwks_document(rsa_key, ec_key=None, rsa_kid: str = RSA_KID) -> dict[str, list[dict[str, str]]]:
    keys = [_rsa_jwk(rsa_key, rsa_kid)]
    if ec_key is not None:
        keys.append(_ec_jwk(ec_key))
    return {"keys": keys}


def _settings() -> Settings:
    # _env_file=None keeps the developer's .env (e.g. a real SUPABASE_URL) out of the tests.
    return Settings(_env_file=None, supabase_url=SUPABASE_URL, supabase_jwt_audience=AUDIENCE)


def request(header: str | None = None) -> Request:
    headers = [] if header is None else [(b"authorization", header.encode())]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


class FakeSession:
    def __init__(self, user=None) -> None:
        self.user = user

    def scalar(self, _statement):
        return self.user


def active_user(**overrides) -> SimpleNamespace:
    values = {
        "id": uuid.uuid4(),
        "auth_subject": SUBJECT,
        "status": UserStatus.ACTIVE.value,
        "email": "user@example.com",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def authenticate(header: str | None, session: FakeSession, settings: Settings | None = None):
    return auth.get_authenticated_user(request(header), session, settings or _settings())


def expect_401(header: str | None, session: FakeSession, settings: Settings | None = None) -> HTTPException:
    with pytest.raises(HTTPException) as error:
        authenticate(header, session, settings)
    assert error.value.status_code == 401
    assert error.value.headers == {"WWW-Authenticate": "Bearer"}
    return error.value


# ── header / configuration ───────────────────────────────────────────────────


@pytest.mark.parametrize("header", [None, "Basic abc", "Bearer ", "Bearer    ", "bearer token"])
def test_missing_or_malformed_bearer_is_401(header):
    expect_401(header, FakeSession(active_user()))


def test_missing_configuration_fails_closed():
    with pytest.raises(HTTPException) as error:
        authenticate("Bearer token", FakeSession(active_user()), Settings(_env_file=None))
    assert error.value.status_code == 503
    assert "token" not in error.value.detail.lower()


def test_issuer_is_derived_from_the_configured_url(monkeypatch, rsa_key):
    use_jwks(monkeypatch, jwks_document(rsa_key))
    settings = Settings(_env_file=None, supabase_url="http://localhost:54321/", supabase_jwt_audience=AUDIENCE)
    token = _token(rsa_key, iss="http://localhost:54321/auth/v1")
    assert authenticate(f"Bearer {token}", FakeSession(active_user()), settings).subject == SUBJECT


# ── successful verification ─────────────────────────────────────────────────


def test_valid_rs256_token_maps_active_identity(monkeypatch, rsa_key):
    use_jwks(monkeypatch, jwks_document(rsa_key))
    user = active_user()
    principal = authenticate(f"Bearer {_token(rsa_key)}", FakeSession(user))
    assert principal.user is user
    assert principal.subject == SUBJECT


def test_valid_es256_token_is_accepted(monkeypatch, ec_key):
    use_jwks(monkeypatch, jwks_document(_rsa_key(), ec_key))
    user = active_user()
    principal = authenticate(f"Bearer {_token(ec_key, 'ES256', EC_KID)}", FakeSession(user))
    assert principal.user is user


def test_trailing_slash_in_configured_url_does_not_break_issuer(monkeypatch, rsa_key):
    use_jwks(monkeypatch, jwks_document(rsa_key))
    settings = Settings(_env_file=None, supabase_url=f"{SUPABASE_URL}/", supabase_jwt_audience=AUDIENCE)
    principal = authenticate(f"Bearer {_token(rsa_key)}", FakeSession(active_user()), settings)
    assert principal.subject == SUBJECT


def test_extra_whitespace_around_token_is_tolerated(monkeypatch, rsa_key):
    use_jwks(monkeypatch, jwks_document(rsa_key))
    assert authenticate(f"Bearer  {_token(rsa_key)}  ", FakeSession(active_user())).subject == SUBJECT


# ── signature, algorithm and claim failures ─────────────────────────────────


def test_signature_from_foreign_key_is_rejected(monkeypatch, rsa_key):
    use_jwks(monkeypatch, jwks_document(rsa_key))  # publishes rsa_key
    other = _rsa_key()  # attacker key reusing the same kid
    expect_401(f"Bearer {_token(other)}", FakeSession(active_user()))


def test_expired_token_is_rejected(monkeypatch, rsa_key):
    use_jwks(monkeypatch, jwks_document(rsa_key))
    expired = _token(rsa_key, exp=int(time.time()) - 60)
    expect_401(f"Bearer {expired}", FakeSession(active_user()))


def test_token_without_exp_is_rejected(monkeypatch, rsa_key):
    use_jwks(monkeypatch, jwks_document(rsa_key))
    expect_401(f"Bearer {_token(rsa_key, exp=None)}", FakeSession(active_user()))


def test_incorrect_issuer_is_rejected(monkeypatch, rsa_key):
    use_jwks(monkeypatch, jwks_document(rsa_key))
    bad = _token(rsa_key, iss="https://evil.example.com/auth/v1")
    expect_401(f"Bearer {bad}", FakeSession(active_user()))


def test_incorrect_audience_is_rejected(monkeypatch, rsa_key):
    use_jwks(monkeypatch, jwks_document(rsa_key))
    bad = _token(rsa_key, aud="service_role")
    expect_401(f"Bearer {bad}", FakeSession(active_user()))


def test_missing_audience_is_rejected(monkeypatch, rsa_key):
    use_jwks(monkeypatch, jwks_document(rsa_key))
    expect_401(f"Bearer {_token(rsa_key, aud=None)}", FakeSession(active_user()))


@pytest.mark.parametrize("subject", [None, "", 123, ["nested"]])
def test_missing_or_non_string_subject_is_rejected(monkeypatch, rsa_key, subject):
    use_jwks(monkeypatch, jwks_document(rsa_key))
    expect_401(f"Bearer {_token(rsa_key, sub=subject)}", FakeSession(active_user()))


def test_hs256_token_is_rejected_by_algorithm_allowlist(monkeypatch, rsa_key):
    use_jwks(monkeypatch, jwks_document(rsa_key))
    forged = jwt.encode(_claims(), "shared-secret-shared-secret-shared-secret!", algorithm="HS256", headers={"kid": RSA_KID})
    expect_401(f"Bearer {forged}", FakeSession(active_user()))


def test_alg_none_token_is_rejected(monkeypatch, rsa_key):
    use_jwks(monkeypatch, jwks_document(rsa_key))
    forged = jwt.encode(_claims(), None, algorithm="none", headers={"kid": RSA_KID})
    expect_401(f"Bearer {forged}", FakeSession(active_user()))


def test_hmac_confusion_with_public_key_material_is_rejected(monkeypatch, rsa_key):
    """Classic key-type confusion: HS256 signed with the RSA public key as the HMAC secret."""
    use_jwks(monkeypatch, jwks_document(rsa_key))
    public_pem = rsa_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    forged = _forge_hs256(public_pem)
    expect_401(f"Bearer {forged}", FakeSession(active_user()))


def test_es256_token_against_rsa_key_is_rejected(monkeypatch, rsa_key, ec_key):
    """Algorithm/key-type confusion: the published key for this kid is RSA, token claims ES256."""
    use_jwks(monkeypatch, jwks_document(rsa_key))
    forged = _token(ec_key, "ES256", RSA_KID)
    expect_401(f"Bearer {forged}", FakeSession(active_user()))


def test_unknown_key_id_is_rejected(monkeypatch, rsa_key):
    use_jwks(monkeypatch, jwks_document(rsa_key))
    expect_401(f"Bearer {_token(rsa_key, kid='ghost-key')}", FakeSession(active_user()))


def test_token_without_key_id_is_rejected(monkeypatch, rsa_key):
    use_jwks(monkeypatch, jwks_document(rsa_key))
    expect_401(f"Bearer {_token(rsa_key, kid=None)}", FakeSession(active_user()))


# ── JWKS robustness ─────────────────────────────────────────────────────────


def test_malformed_jwks_is_rejected(monkeypatch, rsa_key):
    use_jwks(monkeypatch, {"keys": [{"kty": "RSA", "kid": RSA_KID}]})
    expect_401(f"Bearer {_token(rsa_key)}", FakeSession(active_user()))


def test_jwks_without_keys_member_is_rejected(monkeypatch, rsa_key):
    use_jwks(monkeypatch, {"nope": []})
    expect_401(f"Bearer {_token(rsa_key)}", FakeSession(active_user()))


def test_jwks_network_failure_is_rejected(monkeypatch, rsa_key):
    use_jwks(monkeypatch, {}, error=URLError("connection refused"))
    expect_401(f"Bearer {_token(rsa_key)}", FakeSession(active_user()))


def test_jwks_is_fetched_once_then_cached(monkeypatch, rsa_key):
    client = use_jwks(monkeypatch, jwks_document(rsa_key))
    for _ in range(3):
        assert authenticate(f"Bearer {_token(rsa_key)}", FakeSession(active_user())).subject == SUBJECT
    assert client.fetches == 1


def test_key_rotation_is_picked_up_by_a_new_client(monkeypatch, rsa_key, ec_key):
    old = OfflineJWKSClient(jwks_document(rsa_key))
    monkeypatch.setattr(auth, "_jwks_client", lambda _url: old)
    assert authenticate(f"Bearer {_token(rsa_key)}", FakeSession(active_user())).subject == SUBJECT
    expect_401(f"Bearer {_token(ec_key, 'ES256', EC_KID)}", FakeSession(active_user()))

    # Rotation: the provider now publishes a different key set (new kid).
    rotated = OfflineJWKSClient({"keys": [_ec_jwk(ec_key)]})
    monkeypatch.setattr(auth, "_jwks_client", lambda _url: rotated)
    assert authenticate(f"Bearer {_token(ec_key, 'ES256', EC_KID)}", FakeSession(active_user())).subject == SUBJECT


def test_jwks_client_cache_is_per_url_and_clearable():
    first = auth._jwks_client(JWKS_URL)
    assert auth._jwks_client(JWKS_URL) is first
    assert auth._jwks_client("https://other.supabase.co/auth/v1/.well-known/jwks.json") is not first
    auth._jwks_client.cache_clear()
    assert auth._jwks_client(JWKS_URL) is not first


# ── identity mapping ────────────────────────────────────────────────────────


def test_unknown_identity_is_rejected(monkeypatch, rsa_key):
    use_jwks(monkeypatch, jwks_document(rsa_key))
    expect_401(f"Bearer {_token(rsa_key)}", FakeSession(None))


@pytest.mark.parametrize("status", [UserStatus.SUSPENDED.value, UserStatus.PENDING.value])
def test_non_active_identity_is_rejected(monkeypatch, rsa_key, status):
    use_jwks(monkeypatch, jwks_document(rsa_key))
    expect_401(f"Bearer {_token(rsa_key)}", FakeSession(active_user(status=status)))


def test_auth_subject_mismatch_is_rejected(monkeypatch, rsa_key):
    """The local row is found by verified ``sub``; a token for another subject cannot reuse it."""
    use_jwks(monkeypatch, jwks_document(rsa_key))
    expect_401(
        f"Bearer {_token(rsa_key, sub='another-supabase-user')}",
        FakeSession(None),  # rows are looked up by that subject and none exists
    )


# ── information disclosure ──────────────────────────────────────────────────


def test_errors_do_not_leak_token_or_subject(monkeypatch, rsa_key):
    use_jwks(monkeypatch, jwks_document(rsa_key))
    raw = _token(_rsa_key())
    error = expect_401(f"Bearer {raw}", FakeSession(active_user()))
    assert raw not in error.detail and SUBJECT not in error.detail
    assert "invalid or expired access token" in error.detail.lower()
