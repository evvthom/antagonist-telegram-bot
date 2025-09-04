# bot.py — Antagnoist Oracle (Animated ASCII, squarer cards, no 𓂀) + “Draw again” + Onboarding
# Deck: antagonist_strategies.txt (one card per line, UTF-8)

import os
import re
import json
import random
import asyncio
from textwrap import wrap
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)
from telegram.error import BadRequest

# -------------------- ENV --------------------
load_dotenv()
BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("Missing TG_BOT_TOKEN in .env")

# -------------------- CONFIG --------------------
PACING = {
    "line_reveal_min": 0.28,
    "line_reveal_max": 0.65,
    "glitch_min": 0.08,
    "glitch_max": 0.18,
    "drip_step": 0.06,
    "settle_pause": 0.22,
    "flicker_pause": 0.16,
}
RARE_EVENT_CHANCE = 0.012
MAX_LINES = 10
MIN_WIDTH = 24
MAX_WIDTH = 48

DECK_FILE = Path("antagonist_strategies.txt")
USER_DATA_FILE = Path("user_prefs.json")

# -------------------- PERSISTENCE --------------------
def load_users() -> dict:
    if USER_DATA_FILE.exists():
        try:
            return json.loads(USER_DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_users(data: dict) -> None:
    try:
        USER_DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

USERS = load_users()

def get_user_profile(user_id: int) -> dict:
    return USERS.get(str(user_id), {})

def set_user_profile(user_id: int, profile: dict) -> None:
    USERS[str(user_id)] = profile
    save_users(USERS)

# -------------------- DECK LOADING --------------------
def load_deck() -> list[str]:
    if not DECK_FILE.exists():
        return []
    text = DECK_FILE.read_text(encoding="utf-8", errors="ignore")
    seen, cards = set(), []
    for ln in (l.strip() for l in text.splitlines()):
        if ln and ln not in seen:
            seen.add(ln)
            cards.append(ln)
    return cards

CARDS = load_deck()

# -------------------- FRAMES & GLITCH --------------------
FRAME_STYLES = [
    {"tl":"╭","tr":"╮","bl":"╰","br":"╯","h":"─","v":"│","orn":"☽☾"},
    {"tl":"┏","tr":"┓","bl":"┗","br":"┛","h":"━","v":"┃","orn":"✦✦"},
    {"tl":"┌","tr":"┐","bl":"└","br":"┘","h":"─","v":"│","orn":"❖"},
    {"tl":"╔","tr":"╗","bl":"╚","br":"╝","h":"═","v":"║","orn":"✶✶"},
]
GLITCH_GLYPHS = list("▒▓░◼◻◾◽▞▚▣▤▥▦▧▨▩◆◇◈✧✦✴✹✺✵✷✸✢✣✤✥※¤•·")

# -------------------- UI: Inline Button --------------------
KEYBOARD = InlineKeyboardMarkup(
    [[InlineKeyboardButton("✦  d r a w   a g a i n  ✦", callback_data="draw_again")]]
)

# -------------------- EDIT CACHE --------------------
LAST_TEXT: dict[tuple[int, int], str] = {}

# -------------------- HELPERS --------------------
def wrap_card_text(text: str, inner_width: int) -> list[str]:
    lines = wrap(text, width=max(8, inner_width), break_long_words=False, break_on_hyphens=False)
    return lines[:MAX_LINES]

def pad_center(s: str, width: int) -> str:
    if len(s) >= width:
        return s[:width]
    left = (width - len(s)) // 2
    right = width - len(s) - left
    return " " * left + s + " " * right

def html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def fence(s: str) -> str:
    # HTML <pre> keeps alignment (some clients show a small "copy" UI)
    return f"<pre>{html_escape(s)}</pre>"

def compute_inner_width(text: str) -> int:
    words = re.split(r"\s+", text)
    longest_word = max((len(w) for w in words), default=6)
    return min(MAX_WIDTH, max(MIN_WIDTH, longest_word + 8))

# Aspect/height
TARGET_HEIGHT_RATIO = 0.20
MAX_EXTRA_ROWS      = 10

def compute_square_padding(inner_width: int, line_count: int) -> tuple[int, int]:
    target_height = int(inner_width * TARGET_HEIGHT_RATIO)
    target_height = max(line_count + 2, target_height)
    max_height = line_count + MAX_EXTRA_ROWS
    target_height = min(target_height, max_height)
    extra = max(0, target_height - line_count)
    top = extra // 2
    bottom = extra - top
    return top, bottom

def build_card(lines: list[str], style: dict, inner_width: int, pad_top: int, pad_bottom: int) -> str:
    tl, tr, bl, br, h, v, orn = style["tl"], style["tr"], style["bl"], style["br"], style["h"], style["v"], style["orn"]
    head = pad_center(orn, inner_width)
    foot = pad_center(orn[::-1], inner_width)
    top = tl + h*(inner_width+2) + tr
    bot = bl + h*(inner_width+2) + br

    out = [top, f"{v} {head} {v}", f"{v} {' '*inner_width} {v}"]
    for _ in range(pad_top):
        out.append(f"{v} {' '*inner_width} {v}")
    for ln in lines:
        out.append(f"{v} {pad_center(ln, inner_width)} {v}")
    for _ in range(pad_bottom):
        out.append(f"{v} {' '*inner_width} {v}")
    out.append(f"{v} {' '*inner_width} {v}")
    out.append(f"{v} {foot} {v}")
    out.append(bot)
    return "\n".join(out)

def build_masked(lines: list[str], revealed: list[list[bool]]) -> list[str]:
    out = []
    for i, ln in enumerate(lines):
        mask = revealed[i] if i < len(revealed) else []
        chars = []
        for j, ch in enumerate(ln):
            if j < len(mask) and mask[j]:
                chars.append(ch)
            else:
                chars.append(" ")
        out.append("".join(chars))
    return out

def random_glitch(lines: list[str], intensity: float = 0.2) -> list[str]:
    glitched = []
    for ln in lines:
        chars = list(ln)
        for i, c in enumerate(chars):
            if c != " " and random.random() < intensity:
                chars[i] = random.choice(GLITCH_GLYPHS)
        glitched.append("".join(chars))
    return glitched

async def safe_edit(msg, text: str, *, reply_markup=None, parse_mode: str = "HTML"):
    key = (msg.chat_id, msg.message_id)
    last = LAST_TEXT.get(key)
    if last == text:
        return
    try:
        await msg.edit_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
        LAST_TEXT[key] = text
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise

# -------------------- ANIMATIONS --------------------
async def reveal_lines(msg, style, inner_width, final_lines, pad_top, pad_bottom, context):
    working = [""] * pad_top + final_lines + [""] * pad_bottom
    masked = [" " * len(pad_center(ln, inner_width)) for ln in working]
    await safe_edit(msg, fence(build_card(masked, style, inner_width, 0, 0)), reply_markup=KEYBOARD)

    for i in range(len(working)):
        await context.bot.send_chat_action(chat_id=msg.chat_id, action=ChatAction.TYPING)
        await asyncio.sleep(random.uniform(PACING["line_reveal_min"], PACING["line_reveal_max"]))

        if working[i]:
            masked[i] = working[i]
            if random.random() < 0.3:
                gl = random_glitch([working[i]], intensity=random.uniform(0.25, 0.55))[0]
                tmp = masked.copy()
                tmp[i] = gl
                await safe_edit(msg, fence(build_card(tmp, style, inner_width, 0, 0)), reply_markup=KEYBOARD)
                await asyncio.sleep(random.uniform(PACING["glitch_min"], PACING["glitch_max"]))
        await safe_edit(msg, fence(build_card(masked, style, inner_width, 0, 0)), reply_markup=KEYBOARD)

    if random.random() < 0.4:
        alt = dict(style); alt["orn"] = style["orn"][::-1]
        await asyncio.sleep(PACING["flicker_pause"])
        await safe_edit(msg, fence(build_card(masked, alt, inner_width, 0, 0)), reply_markup=KEYBOARD)
        await asyncio.sleep(PACING["flicker_pause"])
        await safe_edit(msg, fence(build_card(masked, style, inner_width, 0, 0)), reply_markup=KEYBOARD)

async def reveal_drip(msg, style, inner_width, final_lines, pad_top, pad_bottom, context):
    working = [""] * pad_top + final_lines + [""] * pad_bottom
    padded = [pad_center(ln, inner_width) for ln in working]
    width, height = inner_width, len(padded)
    revealed = [[False]*width for _ in range(height)]

    for col in range(width):
        await context.bot.send_chat_action(chat_id=msg.chat_id, action=ChatAction.TYPING)
        await asyncio.sleep(PACING["drip_step"])

        for row in range(height):
            if padded[row][col] != " " and random.random() > 0.12:
                revealed[row][col] = True

        show_lines = build_masked(padded, revealed)
        if random.random() < 0.15:
            glitched = random_glitch(show_lines, intensity=0.12)
            await safe_edit(msg, fence(build_card(glitched, style, inner_width, 0, 0)), reply_markup=KEYBOARD)
            await asyncio.sleep(random.uniform(PACING["glitch_min"], PACING["glitch_max"]))

        await safe_edit(msg, fence(build_card(show_lines, style, inner_width, 0, 0)), reply_markup=KEYBOARD)

    await asyncio.sleep(PACING["settle_pause"])
    await safe_edit(msg, fence(build_card([ln.strip() for ln in padded], style, inner_width, 0, 0)), reply_markup=KEYBOARD)

async def reveal_void(msg, style, inner_width, final_lines, pad_top, pad_bottom, context):
    working = [""] * pad_top + final_lines + [""] * pad_bottom
    targets = [pad_center(ln, inner_width) for ln in working]

    corrupted = ["".join(random.choice(GLITCH_GLYPHS) if c!=" " else " " for c in t) for t in targets]
    await safe_edit(msg, fence(build_card(corrupted, style, inner_width, 0, 0)), reply_markup=KEYBOARD)

    for _ in range(random.randint(3, 5)):
        await context.bot.send_chat_action(chat_id=msg.chat_id, action=ChatAction.TYPING)
        await asyncio.sleep(random.uniform(0.15, 0.33))
        healed = []
        for cur, tgt in zip(corrupted, targets):
            chars = list(cur)
            for j in range(len(chars)):
                if chars[j] != tgt[j] and random.random() < 0.35:
                    chars[j] = tgt[j]
            healed.append("".join(chars))
        corrupted = healed
        await safe_edit(msg, fence(build_card(healed, style, inner_width, 0, 0)), reply_markup=KEYBOARD)

    await asyncio.sleep(0.25)
    await safe_edit(msg, fence(build_card(targets, style, inner_width, 0, 0)), reply_markup=KEYBOARD)
    if random.random() < 0.5:
        alt = dict(style); alt["orn"] = style["orn"][::-1]
        await asyncio.sleep(PACING["flicker_pause"])
        await safe_edit(msg, fence(build_card(targets, alt, inner_width, 0, 0)), reply_markup=KEYBOARD)
        await asyncio.sleep(PACING["flicker_pause"])
        await safe_edit(msg, fence(build_card(targets, style, inner_width, 0, 0)), reply_markup=KEYBOARD)

# -------------------- ORCHESTRATOR --------------------
async def animated_card_reveal(chat_id: int, context: ContextTypes.DEFAULT_TYPE, text: str):
    style = random.choice(FRAME_STYLES)
    inner_width = compute_inner_width(text)
    body_lines = wrap_card_text(text, inner_width)

    pad_top, pad_bottom = compute_square_padding(inner_width, len(body_lines))

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    visual_blank = build_card([""] * (pad_top + len(body_lines) + pad_bottom), style, inner_width, 0, 0)
    msg = await context.bot.send_message(chat_id, fence(visual_blank), parse_mode="HTML", reply_markup=KEYBOARD)
    LAST_TEXT[(msg.chat_id, msg.message_id)] = fence(visual_blank)

    if random.random() < RARE_EVENT_CHANCE:
        await reveal_void(msg, style, inner_width, body_lines, pad_top, pad_bottom, context)
    else:
        anim = random.choice(["lines", "drip", "lines", "lines"])
        if anim == "lines":
            await reveal_lines(msg, style, inner_width, body_lines, pad_top, pad_bottom, context)
        else:
            await reveal_drip(msg, style, inner_width, body_lines, pad_top, pad_bottom, context)

# -------------------- ONBOARDING (Conversation) --------------------
YEAR, MONTH, DAY, LOCATION = range(4)

def profile_complete(user_id: int) -> bool:
    p = get_user_profile(user_id)
    return all(k in p for k in ("year", "month", "day", "location"))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not CARDS:
        return await update.message.reply_text("Add your deck to antagonist_strategies.txt (one per line), then /draw.")

    if profile_complete(user_id):
        await update.message.reply_text("Type /draw")
        return ConversationHandler.END

    await update.message.reply_text("First, a small attunement.\nWhat is your year of birth?")
    return YEAR

async def ask_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or not (1900 <= int(text) <= datetime.now().year):
        return await update.message.reply_text("Use 4 digits, e.g. 1990. What is your year of birth?")
    context.user_data["year"] = int(text)
    await update.message.reply_text("And the month? (1–12)")
    return MONTH

async def ask_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or not (1 <= int(text) <= 12):
        return await update.message.reply_text("Please reply with a number 1–12 for the month.")
    context.user_data["month"] = int(text)
    await update.message.reply_text("And the day? (1–31)")
    return DAY

async def ask_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or not (1 <= int(text) <= 31):
        return await update.message.reply_text("Please reply with a number 1–31 for the day.")
    context.user_data["day"] = int(text)
    await update.message.reply_text("Where are you located? (city or place)")
    return LOCATION

async def finish_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE):
    loc = update.message.text.strip()
    # save profile
    user_id = update.effective_user.id
    profile = {
        "year": context.user_data.get("year"),
        "month": context.user_data.get("month"),
        "day": context.user_data.get("day"),
        "location": loc,
    }
    set_user_profile(user_id, profile)

    # little mystical confirmation
    await update.message.reply_text("Absorbing… adjusting.")
    await asyncio.sleep(0.6)
    await update.message.reply_text("Attunement complete. Type /draw or press the button below after your first card.")
    return ConversationHandler.END

