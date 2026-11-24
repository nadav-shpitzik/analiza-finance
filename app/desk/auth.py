"""The monday sessionToken, verified (Analiza-Monday backlog 0301 D1; the desk move plan §2).

Ported on 24.11 from analiza-metrics `app/monday_auth.py` at `da671b2`, where it has verified the board view's tokens since
2026-09. Hand-rolled HS256 on the standard library: the allow-list is structural, since the HMAC is computed here, so
`alg: none` and an asymmetric downgrade cannot verify by construction.

The new view "כספים" is a second Board View feature of the same monday app (the plan §2, option A), so monday signs its
tokens with the same Client Secret: `MONDAY_CLIENT_SECRET` here holds the value analiza-metrics holds.

The rules:
1. the header's `alg` is exactly HS256;
2. the signature matches the Client Secret, compared in constant time;
3. `exp` is required, a finite number, and enforced with 60 s of leeway: a token without one, or with NaN or Infinity
   (which `json.loads` accepts; D1's review), would be a credential for ever;
4. `dat.account_id` is Analiza's account, pinned here. No setting unpins it (in analiza-metrics an explicit "0" did);
5. who it is comes from the verified payload only: the user, the account (each an integer, or a string of digits; never
   a float rounded into one, D1's review), and whether monday says a viewer or a guest.
   `dat.is_view_only` and `dat.is_guest` are read as booleans when they are booleans, and as "not said" (None) otherwise:
   their names are not verified live yet (analiza-metrics `docs/monday-app-setup.md` §3ב item 4), and the skeleton's page
   shows which claims a real token carried, so D1 settles it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import time
from dataclasses import dataclass, field
from typing import Optional

EXP_LEEWAY_SECONDS = 60
# Analiza's monday account (verified live 2026-09-01 in analiza-metrics, where it is the default pin).
ANALIZA_ACCOUNT_ID = 33163495
# A sessionToken is a few hundred characters. Anything far longer is not one, and is not decoded.
TOKEN_MAX_CHARS = 4096


class AuthError(Exception):
    """`error_key` is the API's word: 'unauthorized' or 'token_expired'."""

    def __init__(self, error_key: str = "unauthorized"):
        self.error_key = error_key
        super().__init__(error_key)


@dataclass(frozen=True)
class SessionIdentity:
    user_id: int
    account_id: int
    view_only: Optional[bool] = None  # None: the token did not say
    guest: Optional[bool] = None
    claim_names: tuple = ()  # the names in `dat`, sorted: what the token carried, never a value
    claims: dict = field(default_factory=dict, repr=False)


def _b64url_decode(part: str) -> bytes:
    padded = part + "=" * (-len(part) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _flag(value) -> Optional[bool]:
    return value if isinstance(value, bool) else None


def _whole(value) -> Optional[int]:
    """An id as monday sends it: an integer, or a string of ASCII digits. Anything else (a float, a bool) is None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 20:
        return int(value)
    return None


def verify_session_token(token: str, client_secret: str, account_id: int = ANALIZA_ACCOUNT_ID,
                         now: Optional[float] = None) -> SessionIdentity:
    if not client_secret or not account_id:
        raise AuthError("unauthorized")
    token = token or ""
    if len(token) > TOKEN_MAX_CHARS:
        raise AuthError("unauthorized")
    parts = token.split(".")
    if len(parts) != 3 or not all(parts):
        raise AuthError("unauthorized")
    try:
        header = json.loads(_b64url_decode(parts[0]))
        payload = json.loads(_b64url_decode(parts[1]))
        signature = _b64url_decode(parts[2])
    except (ValueError, UnicodeError):
        raise AuthError("unauthorized") from None

    if not isinstance(header, dict) or header.get("alg") != "HS256":
        raise AuthError("unauthorized")
    signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
    expected = hmac.new(client_secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        raise AuthError("unauthorized")

    if not isinstance(payload, dict):
        raise AuthError("unauthorized")
    exp = payload.get("exp")
    if isinstance(exp, bool) or not isinstance(exp, (int, float)) or not math.isfinite(exp):
        raise AuthError("unauthorized")
    current = time.time() if now is None else now
    if current > float(exp) + EXP_LEEWAY_SECONDS:
        raise AuthError("token_expired")

    dat = payload.get("dat")
    if not isinstance(dat, dict):
        raise AuthError("unauthorized")
    user_id, token_account = _whole(dat.get("user_id")), _whole(dat.get("account_id"))
    if user_id is None or token_account is None or user_id <= 0:
        raise AuthError("unauthorized")
    if token_account != int(account_id):
        raise AuthError("unauthorized")
    return SessionIdentity(user_id=user_id, account_id=token_account, view_only=_flag(dat.get("is_view_only")),
                           guest=_flag(dat.get("is_guest")), claim_names=tuple(sorted(str(k) for k in dat)[:40]),
                           claims=payload)
