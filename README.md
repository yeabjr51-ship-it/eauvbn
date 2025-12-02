# EAU Confessions Bot (aiogram v3) — Render Web Service Setup

This repo contains a converted aiogram v3 version of your bot and a small FastAPI app configured to receive Telegram webhook updates. It preserves all features from the original script (confessions, comments, pagination, avatars, profanity checking, cooldowns) and exposes a simple `/` endpoint for uptime pings.

Files added:
- `main.py` — aiogram v3 bot + FastAPI webhook endpoint.
- `requirements.txt` — Python dependencies.

Environment variables (required / recommended):
- `API_TOKEN` (required): your Telegram bot token.
- `CHANNEL_ID` (optional): target channel id. Defaults to `-1003234117416`.
- `WEBHOOK_BASE` (optional but recommended for automatic webhook setup): full base URL of your Render service, e.g. `https://your-app.onrender.com`. If provided, the app will call Telegram `setWebhook` to `WEBHOOK_BASE/webhook/<API_TOKEN>` on startup.
- `DB_PATH` (optional): path to sqlite DB file, default `eaubot.db`.
- `PORT` (optional): port number (Render provides `PORT` automatically).

Render deployment notes:
1. Add a new Web Service on Render (Python) and connect your repository.
2. Add the environment variables in Render service settings (see list above).
3. Use this start command in Render:
   gunicorn -k uvicorn.workers.UvicornWorker main:app --bind 0.0.0.0:$PORT
4. Deploy. If `WEBHOOK_BASE` is set the app will attempt to set Telegram webhook automatically to `WEBHOOK_BASE/webhook/<API_TOKEN>`.

Local testing:

1. Create a virtual environment and install dependencies:
   python -m venv .venv
   .\.venv\Scripts\Activate
   pip install -r requirements.txt

2. Run locally (dev):
   set API_TOKEN=your_token_here; set WEBHOOK_BASE=http://localhost:8000; uvicorn main:app --host 0.0.0.0 --port 8000

3. For webhook testing you can set `WEBHOOK_BASE` to your public URL (or use a tunnel like `ngrok`) so the bot will set webhook automatically on startup.

Security note:
- Do not commit your `API_TOKEN` to source control. Use Render's environment settings.