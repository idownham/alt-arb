"""Weekly refresh: propose new game pairs for leagues whose rules templates are approved.

A league is "approved" once its rules text on both venues has been reviewed and
recorded in data/rule_templates.json (with the resolution note that applies to
every game). After that, each new game only needs mechanical checks:

    1. both venues' rules text still matches the approved template exactly
       (team names, dates and times stripped out),
    2. the date in each venue's own rules text agrees,
    3. the Kalshi team lands on exactly one Polymarket side, consistent with `inverted`,
    4. the matcher didn't flag it (no "AUTO:" note).

Games that pass are written UNVERIFIED to data/refresh.csv for the owner to approve in
chat (CLAUDE.md rule 3). Only then does `--apply` copy them into data/pairs.csv.

    python -m altarb.mapping.refresh                                # propose (approved leagues)
    python -m altarb.mapping.refresh --apply --verified-by Agent    # after approval in chat
    python -m altarb.mapping.refresh --show-templates KXMLBGAME     # review a new league
    python -m altarb.mapping.refresh --approve-template KXMLBGAME --variant 1 --note "..."  # after approval
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from altarb.mapping.leagues import LEAGUES
from altarb.mapping.matcher import fetch_markets, suggest_pairs
from altarb.mapping.pairs import DEFAULT_PATH as PAIRS_PATH
from altarb.mapping.pairs import MarketPair, load_pairs, write_pairs
from altarb.venues import kalshi, polymarket

TEMPLATES_PATH = PAIRS_PATH.with_name("rule_templates.json")
PROPOSAL_PATH = PAIRS_PATH.with_name("refresh.csv")

_DATE = r"(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.? \d{1,2}, \d{4}|\d{4}-\d{2}-\d{2})"
# Ordinary words that also appear in some team names ("Texas A&M", Kalshi's "Tie" market, "The ...").
# Treating them as team names would blank them out of the rules text, e.g. "resolve to a fair price".
_NOT_TEAM_WORDS = {"the", "of", "and", "a", "tie", "draw", "yes", "no", "not", "will", "for", "in", "at", "on",
                   "to", "by", "or", "if", "is", "be", "an", "as", "it"}
_TIME = r"\d{1,2}:\d{2} ?(?:AM|PM)(?: (?:ET|UTC))?"
_SCHEDULED_DATE = re.compile(rf"scheduled for ({_DATE})")


@dataclass(frozen=True)
class Template:
    kalshi: str
    polymarket: str
    note: str  # resolution_notes written on every pair of this league
    approved_on: str


@dataclass(frozen=True)
class Review:
    kalshi_ticker: str
    polymarket_slug: str
    reason: str


# --- templates --------------------------------------------------------------


def load_templates(path: Path | str = TEMPLATES_PATH) -> dict[str, list[Template]]:
    """Approved template variants per league. A league can have several (e.g. soccer's team and tie
    markets use different wording); a game must match one of them exactly."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return {series: [Template(**t) for t in (v if isinstance(v, list) else [v])] for series, v in raw.items()}


def save_templates(templates: dict[str, list[Template]], path: Path | str = TEMPLATES_PATH) -> None:
    data = {s: [dataclasses.asdict(t) for t in ts] for s, ts in sorted(templates.items())}
    Path(path).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def kalshi_rules(m: kalshi.Market) -> str:
    return (m.rules_primary + "\n" + (m.raw.get("rules_secondary") or "")).strip()


def team_words(k: kalshi.Market, p: polymarket.Market, siblings: Iterable[kalshi.Market] = ()) -> set[str]:
    """Every word that names a team in this game, on either venue.

    `siblings` are the other Kalshi markets of the same event: Kalshi's rules name both
    teams, but each market only carries its own team's name.
    """
    kalshi_markets = [m for m in (k, *siblings) if not m.ticker.endswith("-TIE")]
    names = [m.yes_sub_title for m in kalshi_markets] + [m.raw.get("no_sub_title") or "" for m in kalshi_markets]
    for side in p.raw.get("marketSides") or []:
        team = side.get("team") or {}
        names += [str(team.get(f) or "") for f in ("name", "safeName", "alias", "abbreviation", "displayAbbreviation")]
    return {w for n in names for w in re.findall(r"[A-Za-z0-9]+", n.lower())} - _NOT_TEAM_WORDS


