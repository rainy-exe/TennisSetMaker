# simulation_test

Round-based mock test harness. Calls the REAL, unmodified `algorithm.py` /
`helpers.py` in the sibling `Prototype3` folder -- not a reimplementation.

## player_id format

`roster.csv`'s `player_id` column is a UUID4 string, matching
`app/models.py`'s `Player.id` in the backend branch exactly
(`db.Column(db.String(36), primary_key=True, default=new_id)`, where
`new_id()` is `str(uuid.uuid4())`). Chosen deliberately so results shown to
the team look like real backend player IDs, not an invented test-only
scheme -- no format translation needed if this gets compared against
backend output later.

## What's already filled in

`roster.csv` is already built from the real client roster (`NTC_ratings_data.csv`,
names already truncated to first name + last initial by the client) with a
UUID4 `player_id` generated per player and `starting_sit_out_count=0` for
everyone (clean-slate test). `signups.csv` lists every player signed up
for round 1, so round 1 runs immediately with no editing.

You still decide:
- `--num-courts`, matching however many courts you're testing with.
- `signups.csv` rows for round 2 onward -- copy the pattern
  (`round_number,player_id`), list whoever's playing that round. Not
  everyone has to appear in every round.
- Adding a new player mid-season: add a row to `roster.csv` (generate a
  UUID for them -- `python3 -c "import uuid; print(uuid.uuid4())"` ) and
  list them in `signups.csv` from whichever round they join.

## Running it

From inside this folder:

```
python3 generate_round.py --round 1 --num-courts 4 --prototype-dir ../Prototype3
python3 generate_round.py --round 2 --num-courts 4 --prototype-dir ../Prototype3
```

One call per round, in order. Each call reads/updates `state.json`
(created automatically on round 1) and appends to
`results_for_client.csv` -- that's the file to share with the team/Warren,
sortable by `Round` then `Court` in Excel.

## Court preference

`court_pref` is carried through into `results_for_client.csv` for
visibility only. `algorithm.py`'s `raw_court_pref_score()` always returns
0 right now -- it doesn't affect court/team assignment yet. Say if you
want that wired in before the next test round.
