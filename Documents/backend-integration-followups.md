# Backend integration follow-ups

These changes build on `environment` and Kate's existing Club/User/Invitation
models, role helpers and session actor provider. No second accounts system or
matching algorithm is introduced.

## Login and CSRF

`GET /csrf-token` creates an anonymous session and returns `csrf_token`. All
state-changing requests, including login, registration and invitation acceptance,
must send it as `X-CSRFToken`. Keep the session cookie on same-origin requests.
Login, registration and invitation acceptance rotate the session and return a
new token. Fetch a new token before subsequent writes. Logout is now
`POST /logout`, not GET.
Private account/business responses are not cached. SQLite foreign keys are
enabled on every production connection. The old fake-identity unit fixtures
explicitly retain their former unenforced-FK behavior; real integration tests
keep both CSRF and foreign keys enabled.

The working pages are `/login-page`, `/signup-page`, `/invitation-page?token=...`
and `/dashboard`. Login keeps Kimi's two-panel layout from PR #23, with the form
connected to Kate's JSON login rather than GET navigation to a dashboard. The
court-green panel deliberately does not copy an image from another branch.
The other account pages are small functional integration pages. Existing
player/court/template layouts now use the same factory, database and JSON APIs.

## Management page integration

`/players`, `/courts` and `/templates` reuse the existing frontend classes and
stylesheet with the missing shared `base.html`. Page views only read current-club
records; writes still use the existing `/api` endpoints with CSRF. Stable IDs are
kept as strings. Duplicate player names require a separate explicit confirmation.
Player removal is deactivation, not destruction of session history.

`/sessions/create` can use a saved template. `/sessions/<id>` displays saved final
rounds, keeps original generated results in an audit section, and connects live
availability changes, manual player/sit-out swaps, completion and permitted reopen
to the existing backend. Refresh reloads saved data, not a CSV or browser store.
There is no auto-generation that overwrites an unfinished round. Unsaved edits
must be saved before completing a round.

`/accounts` is a small integration page for existing team/invitation APIs. It
does not import or replace Kimi's new unmerged accounts page in PR #23. That page
can consume the same APIs later; display-name and last-login fields are not yet
in Kate's User model and must not be fabricated.

Root `app.py` is now a compatibility entry point for the same factory as `run.py`,
not the old CSV-backed in-memory demo. Existing private/client CSVs are not loaded.
No frontend stylesheet, account model or algorithm source was replaced. Mock
fields that have no persistence contract (player notes, individual court
preferences, court shade, custom template-only priorities) are not editable fake
controls. Same-gender-only format is not silently mapped to open doubles.

These pages are functional integration screens, not completion of every frontend
interaction. Court moves are supported by the round API but have no drag/drop UI;
the current screen allows player/pair/sit-out swaps on the generated courts.

Registration uses `/register`; the older `/api/signup` only checks reCAPTCHA and
does not create an account. Set both `RECAPTCHA_SECRET_KEY` and
`RECAPTCHA_SITE_KEY` when using reCAPTCHA. Set a deployment-specific `SECRET_KEY`.

## Team APIs

| Endpoint | Access / behavior |
| --- | --- |
| `GET /api/users` | Owner/Admin, current club only; never password hashes |
| `PATCH /api/users/<id>` | JSON `role`; existing removal/invitation helpers enforce both target and new role |
| `DELETE /api/users/<id>` | Soft deactivation; cannot remove Owner; preserves round/session audit FKs |
| `GET /api/invitations` | Owner/Admin, current club; does not expose bearer tokens |
| `POST /api/invitations` | JSON `email`, `role`; Owner invites Admin/Staff, Admin invites Staff |
| `DELETE /api/invitations/<id>` | Revokes a pending invitation by expiring it |

An invitation lasts seven days. Creation returns the bearer token and a relative
`accept_path` once for the organiser to share. No email is claimed to have been
sent. Acceptance is one-use and atomic; revoked/expired invitations, disabled
clubs and inactive/unauthorised inviters are rejected. Removed members lose
existing session access and their pending invitations are invalidated.

Staff skill visibility follows Kate's current policy (read allowed, edit denied)
pending team confirmation against the older meeting decision. Staff cannot
manage team members, courts/templates, delete players or reopen completed rounds.

## Algorithm and actual history

The default algorithm path is the bundled `Backend/Set_Algorithm/Prototype3`.
`TENNIS_ALGORITHM_PATH` can still point to another compatible checkout. The
adapter loads helpers/refinement/algorithm without leaving global bare-import
aliases behind, passes stable `player_id` and a private `random.Random`, and
creates fresh algorithm Player instances for every call. Tests can set
`ALGORITHM_RANDOM_SEED`; production leaves it unset.

History comes only from completed **final** outcomes, including manual changes.
Former partners/opponents remain in history even after they leave the eligible
pool. Consecutive actual sit-outs are counted within the current session; playing
or a missed/unavailable round breaks that streak. The existing backend facts
remain the source of truth; algorithm-side mutations are discarded.

The algorithm still consumes `sit_out_count`, not a final dynamic sit-out ratio.
Court type/preference scoring remains a Rai/Eric dependency. These changes do not
implement arbitrary weights, a new matching algorithm or open-field 1v1.

## Migration safety

Fresh upgrade/downgrade/upgrade and metadata checks are tested. The Accounts
migration now refuses **before DDL** when old opaque-ID records exist, because
it cannot safely invent a club/owner mapping. A regression test retains a
completed round's generated/final history byte-for-byte at the SQL dump level
after refusal. Databases already at the Accounts revision are unaffected.

Back up any pre-Accounts populated database and coordinate a real club/user ID
mapping with Kate before upgrading it. This is not an automatic legacy-data
conversion; do not delete data to bypass the check. Run this migration online,
not as an offline SQL export, so the preflight can inspect the target database.

## Remaining coordination

- Real reset email delivery is not implemented/configured. The password-help
  page now says so instead of falsely claiming an email was sent.
- QR sharing requires a decision on authenticated-club access versus bearer
  access for players. Do not publish anonymous results by default.
- Kimi's unmerged accounts/landing screens, full frontend polish and individual
  player court-preference persistence still need team coordination.
- These changes are local for review; do not merge PR #7/#23 or push without
  the user's confirmation.
