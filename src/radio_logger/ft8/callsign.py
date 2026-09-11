from __future__ import annotations

import re

# Amateur callsigns: optional prefix, then 1-3 letters + digit + suffix, optional /P etc.
CALL_RE = re.compile(
    r"""
    ^
    (?:(?P<pre>[A-Z0-9]{1,4})/)?
    (?P<base>
        [A-Z]{1,2}\d[A-Z0-9]{1,4}
        |\d[A-Z]{1,2}\d[A-Z0-9]{1,3}
        |\d[A-Z]\d[A-Z0-9]{1,3}
        |[A-Z]\d[A-Z]{1,4}
        |[A-Z]{1,3}\d[A-Z]{1,4}
    )
    (?:/(?P<suf>[A-Z0-9]{1,4}))?
    $
    """,
    re.VERBOSE,
)

HASHED_CALL_RE = re.compile(r"^<([A-Z0-9/]+)>$")
BARE_HASH = "<...>"

# Tokens that are never transmitting callsigns in FT8.
NOT_CALL = {
    "CQ",
    "QRZ",
    "DE",
    "DX",
    "POTA",
    "SOTA",
    "TEST",
    "FD",
    "RU",
    "NA",
    "EU",
    "AS",
    "AF",
    "OC",
    "SA",
    "JA",
    "RRR",
    "RR73",
    "73",
    "RRR73",
    "FT8",
    "FT4",
    "JT65",
    "JT9",
    "WSPR",
}


def looks_like_callsign(token: str | None) -> bool:
    if not token:
        return False
    text = token.strip().upper()
    if text in NOT_CALL or text == BARE_HASH:
        return False
    hashed = HASHED_CALL_RE.match(text)
    if hashed:
        return looks_like_callsign(hashed.group(1))
    if is_grid_like(text):
        return False
    if CALL_RE.match(text):
        return True
    # Last-resort compound calls must still have a letter after a digit.
    if re.match(r"^[A-Z0-9]{3,12}(?:/[A-Z0-9]{1,4})?$", text) and re.search(r"\d[A-Z]", text):
        return True
    return False


def is_grid_like(token: str) -> bool:
    t = token.strip().upper()
    if t in {"RR73", "RRR73"}:
        return False
    return bool(re.match(r"^[A-R]{2}[0-9]{2}(?:[A-X]{2})?$", t))


def normalize_call(token: str) -> str:
    text = token.strip().upper()
    hashed = HASHED_CALL_RE.match(text)
    if hashed:
        return hashed.group(1)
    return text


def extract_base_call(callsign: str) -> str:
    """Home call without portable prefix/suffix, for station index keys.

    Compound portable still stores the full tx_callsign on the observation.
    """
    call = normalize_call(callsign)
    parts = [p for p in call.split("/") if p]
    if not parts:
        return call
    scored = sorted(parts, key=lambda p: (any(c.isdigit() for c in p), len(p)), reverse=True)
    return scored[0]


def parse_call_token(token: str) -> str | None:
    if not looks_like_callsign(token):
        return None
    return normalize_call(token)
