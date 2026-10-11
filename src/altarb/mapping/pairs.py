"""The Kalshi <-> Polymarket market-pair table (data/pairs.csv).

The CSV is the source of truth for which markets are the same event. A pair is
only usable for trading or edge calculation when `verified` is true, meaning a
human compared resolution criteria, source, expiry, and edge cases on both
venues (see docs/pairs.md). Automated matching may add unverified rows; it
must never set `verified`.

Columns:
    kalshi_ticker     Kalshi market ticker, e.g. KXNHLGAME-26OCT16BOSANA-BOS
    polymarket_slug   Polymarket US market slug (what the book endpoint takes)
    inverted          true if Kalshi YES == Polymarket NO (opposite phrasing)
    description       short human-readable label
    resolution_notes  differences or edge cases found while comparing rules
    expiry            ISO date or datetime the pair resolves by
    verified          true/false
    verified_by       who verified it (required when verified is true)
    verified_on       ISO date it was verified (required when verified is true)
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

COLUMNS = [
    "kalshi_ticker",
    "polymarket_slug",
    "inverted",
    "description",
    "resolution_notes",
    "expiry",
    "verified",
    "verified_by",
    "verified_on",
]

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "data" / "pairs.csv"


class PairsFileError(ValueError):
    """The pairs file is malformed. The message names the offending line."""


@dataclass(frozen=True)
class MarketPair:
    kalshi_ticker: str
    polymarket_slug: str
    inverted: bool
    description: str
    resolution_notes: str
    expiry: datetime | date | None
    verified: bool
    verified_by: str
    verified_on: date | None


def load_pairs(path: Path | str = DEFAULT_PATH) -> list[MarketPair]:
    """Load and validate every row. Raises PairsFileError on any bad row."""
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != COLUMNS:
            raise PairsFileError(f"{path}: header must be {','.join(COLUMNS)}, got {reader.fieldnames}")

        pairs: list[MarketPair] = []
        seen: set[tuple[str, str]] = set()
        for row in reader:
            line = reader.line_num
            pair = _parse_row(row, f"{path}:{line}")
            key = (pair.kalshi_ticker, pair.polymarket_slug)
            if key in seen:
                raise PairsFileError(f"{path}:{line}: duplicate pair {key}")
            seen.add(key)
            pairs.append(pair)
    return pairs


def write_pairs(path: Path | str, pairs: list[MarketPair]) -> None:
    """Write pairs in the pairs.csv schema (used for data/candidates.csv)."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        for p in pairs:
            writer.writerow([
                p.kalshi_ticker,
                p.polymarket_slug,
                str(p.inverted).lower(),
                p.description,
                p.resolution_notes,
                p.expiry.isoformat() if p.expiry else "",
                str(p.verified).lower(),
                p.verified_by,
                p.verified_on.isoformat() if p.verified_on else "",
            ])


def verified_pairs(pairs: list[MarketPair]) -> list[MarketPair]:
    """The only pairs the scanner may use (CLAUDE.md hard rule 3)."""
    return [p for p in pairs if p.verified]


def _parse_row(row: dict[str, str], where: str) -> MarketPair:
    values = {k: (v or "").strip() for k, v in row.items()}
    for required in ("kalshi_ticker", "polymarket_slug"):
        if not values[required]:
            raise PairsFileError(f"{where}: {required} is empty")

    verified = _parse_bool(values["verified"], "verified", where)
    verified_on = _parse_date(values["verified_on"], "verified_on", where)
    if verified and not (values["verified_by"] and verified_on):
        raise PairsFileError(f"{where}: verified rows need verified_by and verified_on")

    return MarketPair(
        kalshi_ticker=values["kalshi_ticker"],
        polymarket_slug=values["polymarket_slug"],
        inverted=_parse_bool(values["inverted"], "inverted", where),
        description=values["description"],
        resolution_notes=values["resolution_notes"],
        expiry=_parse_expiry(values["expiry"], where),
        verified=verified,
        verified_by=values["verified_by"],
        verified_on=verified_on,
    )


def _parse_bool(value: str, name: str, where: str) -> bool:
    # Strict on purpose: a typo like "ture" must not silently become false or true.
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    raise PairsFileError(f"{where}: {name} must be true or false, got {value!r}")


def _parse_date(value: str, name: str, where: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise PairsFileError(f"{where}: {name} must be YYYY-MM-DD, got {value!r}") from None


def _parse_expiry(value: str, where: str) -> datetime | date | None:
    if not value:
        return None
    try:
        if "T" in value:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return date.fromisoformat(value)
    except ValueError:
        raise PairsFileError(f"{where}: expiry must be an ISO date or datetime, got {value!r}") from None
