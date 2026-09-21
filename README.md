# CITS3200-Group 5-TennisSetMaker-Project


## Team Members

| UWA ID | Name | Github Username |
|--------|------|----------------|
| 23887375 | Kate Fu | Kateefu |
| 23958717 | Rai Levinson | rainy-exe |
| 23993135 | Kimi Chong | KimiChong |
| 24220855 | Eric Townsend | yamato1327 |
| 24470644 | Lena Nguyen | lenanguyen401 |
| 24623266 | Zongrui Ma | Wssb6662333 |

## Getting Started

All users are expected to use the latest stable version of Python to run the application and execute the testing procedures.

## How to run the app in VScode
** 1. Make sure VS Code opened the repository folder
```bash
pwd
```
** 2. Create a Virtual Environment in VSCode
```bash
python3 -m venv venv
```
** 3. Activate the Virtual Environment
```bash
source venv/bin/activate
```
** 4. Install dependencies
```bash
pip install -r requirements.txt
```
5. ...

## Session-management backend

The Players, Courts, Session Configuration, Live Changes, Round Persistence,
and Manual Override API is documented in
[`Documents/backend-session-api.md`](Documents/backend-session-api.md).
For a Bash-based copy-and-run team demo, use
[`Documents/backend-quickstart.md`](Documents/backend-quickstart.md).

Before using a persistent database, run:

```bash
flask --app run.py db upgrade
```

Protected API routes require the Accounts/Club integration to configure a
`CURRENT_ACTOR_PROVIDER`. The matching adapter can be pointed at the team's
Prototype3 checkout with `TENNIS_ALGORITHM_PATH`.

For backend development and tests, install `requirements-dev.txt`, then run:

```bash
python -m pytest
ruff check app tests migrations/versions
```


## How to makesure your repository is up-to-date
** 1. Branch Check - check which branch you are in
```bash
git branch
```
** 2. Check whether you have any local changes
```bash
git status
```
** 3. Update your local main
```bash
git pull origin main
```
Make sure you see: Already up to date.

** 4. Update dependencies to the newest version
```bash
pip install -r requirements.txt
```
If there's any depency is needed to update, follow the procedures.