_TOKEN = re.compile(r"[^\W_][\w.'’]*|\s+|[^\w\s]")
_JOINERS = {" ", "/", "&", "'", "’", "-", "(", ")", "."}


def to_template(text: str, words: set[str]) -> str:
    """Strip dates, times and team names so two games of one league compare equal.

    A team name in rules text is often longer than the team data ("Tottenham Hotspur FC",
    "FK Bodø/Glimt", "NOLA Saints", "New York R"), so any capitalized word or number joined
    to a recognized team word counts as part of the name. Lowercase words, where the
    substance of the rules lives ("two weeks", "extra time"), are always compared exactly.
    """
    text = re.sub(_DATE, "<DATE>", text)
    text = re.sub(_TIME, "<TIME>", text)
    tokens = _TOKEN.findall(text)
    kinds = []  # "T" team word, "C" capitalized word or number, "J" joiner, "" anything else
    sentence_start = True
    for tok in tokens:
        bare = tok.lower().rstrip(".").removesuffix("'s").removesuffix("’s")
        starts = sentence_start and not tok.isspace()
        if not tok.isspace():
            sentence_start = tok.endswith((".", "?", "!", ":")) and tok not in _JOINERS - {"."}
        elif "\n" in tok:
            sentence_start = True
        if bare in words:
            kinds.append("T")
        elif (tok[0].isupper() or tok.isdigit()) and not starts:  # "If", "The" opening a sentence aren't names
            kinds.append("C")
        elif tok in _JOINERS or tok.isspace() and "\n" not in tok:
            kinds.append("J")
        else:
            kinds.append("")
    out, i = [], 0
    while i < len(tokens):
        if kinds[i] in ("T", "C"):
            # Extend over name-like tokens and the joiners between them.
            j, last_name = i, i
            while j < len(tokens) and kinds[j] in ("T", "C", "J"):
                if kinds[j] in ("T", "C"):
                    last_name = j
                j += 1
            if "T" in kinds[i:last_name + 1]:
                out.append("<TEAMS>")
                i = last_name + 1
                continue
            out.extend(tokens[i:last_name + 1])
            i = last_name + 1
            continue
        out.append(tokens[i])
        i += 1
    return "".join(out)


def siblings_of(k: kalshi.Market, km: dict[str, kalshi.Market]) -> list[kalshi.Market]:
    return [m for m in km.values() if m.event_ticker == k.event_ticker and m.ticker != k.ticker]


def observed_templates(pairs: Iterable[MarketPair], km: dict[str, kalshi.Market], pm: dict[str, polymarket.Market]) -> tuple[Counter, Counter]:
    kt, pt = Counter(), Counter()
    for (k_text, p_text), n in observed_variants(pairs, km, pm).items():
        kt[k_text] += n
        pt[p_text] += n
    return kt, pt


def observed_variants(pairs: Iterable[MarketPair], km: dict[str, kalshi.Market], pm: dict[str, polymarket.Market]) -> Counter:
    """How many pairs use each (Kalshi template, Polymarket template) combination."""
    variants = Counter()
    for pair in pairs:
        k, p = km[pair.kalshi_ticker], pm[pair.polymarket_slug]
        words = team_words(k, p, siblings_of(k, km))
        variants[(to_template(kalshi_rules(k), words), to_template(p.description, words))] += 1
    return variants


# --- per-game checks --------------------------------------------------------


def matching_variant(k: kalshi.Market, p: polymarket.Market, templates: list[Template], siblings: Iterable[kalshi.Market] = ()) -> Template | None:
    words = team_words(k, p, siblings)
    k_text, p_text = to_template(kalshi_rules(k), words), to_template(p.description, words)
    return next((t for t in templates if t.kalshi == k_text and t.polymarket == p_text), None)


