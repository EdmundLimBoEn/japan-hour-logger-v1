from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path

PREFIX_TOKEN_RE = re.compile(
    r"^(=)?([A-Z0-9/]+)"
    r"(?:\((\d+)\))?"
    r"(?:\[(\d+)\])?"
    r"(?:\{([A-Z]{2})\})?"
    r"(?:<([^>]+)>)?"
    r"(~)?$",
    re.IGNORECASE,
)

PORTABLE_SUFFIXES = {
    "P",
    "M",
    "MM",
    "AM",
    "QRP",
    "A",
    "LH",
    "BM",
    "AG",
    "R",
    "B",
    "JOTA",
    "YOTA",
    "ND",
}


@dataclass(frozen=True)
class DxccEntity:
    name: str
    cqz: int
    ituz: int
    continent: str
    lat: float
    lon: float
    tz: float
    primary_prefix: str


@dataclass(frozen=True)
class PrefixRule:
    prefix: str
    entity: DxccEntity
    cqz: int
    ituz: int
    continent: str
    exact: bool = False


@dataclass(frozen=True)
class DxccMatch:
    callsign: str
    entity_name: str
    primary_prefix: str
    continent: str
    cqz: int
    ituz: int
    matched_prefix: str
    lat: float | None = None
    lon: float | None = None


class DxccLookup:
    def __init__(self, rules: list[PrefixRule]):
        self._exact: dict[str, PrefixRule] = {}
        prefixes: list[PrefixRule] = []
        for rule in rules:
            if rule.exact:
                self._exact[rule.prefix] = rule
            else:
                prefixes.append(rule)
        prefixes.sort(key=lambda r: len(r.prefix), reverse=True)
        self._prefixes = prefixes
        self._entities = {r.entity.primary_prefix: r.entity for r in rules}

    @classmethod
    def from_cty_dat(cls, path: Path | None = None) -> "DxccLookup":
        text = _read_cty(path)
        return cls(_parse_cty(text))

    def lookup(self, callsign: str | None) -> DxccMatch | None:
        if not callsign:
            return None
        call = callsign.strip().upper()
        if not call or call in {"...", "<...>"}:
            return None
        call = call.strip("<>")
        candidates = _dxcc_candidates(call)
        for candidate in candidates:
            exact = self._exact.get(candidate)
            if exact:
                return _match_from_rule(call, exact)
        for candidate in candidates:
            for rule in self._prefixes:
                if candidate.startswith(rule.prefix):
                    return _match_from_rule(call, rule)
        return None


def _match_from_rule(call: str, rule: PrefixRule) -> DxccMatch:
    return DxccMatch(
        callsign=call,
        entity_name=rule.entity.name,
        primary_prefix=rule.entity.primary_prefix,
        continent=rule.continent,
        cqz=rule.cqz,
        ituz=rule.ituz,
        matched_prefix=rule.prefix,
        lat=rule.entity.lat,
        lon=rule.entity.lon,
    )


def _dxcc_candidates(call: str) -> list[str]:
    """Return lookup keys, most specific first.

    Portable rules:
    - CALL/P /M /digit stay in the home entity.
    - PREFIX/CALL (KH2/W1ABC) uses PREFIX if it looks like a DXCC prefix.
    - CALL/PREFIX (W1ABC/KH2) uses PREFIX.
    - /MM and /AM have no DXCC; we still try the home call so the row stores.
    """
    parts = [p for p in call.split("/") if p]
    if not parts:
        return []
    while len(parts) > 1 and _is_non_entity_suffix(parts[-1]):
        parts = parts[:-1]
    if len(parts) == 1:
        return [parts[0]]
    a, b = parts[0], parts[1]
    a_digit = any(ch.isdigit() for ch in a)
    b_digit = any(ch.isdigit() for ch in b)
    if not a_digit and b_digit:
        return [a, b]
    if a_digit and not b_digit:
        return [b, a]
    # Both parts look like calls (W1ABC/KH6, KH2/W1ABC, W4/JA1XYZ).
    # Prefer the shorter token first; it is usually the DXCC prefix.
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return [shorter, longer, a]


def _is_non_entity_suffix(token: str) -> bool:
    return token in PORTABLE_SUFFIXES or (token.isdigit() and len(token) <= 2)


def _read_cty(path: Path | None) -> str:
    if path is not None:
        return path.read_text(encoding="latin-1")
    resource = resources.files("radio_logger").joinpath("resources/cty.dat")
    return resource.read_text(encoding="latin-1")


def _parse_cty(text: str) -> list[PrefixRule]:
    rules: list[PrefixRule] = []
    blob = " ".join(line.strip() for line in text.replace("\r", "").splitlines() if line.strip())
    records = [r.strip() for r in blob.split(";") if r.strip()]
    for record in records:
        header, _, prefix_blob = record.partition(":")
        fields = [f.strip() for f in (header + ":" + prefix_blob).split(":")]
        if len(fields) < 8:
            continue
        name = fields[0]
        try:
            cqz = int(float(fields[1]))
            ituz = int(float(fields[2]))
            continent = fields[3]
            lat = float(fields[4])
            lon_west_positive = float(fields[5])
            tz = float(fields[6])
        except ValueError:
            continue
        primary = fields[7]
        prefixes_field = ":".join(fields[8:]) if len(fields) > 8 else ""
        entity = DxccEntity(
            name=name,
            cqz=cqz,
            ituz=ituz,
            continent=continent,
            lat=lat,
            lon=-lon_west_positive,
            tz=tz,
            primary_prefix=primary,
        )
        tokens = [t.strip() for t in prefixes_field.replace("\n", " ").split(",") if t.strip()]
        if primary and primary not in {t.lstrip("=").split("(")[0].split("[")[0] for t in tokens}:
            tokens.append(primary)
        for token in tokens:
            parsed = _parse_prefix_token(token, entity)
            if parsed:
                rules.append(parsed)
    return rules


def _parse_prefix_token(token: str, entity: DxccEntity) -> PrefixRule | None:
    token = token.strip()
    if not token:
        return None
    match = PREFIX_TOKEN_RE.match(token)
    if not match:
        prefix = re.split(r"[(\[{<~]", token)[0].lstrip("=")
        if not prefix:
            return None
        return PrefixRule(
            prefix=prefix.upper(),
            entity=entity,
            cqz=entity.cqz,
            ituz=entity.ituz,
            continent=entity.continent,
            exact=token.startswith("="),
        )
    exact, prefix, cqz_s, ituz_s, cont, _latlon, _ignore = match.groups()
    if not prefix:
        return None
    return PrefixRule(
        prefix=prefix.upper(),
        entity=entity,
        cqz=int(cqz_s) if cqz_s else entity.cqz,
        ituz=int(ituz_s) if ituz_s else entity.ituz,
        continent=(cont or entity.continent).upper(),
        exact=bool(exact),
    )


@lru_cache(maxsize=1)
def default_lookup() -> DxccLookup:
    return DxccLookup.from_cty_dat()
