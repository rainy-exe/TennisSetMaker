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
- `refinement.py` — bounded local-search pass that flattens the greedy fill's court-position skill-spread gradient; shared by both engines.
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

Two bugs fixed after review with the team, plus three follow-up fixes found
during deeper testing (identity, input validation, and the court-position
skill gradient) — five in total:

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
- **Player identity for history tracking no longer collides on shared names.**
  `partners_played`/`opponents_played` used to be keyed by `player.name` (a
  string), so two distinct real people sharing a name could get falsely
  flagged as repeat partners/opponents. `Player` now has a dedicated
  `player_id` field that those sets key on instead — it defaults to `name`
  when a caller doesn't supply one (every existing call site keeps working
  unchanged), but a real deployment should pass a real unique ID (e.g. a
  database row ID) to fully close the gap for CSV-loaded players. The
  `backend-session-management` branch already works around the original
  limitation today by substituting its own DB row IDs into the prototype's
  `name` field — this fix doesn't touch that branch, it just gives
  Prototype3 a clean field to receive a real ID directly, without
  overloading `name`, once a caller supplies one.
- **`Player` now rejects an invalid rating at construction time.**
  `raw_skill_diff`/`raw_skill_spread` would previously crash with an
  obscure `TypeError` deep inside a scoring loop if any player's rating
  was `None` or otherwise non-numeric. `Player.__post_init__` now raises a
  clear `ValueError` naming the offending player immediately on
  construction instead — provisional/unrated players must be filtered out
  by the caller before constructing a `Player`.
- **Skill balance's court-position degradation is reduced, not eliminated.**
  The greedy per-court fill always fills court 1 first, so it
  systematically front-loads the tightest-clustered foursome onto the
  earliest courts — measured on the real dataset at 60 players/15 courts:
  courts 1-7 averaged skill spread 0.82, courts 9-15 averaged 2.42 (a gap
  of +1.60). A bounded local-search refinement pass (`refinement.py`) now
  runs after the greedy fill each round, swapping players between courts
  to shrink that gap — measured result: gap down to +1.22 (~24% smaller),
  at negligible extra cost (151.9s vs 152.2s for the same 20-round run).
  It's deliberately conservative: a court that's already perfectly
  clustered (spread 0) is never disturbed, and a swap is only ever
  accepted if it doesn't regress format/partner/opponent quality
  (priorities 2-4) on either court touched — skill balance stays strictly
  below those in the priority hierarchy, so this pass can only spend its
  own budget, never borrow against a higher priority. The residual gap is
  a real ceiling of this search's move vocabulary (single pairwise swaps
  between two courts at a time) rather than an iteration-budget limit —
  raising the iteration cap from 5 to 30 produced an identical result, so
  closing the gap further would need a richer neighborhood (e.g. chained
  multi-court moves), not just a bigger budget. Applies to both engines.
  See `tests/test_skill_refinement.py` for the automated non-regression
  checks and a smaller-scale (24p/6c) version of the court-position
  measurement; the exact 60p/15c numbers quoted above come from a one-off
  manual diagnostic, not the automated suite, since running 40 rounds at
  full scale is too slow for every test run.

See `tests/` for automated coverage of all five fixes (`pip install -r requirements-dev.txt`, then `python3 -m pytest`).

## Known prototype limitations (intentional, out of scope for this pass)
- Court preferences (priority 6) and "requested number of sets" (priority 7) are not
  implemented — no data for them yet, scored as always-0/disabled per the default preset.
- Sit-out history is seeded with random plausible values for demo purposes only, so
  priority 1 has a visible effect from round 1. In the real system this starts at 0
  and accrues from actual session data.
- Skill balance is still greedy-then-refined, not a full global optimization —
  the local-search refinement pass (see "Changes in Prototype3" above) shrinks
  the measured court-position gradient by about a quarter (60p/15c: +1.60 down
  to +1.22) without needing a full rewrite, but it doesn't eliminate it. The
  residual gap is a ceiling of the search's move vocabulary (single pairwise
  swaps between two courts at a time, one per iteration) — confirmed by
  testing that raising the iteration cap well beyond the current default
  produces an identical result, so a bigger budget alone won't close it
  further. Worth revisiting with Warren/the team if a real session needs a
  smaller gap than this — the next lever would be a richer move vocabulary
  (e.g. chained multi-court swaps in one step), not just more iterations.
- Player identity for the algorithm's own history tracking is resolved (see
  the `player_id` fix above) *provided* the caller supplies a real unique ID.
  `load_players_from_csv`/`pull_random_players` still have no ID column in
  the CSV format, so `player_id` silently falls back to `name` for
  CSV-loaded players — a real deployment (or a future CSV format revision)
  needs to supply a real ID to fully close the gap for that path specifically.
