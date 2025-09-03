# Oblique Telegram Bot

A mystical Telegram bot that sends animated, cryptic cards for daily inspiration.

## Commands

- `/start` – Attunes you (asks for DOB & location).
- `/draw` – Draws a random card from `oblique_strategies.txt`.
- Or tap the ✦ draw again ✦ button after a card is revealed.

## Deploying on Render

1. Push this repo to GitHub.
2. On Render, create a **Background Worker**.
3. Build command: `pip install -r requirements.txt`
4. Start command: `python -u bot.py`
5. Add your `TG_BOT_TOKEN` as an environment variable.

(Optional) Add a Persistent Disk and set `DATA_DIR=/data` if you want user data to survive redeploys.