def check_pair(
    pair: MarketPair, k: kalshi.Market, p: polymarket.Market, templates: Template | list[Template],
    siblings: Iterable[kalshi.Market] = (),
) -> list[str]:
    """Reasons this pair can't be proposed; empty if it passes every check."""
    templates = [templates] if isinstance(templates, Template) else templates
    problems = []
    if matching_variant(k, p, templates, siblings) is None:
        words = team_words(k, p, siblings)
        k_text, p_text = to_template(kalshi_rules(k), words), to_template(p.description, words)
        if not any(t.kalshi == k_text for t in templates):
            problems.append("Kalshi rules text differs from the approved template")
        if not any(t.polymarket == p_text for t in templates):
            problems.append("Polymarket rules text differs from the approved template")
        if not problems:
            problems.append("this combination of Kalshi and Polymarket rules text isn't an approved variant")
    if pair.resolution_notes:
        problems.append(f"matcher flagged it: {pair.resolution_notes}")

    k_date, p_date = _scheduled_date(kalshi_rules(k)), _scheduled_date(p.description)
    if k_date is None or p_date is None:
        problems.append("no scheduled date in rules text")
    elif k_date != p_date:
        problems.append(f"rules dates differ: Kalshi {k_date}, Polymarket {p_date}")

    expected = _expected_inverted(k, p)
    if expected is None:
        problems.append(f"can't place Kalshi YES ({k.yes_sub_title!r}) on exactly one Polymarket side")
    elif expected != pair.inverted:
        problems.append(f"inverted should be {str(expected).lower()}")
    return problems


def _scheduled_date(text: str) -> date | None:
    match = _SCHEDULED_DATE.search(text)
    if not match:
        return None
    raw = match.group(1).replace(".", "").replace("Sept ", "Sep ")
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    return None


def _expected_inverted(k: kalshi.Market, p: polymarket.Market) -> bool | None:
    code = k.ticker.rsplit("-", 1)[1].lower()
    name = set(re.findall(r"[a-z0-9]+", k.yes_sub_title.lower()))
    sides = p.raw.get("marketSides") or []
    if p.slug.startswith("atc-"):  # three-way: one market per outcome, never inverted
        if code == "tie":
            return False if p.slug.endswith("-draw") else None
        team = (sides[0].get("team") or {}) if sides else {}
        hit = code == str(team.get("abbreviation") or "").lower() or (name and name <= _team_tokens(team))
        return False if hit else None
    hits = [s for s in sides if code == str((s.get("team") or {}).get("abbreviation") or "").lower()
            or (name and name <= _team_tokens(s.get("team") or {}))]
    return (not hits[0].get("long")) if len(hits) == 1 else None


def _team_tokens(team: dict) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", " ".join(str(team.get(f) or "") for f in ("name", "safeName", "alias")).lower()))


# --- propose / apply ----------------------------------------------------------


def propose(
    candidates: Iterable[MarketPair],
    km: dict[str, kalshi.Market],
    pm: dict[str, polymarket.Market],
    templates: dict[str, list[Template]],
) -> tuple[list[MarketPair], list[Review]]:
    ready, review = [], []
    for pair in candidates:
        series = pair.kalshi_ticker.split("-")[0]
        variants = templates.get(series)
        if not variants:
            review.append(Review(pair.kalshi_ticker, pair.polymarket_slug, "league has no approved template"))
            continue
        k, p = km[pair.kalshi_ticker], pm[pair.polymarket_slug]
        problems = check_pair(pair, k, p, variants, siblings_of(k, km))
        if problems:
            review.append(Review(pair.kalshi_ticker, pair.polymarket_slug, "; ".join(problems)))
        else:
            variant = matching_variant(k, p, variants, siblings_of(k, km))
            ready.append(dataclasses.replace(pair, resolution_notes=variant.note))
    return ready, review


def apply(proposal_path: Path | str, pairs_path: Path | str, verified_by: str, today: date) -> list[MarketPair]:
    """Copy proposed rows into pairs.csv as verified. Run only after the owner approves them."""
    existing = load_pairs(pairs_path)
    have = {(p.kalshi_ticker, p.polymarket_slug) for p in existing}
    new = [
        dataclasses.replace(p, verified=True, verified_by=verified_by, verified_on=today)
        for p in load_pairs(proposal_path)
        if (p.kalshi_ticker, p.polymarket_slug) not in have
    ]
    write_pairs(pairs_path, existing + new)
    return new


# --- CLI -------------------------------------------------------------------------


def _fetch(series: list[str]) -> tuple[dict[str, kalshi.Market], dict[str, polymarket.Market]]:
    with kalshi.KalshiClient(max_retries=6) as k, polymarket.PolymarketClient(max_retries=6) as p:
        kalshi_markets, pm_markets = fetch_markets(k, p, series)
    return {m.ticker: m for m in kalshi_markets}, {m.slug: m for m in pm_markets}