async def cancel_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Attunement dismissed. You can /start again anytime.")
    return ConversationHandler.END

# -------------------- COMMANDS --------------------
async def draw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not CARDS:
        return await update.message.reply_text("Deck is empty. Add lines to antagnoist_strategies.txt.")
    card = random.choice(CARDS)
    await animated_card_reveal(update.effective_chat.id, context, card)

# Button callback: draw again
async def draw_again(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("shuffling ✦")
    if not CARDS:
        return await q.message.reply_text("Deck is empty. Add lines to antagonist_strategies.txt.")
    card = random.choice(CARDS)
    await animated_card_reveal(q.message.chat_id, context, card)

# -------------------- ERROR HANDLER --------------------
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    try:
        print("[ERROR]", repr(context.error))
    except Exception:
        print("[ERROR] unknown")

# -------------------- APP --------------------
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Onboarding conversation
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            YEAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_month)],
            MONTH: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_day)],
            DAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_location)],
            LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, finish_onboarding)],
        },
        fallbacks=[CommandHandler("cancel", cancel_onboarding)],
        name="onboarding",
        persistent=False,
    )
    app.add_handler(conv)

    # Other handlers
    app.add_handler(CommandHandler("draw", draw))
    app.add_handler(CallbackQueryHandler(draw_again, pattern="^draw_again$"))
    app.add_error_handler(on_error)

    app.run_polling()

if __name__ == "__main__":
    main()
