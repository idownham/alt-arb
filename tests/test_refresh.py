from datetime import date

import pytest

from altarb.mapping.pairs import MarketPair, load_pairs, write_pairs
from altarb.mapping.refresh import (
    Template,
    apply,
    check_pair,
    load_templates,
    observed_templates,
    propose,
    save_templates,
    team_words,
    to_template,
)
from altarb.venues import kalshi, polymarket

K_RULES = ("If {yes} wins the {a} vs {b} NHL game originally scheduled for {d}, then the market resolves to Yes.\n"
           "If the game is postponed but begins within 48 hours of its originally scheduled start time, "
           "the market will remain open and resolve based on the official final result.")
P_RULES = ("This market will settle to the winner of the {a} vs {b} NHL game scheduled for {d} at 7:00PM ET. "
           "Overtime and any shootout are included if played. Outcome sourced from NHL.")


def team(name, safe, abbr):
    return {"name": name, "safeName": safe, "abbreviation": abbr}


def game(code_a, name_a, team_a, code_b, name_b, team_b, day="Oct 15, 2026", k_text=K_RULES, p_text=P_RULES,
         slug=None, ticker_date="26OCT15"):
    """Kalshi markets for both teams + the Polymarket instrument (team_a is its YES side)."""
    event = f"KXNHLGAME-{ticker_date}{code_a}{code_b}"
    slug = slug or f"aec-nhl-{team_a['abbreviation']}-{team_b['abbreviation']}-2026-10-15"
    km = {}
    for code, name in ((code_a, name_a), (code_b, name_b)):
        km[f"{event}-{code}"] = kalshi.Market.from_api({
            "ticker": f"{event}-{code}", "event_ticker": event, "title": f"{name} wins", "yes_sub_title": name,
            "rules_primary": k_text.format(yes=name, a=name_a, b=name_b, d=day).split("\n")[0],
            "rules_secondary": k_text.format(yes=name, a=name_a, b=name_b, d=day).split("\n", 1)[1],
        })
    pm = polymarket.Market.from_api({
        "id": "1", "slug": slug, "question": "Who wins?", "sportsMarketType": "hockey_team_full_game_winner",
        "description": p_text.format(a=team_a["safeName"], b=team_b["safeName"], d=day),
        "marketSides": [{"description": team_a["name"], "long": True, "team": team_a},
                        {"description": team_b["name"], "long": False, "team": team_b}],
    })
    return km, pm


def pair(ticker, slug, inverted, notes=""):
    return MarketPair(ticker, slug, inverted, "", notes, date(2026, 10, 16), False, "", None)


BOS_SJ = game("BOS", "Boston", team("Bruins", "BOS Bruins", "bos"), "SJ", "San Jose", team("Sharks", "SJS Sharks", "sj"))
NYR_NJ = game("NYR", "New York R", team("Rangers", "NYR Rangers", "nyr"), "NJ", "New Jersey", team("Devils", "NJ Devils", "nj"))


def template_from(*games):
    km = {t: m for g in games for t, m in g[0].items()}
    pm = {g[1].slug: g[1] for g in games}
    pairs = [pair(t, games[i][1].slug, False) for i, g in enumerate(games) for t in g[0]]
    kt, pt = observed_templates(pairs, km, pm)
    return kt, pt, km, pm


def test_different_games_share_one_template():
    kt, pt, *_ = template_from(BOS_SJ, NYR_NJ)
    assert len(kt) == 1 and len(pt) == 1
    assert "If <TEAMS> wins the <TEAMS> vs <TEAMS> NHL game originally scheduled for <DATE>" in next(iter(kt))


def test_name_fragments_absorbed():
    # "New York R" (Rangers) and "NOLA Saints": fragments not in the team data.
    words = {"rangers", "saints", "new", "york", "nyr", "orleans", "no"}
    assert to_template("the New York R vs NOLA Saints game", words) == "the <TEAMS> vs <TEAMS> game"
    # ...but a real league word next to a team is kept.
    assert to_template("Saints NFL game", {"saints"}) == "<TEAMS> NFL game"


@pytest.fixture
def approved():
    kt, pt, km, pm = template_from(BOS_SJ, NYR_NJ)
    return Template(next(iter(kt)), next(iter(pt)), "NHL note", "2026-10-10"), km, pm


def test_good_pair_passes(approved):
    t, km, pm = approved
    bos, sj = km["KXNHLGAME-26OCT15BOSSJ-BOS"], km["KXNHLGAME-26OCT15BOSSJ-SJ"]
    # Polymarket's team data says "SJS Sharks", never "San Jose": the sibling Kalshi market supplies it.
    assert check_pair(pair(bos.ticker, BOS_SJ[1].slug, False), bos, BOS_SJ[1], t, [sj]) == []
    assert check_pair(pair(sj.ticker, BOS_SJ[1].slug, True), sj, BOS_SJ[1], t, [bos]) == []
    assert check_pair(pair(bos.ticker, BOS_SJ[1].slug, False), bos, BOS_SJ[1], t) == [
        "Kalshi rules text differs from the approved template"]  # without the sibling, "San Jose" isn't recognized


