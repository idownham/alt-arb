from datetime import date

from altarb.mapping.matcher import suggest_pairs
from altarb.mapping.pairs import load_pairs, write_pairs
from altarb.venues import kalshi, polymarket

LEAGUES = {"KXNHLGAME": frozenset({"nhl"}), "KXNBAGAME": frozenset({"nba"}), "KXMLSGAME": frozenset({"mls"})}


def k(ticker: str, team: str) -> kalshi.Market:
    event = ticker.rsplit("-", 1)[0]
    return kalshi.Market.from_api({
        "ticker": ticker,
        "event_ticker": event,
        "title": f"{team} wins",
        "yes_sub_title": team,
        "expected_expiration_time": "2026-10-10T20:00:00Z",
    })


def team(name: str, safe: str, abbr: str) -> dict:
    return {"name": name, "safeName": safe, "abbreviation": abbr}


def pm_two_way(slug: str, long_team: dict, short_team: dict, kind="hockey_team_full_game_winner"):
    return polymarket.Market.from_api({
        "id": "1",
        "slug": slug,
        "question": "Who will win?",
        "sportsMarketType": kind,
        "marketSides": [
            {"description": long_team["name"], "long": True, "team": long_team},
            {"description": short_team["name"], "long": False, "team": short_team},
        ],
    })


def pm_outcome(slug: str, question: str, t: dict | None = None):
    sides = [{"description": "Yes", "long": True, "team": t}, {"description": "No", "long": False, "team": t}]
    return polymarket.Market.from_api({
        "id": "2", "slug": slug, "question": question,
        "sportsMarketType": "soccer_team_full_time_winner", "marketSides": sides,
    })


FLYERS = team("Flyers", "Philadelphia Flyers", "phi")
BRUINS = team("Bruins", "Boston Bruins", "bos")
NHL_GAME = pm_two_way("aec-nhl-phi-bos-2026-10-10", FLYERS, BRUINS)
KALSHI_NHL = [k("KXNHLGAME-26OCT10PHIBOS-PHI", "Philadelphia"), k("KXNHLGAME-26OCT10PHIBOS-BOS", "Boston")]


def by_ticker(pairs):
    return {p.kalshi_ticker: p for p in pairs}


def test_two_way_game_sets_inverted_per_side():
    pairs = by_ticker(suggest_pairs(KALSHI_NHL, [NHL_GAME], LEAGUES))

    phi, bos = pairs["KXNHLGAME-26OCT10PHIBOS-PHI"], pairs["KXNHLGAME-26OCT10PHIBOS-BOS"]
    assert phi.polymarket_slug == bos.polymarket_slug == "aec-nhl-phi-bos-2026-10-10"
    assert phi.inverted is False  # Kalshi "Philadelphia wins" == Polymarket YES (Flyers)
    assert bos.inverted is True  # Kalshi "Boston wins" == Polymarket NO
    assert phi.verified is False and phi.verified_by == ""
    assert phi.expiry == date(2026, 10, 10)


def test_matches_by_name_when_abbreviations_differ():
    kalshi_markets = [k("KXNHLGAME-26OCT14MTLWSH-MTL", "Montreal"), k("KXNHLGAME-26OCT14MTLWSH-WSH", "Washington")]
    game = pm_two_way(
        "aec-nhl-mon-was-2026-10-14",
        team("Canadiens", "Montreal Canadiens", "mon"),
        team("Capitals", "Washington Capitals", "was"),
    )

    pairs = by_ticker(suggest_pairs(kalshi_markets, [game], LEAGUES))

    assert pairs["KXNHLGAME-26OCT14MTLWSH-MTL"].inverted is False
    assert pairs["KXNHLGAME-26OCT14MTLWSH-WSH"].inverted is True


def test_never_crosses_leagues():
    nba_game = pm_two_way(
        "aec-nba-phi-bos-2026-10-10",
        team("76ers", "Philadelphia 76ers", "phi"),
        team("Celtics", "Boston Celtics", "bos"),
        kind="basketball_team_full_game_winner",
    )

    pairs = suggest_pairs(KALSHI_NHL, [nba_game], LEAGUES)

    assert pairs == []


