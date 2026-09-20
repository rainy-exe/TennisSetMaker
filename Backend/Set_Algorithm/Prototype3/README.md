# TennisSetMaker — Prototype Set-Matching Algorithm

Run with: `python3 main.py`

Clean up previously generated output (`results_*.csv`, `sitouts_*.csv`,
`seed_*.json`) with: `python3 main.py --cleanup`
Add `--yes` to skip the confirmation prompt (e.g. `python3 main.py --cleanup --yes`).
Cleanup never touches `NTC_ratings_data.csv` or the `.py` source files —
only files matching the program's own output naming pattern.

## Files
- `config.py`    — run inputs only (courts, players, rounds, mode toggles, seed). No algorithm logic.
- `helpers.py`   — CSV loading, random player pull, seed/state logging, output writers. No algorithm logic.
- `algorithm.py` — the actual priority-weight block chain (the part that matters for client/mentor feedback).
- `main.py`      — wires the three together.
- `NTC_ratings_data.csv` — MOCK 76-player dataset (Name, Gender, Rating 1-10), matching the format
  described. Replace this file with the real NTC ratings CSV (same column names) — nothing else changes.

## Two scoring engines

**`algorithm.py` (default, lexicographic hierarchy)** — strict priority
order, matching the Scope of Work's default preset exactly: priority 2
always outranks priority 3, no matter how large the difference. Run with:
```
python3 main.py
```
Prompts for players/courts/rounds/seed plus `mixed_mode` and `variety_mode`
toggles as before.

**`algorithm_weighted.py` (opt-in, dynamic weight vector)** — a normalized
weighted-sum model instead of a hard hierarchy. Five values from 0.0-1.0,
in priority order `[mixed, partner, opponent, skill, court_pref]`, where
`0.0` fully switches a criterion off and higher values mean more influence
— this replaces `mixed_mode`/`variety_mode` entirely (a weight of 0 already
means "ignore this"). Run with:
```
python3 main.py --weights 0.5 1.0 0.6 1.0 0.0
```
Trade-off to know: unlike the lexicographic engine, a moderate value in a
lower-weighted criterion CAN outweigh a small value in a higher-weighted
one, since everything is summed rather than strictly ordered. Both engines
use identical greedy court-by-court assignment — only the scoring function
differs — so outputs from the two are directly comparable for the same
seed/player pool. Both engines' output files are tagged (`results_lex_...`
vs `results_wtd_...`) so running both back-to-back never overwrites either.


Prompts for: number of players to randomly pull from the CSV, number of active courts,
number of rounds, mixed-mode toggle, variety-mode toggle, and an optional seed.

Each round:
1. Picks who plays vs sits out based on seeded sit-out history (priority 1 — sit-out fairness).
2. Greedily fills courts, scoring every candidate foursome + team split against a
   priority chain: format compliance -> partner variety -> opponent variety -> skill
   balance (mirrors the Scope of Work's default preset order, priorities 2-5).
3. Logs results to console + writes `results_<seed>_<timestamp>.csv`,
   `sitouts_<seed>_<timestamp>.csv`, and `seed_<seed>_<timestamp>.json` (full reproducible
   starting state, so any demo run can be handed to Warren/mentor and rerun exactly).

## Toggles to play with
- `mixed_mode`: 0 = open, 1 = enforce 1M+1F per team (auto-relaxes since it's a soft
  scoring priority, not a hard filter — never crashes if gender balance is impossible).
- `variety_mode`: 1 = hard avoid repeat partners/opponents, 2 = soft penalty, 3 = round-robin
  bias (also penalises any prior connection, not just direct repeats).

## Changes in Prototype3 (vs Prototype2)

Two bugs fixed after review with the team:

- **Skill pairing now clusters similar-rated players onto the same court first**
  (`raw_skill_spread` in `algorithm.py`), instead of only balancing team sums. A
  foursome of four similarly-rated players (e.g. all around a rating of 5) is now
  preferred over one spanning the whole rating range, even if both could produce a
  sum-balanced match. A wide-spread foursome (e.g. ratings 1, 2, 8, 9) still only
  happens when the pool genuinely forces it — and in that case the existing
  between-team balancing (`raw_skill_diff`) still picks the fairest split available
  (e.g. 2&8 vs 1&9 over 1&2 vs 8&9). Applies to both engines.
- **Sit-out fairness had two real bugs, both fixed:**
  - Ties used to break alphabetically by name every round, which systematically
    under-selected early-alphabet names for sit-out over a whole session. Ties now
    break via the run's seeded RNG instead — still fully reproducible per-seed, no
    longer biased toward any particular name.
  - The "avoid picking the same person twice in a row" nudge used to be a flat +1
    for one round only, which wasn't enough to stop someone sitting out several
    rounds in a row whenever their sit-out count started well below everyone
    else's. It's now `consecutive_sit_outs`-based and escalates the longer a
    player's streak runs (+1, +2, +3, ...), while staying soft — it still yields if
    truly unavoidable, consistent with `mixed_mode`'s auto-relax philosophy.

See `tests/` for automated coverage of both fixes (`pip install -r requirements-dev.txt`, then `python3 -m pytest`).

## Known prototype limitations (intentional, out of scope for this pass)
- Court preferences (priority 6) and "requested number of sets" (priority 7) are not
  implemented — no data for them yet, scored as always-0/disabled per the default preset.
- Sit-out history is seeded with random plausible values for demo purposes only, so
  priority 1 has a visible effect from round 1. In the real system this starts at 0
  and accrues from actual session data.
- Skill balance is greedy per-court (fills best court first), so later courts in a
  round can end up mildly imbalanced if earlier courts "used up" the best pairings —
  worth discussing with Warren whether a look-ahead/global optimization is worth the
  added complexity for V1, or whether greedy is good enough. The Prototype3 skill-spread
  fix reduces how often this matters (similar-rated players are grouped together before
  courts are filled) but doesn't eliminate it.
- Player identity is still tracked by name, not a unique ID (`partners_played`,
  `opponents_played` in `helpers.py`) — two players sharing a name would collide.
  Not addressed in this pass; flagged as a separate, pre-existing risk.
