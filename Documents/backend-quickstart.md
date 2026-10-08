# Backend quick start

Branch: `backend-integration-followups`. Python 3.11+ (tested: 3.12).
Use a separate checkout if you have local work; no branch merge is needed.

## 1. Set up a separate checkout

```bash
git clone --branch backend-integration-followups --single-branch https://github.com/rainy-exe/TennisSetMaker.git TennisSetMaker-backend-test
cd TennisSetMaker-backend-test
```

Already on this branch? Skip the clone. Private-repo access is required.

Git Bash on Windows or Terminal on macOS/Linux:

```bash
python3 -m venv .venv  # use python instead if that is your Python 3 command
if [ -f .venv/Scripts/activate ]; then
  source .venv/Scripts/activate
else
  source .venv/bin/activate
fi
python -m pip install -r requirements.txt
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
export DATABASE_URL="sqlite:///quickstart-$(python -c 'import uuid; print(uuid.uuid4().hex)').db"
python -m flask --app run.py db upgrade
python -m flask --app run.py run --host 127.0.0.1 --port 5000
```

PowerShell alternative, from the same repository folder:

```powershell
python -m venv .venv
# Using the interpreter directly does not need script execution-policy changes.
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:SECRET_KEY = & .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
$env:DATABASE_URL = "sqlite:///quickstart-$([guid]::NewGuid().ToString('N')).db"
.\.venv\Scripts\python.exe -m flask --app run.py db upgrade
.\.venv\Scripts\python.exe -m flask --app run.py run --host 127.0.0.1 --port 5000
```

- Keep this terminal open. The commands create a fresh database under `instance`.
  Back up existing data and check [migration safety](backend-integration-followups.md#migration-safety)
  before upgrading a populated database; do not delete it to bypass migration checks.
- Use `run.py`, not root `app.py`. Prototype4 is bundled and is the default; no export/path setup needed.
- Keep `SECRET_KEY` stable across restarts; never commit secrets or deploy the default.
- The demo assumes local registration without reCAPTCHA configured. If configured,
  registration also needs a valid `recaptcha_token`.

## 2. Run a session through the API

Activate the same environment in a second terminal. Save the Python block below
as a local script **outside the repository**, then run `python path/to/demo.py`
(or `.\.venv\Scripts\python.exe path/to/demo.py` in PowerShell). In Git Bash,
you can instead wrap the block with `python - <<'PY'` and a closing `PY`.

The demo uses real login/cookies/CSRF and synthetic data. It checks saved pairing
and sit-out changes, reload, late arrival, early departure and round two.
Run only against a local test server.

<details>
<summary>Full Python demo (click to expand)</summary>

```python
import copy
import json
import os
import secrets
from datetime import datetime
from http.cookiejar import CookieJar
from urllib.request import HTTPCookieProcessor, Request, build_opener
from uuid import uuid4

BASE = os.environ.get("SETMAKER_URL", "http://127.0.0.1:5000")
client = build_opener(HTTPCookieProcessor(CookieJar()))


def call(method, path, body=None):
    headers = {"Content-Type": "application/json"}
    if method not in {"GET", "HEAD"}:
        # Fetch before each write so login's rotated session is handled too.
        headers["X-CSRFToken"] = call("GET", "/csrf-token")["csrf_token"]
    request = Request(
        BASE + path,
        data=None if body is None else json.dumps(body).encode(),
        headers=headers,
        method=method,
    )
    with client.open(request, timeout=30) as response:
        return None if response.status == 204 else json.load(response)


email = f"demo-{uuid4().hex}@example.invalid"
password = secrets.token_urlsafe(24)
call("POST", "/register", {
    "email": email, "password": password, "club_name": "Synthetic demo club"
})
client = build_opener(HTTPCookieProcessor(CookieJar()))
account = call("POST", "/login", {"email": email, "password": password})
assert account["user"]["role"] == "owner"
assert call("GET", "/check-session")["logged_in"]

names = ["Alex", "Ben", "Chris", "David", "Emma", "Fiona", "Grace", "Helen", "Iris"]
players = [
    call("POST", "/api/players", {
        "name": name, "gender": "M" if index < 4 else "F", "skill": 5
    })["player"]
    for index, name in enumerate(names)
]
courts = [
    call("POST", "/api/courts", {
        "number": number, "court_type": "hard", "priority": number - 1
    })["court"]
    for number in (1, 2)
]
template = call("POST", "/api/session-templates", {
    "name": "Saturday Social", "number_of_rounds": 6,
    "session_duration_minutes": 180, "round_duration_minutes": 30,
    "start_time": "13:00", "round_format": "open", "strict_format": False,
    "tea_break_after_round": 3, "court_ids": [court["id"] for court in courts]
})["session_template"]
session = call("POST", "/api/sessions", {
    "name": "Saturday demo", "template_id": template["id"],
    "starts_at": datetime.now().replace(microsecond=0).isoformat(),
    "player_ids": [player["id"] for player in players]
})["session"]
round1 = call("POST", f"/api/sessions/{session['id']}/rounds/generate")["round"]
original = copy.deepcopy(round1["generated"])
final = copy.deepcopy(round1["final"])
first, second = final["assignments"]
first["team_a_player_ids"][1], second["team_a_player_ids"][1] = (
    second["team_a_player_ids"][1], first["team_a_player_ids"][1]
)
first["team_a_player_ids"][0], final["sit_out_player_ids"][0] = (
    final["sit_out_player_ids"][0], first["team_a_player_ids"][0]
)
saved = call("PUT", f"/api/rounds/{round1['id']}/final", final)["round"]
reloaded = call("GET", f"/api/rounds/{round1['id']}")["round"]
assert reloaded["final"] == saved["final"]
assert reloaded["generated"] == original
assert reloaded["override_revision"] == 1
call("POST", f"/api/rounds/{round1['id']}/complete")

late = call("POST", "/api/players", {
    "name": "Late Player", "gender": "F", "skill": 5
})["player"]
call("POST", f"/api/sessions/{session['id']}/players", {"player_id": late["id"]})
departed = first["team_a_player_ids"][0]
call("PATCH", f"/api/sessions/{session['id']}/players/{departed}", {
    "reason": "Left early"
})
round2 = call("POST", f"/api/sessions/{session['id']}/rounds/generate")["round"]
statuses = {item["player_id"]: item["status"] for item in round2["eligibility"]}
assert statuses[late["id"]] == "eligible"
assert statuses[departed] == "unavailable"
assigned = [
    player_id for assignment in round2["final"]["assignments"]
    for team in ("team_a_player_ids", "team_b_player_ids")
    for player_id in assignment[team]
] + round2["final"]["sit_out_player_ids"]
assert departed not in assigned
assert len(assigned) == len(set(assigned)) == 9
assert len(call("GET", f"/api/sessions/{session['id']}")["session"]["rounds"]) == 2
print("OK: login, saved pairing/sit-out override, reload, late arrival and next round")
```

</details>

Do not use private ratings CSV data. Demo credentials are generated, not printed or committed.

## 3. Connect your part

- **Kimi/Lena:** [API guide](backend-session-api.md) — login/CSRF, routes, final-round flow.
- **Rai/Eric:** [adapter contract](backend-algorithm-contract.md) — IDs, actual history, ratio/courts.

For backend checks, install `requirements-dev.txt` in the same environment, then
run `python -m pytest` and `python -m flask --app run.py db check` with the same
`DATABASE_URL` used for the upgrade.
