# Frontend API guide — Kimi / Lena

Branch: `backend-integration-followups`.
[Start the backend](backend-quickstart.md) through `run.py`, not the old
root `app.py` demo. This guide covers the API; frontend pages stay with your team.

The existing management pages now consume these same APIs. Player payloads also
support nullable `notes` (up to 4000 characters) and `court_preference` (Clay,
Hard, Grass, Synthetic); courts support nullable `cover`. Preferences are saved,
but are not yet used in algorithm scoring. Skill remains 1–10.

The live page is `/sessions/<session_id>` and renders persisted **final** results.
Overrides may send `expected_revision` alongside assignments/sit-outs; a stale
revision returns 409 `stale_round`. Login/signup/account screens and same-gender
mode still have separate team dependencies; no frontend PR is merged by this pass.

## 1. Login and requests

Use relative URLs on the **same origin** as Flask (or a same-origin dev proxy).
Separate ports and `file://` need extra setup; CORS is not configured.

Authentication uses cookies. Every write, including login, needs CSRF.
This helper keeps cookies and fetches a fresh token before each write:

```javascript
async function api(path, { method = "GET", body } = {}) {
  method = method.toUpperCase();
  const headers = { "Content-Type": "application/json" };
  if (!["GET", "HEAD"].includes(method)) {
    const tokenResponse = await fetch("/csrf-token", {
      credentials: "same-origin", cache: "no-store"
    });
    const token = await tokenResponse.json();
    if (!tokenResponse.ok) throw new Error(token.message || "Could not get CSRF token");
    headers["X-CSRFToken"] = token.csrf_token;
  }
  const response = await fetch(path, {
    method, headers, credentials: "same-origin", cache: "no-store",
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  const result = response.status === 204 ? null : await response.json();
  if (!response.ok) {
    const error = new Error(result?.message || "Request failed");
    error.status = response.status;
    error.code = result?.error;
    error.details = result?.details;
    throw error;
  }
  return result;
}
```

Use your form values and selected IDs in this example:

```javascript
const login = await api("/login", {
  method: "POST", body: { email, password }
});
const { players } = await api("/api/players");
const { courts } = await api("/api/courts");
const { session_templates } = await api("/api/session-templates");
const { session } = await api("/api/sessions", {
  method: "POST",
  body: {
    name: "Saturday Social", starts_at: "2026-10-03T13:00:00",
    template_id: selectedTemplateId, player_ids: selectedPlayerIds
  }
});
const { round } = await api(`/api/sessions/${session.id}/rounds/generate`, {
  method: "POST"
});
// Display round.final, not round.generated.
```

- `GET /check-session` returns `logged_in` and `user`:
  `id`, `email`, `role`, `club_id`. Access comes from the session, not
  a client-supplied role or club ID.
- `POST /register`: `email`, `password`, `club_name`; creates a new club
  and Owner. Include `recaptcha_token` when reCAPTCHA is configured.
  Existing-club members join by invitation. `/api/signup` only checks reCAPTCHA.
- `POST /logout` needs CSRF but returns a **302 redirect**, not JSON.
  Handle it separately from this helper.
- Account templates may return 503 `frontend_unavailable` until integrated.
  Password reset/email delivery and protected QR sharing are not ready.

## 2. Permissions

Roles are `owner`, `admin`, `staff`. Backend checks remain mandatory.

| Action | Owner / Admin | Staff |
| --- | --- | --- |
| Read players/skill, courts and templates | Yes | Yes |
| Create/edit player name and gender | Yes | Yes |
| Set skill, change active state or delete player | Yes | No |
| Manage courts/templates | Yes | No |
| Run sessions, live changes, generation and overrides | Yes | Yes |
| Reopen latest completed round | Yes | No |
| Manage members/invitations | Owner: Admin/Staff; Admin: Staff only | No |

Staff must omit `skill` and `active` from player writes. Their new players
have `skill: null`, `skill_ready: false` and cannot play until a manager rates
them. Staff skill visibility follows Kate's current code, pending team confirmation.
Owner cannot be removed or demoted.

## 3. Players, courts and templates

| Resource | Collection GET / POST | Item methods | Response key |
| --- | --- | --- | --- |
| Players | `/api/players` | GET / PATCH / DELETE `/api/players/<id>` | `players` / `player` |
| Courts | `/api/courts` | PATCH / DELETE `/api/courts/<id>` | `courts` / `court` |
| Templates | `/api/session-templates` | PATCH / DELETE `/api/session-templates/<id>` | `session_templates` / `session_template` |

POST returns 201; DELETE returns 204 and soft-deactivates. Keep stable IDs,
not display names or court numbers. Player lists default to active only
(`?include_inactive=true` includes the rest); court/template lists include inactive.

- Player body: `name`, `gender` (`M`/`F`), manager-only `skill`
  (integer 1–10 or null). PATCH accepts changed fields.
- Court body: `number` (positive, unique per club), `court_type` (e.g. hard/soft),
  optional `active` (default true), `priority` (non-negative, lower preferred).
