"""Suggest Kalshi <-> Polymarket US pairs for game-winner markets.

Suggestions are written as unverified rows in the pairs.csv schema. A human
must review each one (docs/pairs.md) before copying it into data/pairs.csv;
nothing here ever sets `verified`.

Matching is structured, not fuzzy: both venues encode league, date and teams
in their identifiers, e.g.

    Kalshi      KXNHLGAME-26OCT10PHIBOS-BOS       (one market per outcome)
    Polymarket  aec-nhl-phi-bos-2026-10-10        (two-way: one instrument)
                atc-mls-tor-mim-2026-10-10-draw   (three-way: one per outcome)

Games are compared only within a mapped league (leagues.py) and date, and teams
are matched by name (abbreviations differ between venues: MTL vs mon).

Run:  python -m altarb.mapping.matcher   (writes data/candidates.csv)
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from altarb.mapping.leagues import LEAGUES
from altarb.mapping.pairs import MarketPair
from altarb.venues import kalshi, polymarket

TIE = "TIE"
WINNER_TYPE_SUFFIXES = ("full_game_winner", "full_time_winner", "match_winner", "fight_winner")
_PM_SLUG = re.compile(r"^(aec|atc)-([a-z0-9]+)-(.+)-(\d{4}-\d{2}-\d{2})(?:-(.+))?$")
_KALSHI_DATE = re.compile(r"^(\d{2}[A-Z]{3}\d{2})")


@dataclass
class _KalshiGame:
    series: str
    day: date
    outcomes: dict[str, kalshi.Market] = field(default_factory=dict)  # code -> market


@dataclass
class _PMTeam:
    tokens: frozenset[str]  # every word in the team's names
    codes: frozenset[str]  # abbreviations
    market: polymarket.Market
    long: bool  # True if this team is the instrument's YES side


@dataclass
class _PMGame:
    league: str
    day: date
    teams: list[_PMTeam] = field(default_factory=list)
    draw: polymarket.Market | None = None


def suggest_pairs(
    kalshi_markets: Iterable[kalshi.Market],
    pm_markets: Iterable[polymarket.Market],
    leagues: dict[str, frozenset[str]] = LEAGUES,
) -> list[MarketPair]:
    """Return unverified pair suggestions for game-winner markets."""
    kalshi_games = _kalshi_games(kalshi_markets, leagues)
    pm_by_key: dict[tuple[str, date], list[_PMGame]] = defaultdict(list)
    for game in _pm_games(pm_markets):
        pm_by_key[(game.league, game.day)].append(game)

    pairs: list[MarketPair] = []
    for kg in kalshi_games:
        for offset, note in ((0, ""), (-1, "AUTO: dates differ by 1 day"), (1, "AUTO: dates differ by 1 day")):
            day = kg.day + timedelta(days=offset)
            found = [
                p
                for league in leagues[kg.series]
                for pg in pm_by_key.get((league, day), [])
                if (p := _pair_game(kg, pg, note))
            ]
            if found:
                if len(found) > 1:  # e.g. both teams play twice in the window
                    found = [[_with_note(p, "AUTO: multiple Polymarket games matched") for p in ps] for ps in found]
                pairs.extend(p for ps in found for p in ps)
                break
    return pairs


def _kalshi_games(markets: Iterable[kalshi.Market], leagues: dict[str, frozenset[str]]) -> list[_KalshiGame]:
    games: dict[str, _KalshiGame] = {}
    for m in markets:
        series, _, rest = m.event_ticker.partition("-")
        match = _KALSHI_DATE.match(rest)
        if series not in leagues or not match:
            continue
        day = datetime.strptime(match.group(1), "%y%b%d").date()
        game = games.setdefault(m.event_ticker, _KalshiGame(series, day))
        game.outcomes[m.ticker.rsplit("-", 1)[1]] = m
    return list(games.values())


def _pm_games(markets: Iterable[polymarket.Market]) -> list[_PMGame]:
    games: dict[tuple[str, ...], _PMGame] = {}
    for m in markets:
        match = _PM_SLUG.match(m.slug)
        if not match or not m.sports_market_type.endswith(WINNER_TYPE_SUFFIXES):
            continue
        kind, league, teams, day, outcome = match.groups()
        game = games.setdefault((league, teams, day), _PMGame(league, date.fromisoformat(day)))
        if kind == "aec":  # two-way: one instrument, each side is a team
            for side in m.raw.get("marketSides") or []:
                game.teams.append(_pm_team(side.get("team") or {}, m, long=bool(side.get("long"))))
        elif not outcome or "-" in outcome:
            continue  # not a plain outcome, e.g. "fh-tor" (halftime) mislabeled as a winner market
        elif outcome == "draw":
            game.draw = m
        else:  # three-way: this market is "will <team> win"
            sides = m.raw.get("marketSides") or [{}]
            team = _pm_team(sides[0].get("team") or {}, m, long=True)
            team.codes |= {outcome}
            game.teams.append(team)
    return list(games.values())


def _pm_team(team: dict[str, Any], market: polymarket.Market, long: bool) -> _PMTeam:
    names = " ".join(str(team.get(k) or "") for k in ("name", "safeName", "alias"))
    codes = {str(team.get(k) or "").lower() for k in ("abbreviation", "displayAbbreviation")} - {""}
    return _PMTeam(_tokens(names), frozenset(codes), market, long)


def _pair_game(kg: _KalshiGame, pg: _PMGame, note: str) -> list[MarketPair]:
    """Pair every Kalshi outcome with its Polymarket market, or [] if the games differ."""
    team_codes = [c for c in kg.outcomes if c != TIE]
    if len(team_codes) != len(pg.teams):
        return []

    assigned: dict[str, _PMTeam] = {}
    for code in team_codes:
        hits = [t for t in pg.teams if _same_team(code, kg.outcomes[code].yes_sub_title, t)]
        if len(hits) != 1 or any(hits[0] is t for t in assigned.values()):
            return []  # no match, or ambiguous (e.g. two "New York" teams)
        assigned[code] = hits[0]

    pairs = [
        _pair(kg.outcomes[code], team.market, inverted=not team.long, note=note)
        for code, team in assigned.items()
    ]
    if TIE in kg.outcomes and pg.draw is not None:
        pairs.append(_pair(kg.outcomes[TIE], pg.draw, inverted=False, note=note))
    return pairs


def _same_team(code: str, name: str, team: _PMTeam) -> bool:
    name_tokens = _tokens(name)
    # "Montreal" matches "Montreal Canadiens"; abbreviations are the fallback.
    return (bool(name_tokens) and name_tokens <= team.tokens) or code.lower() in team.codes


def _pair(k: kalshi.Market, p: polymarket.Market, inverted: bool, note: str) -> MarketPair:
    pm_yes = p.yes_label if p.yes_label not in ("", "Yes") else p.question
    expiry = k.expected_expiration_time or p.end_date
    return MarketPair(
        kalshi_ticker=k.ticker,
        polymarket_slug=p.slug,
        inverted=inverted,
        description=f"Kalshi YES: {k.title} | Polymarket YES: {pm_yes}",
        resolution_notes=note,
        expiry=expiry.date() if expiry else None,
        verified=False,
        verified_by="",
        verified_on=None,
    )


def _with_note(pair: MarketPair, note: str) -> MarketPair:
    notes = "; ".join(n for n in (pair.resolution_notes, note) if n)
    return MarketPair(**{**pair.__dict__, "resolution_notes": notes})


def _tokens(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9]+", text.lower()))


def main() -> None:
    from altarb.mapping.pairs import DEFAULT_PATH, load_pairs, write_pairs

    out = DEFAULT_PATH.with_name("candidates.csv")
    print("Fetching open markets (this takes a couple of minutes)...")
    with kalshi.KalshiClient(max_retries=6) as k:
        kalshi_markets = [m for m in k.list_markets() if m.event_ticker.partition("-")[0] in LEAGUES]
    with polymarket.PolymarketClient() as p:
        pm_markets = list(p.list_markets(categories=["sports"]))

    existing = {(x.kalshi_ticker, x.polymarket_slug) for x in load_pairs(DEFAULT_PATH)}
    suggestions = [s for s in suggest_pairs(kalshi_markets, pm_markets) if
                   (s.kalshi_ticker, s.polymarket_slug) not in existing]
    write_pairs(out, suggestions)

    flagged = sum(1 for s in suggestions if s.resolution_notes)
    print(f"Kalshi game markets: {len(kalshi_markets)}, Polymarket sports markets: {len(pm_markets)}")
    print(f"Wrote {len(suggestions)} unverified suggestions to {out} ({flagged} flagged for extra care)")
    print("Review each against docs/pairs.md before copying it into data/pairs.csv.")


if __name__ == "__main__":
    main()
