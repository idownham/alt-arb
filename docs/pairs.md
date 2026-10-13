# Market pairs (`data/pairs.csv`)

Each row says "this Kalshi market and this Polymarket US market resolve on the same event".
The scanner only uses rows with `verified = true` (CLAUDE.md hard rule 3).

## Daily refresh (leagues with an approved template)

Once a league's rules have been reviewed, its rules text on both venues is stored in
`data/rule_templates.json` along with the note that applies to every game. New games then only
need mechanical checks, which one command runs:

```bash
.venv/bin/python -m altarb.mapping.refresh                              # propose (~10s)
.venv/bin/python -m altarb.mapping.refresh --apply --verified-by Agent  # only after approval in chat
```

The first command proposes games whose rules match the approved template word for word, whose
dates agree in both rules texts, and whose `inverted` checks out. It writes them **unverified** to
`data/refresh.csv` and prints the list; anything else is listed under "needs review" with the reason.
`--apply` copies the proposed rows into `pairs.csv` as verified.

**Automatic daily run** (owner-approved 2026-10-13, CLAUDE.md rule 3): a scheduled task in the Claude
desktop app runs the refresh every morning and applies what passes with `--verified-by Auto`, then
commits and pushes, so the server has the new games within 15 minutes. "Needs review" games are never
auto-approved; check the task's summary for them. It runs while the app is open (or on next launch).

To approve a new league or rules variant: `--show-templates KXMLBGAME` lists its variants (Kalshi +
Polymarket wording pairs) with how many markets use each; after the owner approves one,
`--approve-template KXMLBGAME --variant 1 --note "..."` records it. A league can have several approved
variants (soccer's team vs tie markets; two Polymarket rule versions). If a venue changes its
wording, that league's games land in "needs review" until the new wording is approved.

## Finding candidates

```bash
.venv/bin/python -m altarb.mapping.matcher --league KXNBAGAME   # one league: a few seconds
.venv/bin/python -m altarb.mapping.matcher                      # every league in leagues.py: ~4 min
```

`--league` takes Kalshi series tickers from `src/altarb/mapping/leagues.py` and can be repeated.
Pairs already in `data/pairs.csv` are left out. This writes `data/candidates.csv` (not committed; regenerate any time) with suggested
game-winner pairs in the same columns, all `verified = false`. For each one you want:

1. Open both markets and work through the checklist below.
2. Copy the row into `data/pairs.csv`, fix anything wrong, set `verified`, `verified_by`, `verified_on`.
3. Run `.venv/bin/pytest`. It checks `pairs.csv` is still valid.

Rows with `AUTO:` in `resolution_notes` need extra care (dates differ by a day, or more than one
game matched). Replace that note with your own findings when you verify.

The matcher only covers leagues listed in `src/altarb/mapping/leagues.py`. Politics, economics
and other non-game markets aren't matched automatically yet.

## Columns

| Column | Example (illustrative) | Notes |
| - | - | - |
| `kalshi_ticker` | `KXNHLGAME-26OCT16BOSANA-BOS` | From the Kalshi market |
| `polymarket_slug` | `aec-nhl-bos-ana-2026-10-16` | Polymarket US market slug |
| `inverted` | `false` | `true` if Kalshi YES means Polymarket NO. See below. |
| `description` | `BOS @ ANA, Oct 16, Boston wins` | For humans |
| `resolution_notes` | `PM settles at fair price if postponed >2wk; Kalshi ...` | Differences you found |
| `expiry` | `2026-10-17` | Date (or datetime) the pair resolves by |
| `verified` | `true` | Only after the checklist below |
| `verified_by` | `Ian` | Required when verified. `Agent` = an agent did the comparison and Ian approved it in chat; `Auto` = the daily refresh, against an approved template (CLAUDE.md rule 3) |
| `verified_on` | `2026-10-12` | Required when verified |

The loader (`altarb.mapping.pairs.load_pairs`) rejects the whole file on any bad row,
so a typo can't quietly enable or disable a pair.

## Getting `inverted` right

Both venues phrase a market as "will X happen". Check that X is the same thing.

- Kalshi "Boston wins", Polymarket instrument "Boston wins" → `inverted = false`
- Kalshi "Boston wins", Polymarket instrument "Anaheim wins" → `inverted = true`

On Polymarket US the instrument's outcome is its YES side (`yes_label` in the client).
Getting this wrong turns a hedge into a doubled bet, so check it every time.

## Verification checklist

Mark `verified = true` only when every item matches, or the difference is written in
`resolution_notes` and you've decided it's acceptable.

1. **Same event:** same teams/candidates/threshold, same date. Watch for doubleheaders and rematches.
2. **Same direction:** `inverted` is set correctly (above).
3. **Resolution criteria:** read both rules texts in full (Kalshi `rules_primary`, Polymarket `description`).
   Overtime, extra innings, "at close" vs "at any time", rounding of numeric thresholds.
4. **Resolution source:** which official source each venue uses (league, AP, BLS, etc.).
5. **Deadline/expiry:** when each market closes and must resolve by.
6. **Edge cases:** ties/draws, cancellation, postponement, and what each venue pays out in each.
   A market that "settles at last fair price" on one venue and voids on the other is not a clean pair.

If anything is unclear, leave `verified = false` and write the question in `resolution_notes`.
