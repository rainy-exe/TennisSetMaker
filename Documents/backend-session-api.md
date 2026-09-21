# Session-management backend

This module extends Kate's Flask application factory. It deliberately does not
define `User`, `Club`, or `Role` database tables. Authentication is supplied by
`CURRENT_ACTOR_PROVIDER`, which must return `app.permissions.CurrentActor` with
an opaque actor ID, club ID, and mapped role.

## Confirmed behaviour

- Saturday Social sessions generate one round at a time.
- Players and courts are soft-deactivated, and all relationships use stable IDs.
- Same or similar player names produce a warning. The caller may create a
  distinct same-name player with `confirm_duplicate: true`.
- Owner and Administrator can manage skill, courts, and templates. User can
  manage non-skill player fields and run sessions, live changes, generation,
  and current-round overrides. Only Owner and Administrator can reopen a
  completed round.
- A User-created player has provisional (`null`) skill. Skill is not returned
  to Users, and a provisional player is excluded from generation until an
  Owner or Administrator supplies the rating.
- Late players join an upcoming round. Remainder-unavailable players are
  excluded from future eligible pools and are recorded as unavailable, not as
  ordinary sit-outs.
- Each generated round stores an immutable `generated` snapshot and a separate
  `final` snapshot. Overrides replace only `final`. Completed final outcomes
  drive later partner, opponent, and sit-out history.
- Completed rounds are locked. A manager may reopen only the latest round;
  reopening an earlier round after later generation is rejected so downstream
  history cannot silently become stale.

## Integration boundaries

The app refuses protected API requests until `CURRENT_ACTOR_PROVIDER` is
configured. Tests install a fake provider; the future Accounts/Club/Role work
should replace only this provider and later add formal club foreign keys.

`ALGORITHM_ENGINE` may be injected directly. Otherwise `ALGORITHM_PATH` (or
the `TENNIS_ALGORITHM_PATH` environment variable) points to the current
`Backend/Set_Algorithm/Prototype3` directory. The adapter rebuilds temporary
prototype players from completed final history on each round, uses player IDs
in the prototype's name-based identity fields, and maps its numeric court
positions onto selected stable court IDs in priority order.

The current Prototype3 algorithm accepts `sit_out_count`, not a dynamic ratio.
The database retains eligible/unavailable facts per round so the adapter can be
updated without a schema rewrite. Rai/Eric still need to confirm and implement
the final dynamic sit-out-ratio contract. Prototype3 also lacks native court
type/preference input; the current adapter honours persistent court priority by
ordering the available court IDs before assignment.

## Main API routes

- `/api/players` and `/api/players/<id>`: player CRUD/deactivation
- `/api/courts` and `/api/courts/<id>`: court CRUD/deactivation
- `/api/session-templates`: persistent session presets
- `/api/sessions` and `/api/sessions/<id>`: create/read/edit draft sessions
- `/api/sessions/<id>/players`: add a late player
- `/api/sessions/<id>/players/<player-id>`: unavailable for the remainder
- `/api/sessions/<id>/rounds/generate`: generate the next round atomically
- `/api/rounds/<id>/final`: validate and persist a manual override
- `/api/rounds/<id>/complete` and `/api/rounds/<id>/reopen`: round lifecycle

Run `flask db upgrade` before starting against a persistent database. Run the
test suite with `python -m pytest`. To exercise the real algorithm adapter,
export Prototype3 and set `TENNIS_ALGORITHM_PATH` before running the contract
test.
