# Open-field 1v1 — design sketch (proposal, not yet implemented)

**Status:** design sketch only. No code has been written for this. This
document exists so that discussion has a concrete shape to react to,
not to commit the team to a specific implementation yet.

## The idea

Instead of two players just sitting out a round, put them on a spare
court/space for an informal, untracked 1-on-1 game. It isn't a real
doubles set — no partner exists in a 1v1, and it shouldn't be printed
as a ranked/tracked match the way a court result is. But the two
players involved should have their sit-out fairness treated as if they
*had* played this round, not as an ordinary sit-out.

## Why this needs real design (not just a quick patch)

Today the round loop only knows two states per player: `playing` (on a
doubles court) or `sitting_out` (counted against their fairness
history). This feature needs a genuine third state — "played something,
but not a tracked doubles set" — that doesn't exist anywhere in
Prototype3 today. Confirmed by research: nothing resembling this exists
on this branch, and the closest concept on the separate
`backend-session-management` branch (`RoundEligibility.status`'s
`unavailable` state) solves the opposite problem — excluding a player
from fairness bookkeeping entirely, not crediting them for an untracked
game. There's no prior art to lean on here.

## Recommended design (a starting point, not a final answer)

**Where it fits in the round loop:** after `select_round_players`
produces `sitting_out`, before `_record_sit_outs` runs. Concretely:
peel candidates off the *front* of `sitting_out` — since
`select_round_players` already ranks that list by fairness
(`effective_count = sit_out_count + consecutive_sit_outs`, descending),
the front of `sitting_out` holds whichever sitters have *already* sat
out the most among the group forced to sit this round. Giving them the
open-field slot (instead of an arbitrary pick) reuses the existing
fairness ranking rather than inventing a second one.

**Trigger condition (opt-in):** a new toggle, following the exact
pattern of `mixed_mode`/`variety_mode` in `RunConfig`/`BasicRunInputs`
(`config.py`) — e.g. `open_field_mode: bool`, defaulting to `False` so
every existing test and current behavior is completely unaffected
unless a caller explicitly turns it on.

**Data shape:** a new list returned alongside the existing court
results — e.g. `open_field_matches: [{"players": [p1, p2]}]` — kept
structurally distinct from a doubles court dict (`{court, team_a,
team_b, players, team_a_sum, team_b_sum}`) rather than shoehorned into
the same shape, since it has no teams, no court number in the same
sense, and no skill-diff concept. `main.py`'s `print_round_summary` and
`write_results_csv` would need a small extension to display these
separately (e.g. "Open Field: PlayerX vs PlayerY").

**Bookkeeping:** the two players get `consecutive_sit_outs = 0` (same
reset as if they'd played) but their `sit_out_count` does **not**
increment. For transparency, `Player` would likely want a new
`open_field_count` field so reporting can distinguish "played N real
matches" from "had N open-field games" — cleaner than overloading an
existing field.

**Opponent tracking (already decided):** the pair gets added to each
other's `opponents_played` (reusing the existing `player_id`-keyed
mechanism from `algorithm.py`), so the algorithm can eventually try not
to repeat the same open-field pairing back-to-back — same philosophy as
doubles opponent variety. No partner tracking applies; there's no
partner in a 1v1.

**Engine coverage:** this logic sits at the round level (choosing who
plays vs sits out vs goes to open field), not inside per-court scoring
— so it shouldn't need duplicating between `algorithm.py` and
`algorithm_weighted.py`. A shared helper (similar in spirit to how
`refinement.py` is already shared by both engines) is the likely right
shape, but that needs confirming once real implementation starts.

## Open questions for Warren (needs an actual answer before building this)

1. **Odd leftover counts.** After courts are filled, there could be 1,
   2, or 3 leftover sit-out players depending on pool size and court
   count. With exactly 2, the pairing is obvious. What happens with 1
   (can't pair) or 3 (one player left over even after pairing two)? Do
   they just sit out normally?
2. **Multiple pairs.** If there are 4+ leftover players, should this
   form multiple open-field pairs (e.g. 2 pairs from 4 leftovers), or
   is it capped at one pair per round regardless?
3. **Selection nuance.** Is "whoever's already sat out the most among
   this round's sitters" the right selection rule, or did Warren have
   something more specific in mind (e.g. players who explicitly opted
   into casual games)?
4. **Format/reporting expectations.** Does Warren want this to show up
   on the printed schedule at all, or literally just be a verbal
   "go use court 5" with no paper trail? That changes how much of the
   output-formatting work is actually needed.
5. **Is this in scope for the current work, or a later phase?** Given
   it's a new feature idea relayed secondhand rather than a scoped
   requirement, worth confirming priority against everything else still
   open (the sit-out-ratio contract, court preferences, QR/print output).

## Rough scope if greenlit

Touches: `config.py` (new toggle), a new shared helper module, `main.py`
(both run paths), `helpers.py` (`Player.open_field_count`),
`print_round_summary`/`write_results_csv`, and a new test file covering:
normal pairing, odd leftover counts, the opt-in toggle defaulting to off
(zero behavior change for existing tests), and opponent-history updates
for the pair. Comparable in size to the skill-clustering refinement work
already done (`refinement.py` + `tests/test_skill_refinement.py`) — a
real but contained piece of work once the open questions above are
answered.
