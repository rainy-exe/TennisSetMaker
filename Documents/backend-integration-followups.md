# Backend integration follow-ups

These changes build on `environment` and Kate's existing Club/User/Invitation
models, role helpers and session actor provider. No second accounts system or
matching algorithm is introduced.

The original follow-up was backend-only. The later integration pass connects
the management pages now present on `environment` to the same JSON APIs,
without merging the still-open frontend PRs. Use the factory through `run.py`;
the old root `app.py` CSV demo is not the persistence/authentication implementation.

## Login and CSRF

`GET /csrf-token` creates an anonymous session and returns `csrf_token`. All
state-changing requests, including login, registration and invitation acceptance,
must send it as `X-CSRFToken`. Keep the session cookie on same-origin requests.
Login, registration and invitation acceptance rotate the session and return a
new token. Fetch a new token before subsequent writes. Logout is now
`POST /logout`, not GET.

`POST /login` expects JSON email/password, not URL query parameters.
Registration uses `POST /register`; the older `/api/signup` only checks reCAPTCHA
and does not create an account. Set a deployment-specific `SECRET_KEY` and
configure `RECAPTCHA_SECRET_KEY` when using reCAPTCHA.

Private account/business responses are not cached. SQLite foreign keys are
enabled on every production connection. The old fake-identity unit fixtures
explicitly retain their former unenforced-FK behavior; real integration tests
keep both CSRF and foreign keys enabled.

Kate's `/login-page` and `/signup-page` endpoints can render team-provided templates.
Until those templates are integrated, they return an explicit 503 JSON
`frontend_unavailable` error rather than an unhandled missing-template 500.
Login/signup/accounts/invitation screens remain frontend dependencies. Existing
dashboard, player/court/template and session pages are connected; the small
`base.html` compatibility shell can be replaced by the frontend team's layout.
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

An invitation lasts seven days. Creation returns the bearer `token` once and
`accept_endpoint: "/accept-invitation"`. This is a POST API endpoint, not a
browser invitation link. A client must send JSON `token` and `password` with
CSRF to accept it; no email is claimed to have been sent. Acceptance is one-use
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

Open/mixed formats and fixed variety mode 2 remain the current contract.
Same-gender mode awaits algorithm PR #26 and team confirmation; it is visibly
disabled instead of silently treating it as open. Cross-session partner/opponent
history still uses 120 days; the PR #26 discussion about resetting it needs
coordination before changing existing behaviour. Court drag-order UI, detailed
player statistics, account screens, invitation UI, result/QR screens and export
are not completed by this pass. No password-reset email is claimed to be sent.

## Migration safety

Fresh upgrade/downgrade/upgrade and metadata checks are tested. The Accounts
migration refuses **before DDL** when old opaque-ID records exist, because it
cannot safely invent a club/owner mapping. A regression test retains a completed
round's generated/final history byte-for-byte at the SQL dump level after refusal.
Databases already at the Accounts revision are unaffected.

Back up any pre-Accounts populated database and coordinate a real club/user ID
mapping with Kate before upgrading it. This is not an automatic legacy-data
conversion; do not delete data to bypass the check. Run this migration online,
not as an offline SQL export, so the preflight can inspect the target database.

## Remaining coordination

- Password reset/email delivery is not implemented/configured. The endpoint
  returns an explicit 503 and does not claim an email was sent.
- QR sharing needs a decision on authenticated-club access versus bearer access
  for players. Do not publish anonymous results by default.
- Account/management frontend integration belongs to the frontend workstream.
- Do not merge or send reviews without the user's confirmation.