def test_three_way_soccer_pairs_each_outcome_including_draw():
    kalshi_markets = [
        k("KXMLSGAME-26OCT10TORMTL-TOR", "Toronto"),
        k("KXMLSGAME-26OCT10TORMTL-MTL", "Montreal"),
        k("KXMLSGAME-26OCT10TORMTL-TIE", "Tie"),
    ]
    pm_markets = [
        pm_outcome("atc-mls-tor-mim-2026-10-10-tor", "Will Toronto FC win?", team("Toronto FC", "Toronto FC", "tor")),
        pm_outcome("atc-mls-tor-mim-2026-10-10-mim", "Will CF Montreal win?", team("CF Montreal", "CF Montreal", "mtl")),
        pm_outcome("atc-mls-tor-mim-2026-10-10-draw", "Will it end in a draw?"),
        pm_outcome("atc-mls-tor-mim-2026-10-10-fh-tor", "Will Toronto lead at halftime?"),
    ]

    pairs = by_ticker(suggest_pairs(kalshi_markets, pm_markets, LEAGUES))

    assert {t: p.polymarket_slug for t, p in pairs.items()} == {
        "KXMLSGAME-26OCT10TORMTL-TOR": "atc-mls-tor-mim-2026-10-10-tor",
        "KXMLSGAME-26OCT10TORMTL-MTL": "atc-mls-tor-mim-2026-10-10-mim",
        "KXMLSGAME-26OCT10TORMTL-TIE": "atc-mls-tor-mim-2026-10-10-draw",
    }
    assert not any(p.inverted for p in pairs.values())


def test_ignores_non_winner_markets():
    spread = pm_two_way(
        "asc-nhl-phi-bos-2026-10-10-neg-1pt5", FLYERS, BRUINS, kind="hockey_team_full_game_spread"
    )

    assert suggest_pairs(KALSHI_NHL, [spread], LEAGUES) == []


def test_skips_ambiguous_team_names():
    kalshi_markets = [k("KXNBAGAME-26OCT10NYKBKN-NYK", "New York"), k("KXNBAGAME-26OCT10NYKBKN-BKN", "Brooklyn")]
    game = pm_two_way(
        "aec-nba-nyk-nyx-2026-10-10",
        team("Knicks", "New York Knicks", "nyk2"),
        team("Liberty", "New York Liberty", "nyl"),
        kind="basketball_team_full_game_winner",
    )

    assert suggest_pairs(kalshi_markets, [game], LEAGUES) == []


def test_one_day_offset_is_matched_but_flagged():
    late = pm_two_way("aec-nhl-phi-bos-2026-10-11", FLYERS, BRUINS)

    pairs = suggest_pairs(KALSHI_NHL, [late], LEAGUES)

    assert len(pairs) == 2
    assert all("differ by 1 day" in p.resolution_notes for p in pairs)


def test_exact_date_preferred_over_offset():
    late = pm_two_way("aec-nhl-phi-bos-2026-10-11", FLYERS, BRUINS)

    pairs = suggest_pairs(KALSHI_NHL, [NHL_GAME, late], LEAGUES)

    assert {p.polymarket_slug for p in pairs} == {"aec-nhl-phi-bos-2026-10-10"}
    assert all(p.resolution_notes == "" for p in pairs)


def test_unmapped_series_ignored():
    unmapped = [k("KXXFLGAME-26OCT10PHIBOS-PHI", "Philadelphia"), k("KXXFLGAME-26OCT10PHIBOS-BOS", "Boston")]

    assert suggest_pairs(unmapped, [NHL_GAME], LEAGUES) == []


def test_suggestions_round_trip_through_pairs_schema(tmp_path):
    path = tmp_path / "candidates.csv"
    pairs = suggest_pairs(KALSHI_NHL, [NHL_GAME], LEAGUES)

    write_pairs(path, pairs)

    assert load_pairs(path) == pairs


def test_fetch_markets_queries_only_requested_series_and_leagues():
    from altarb.mapping.matcher import POLYMARKET_PACE_SECONDS, fetch_markets

    class FakeKalshi:
        def __init__(self):
            self.series = []

        def list_markets(self, series_ticker):
            self.series.append(series_ticker)
            return iter(KALSHI_NHL if series_ticker == "KXNHLGAME" else [])

    class FakePolymarket:
        def __init__(self):
            self.leagues = []

        def list_league_markets(self, league):
            self.leagues.append(league)
            return iter([NHL_GAME] if league == "nhl" else [])

    k, p, sleeps = FakeKalshi(), FakePolymarket(), []
    leagues = {**LEAGUES, "KXSERIECGAME": frozenset({"serca", "sercb", "sercc"})}

    kalshi_markets, pm_markets = fetch_markets(k, p, ["KXNHLGAME", "KXSERIECGAME"], leagues, sleep=sleeps.append)

    assert k.series == ["KXNHLGAME", "KXSERIECGAME"]
    assert p.leagues == ["nhl", "serca", "sercb", "sercc"]
    assert sleeps == [POLYMARKET_PACE_SECONDS] * 3  # paced between Polymarket requests
    assert len(suggest_pairs(kalshi_markets, pm_markets, leagues)) == 2
