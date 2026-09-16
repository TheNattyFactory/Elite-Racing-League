# Elite Racing League — v6.3 Production Set

Railway start command: `python escl_v6_3_webapp.py`

Production UI: `escl_v6_3_index.html`
Production service: `escl_v6_3_service.py`

Required compatibility/runtime modules retained because v6.3 imports or dynamically loads them:
- `escl_v3_1_database.py`
- `escl_v3_1_service.py`
- `escl_v3_2_lifecycle.py`
- `escl_v5_0_locked_race_engine.py`
- `escl_living_league_v1_9.py`

Do not commit a live SQLite database. Production uses `ESCL_DB_PATH=/data/escl_v5_0.db` on the Railway volume.
Historical v5.x/v6.0-v6.2 frontends, webapps, services, frontend checks, backup/smoke scripts, and `__pycache__` are intentionally excluded from this production set.
