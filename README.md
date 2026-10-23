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

## Docker
1. Copy `.env.example` to `.env` and set `BOT_TOKEN`, `BOT_USERNAME`, `APP_URL`, and `ADMIN_DOCTOR_ID`.
2. `APP_URL` must be the public HTTPS URL of this API (a tunnel or reverse proxy in front of port 8000). Telegram will not open an `http://` Mini App URL.
3. Start both processes: `docker compose up --build -d`
4. The API listens on port 8000. The bot polls Telegram. They share a Docker volume at `/data/mood.sqlite3`, so the database survives container restarts and is not baked into the image.

## Clinical recommendations RAG (prototype)
Doctors see a "Варианты лечения по клиническим рекомендациям" card on a patient's page. It retrieves guideline fragments and asks an LLM for 2–4 treatment alternatives, each of which must cite those fragments.

1. `guidelines/` holds the official Russian clinical guidelines from the Ministry of Health rubricator (cr.minzdrav.gov.ru), converted to Markdown: КР301 depressive episode / recurrent depressive disorder, КР457 generalized anxiety disorder, КР456 panic disorder, КР455 phobic anxiety disorders. Refresh them or add others by code with `python fetch_guidelines.py [code ...]`. Any other `.md`, `.txt` or `.pdf` files placed there are indexed too; Markdown headings become citation sections.
2. Set `LLM_API_KEY`, `LLM_MODEL` and optionally `LLM_BASE_URL` (any OpenAI-compatible API, including a local Ollama for data that must not leave the host). Without a key the card shows only the retrieved fragments.
3. Optional: set `EMBEDDING_MODEL` for hybrid BM25 + embedding retrieval. Embeddings are cached in `guidelines/.cache/`.
4. After changing documents, the main doctor calls `POST /api/admin/guidelines/reload` (or restarts the API).

Only a de-identified summary of the patient's data is sent to the LLM (mood statistics, emotions, questionnaire scores, BDI item 9). Names, Telegram IDs and free-text notes are not sent. Every request and response is stored in the `treatment_suggestions` table for audit.

The main doctor's account is assigned the doctor role when the API starts. The database is created locally and must not be committed.
