# Backend quick start

Use this guide to run the existing backend and test the main session flow.
Commands below are for PowerShell.

## 1. Set up the branch

```powershell
git fetch origin
git switch backend-session-management
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
flask --app run.py db upgrade
```

Prototype3 is currently on `main`, so keep it in a separate worktree. Do not
merge it into this branch.

```powershell
git worktree add ..\TennisSetMaker-main origin/main
$env:TENNIS_ALGORITHM_PATH = (Resolve-Path "..\TennisSetMaker-main\Backend\Set_Algorithm\Prototype3")
```

## 2. Run before Accounts integration

The protected API needs a current actor. This command starts a local-only
Owner for development. Do not use this actor in production.

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
python -c "from app import create_app; from app.permissions import CurrentActor, Role; app=create_app({'CURRENT_ACTOR_PROVIDER': lambda: CurrentActor('demo-owner', 'demo-club', Role.OWNER)}); app.run(debug=False)"
```

Keep that terminal open. Use a second PowerShell terminal for the next steps.

## 3. Run the session flow

This creates eight players, two courts, a template and a session. It then
generates a round, changes one pairing, completes the round, records a late
arrival and an early departure, and generates the next round.

```powershell
$api = "http://127.0.0.1:5000/api"

$playerInput = @(
    @{name="Alex"; gender="M"; skill=5},
    @{name="Ben"; gender="M"; skill=6},
    @{name="Chris"; gender="M"; skill=4},
    @{name="David"; gender="M"; skill=5},
    @{name="Emma"; gender="F"; skill=5},
    @{name="Fiona"; gender="F"; skill=6},
    @{name="Grace"; gender="F"; skill=4},
    @{name="Helen"; gender="F"; skill=5}
)

$players = foreach ($item in $playerInput) {
    (Invoke-RestMethod -Method Post -Uri "$api/players" `
        -ContentType "application/json" -Body ($item | ConvertTo-Json)).player
}

$court1 = (Invoke-RestMethod -Method Post -Uri "$api/courts" `
    -ContentType "application/json" `
    -Body (@{number=1; court_type="hard"; priority=0} | ConvertTo-Json)).court
$court2 = (Invoke-RestMethod -Method Post -Uri "$api/courts" `
    -ContentType "application/json" `
    -Body (@{number=2; court_type="hard"; priority=1} | ConvertTo-Json)).court

$templateBody = @{
    name = "Saturday Social"
    number_of_rounds = 6
    session_duration_minutes = 180
    round_duration_minutes = 30
    start_time = "13:00"
    round_format = "open"
    strict_format = $false
    tea_break_after_round = 3
    court_ids = @($court1.id, $court2.id)
}
$template = (Invoke-RestMethod -Method Post -Uri "$api/session-templates" `
    -ContentType "application/json" `
    -Body ($templateBody | ConvertTo-Json -Depth 5)).session_template

$sessionBody = @{
    name = "Saturday Social Demo"
    starts_at = (Get-Date).Date.AddHours(13).ToString("s")
    template_id = $template.id
    player_ids = @($players.id)
}
$session = (Invoke-RestMethod -Method Post -Uri "$api/sessions" `
    -ContentType "application/json" `
    -Body ($sessionBody | ConvertTo-Json -Depth 5)).session

$round1 = (Invoke-RestMethod -Method Post `
    -Uri "$api/sessions/$($session.id)/rounds/generate").round

# Example manual pairing change: swap one player between the two courts.
$swap = $round1.final.assignments[0].team_a_player_ids[1]
$round1.final.assignments[0].team_a_player_ids[1] = `
    $round1.final.assignments[1].team_a_player_ids[1]
$round1.final.assignments[1].team_a_player_ids[1] = $swap
$finalBody = @{
    assignments = $round1.final.assignments
    sit_out_player_ids = @($round1.final.sit_out_player_ids)
}
$round1 = (Invoke-RestMethod -Method Put -Uri "$api/rounds/$($round1.id)/final" `
    -ContentType "application/json" `
    -Body ($finalBody | ConvertTo-Json -Depth 10)).round

Invoke-RestMethod -Method Post -Uri "$api/rounds/$($round1.id)/complete"

$late = (Invoke-RestMethod -Method Post -Uri "$api/players" `
    -ContentType "application/json" `
    -Body (@{name="Late Player"; gender="F"; skill=5} | ConvertTo-Json)).player
Invoke-RestMethod -Method Post -Uri "$api/sessions/$($session.id)/players" `
    -ContentType "application/json" `
    -Body (@{player_id=$late.id} | ConvertTo-Json)
Invoke-RestMethod -Method Patch `
    -Uri "$api/sessions/$($session.id)/players/$($players[0].id)" `
    -ContentType "application/json" `
    -Body (@{reason="Left early"} | ConvertTo-Json)

$round2 = (Invoke-RestMethod -Method Post `
    -Uri "$api/sessions/$($session.id)/rounds/generate").round
$round2 | ConvertTo-Json -Depth 10
```

Use the returned IDs for later requests. Never use player names as IDs.

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

The frontend should call the `/api` routes and keep the stable IDs returned by
the API. Run `flask --app run.py db upgrade` when a migration is added. Do not
create or update these tables directly from frontend or Accounts code.

The private ratings CSV is not loaded automatically and must not be committed.
Create players through `POST /api/players`; confirm duplicate warnings before
using `confirm_duplicate: true`.

See [`backend-session-api.md`](backend-session-api.md) for the route list,
permissions and integration boundaries.
