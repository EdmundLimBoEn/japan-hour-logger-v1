from __future__ import annotations

import re

from radio_logger.ft8.callsign import is_grid_like, looks_like_callsign, parse_call_token
from radio_logger.models import ParsedFt8

REPORT_RE = re.compile(r"^R?[+-]\d{1,2}$")
CONTEST_EXCH_RE = re.compile(r"^(?:[0-9]{2,4}|[A-Z]{2}[0-9]{2,4}|[0-9]{3})$")
DIRECTIVE_RE = re.compile(r"^[A-Z]{1,4}$")


def parse_ft8_message(raw: str) -> ParsedFt8:
    """Parse common FT8 text. Unknown/malformed messages stay raw and never raise."""
    try:
        return _parse(raw)
    except Exception:
        return ParsedFt8(raw_message=raw or "", message_type="unknown")


def _parse(raw: str) -> ParsedFt8:
    message = (raw or "").strip()
    if not message:
        return ParsedFt8(raw_message="", message_type="unknown")
    tokens = [t.upper() if t.upper() != "<...>" else "<...>" for t in message.split() if t]
    if not tokens:
        return ParsedFt8(raw_message=message, message_type="unknown")

    if tokens[0] in {"CQ", "QRZ"}:
        return _parse_cq(message, tokens)

    calls = [i for i, tok in enumerate(tokens) if looks_like_callsign(tok) or tok == "<...>"]
    if len(tokens) >= 2 and (looks_like_callsign(tokens[0]) or tokens[0] == "<...>"):
        rx = _call_or_none(tokens[0])
        if looks_like_callsign(tokens[1]) or tokens[1] == "<...>":
            tx = _call_or_none(tokens[1])
            rest = tokens[2:]
            return _parse_exchange(message, rx, tx, rest)

    if len(calls) == 1:
        tx = _call_or_none(tokens[calls[0]])
        return ParsedFt8(
            raw_message=message,
            message_type="free_text",
            tx_callsign=tx,
            extra={"tokens": tokens},
        )
    return ParsedFt8(raw_message=message, message_type="unknown", extra={"tokens": tokens})


def _parse_cq(message: str, tokens: list[str]) -> ParsedFt8:
    rest = tokens[1:]
    directive = None
    if rest and not looks_like_callsign(rest[0]) and rest[0] != "<...>":
        token = rest[0]
        if DIRECTIVE_RE.match(token) or token.isdigit() or (len(token) <= 4 and token.isalnum()):
            directive = token
            rest = rest[1:]
    tx = _call_or_none(rest[0]) if rest else None
    grid = None
    extra: dict = {}
    if len(rest) >= 2 and is_grid_like(rest[1]):
        grid = rest[1]
    elif len(rest) >= 2:
        extra["tail"] = rest[1:]
    kind = "cq"
    if directive in {"DX"}:
        kind = "cq_dx"
    elif directive in {"POTA", "SOTA"}:
        kind = "cq_pota"
    elif directive:
        kind = "cq_directed"
        extra["directive"] = directive
    return ParsedFt8(
        raw_message=message,
        message_type=kind,  # type: ignore[arg-type]
        is_cq=True,
        tx_callsign=tx,
        tx_grid=grid,
        extra=extra,
    )


def _parse_exchange(message: str, rx: str | None, tx: str | None, rest: list[str]) -> ParsedFt8:
    if not rest:
        return ParsedFt8(
            raw_message=message,
            message_type="grid_exchange",
            tx_callsign=tx,
            rx_callsign=rx,
        )
    payload = rest[0]
    grid = rest[1] if len(rest) > 1 and is_grid_like(rest[1]) else None
    if is_grid_like(payload):
        return ParsedFt8(
            raw_message=message,
            message_type="grid_exchange",
            tx_callsign=tx,
            rx_callsign=rx,
            tx_grid=payload,
        )
    if payload in {"RR73", "RRR73"}:
        return ParsedFt8(
            raw_message=message,
            message_type="rr73",
            tx_callsign=tx,
            rx_callsign=rx,
        )
    if payload == "RRR":
        return ParsedFt8(
            raw_message=message,
            message_type="rrr",
            tx_callsign=tx,
            rx_callsign=rx,
        )
    if payload == "73":
        return ParsedFt8(
            raw_message=message,
            message_type="73",
            tx_callsign=tx,
            rx_callsign=rx,
        )
    if REPORT_RE.match(payload):
        kind = "r_report" if payload.startswith("R") else "report"
        return ParsedFt8(
            raw_message=message,
            message_type=kind,  # type: ignore[arg-type]
            tx_callsign=tx,
            rx_callsign=rx,
            extra={"report": payload},
        )
    if CONTEST_EXCH_RE.match(payload) or (len(rest) >= 1 and payload.isdigit()):
        return ParsedFt8(
            raw_message=message,
            message_type="contest",
            tx_callsign=tx,
            rx_callsign=rx,
            tx_grid=grid,
            extra={"exchange": " ".join(rest)},
        )
    if looks_like_callsign(payload):
        return ParsedFt8(
            raw_message=message,
            message_type="unknown",
            tx_callsign=tx,
            rx_callsign=rx,
            extra={"tail": rest},
        )
    return ParsedFt8(
        raw_message=message,
        message_type="unknown",
        tx_callsign=tx,
        rx_callsign=rx,
        extra={"tail": rest},
    )


def _call_or_none(token: str) -> str | None:
    if token == "<...>":
        return None
    parsed = parse_call_token(token)
    return parsed
