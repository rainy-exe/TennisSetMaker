# Backend quick start

Use Git Bash on Windows or Terminal on macOS. This guide runs the existing
backend and its main session flow.

## 1. Set up

```bash
git fetch origin
git switch backend-session-management

PYTHON=python3
command -v "$PYTHON" >/dev/null 2>&1 || PYTHON=python
"$PYTHON" -m venv .venv

if [ -f .venv/Scripts/activate ]; then
  source .venv/Scripts/activate
else
  source .venv/bin/activate
fi

python -m pip install -r requirements.txt
export DATABASE_URL="sqlite:///quickstart-$(date +%s).db"
python -m flask --app run.py db upgrade
```

Prototype3 is currently on `main`. Keep it in a separate worktree; do not
merge it into this branch.

```bash
if [ ! -d ../TennisSetMaker-main ]; then
  git worktree add ../TennisSetMaker-main origin/main
fi
export TENNIS_ALGORITHM_PATH="$(cd ../TennisSetMaker-main/Backend/Set_Algorithm/Prototype3 && pwd)"
```

## 2. Start the local API

Until Accounts is connected, use this local-only Owner. Do not deploy this
development actor.

```bash
export PYTHONDONTWRITEBYTECODE=1
python -c 'from app import create_app; from app.permissions import CurrentActor, Role; app=create_app({"CURRENT_ACTOR_PROVIDER": lambda: CurrentActor("demo-owner", "demo-club", Role.OWNER)}); app.run(debug=False)'
```

Keep this terminal open. Activate the same virtual environment in a second
terminal, then run the next block.

## 3. Run the session flow

This creates players, courts, a template and a session. It then generates a
round, changes a pairing, completes the round, records player availability
changes and generates the next round.

```bash
python - <<'PY'
import json
from datetime import datetime
from urllib.request import Request, urlopen


BASE = "http://127.0.0.1:5000/api"


def call(method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request) as response:
        if response.status == 204:
            return None
        return json.load(response)


player_data = [
    ("Alex", "M", 5),
    ("Ben", "M", 6),
    ("Chris", "M", 4),
    ("David", "M", 5),
    ("Emma", "F", 5),
    ("Fiona", "F", 6),
    ("Grace", "F", 4),
    ("Helen", "F", 5),
]
players = [
    call("POST", "/players", {"name": name, "gender": gender, "skill": skill})["player"]
    for name, gender, skill in player_data
]

court1 = call(
    "POST", "/courts", {"number": 1, "court_type": "hard", "priority": 0}
)["court"]
court2 = call(
    "POST", "/courts", {"number": 2, "court_type": "hard", "priority": 1}
)["court"]

template = call(
    "POST",
    "/session-templates",
    {
        "name": "Saturday Social",
        "number_of_rounds": 6,
        "session_duration_minutes": 180,
        "round_duration_minutes": 30,
        "start_time": "13:00",
        "round_format": "open",
        "strict_format": False,
        "tea_break_after_round": 3,
        "court_ids": [court1["id"], court2["id"]],
    },
)["session_template"]

session = call(
    "POST",
    "/sessions",
    {
        "name": "Saturday Social Demo",
        "starts_at": datetime.now().replace(microsecond=0).isoformat(),
        "template_id": template["id"],
        "player_ids": [player["id"] for player in players],
    },
)["session"]

round1 = call("POST", f"/sessions/{session['id']}/rounds/generate")["round"]
generated_before = round1["generated"]

# Manual override: swap one player between the two courts.
first = round1["final"]["assignments"][0]
second = round1["final"]["assignments"][1]
first["team_a_player_ids"][1], second["team_a_player_ids"][1] = (
    second["team_a_player_ids"][1],
    first["team_a_player_ids"][1],
)
round1 = call(
    "PUT",
    f"/rounds/{round1['id']}/final",
    {
        "assignments": round1["final"]["assignments"],
        "sit_out_player_ids": round1["final"]["sit_out_player_ids"],
    },
)["round"]
assert round1["generated"] == generated_before
call("POST", f"/rounds/{round1['id']}/complete")

late = call(
    "POST", "/players", {"name": "Late Player", "gender": "F", "skill": 5}
)["player"]
call("POST", f"/sessions/{session['id']}/players", {"player_id": late["id"]})
call(
    "PATCH",
    f"/sessions/{session['id']}/players/{players[0]['id']}",
    {"reason": "Left early"},
)
round2 = call("POST", f"/sessions/{session['id']}/rounds/generate")["round"]

statuses = {item["player_id"]: item["status"] for item in round2["eligibility"]}
print(
    json.dumps(
        {
            "session_id": session["id"],
            "rounds_generated": [round1["number"], round2["number"]],
            "generated_result_preserved": round1["generated"] == generated_before,
            "late_player": statuses[late["id"]],
            "departed_player": statuses[players[0]["id"]],
        },
        indent=2,
    )
)
PY
```

Keep the stable IDs returned by the API. Never use player names as IDs.

## 4. Connect Accounts and the frontend

Accounts only needs to provide the logged-in actor:

```python
from app.permissions import CurrentActor, Role

# Import current_user from the team's authentication implementation.


def current_actor_provider():
    return CurrentActor(
        actor_id=str(current_user.id),
        club_id=str(current_user.club_id),
        role=Role(current_user.role),
    )


app.config["CURRENT_ACTOR_PROVIDER"] = current_actor_provider
```

The frontend should call the `/api` routes. Run
`python -m flask --app run.py db upgrade` when a migration is added. Do not
write to these tables directly from frontend or Accounts code.

The private ratings CSV is not loaded automatically and must not be committed.
Create players through `POST /api/players`; review duplicate warnings before
using `confirm_duplicate: true`.

See [`backend-session-api.md`](backend-session-api.md) for the route list,
permissions and integration boundaries.
