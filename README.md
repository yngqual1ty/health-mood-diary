# Mood Diary TMA MVP

Prototype mood diary: Telegram bot + Telegram Mini App + FastAPI + SQLite.

## Run
1. Create a bot with @BotFather.
2. Copy `.env.example` to `.env` and set `BOT_TOKEN`, `BOT_USERNAME`, `APP_URL`, and `ADMIN_DOCTOR_ID`.
   `ADMIN_DOCTOR_ID` is the Telegram numeric ID of the main doctor. Keep `.env` private.
3. Install Python 3.11+: `pip install -r requirements.txt`
4. Build the Mini App: `cd frontend && npm install && NEXT_OUTPUT=export npm run build`
5. Run the API: `uvicorn app:app --host 0.0.0.0 --port 8000`
   For UI development, run `npm run dev` in `frontend/` and keep the API on port 8000.
6. Serve the app over HTTPS and set `APP_URL` to its URL. A tunnel can be used for local demos.
7. Run the bot: `python bot.py`
8. Configure the Mini App URL with BotFather. Doctors can invite patients from the app.

## Data reset
`POST /api/dev/reset` wipes **all** diary data so Telegram onboarding (`/start`, ОСР, HADS/BAI/BDI, ШПАНА, pills, mood) can be rerun. No Telegram auth. Anyone who knows `DEV_RESET_TOKEN` can call it; if that env var is empty, every request is refused (403).

This deletes users, doctor/patient links, invites, answers, moods, pills log/prompts, regimen, reminders, treatment suggestions, medications + dose logs, sessions, and patient document files + rows. The main doctor from `ADMIN_DOCTOR_ID` is re-inserted afterward.

```bash
curl -X POST 'http://127.0.0.1:8000/api/dev/reset' \
  -H "X-Dev-Reset-Token: $DEV_RESET_TOKEN"
```

Response: `{"ok": true, "cleared": ["answers", ...]}`. Treat the token like a password: anyone with it can erase the database.

## Doctor web cabinet
Doctors can open the cabinet as a normal website at `$APP_URL/login/` (this deployment: https://bot.koni.moscow/login/), not only inside the Telegram Mini App.

The Telegram Login Widget works only after the bot’s domain is registered in @BotFather:

1. Open @BotFather.
2. Send `/setdomain` (or Bot Settings → Domain).
3. Set the domain to `bot.koni.moscow` — host only, no `https://` and no path.

Patients cannot log in on the website; they use the diary in Telegram. Mini App requests still send `Authorization: tma <initData>` and the server still validates that hash. Website sessions use an HTTP-only cookie (`Path=/; Secure; SameSite=Lax`) or `Authorization: Bearer`.

## Docker
1. Copy `.env.example` to `.env` and set `BOT_TOKEN`, `BOT_USERNAME`, `APP_URL`, and `ADMIN_DOCTOR_ID`.
2. `APP_URL` must be the public HTTPS URL of this API (a tunnel or reverse proxy in front of port 8000). Telegram will not open an `http://` Mini App URL.
3. Start both processes: `docker compose up --build -d`
4. The API listens on port 8000 and serves the Next.js Mini App from `frontend/out`. The bot polls Telegram. They share a Docker volume at `/data/mood.sqlite3`, so the database survives container restarts and is not baked into the image.

## Clinical recommendations RAG (prototype)
Doctors see a "Варианты лечения по клиническим рекомендациям" card on a patient's page. It retrieves guideline fragments and asks an LLM for 2–4 treatment alternatives, each of which must cite those fragments.

1. `guidelines/` holds the official Russian clinical guidelines from the Ministry of Health rubricator (cr.minzdrav.gov.ru), converted to Markdown: КР301 depressive episode / recurrent depressive disorder, КР457 generalized anxiety disorder, КР456 panic disorder, КР455 phobic anxiety disorders. Refresh them or add others by code with `python fetch_guidelines.py [code ...]`. Two more guidelines are Markdown converted from uploaded КР PDFs: `kr_okr.md` (original «КР ОКР.pdf» — обсессивно-компульсивное расстройство) and `kr_rl.md` (original «КР РЛ.pdf» — специфическое расстройство личности). Any other `.md`, `.txt` or `.pdf` files placed there are indexed too; Markdown headings become citation sections.
2. Set `LLM_API_KEY`, `LLM_MODEL` and optionally `LLM_BASE_URL` (any OpenAI-compatible API, including a local Ollama for data that must not leave the host). Without a key the card shows only the retrieved fragments.
3. Optional: set `EMBEDDING_MODEL` for hybrid BM25 + embedding retrieval. Embeddings are cached in `guidelines/.cache/`.
4. After changing documents, the main doctor calls `POST /api/admin/guidelines/reload` (or restarts the API).

Only a de-identified summary of the patient's data is sent to the LLM (mood statistics, emotions, questionnaire scores, BDI item 9). Names, Telegram IDs and free-text notes are not sent. Every request and response is stored in the `treatment_suggestions` table for audit.

The main doctor's account is assigned the doctor role when the API starts. The database is created locally and must not be committed. Patients and their linked doctor share one document area: documents are grouped by uploader, and each can delete only the documents they uploaded.

Medication reminders use the `APP_TIMEZONE` setting (default `Europe/Moscow`) and are sent by the running bot process, so keep the bot running continuously. Each patient must start the bot before Telegram can deliver reminders. Uploaded patient documents are stored privately in `PATIENT_DOCUMENTS_DIR` (default `patient_documents/`); include that directory in protected backups and never commit it.
