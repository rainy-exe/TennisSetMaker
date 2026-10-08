# Algorithm adapter guide — Rai / Eric

Branch: `backend-integration-followups`.
[Quick start](backend-quickstart.md) runs the bundled
`Backend/Set_Algorithm/Prototype4`. No separate export or algorithm rewrite needed.

## Who handles what

`app/round_service.py` selects eligible players/courts, builds actual history,
calls `app/algorithm_adapter.py`, validates and saves the round.
Backend owns persistence, availability and overrides; algorithm owns matching/fairness.

Saturday Social generates **one round at a time**:
`generate -> optional override -> complete final -> history -> next round`.

## Engine input/output

The backend-facing method is:

```python
def generate(self, players, court_ids, round_number, mixed_mode, strict_format):
    # Return AlgorithmRound(assignments, sit_out_player_ids).
    ...
```

`players` contains `AlgorithmPlayer` objects from `app.algorithm_adapter`:

| Field | Meaning |
| --- | --- |
| `id`, `gender`, `rating` | Stable ID, M/F, skill 1–10 |
| `sit_out_count` | Actual sit-outs in completed final history |
| `partner_ids`, `opponent_ids` | Sets of stable IDs from that history |
| `rounds_played` | Actual playing-round count, **not eligible-round count** |
| `last_sit_out_round` | Last sit-out number in this session, or -1 |
| `consecutive_sit_outs` | Actual sit-out streak in this session |
| `session_sit_outs` | Actual sit-outs in this session only; resets in a new session |
| `recent_sit_outs`, `recent_rounds` | Sit-outs and attended rounds in the last 50 completed final outcomes |

`court_ids` is an ordered list of stable IDs, not Court objects.
Session court order is saved from priority/number; inactive courts are filtered
each round. Lower priority is preferred; mid-session priority edits do not
reorder its saved selection. `round_number` starts at 1; format flags are booleans.
Start time, duration and tea break stay in session metadata.

Players who have not yet arrived, are unavailable, inactive or missing skill
are excluded, not counted as sit-outs. Generation needs four eligible rated players and one
active selected court. Supported formats are open/mixed, not singles or same-gender-only.

Return these dataclasses:

```python
from app.algorithm_adapter import AlgorithmRound, CourtAssignment

# Replace these illustrative strings with stable IDs from the input.
result = AlgorithmRound(
    assignments=[
        CourtAssignment("court-id", ["player-1", "player-2"], ["player-3", "player-4"])
    ],
    sit_out_player_ids=["player-5"],
)
```

Every eligible player appears exactly once. Each court is used once, with two
teams of two. Expected playing count:
`floor(min(player_count, court_count * 4) / 4) * 4`.
Strict mixed requires one M and one F per team. Invalid output is rejected
before saving, not silently repaired.

## Current Prototype3 mapping

Fresh prototype Players are built for each call:

| Backend | Prototype3 |
| --- | --- |
| Stable `id` | Both `player_id` and `name` (avoids same-name collisions) |
| `rating`, gender and sit-out fields | Corresponding Player fields |
| `partner_ids` / `opponent_ids` | `partners_played` / `opponents_played` |
| `rounds_played` | `sessions_played` (legacy name) |
| Ordered `court_ids` | Result `court` maps to `court_ids[court - 1]` |

Adapter calls:

```python
select_round_players(temporary_players, num_to_play, rng=random.Random(seed))
assign_courts_for_round(playing, len(court_ids), 1 if mixed_mode else 0, 2)
```

`2` is the fixed current variety mode, not a custom weight preset.
Prototype3 does not receive `round_number` or `strict_format` directly;
the backend builds round history and validates strict mixed after matching.

Optional `TENNIS_ALGORITHM_PATH` points to compatible
`helpers.py`, `refinement.py`, `algorithm.py`.
Prototype4 from merged PR #25 is the default. The adapter passes the three new
history fields when the selected Player constructor accepts them; Prototype3
continues to work when explicitly selected for compatibility checks.
Errors: missing dependency 503 `algorithm_unavailable`; strict mixed failure
422 `no_valid_solution`; invalid output 502 `invalid_algorithm_result`.
Prototype3 has no separate structured no-solution result.

## Actual history

- Read **completed final** outcomes in the current club, including manual changes.
  Default window: `HISTORY_WINDOW_DAYS=120`, including past sessions.
- Generated snapshot stays unchanged; final is saved separately. An open round
  contributes no history and must be completed before the next generation.
- Departed partners/opponents remain in others' history.
- Last sit-out round and streak are session-local. Playing or a missed/unavailable
  round breaks the streak; unavailable is not a sit-out.
- Prototype4's recent window is **50 attended rounds per player**, even when
  those rounds are older than 120 days. Playing and actual sit-outs count as
  attendance; unavailable, not-arrived and unrated rounds do not. Reopened or
  uncompleted rounds contribute nothing until completed again. The backend
  supplies counts, not a ratio formula; Prototype4 owns newcomer padding and scoring.
- Algorithm-side Player mutations are temporary, never persistent history.

## Integration notes

1. **Dynamic ratio:** Prototype3 still uses `sit_out_count`. Prototype4 from PR #25
   now receives session and last-50-round counts from the backend. Its formula
   and client-facing behaviour still need team/client confirmation; this does
   selects the merged Prototype4, not the unmerged same-gender fix in PR #26.
2. **Court type/preference:** backend stores type and handles availability/order;
   engine receives IDs only. Richer court input/scoring needs an agreed contract.
3. **Future contract changes:** keep stable IDs and update the adapter together
   when changing Player constructors or output shape; routes should not import
   algorithm internals.
4. **Unchanged decisions:** variety mode stays at 2, formats stay open/mixed,
   and partner/opponent history stays club-wide over 120 days. Changing the
   default mode, adding same-gender semantics or resetting that history per
   session needs agreement first. Court preference scoring remains algorithm work.

Run `python -m pytest` for real Prototype3/4 adapter and override/history/availability tests.
Tests can inject `ALGORITHM_ENGINE`; normal startup uses the bundled engine.
The Prototype4 integration tests use the bundled directory and do not skip by
default. `TENNIS_PROTOTYPE4_PATH` optionally selects another reviewed checkout.
Run each prototype's own full suite in a separate process, as shown in README.
