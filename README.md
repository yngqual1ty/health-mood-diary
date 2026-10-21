# Mood Diary TMA MVP

Prototype mood diary: Telegram bot + Telegram Mini App + FastAPI + SQLite.

## Run
1. Create a bot with @BotFather.
2. Copy `.env.example` to `.env` and set `BOT_TOKEN`, `BOT_USERNAME`, `APP_URL`, and `ADMIN_DOCTOR_ID`.
   `ADMIN_DOCTOR_ID` is the Telegram numeric ID of the main doctor. Keep `.env` private.
3. Install Python 3.11+: `pip install -r requirements.txt`
4. Run the API: `uvicorn app:app --host 0.0.0.0 --port 8000`
5. Serve the app over HTTPS and set `APP_URL` to its URL. A tunnel can be used for local demos.
6. Run the bot: `python bot.py`
7. Configure the Mini App URL with BotFather. Doctors can invite patients from the app.

The main doctor's account is assigned the doctor role when the API starts. The database is created locally and must not be committed.

## Limitations
Prototype only, not a production medical system. Before real patient use, add stronger doctor authentication, authorization tests, audit logs, consent/retention controls, backups, rate limits, and secret management. Use HTTPS. Mini App `initData` is validated server-side. This diary is not monitored continuously and is not a substitute for clinical care.
