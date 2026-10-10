# Market pairs (`data/pairs.csv`)

Each row says "this Kalshi market and this Polymarket US market resolve on the same event".
The scanner only uses rows with `verified = true` (CLAUDE.md hard rule 3).

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
| `verified_by` | `Ian` | Required when verified |
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
