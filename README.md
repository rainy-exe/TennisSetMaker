# CITS3200-Group 5-TennisSetMaker-Project

## Team Members

| UWA ID | Name | Github Username |
|--------|------|----------------|
| 23887375 | Kate Fu | KateeFu |
| 23958717 | Rai Levinson | rainy-exe |
| 23993135 | Kimi Chong | KimiChong |
| 24220855 | Eric Townsend | yamato1327 |
| 24470644 | Lena Nguyen | lenanguyen401 |
| 24623266 | Zongrui Ma | Wssb6662333 |

## Getting started

Use Python 3.11 or newer; the backend has been tested with Python 3.12.
This checkout contains the persistent backend integration. Older feature
branches may need the integrated `environment`/`main` updates before using it.

- [Run the backend and a synthetic session demo](Documents/backend-quickstart.md)
- [Frontend API guide: login, CSRF, permissions and session requests](Documents/backend-session-api.md)
- [Algorithm adapter contract for Rai/Eric](Documents/backend-algorithm-contract.md)
- [Backend integration notes and remaining dependencies](Documents/backend-integration-followups.md)

Start the persistent backend through **`run.py`**, which uses Kate's Flask
factory. The root `app.py` is an older CSV/in-memory demo and is not this API.
Login already connects Kate's accounts and roles to the session backend.
Prototype4 is bundled and used by default; a separate algorithm export is not needed.

Before using an existing database, back it up and read the migration notes.
Old populated pre-Accounts databases need a real club/user mapping; the migration
refuses unsafe conversion rather than inventing accounts. Do not delete data to
bypass this check.

## Backend checks

After activating the virtual environment:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
python -m pytest Backend/Set_Algorithm/Prototype3/tests
python -m pytest Backend/Set_Algorithm/Prototype4/tests
python -m flask --app run.py db check
```

`db check` needs an upgraded database. Ruff is available through the development
requirements (`python -m ruff check app tests migrations/versions`); existing
lint findings are separate from the test results.

The algorithm suites run in separate processes because both prototypes use bare
module imports and identical test filenames. See the integration notes for the
connected management pages and remaining frontend/algorithm dependencies.

## Before updating a checkout

```bash
git status
git branch --show-current
git fetch origin
```

Fetching does not merge branches. Keep local changes and coordinate integration
before switching or merging. In particular, do not run `git pull origin main`
from a teammate's feature branch just to update it.