- Similar names: 409 `duplicate_confirmation_required`; `details` is an array
  of candidates (`player_id`, `name`, `active`, `similarity`).
  Ask the user, then retry with `confirm_duplicate: true` if confirmed.
  Never auto-confirm; different people can share a name.

Template example:

```json
{
  "name": "Saturday Social",
  "number_of_rounds": 6,
  "session_duration_minutes": 180,
  "round_duration_minutes": 30,
  "start_time": "13:00",
  "round_format": "open",
  "strict_format": false,
  "tea_break_after_round": 3,
  "court_ids": ["<court-id-1>", "<court-id-2>"]
}
```

Formats: `open` or `mixed`; strict mixed needs one M and one F per team.
Open does not guarantee same-gender teams. Limits: rounds 1–50, total minutes
1–1440, round minutes 1–240; tea break is null or before the last round.
Durations/tea break are metadata, not timers. Templates are not algorithm-weight presets.

## 4. Session flow

**Create -> generate -> review/override -> complete -> generate next round.**

| Method / URL | Body / response |
| --- | --- |
| GET / POST `/api/sessions` | List `{sessions}` / create `{session}` (201) |
| GET / PATCH `/api/sessions/<id>` | Load saved session/rounds / edit draft configuration |
| POST `/api/sessions/<id>/players` | `{player_id}`, optional upcoming `joined_round` |
| PATCH `/api/sessions/<id>/players/<player-id>` | `{reason}`, optional upcoming `unavailable_from_round` |
| POST `/api/sessions/<id>/rounds/generate` | No body; 201 `{round}` |
| GET `/api/rounds/<id>` | Saved `{round}` |
| PUT `/api/rounds/<id>/final` | Full snapshot below; `{round}` |
| POST `/api/rounds/<id>/complete` | Lock round; `{round}` |
| POST `/api/rounds/<id>/reopen` | Manager only, no later round generated; `{round}` |

Create a session with `name`, ISO `starts_at`, `player_ids`, and a
`template_id`. Without a template, also supply `court_ids`,
`number_of_rounds`, `round_duration_minutes`; total minutes defaults to
their product. Other configuration fields match the template. Template
`start_time` does not supply a session date; timezone normalisation is not defined.

Select at least four unique active players and one active court. Generation
needs four eligible players with skill. Configuration locks after generation
starts; editing a template does not rewrite existing sessions.

For pairing, sit-out swaps or court moves, edit a copy of `round.final`
and PUT the **whole snapshot**:

```json
{
  "assignments": [
    {
      "court_id": "<court-id>",
      "team_a_player_ids": ["<player-id-1>", "<player-id-2>"],
      "team_b_player_ids": ["<player-id-3>", "<player-id-4>"]
    }
  ],
  "sit_out_player_ids": ["<player-id-5>"]
}
```

Every eligible player appears once, playing or sitting out. Courts must be
selected/active and used once, with two teams of two; strict mixed still applies.
Invalid changes leave saved results unchanged. Override validation does not
enforce the original court count.

`generated` stays unchanged; `final` is saved and `override_revision`
increases. After refresh, GET saved state and display **final**; do not regenerate.
Only completed final results feed the next round's history.
Round status: `generated -> completed`; session: `draft -> active -> completed`.

Late/unavailable changes apply to the next ungenerated round, not existing rounds.
Round `eligibility` entries contain `player_id`, `status`, `reason`;
statuses are `eligible`, `not_arrived`, `unavailable`, `inactive`,
`missing_skill`. Unavailable is not a sit-out. No return-to-available,
discard, cancel or automatic timer endpoint is provided.

## 5. Members and invitations

- `GET /api/users` -> `{users}`;
  `PATCH /api/users/<id>` with `{role: "admin"|"staff"}`;
  DELETE soft-deactivates and removes existing session access.
- `GET /api/invitations` -> `{invitations}`;
  POST with `{email, role}` -> 201 `{invitation, token, accept_endpoint}`;
  DELETE `/api/invitations/<id>` revokes a pending invitation.
- `POST /accept-invitation` with `{token, password}` plus CSRF/cookie
  -> 201, logs in. Invitations last seven days.
- Token is returned only at creation. `accept_endpoint` is a POST API,
  not a browser page/link. Your acceptance screen must submit it; no email
  is sent. Do not log or publicly expose tokens.

All resources use the logged-in club; foreign-club IDs return 404.

## Errors

Usually `{error, message, details?}`; keep selections and show `message`.

| Status | Usual action |
| --- | --- |
| 400 | Fix validation; refresh token for `csrf_failed` |
| 401 / 403 / 404 | Login required / permission denied / missing or foreign-club ID |
| 409 | Confirm duplicate, finish current round, or respect a locked state |
| 410 / 422 | Expired invitation / not enough eligible players or courts, or invalid strict mixed result |
| 502 / 503 | Algorithm result rejected / dependency unavailable |

After a network timeout, GET saved state before retrying a write: it may already
have succeeded. Render names as text, not raw HTML.