def _print_proposal(ready: list[MarketPair], review: list[Review]) -> None:
    by_game: dict[tuple, list[MarketPair]] = {}
    for p in ready:
        by_game.setdefault((p.kalshi_ticker.split("-")[0], str(p.expiry), p.polymarket_slug), []).append(p)
    for (series, expiry, slug), pairs in sorted(by_game.items()):
        sides = "  ".join(f"{p.kalshi_ticker.rsplit('-', 1)[1]}:inv={str(p.inverted).lower()}" for p in sorted(pairs, key=lambda p: p.kalshi_ticker))
        print(f"  {series:12} {expiry}  {slug:34} {sides}")
    counts = Counter(p.kalshi_ticker.split("-")[0] for p in ready)
    print(f"\nREADY TO APPROVE: {len(ready)} markets in {len(by_game)} games {dict(counts)} -> {PROPOSAL_PATH}")
    if review:
        print(f"NEEDS REVIEW (not proposed): {len(review)}")
        for r in review:
            print(f"  {r.kalshi_ticker:34} {r.polymarket_slug:34} {r.reason}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Propose (and, after approval, apply) new pairs for approved leagues.")
    parser.add_argument("--league", action="append", metavar="KALSHI_SERIES", help="limit to these series (default: approved leagues)")
    parser.add_argument("--apply", action="store_true", help="copy data/refresh.csv into pairs.csv as verified (after approval)")
    parser.add_argument("--verified-by", help="name recorded with --apply, e.g. Agent or Ian")
    parser.add_argument("--show-templates", metavar="KALSHI_SERIES", help="print a league's observed rules templates")
    parser.add_argument("--approve-template", metavar="KALSHI_SERIES", help="record a league's current template (after approval)")
    parser.add_argument("--variant", type=int, help="which variant to approve (number shown by --show-templates)")
    parser.add_argument("--note", help="resolution note for --approve-template")
    args = parser.parse_args()
    templates = load_templates()

    if args.apply:
        if not args.verified_by:
            parser.error("--apply needs --verified-by (e.g. Agent)")
        new = apply(PROPOSAL_PATH, PAIRS_PATH, args.verified_by, date.today())
        print(f"Added {len(new)} verified pairs to {PAIRS_PATH} (verified_by={args.verified_by}).")
        return

    if args.show_templates or args.approve_template:
        series = args.show_templates or args.approve_template
        if series not in LEAGUES:
            parser.error(f"{series} is not in leagues.py")
        km, pm = _fetch([series])
        pairs = suggest_pairs(km.values(), pm.values())
        variants = observed_variants(pairs, km, pm).most_common()
        approved = {(t.kalshi, t.polymarket) for t in templates.get(series, [])}
        print(f"{series}: {len(pairs)} matched markets, {len(variants)} rules variant(s)")
        for i, ((k_text, p_text), n) in enumerate(variants, 1):
            status = "APPROVED" if (k_text, p_text) in approved else "not approved"
            print(f"\n=== variant {i}: {n} market(s), {status}\n--- KALSHI:\n{k_text}\n--- POLYMARKET:\n{p_text}")
        if args.approve_template:
            if not args.variant or not 1 <= args.variant <= len(variants):
                parser.error("--approve-template needs --variant N (a number from the list above)")
            if not args.note:
                parser.error("--approve-template needs --note")
            k_text, p_text = variants[args.variant - 1][0]
            if (k_text, p_text) in approved:
                print(f"\nVariant {args.variant} is already approved.")
                return
            templates.setdefault(series, []).append(Template(k_text, p_text, args.note, date.today().isoformat()))
            save_templates(templates)
            print(f"\nApproved variant {args.variant} for {series}; saved to {TEMPLATES_PATH}")
        return

    series = args.league or sorted(templates)
    if not series:
        parser.error("no approved templates yet; see --show-templates / --approve-template")
    km, pm = _fetch(series)
    existing = {(p.kalshi_ticker, p.polymarket_slug) for p in load_pairs(PAIRS_PATH)}
    candidates = [c for c in suggest_pairs(km.values(), pm.values()) if (c.kalshi_ticker, c.polymarket_slug) not in existing]
    ready, review = propose(candidates, km, pm, templates)
    write_pairs(PROPOSAL_PATH, ready)
    _print_proposal(ready, review)
    if ready:
        print("\nNothing is verified yet. After the owner approves this list in chat, run:\n"
              "  python -m altarb.mapping.refresh --apply --verified-by Agent")


if __name__ == "__main__":
    main()
