# Backend integration follow-ups

These changes build on `environment` and Kate's existing Club/User/Invitation
models, role helpers and session actor provider. No second accounts system or
matching algorithm is introduced.

The original follow-up was backend-only. The later integration pass connects
the management pages now present on `environment` to the same JSON APIs,
using their existing layout and controls. Use the factory through `run.py`;
the old root `app.py` CSV demo is not the persistence/authentication implementation.

## Login and CSRF

`GET /csrf-token` creates an anonymous session and returns `csrf_token`. All
state-changing requests, including login, registration and invitation acceptance,
must send it as `X-CSRFToken`. Keep the session cookie on same-origin requests.
Login, registration and invitation acceptance rotate the session and return a
new token. Fetch a new token before subsequent writes. Logout is now
`POST /logout`, not GET.

`POST /login` expects JSON email/password, not URL query parameters.
Registration uses `POST /register` with `full_name`, email, password and club name,
then requires login; the older `/api/signup` only checks reCAPTCHA
and does not create an account. Set a deployment-specific `SECRET_KEY` and
configure `RECAPTCHA_SECRET_KEY` when using reCAPTCHA.

Private account/business responses are not cached. SQLite foreign keys are
enabled on every production connection. The old fake-identity unit fixtures
explicitly retain their former unenforced-FK behavior; real integration tests
keep both CSRF and foreign keys enabled.

Kate's `/login-page`, `/signup-page` and `/join` now render the team templates.
Missing templates still return 503 `frontend_unavailable`, not an unhandled 500.
Dashboard, player/court/template, session/history and account pages are connected
to persisted club-scoped data. Accounts uses the existing team/invitation APIs;
no second member-management service is introduced.
All management routes require authentication and reuse club/role checks.

## Team APIs

| Endpoint | Access / behavior |
| --- | --- |
| `GET /api/users` | Owner/Admin, current club only; never password hashes |
| `PATCH /api/users/<id>` | JSON `role`; existing helpers enforce target and new role |
| `DELETE /api/users/<id>` | Soft deactivation; cannot remove Owner; preserves audit FKs |
| `GET /api/invitations` | Owner/Admin, current club; does not expose bearer tokens |
| `POST /api/invitations` | JSON `email`, `role`; Owner invites Admin/Staff, Admin invites Staff |
| `DELETE /api/invitations/<id>` | Revokes a pending invitation by expiring it |

An invitation lasts seven days. Kate's sender emails `/join?token=...`; API
responses never contain bearer tokens. Creation requires SMTP configuration and
returns `{invitation, message}` after submission. Missing/failing SMTP returns
502 and expires the unsent invitation so it does not block retry. Acceptance
sends JSON `token`, `full_name` and `password` with CSRF and is one-use
and atomic. Revoked/expired invitations, disabled clubs and inactive/unauthorised
inviters are rejected. Removed members lose existing session access, and their
pending invitations are invalidated.

Staff skill visibility follows Kate's current policy (read allowed, edit denied)
pending team confirmation against the older meeting decision. Staff cannot
manage team members, courts/templates, delete players or reopen completed rounds.

## Algorithm and actual history

The default algorithm path is the bundled `Backend/Set_Algorithm/Prototype4`.
`TENNIS_ALGORITHM_PATH` can still point to another compatible checkout. The
adapter loads helpers/refinement/algorithm without leaving global bare-import
aliases behind, passes stable `player_id` and a private `random.Random`, and
creates fresh algorithm Player instances for every call. Tests can set
`ALGORITHM_RANDOM_SEED`; production leaves it unset.

History comes only from completed **final** outcomes, including manual changes.
Former partners/opponents remain in history even after they leave the eligible
pool. Consecutive actual sit-outs are counted within the current session; playing
or a missed/unavailable round breaks that streak. Persisted backend facts remain
the source of truth; algorithm-side mutations are discarded.

The adapter passes `session_sit_outs`, `recent_sit_outs` and `recent_rounds` from
completed final outcomes. Prototype4 owns the last-50-round ratio calculation.
Court type/preference scoring remains a Rai/Eric dependency. These changes do not
implement arbitrary weights, a new matching algorithm or open-field 1v1.

## Connected pages and current limits

Dashboard and management screens load club-scoped persisted data, not static
rosters. Session creation saves a real session then generates through Prototype4;
if generation fails, the saved session can be reopened and retried. The live
page displays final assignments and supports pairing/sit-out edits, completion,
next-round generation, late arrival and remainder unavailability. Reloading uses
the persisted final state; completed rounds are locked. Overrides send an
expected revision and stale edits return 409 instead of overwriting a newer save.

Migration `b7d482f19c06` adds nullable player notes/court preference and court
cover, without rebuilding parent tables or changing historical assignments.
The established 1–10 skill range is retained; the proposed 1–7 restriction was
not consistent with the existing API, migrations or stored records.

Open/mixed/same-gender formats and fixed variety mode 2 are the current contract.
The adapter passes gender mode 0/1/2 to both Prototype4 selection and assignment.
Strict same-gender requires same-gender partners but permits MM versus FF spares.
Cross-session partner/opponent
history still uses 120 days; the PR #26 discussion about resetting it needs
coordination before changing existing behaviour. Court drag-order UI, detailed
player statistics and export
are not completed by this pass. Kate's QR result route is retained and tested
against final assignments, not replaced. No password-reset email is claimed to be sent.

## Migration safety

Fresh upgrade/downgrade/upgrade and metadata checks are tested. The Accounts
migration refuses **before DDL** when old opaque-ID records exist, because it
cannot safely invent a club/owner mapping. A regression test retains a completed
round's generated/final history byte-for-byte at the SQL dump level after refusal.
Databases already at the Accounts revision are unaffected.

Migration `c82e741bd509` expands the session/template format checks to same-gender.
It follows Kate's QR revision `187dfc1e85e4`, keeping one migration head.
SQLite copies both referenced tables in one transaction, checks foreign keys and
restores enforcement; failure rolls back both copies. Generated/final records
are retained. Downgrade refuses same-gender records or populated account names,
rather than discarding them. Back up the database before applying migrations.

Migration `d947ef8621ab` backfills missing club QR tokens. Existing tokens stay
unchanged; the data-only downgrade does not invalidate shared links. The model
uses the same named unique index as Kate's migration. `qrcode[pil]` is declared
in requirements so a fresh installation can start and render QR images.

Kate's CSV handler and form are retained. Truncated rows are safely skipped,
malformed CSV rejects the whole file before writes, and tests cover permission,
CSRF, duplicates, club isolation and transaction failure. Bulk import skips
potential namesakes; individual CRUD still allows explicit confirmation.

Back up any pre-Accounts populated database and coordinate a real club/user ID
mapping with Kate before upgrading it. This is not an automatic legacy-data
conversion; do not delete data to bypass the check. Run this migration online,
not as an offline SQL export, so the preflight can inspect the target database.

## Remaining coordination

- Password reset is not implemented. The endpoint
  returns an explicit 503 and does not claim an email was sent.
- Kate's QR route uses an unguessable permanent bearer link, not login. Sharing,
  revocation and whether draft rounds should be visible still need team approval
  before deployment. Existing latest-created-session selection is unchanged.
- Phone access is not verified. A QR pointing at localhost cannot reach another
  computer: configure `PUBLIC_BASE_URL` for deployment or a reachable test LAN.
- Real invitation delivery still needs deployment SMTP configuration and testing.
- Kate's CSV import was pushed in `3315402`; no competing importer was added.
- The client-facing user guide stays with the frontend team.
- Do not merge or send reviews without the user's confirmation.