def test_wrong_inverted_is_caught(approved):
    t, km, pm = approved
    sj = km["KXNHLGAME-26OCT15BOSSJ-SJ"]
    problems = check_pair(pair(sj.ticker, BOS_SJ[1].slug, False), sj, BOS_SJ[1], t, [km["KXNHLGAME-26OCT15BOSSJ-BOS"]])
    assert problems == ["inverted should be true"]


def test_changed_rules_wording_is_caught(approved):
    t, *_ = approved
    changed = game("BOS", "Boston", team("Bruins", "BOS Bruins", "bos"), "SJ", "San Jose", team("Sharks", "SJS Sharks", "sj"),
                   p_text=P_RULES.replace("Overtime and any shootout are included", "Regulation time only"))
    problems = check_pair(pair("KXNHLGAME-26OCT15BOSSJ-BOS", changed[1].slug, False), changed[0]["KXNHLGAME-26OCT15BOSSJ-BOS"],
                          changed[1], t, [changed[0]["KXNHLGAME-26OCT15BOSSJ-SJ"]])
    assert problems == ["Polymarket rules text differs from the approved template"]


def test_date_mismatch_is_caught(approved):
    t, *_ = approved
    km, pm = game("BOS", "Boston", team("Bruins", "BOS Bruins", "bos"), "SJ", "San Jose", team("Sharks", "SJS Sharks", "sj"))
    pm = polymarket.Market.from_api({**pm.raw, "description": pm.description.replace("Oct 15", "Oct 16")})
    problems = check_pair(pair("KXNHLGAME-26OCT15BOSSJ-BOS", pm.slug, False), km["KXNHLGAME-26OCT15BOSSJ-BOS"], pm, t,
                          [km["KXNHLGAME-26OCT15BOSSJ-SJ"]])
    assert problems == ["rules dates differ: Kalshi 2026-10-15, Polymarket 2026-10-16"]


def test_matcher_flag_blocks_proposal(approved):
    t, km, pm = approved
    p = pair("KXNHLGAME-26OCT15BOSSJ-BOS", BOS_SJ[1].slug, False, notes="AUTO: dates differ by 1 day")
    assert check_pair(p, km[p.kalshi_ticker], BOS_SJ[1], t, [km["KXNHLGAME-26OCT15BOSSJ-SJ"]]) == [
        "matcher flagged it: AUTO: dates differ by 1 day"]


def test_propose_splits_ready_and_review(approved):
    t, km, pm = approved
    other = pair("KXNBAGAME-26OCT15AB-A", BOS_SJ[1].slug, False)
    good = pair("KXNHLGAME-26OCT15BOSSJ-BOS", BOS_SJ[1].slug, False)
    bad = pair("KXNHLGAME-26OCT15BOSSJ-SJ", BOS_SJ[1].slug, False)

    ready, review = propose([good, bad, other], {**km, other.kalshi_ticker: km[good.kalshi_ticker]}, pm, {"KXNHLGAME": t})
    # (propose finds each market's sibling itself, by event ticker)

    assert [r.kalshi_ticker for r in ready] == [good.kalshi_ticker]
    assert ready[0].resolution_notes == "NHL note" and ready[0].verified is False
    assert {(r.kalshi_ticker, r.reason) for r in review} == {
        (bad.kalshi_ticker, "inverted should be true"),
        (other.kalshi_ticker, "league has no approved template"),
    }


def test_apply_marks_verified_and_skips_existing(tmp_path):
    pairs_path, proposal = tmp_path / "pairs.csv", tmp_path / "refresh.csv"
    existing = MarketPair("KX-1", "pm-1", False, "", "", date(2026, 10, 16), True, "Ian", date(2026, 10, 10))
    write_pairs(pairs_path, [existing])
    write_pairs(proposal, [pair("KX-1", "pm-1", False), pair("KX-2", "pm-2", True, notes="NHL note")])

    added = apply(proposal, pairs_path, "Agent", date(2026, 10, 12))

    assert [p.kalshi_ticker for p in added] == ["KX-2"]
    rows = load_pairs(pairs_path)
    assert [(p.kalshi_ticker, p.verified, p.verified_by, p.verified_on) for p in rows] == [
        ("KX-1", True, "Ian", date(2026, 10, 10)),
        ("KX-2", True, "Agent", date(2026, 10, 12)),
    ]
    assert rows[1].resolution_notes == "NHL note"


def test_templates_round_trip(tmp_path):
    path = tmp_path / "t.json"
    assert load_templates(path) == {}
    save_templates({"KXNHLGAME": Template("k", "p", "note", "2026-10-10")}, path)
    assert load_templates(path) == {"KXNHLGAME": Template("k", "p", "note", "2026-10-10")}


def test_team_words_cover_both_venues():
    km, pm = BOS_SJ
    words = team_words(km["KXNHLGAME-26OCT15BOSSJ-SJ"], pm)
    assert {"san", "jose", "sharks", "sjs", "bruins", "bos"} <= words
