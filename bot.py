# bot.py — Antagonist Strategies
# Animated ASCII card + "draw again" + "share" (PNG on PS1/Win95 card)
# Files required in repo root:
#   - antagonist_strategies.txt  (one card per line, UTF-8)
#   - share_bg.png               (your approved background)
#   - VT323-Regular.ttf          (font for share images)
# .env must contain: TG_BOT_TOKEN=XXXX

import os
import re
import random
import asyncio
import logging
from textwrap import wrap
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from telegram.error import BadRequest

from PIL import Image, ImageDraw, ImageFont

# ---------- ENV & LOGGING ----------
load_dotenv()
BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("Missing TG_BOT_TOKEN in .env")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("antagonist")

# ---------- CONFIG ----------
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
BG_FILE   = Path("share_bg.png")
FONT_FILE = Path("VT323-Regular.ttf")
OUT_DIR   = Path("out"); OUT_DIR.mkdir(exist_ok=True)

# Inline buttons ONLY for drawn cards (not on /start)
KB = InlineKeyboardMarkup([[
    InlineKeyboardButton("✦ draw again ✦", callback_data="draw_again"),
    InlineKeyboardButton("☾ keep/share ☾", callback_data="share_last"),
]])

# ---------- DECK ----------
def load_deck():
    if not DECK_FILE.exists():
        return []
    text = DECK_FILE.read_text(encoding="utf-8", errors="ignore")
    seen, cards = set(), []
    for ln in (l.strip() for l in text.splitlines()):
        if ln and ln not in seen:
            seen.add(ln); cards.append(ln)
    return cards

def pick_card():
    cards = load_deck()  # hot-reload each draw
    return random.choice(cards) if cards else "Deck is empty. Add lines to antagonist_strategies.txt."

# ---------- FRAME ----------
FRAME_STYLES = [
    {"tl":"╭","tr":"╮","bl":"╰","br":"╯","h":"─","v":"│","orn":"☽☾"},
    {"tl":"┏","tr":"┓","bl":"┗","br":"┛","h":"━","v":"┃","orn":"✦✦"},
    {"tl":"┌","tr":"┐","bl":"└","br":"┘","h":"─","v":"│","orn":"❖"},
    {"tl":"╔","tr":"╗","bl":"╚","br":"╝","h":"═","v":"║","orn":"✶✶"},
]
GLITCH_GLYPHS = list("▒▓░◼◻◾◽▞▚▣▤▥▦▧▨▩◆◇◈✧✦✴✹✺✵✷✸✢✣✤✥※¤•·")

# caches
LAST_TEXT_CACHE = {}        # (chat_id, msg_id) -> html text (for safe edit)
LAST_CARD_PER_CHAT = {}     # chat_id -> last card text (for "share")

# ---------- HELPERS ----------
def wrap_card_text(text, inner_width):
    lines = wrap(text, width=max(8, inner_width), break_long_words=False, break_on_hyphens=False)
    return lines[:MAX_LINES]

def pad_center(s, width):
    if len(s) >= width: return s[:width]
    left = (width - len(s)) // 2
    right = width - len(s) - left
    return " " * left + s + " " * right

def html_escape(s):
    return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def fence(s):  # Telegram preformatted block
    return f"<pre>{html_escape(s)}</pre>"

def compute_inner_width(text):
    words = re.split(r"\s+", text)
    longest_word = max((len(w) for w in words), default=6)
    return min(MAX_WIDTH, max(MIN_WIDTH, longest_word + 8))

TARGET_HEIGHT_RATIO = 0.20
MAX_EXTRA_ROWS      = 10

def compute_square_padding(inner_width, line_count):
    target_height = int(inner_width * TARGET_HEIGHT_RATIO)
    target_height = max(line_count + 2, target_height)
    target_height = min(target_height, line_count + MAX_EXTRA_ROWS)
    extra = max(0, target_height - line_count)
    return extra // 2, extra - (extra // 2)

def random_glitch(lines, intensity=0.2):
    glitched = []
    for ln in lines:
        chars = list(ln)
        for i, c in enumerate(chars):
            if c != " " and random.random() < intensity:
                chars[i] = random.choice(GLITCH_GLYPHS)
        glitched.append("".join(chars))
    return glitched

async def safe_edit(msg, text, parse_mode="HTML"):
    key = (msg.chat_id, msg.message_id)
    if LAST_TEXT_CACHE.get(key) == text:
        return
    try:
        await msg.edit_text(text, parse_mode=parse_mode, reply_markup=KB)
        LAST_TEXT_CACHE[key] = text
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise

# ---------- CARD BUILDER ----------
def build_card(lines, style, inner_width, pad_top, pad_bottom):
    tl,tr,bl,br = style["tl"],style["tr"],style["bl"],style["br"]
    h,v,orn = style["h"],style["v"],style["orn"]
    head = pad_center(orn, inner_width)
    foot = pad_center(orn[::-1], inner_width)
    top_border = tl + h*(inner_width+2) + tr
    bot_border = bl + h*(inner_width+2) + br

    out = [top_border, f"{v} {head} {v}", f"{v} {' '*inner_width} {v}"]
    for _ in range(pad_top): out.append(f"{v} {' '*inner_width} {v}")
    for ln in lines: out.append(f"{v} {pad_center(ln, inner_width)} {v}")
    for _ in range(pad_bottom): out.append(f"{v} {' '*inner_width} {v}")
    out.append(f"{v} {' '*inner_width} {v}")
    out.append(f"{v} {foot} {v}")
    out.append(bot_border)
    return "\n".join(out)

# ---------- ANIMATIONS ----------
def build_masked(lines, revealed):
    out = []
    for i, ln in enumerate(lines):
        mask = revealed[i] if i < len(revealed) else []
        chars = []
        for j, ch in enumerate(ln):
            chars.append(ch if (j < len(mask) and mask[j]) else " ")
        out.append("".join(chars))
    return out

async def reveal_lines(msg, style, inner_width, final_lines, pad_top, pad_bottom, context):
    working = [""]*pad_top + final_lines + [""]*pad_bottom
    masked  = [" " * len(pad_center(ln, inner_width)) for ln in working]
    await safe_edit(msg, fence(build_card(masked, style, inner_width, 0, 0)))

    for i in range(len(working)):
        await context.bot.send_chat_action(chat_id=msg.chat_id, action=ChatAction.TYPING)
        await asyncio.sleep(random.uniform(PACING["line_reveal_min"], PACING["line_reveal_max"]))
        if working[i]:
            masked[i] = working[i]
            if random.random() < 0.3:
                gl = random_glitch([working[i]], intensity=random.uniform(0.25,0.55))[0]
                tmp = masked.copy(); tmp[i] = gl
                await safe_edit(msg, fence(build_card(tmp, style, inner_width, 0, 0)))
                await asyncio.sleep(random.uniform(PACING["glitch_min"], PACING["glitch_max"]))
        await safe_edit(msg, fence(build_card(masked, style, inner_width, 0, 0)))

    await asyncio.sleep(PACING["settle_pause"])
    await safe_edit(msg, fence(build_card(masked, style, inner_width, 0, 0)))

    if random.random() < 0.4:
        alt = dict(style); alt["orn"] = style["orn"][::-1]
        await asyncio.sleep(PACING["flicker_pause"])
        await safe_edit(msg, fence(build_card(masked, alt, inner_width, 0, 0)))
        await asyncio.sleep(PACING["flicker_pause"])
        await safe_edit(msg, fence(build_card(masked, style, inner_width, 0, 0)))

async def reveal_drip(msg, style, inner_width, final_lines, pad_top, pad_bottom, context):
    working = [""]*pad_top + final_lines + [""]*pad_bottom
    padded  = [pad_center(ln, inner_width) for ln in working]
    width, height = inner_width, len(padded)
    revealed = [[False]*width for _ in range(height)]

    for col in range(width):
        await context.bot.send_chat_action(chat_id=msg.chat_id, action=ChatAction.TYPING)
        await asyncio.sleep(PACING["drip_step"])
        for row in range(height):
            if padded[row][col] != " " and random.random() > 0.12:
                revealed[row][col] = True
        show = build_masked(padded, revealed)
        if random.random() < 0.15:
            glitched = random_glitch(show, intensity=0.12)
            await safe_edit(msg, fence(build_card(glitched, style, inner_width, 0, 0)))
            await asyncio.sleep(random.uniform(PACING["glitch_min"], PACING["glitch_max"]))
        await safe_edit(msg, fence(build_card(show, style, inner_width, 0, 0)))

    await asyncio.sleep(PACING["settle_pause"])
    final_lines = [ln.strip() for ln in padded]
    await safe_edit(msg, fence(build_card(final_lines, style, inner_width, 0, 0)))

async def reveal_void(msg, style, inner_width, final_lines, pad_top, pad_bottom, context):
    working = [""]*pad_top + final_lines + [""]*pad_bottom
    targets = [pad_center(ln, inner_width) for ln in working]
    corrupted = ["".join(random.choice(GLITCH_GLYPHS) if c!=" " else " " for c in t) for t in targets]
    await safe_edit(msg, fence(build_card(corrupted, style, inner_width, 0, 0)))

    for _ in range(random.randint(3,5)):
        await context.bot.send_chat_action(chat_id=msg.chat_id, action=ChatAction.TYPING)
        await asyncio.sleep(random.uniform(0.15,0.33))
        healed = []
        for cur, tgt in zip(corrupted, targets):
            chars = list(cur)
            for j in range(len(chars)):
                if chars[j] != tgt[j] and random.random() < 0.35:
                    chars[j] = tgt[j]
            healed.append("".join(chars))
        corrupted = healed
        await safe_edit(msg, fence(build_card(corrupted, style, inner_width, 0, 0)))

    await asyncio.sleep(0.25)
    await safe_edit(msg, fence(build_card(targets, style, inner_width, 0, 0)))
    if random.random() < 0.5:
        alt = dict(style); alt["orn"] = style["orn"][::-1]
        await asyncio.sleep(PACING["flicker_pause"])
        await safe_edit(msg, fence(build_card(targets, alt, inner_width, 0, 0)))
        await asyncio.sleep(PACING["flicker_pause"])
        await safe_edit(msg, fence(build_card(targets, style, inner_width, 0, 0)))

# ---------- SHARE RENDERING (VT323 + big centered text) ----------
def _load_font(size: int):
    # Use VT323; fallback to PIL default if missing
    if FONT_FILE.exists():
        return ImageFont.truetype(str(FONT_FILE), size=size)
    return ImageFont.load_default()

def render_share_image(text: str, out_path: Path) -> Path:
    # background
    if not BG_FILE.exists():
        img = Image.new("RGB", (1000, 1250), "black")
    else:
        img = Image.open(BG_FILE).convert("RGB")

    W, H = img.size
    draw = ImageDraw.Draw(img)

    # generous centered box (tune if your frame differs)
    left   = int(W * 0.12)
    right  = W - int(W * 0.12)
    top    = int(H * 0.14)
    bottom = int(H * 0.88)
    box_w, box_h = right-left, bottom-top

    def wrap_for_width(font):
        words, lines, cur = text.split(), [], []
        for w in words:
            test = " ".join(cur + [w])
            if draw.textlength(test, font=font) <= box_w:
                cur.append(w)
            else:
                if cur: lines.append(" ".join(cur))
                cur = [w]
        if cur: lines.append(" ".join(cur))
        return lines

    def total_height(lines, font):
        line_h = font.getbbox("Hg")[3] - font.getbbox("Hg")[1]
        return len(lines)*line_h + max(0, len(lines)-1)*int(line_h*0.35)

    # Binary search largest font size that fits
    fs_lo = max(16, int(W*0.05))
    fs_hi = int(W*0.14)
    best_size, best_lines = fs_lo, [""]

    while fs_lo <= fs_hi:
        fs_mid = (fs_lo + fs_hi)//2
        font = _load_font(fs_mid)
        lines = wrap_for_width(font)
        th = total_height(lines, font)
        if th <= box_h and all(draw.textlength(l, font=font) <= box_w for l in lines):
            best_size, best_lines = fs_mid, lines
            fs_lo = fs_mid + 2
        else:
            fs_hi = fs_mid - 2

    font = _load_font(best_size)
    line_h = font.getbbox("Hg")[3] - font.getbbox("Hg")[1]
    total_h = total_height(best_lines, font)
    y = top + (box_h - total_h)//2

    # VT323 benefits from a subtle black stroke for contrast
    for line in best_lines:
        w = draw.textlength(line, font=font)
        x = left + (box_w - w)//2
        draw.text((x, y), line, font=font, fill=(235,235,235),
                  stroke_width=2, stroke_fill=(0,0,0))
        y += line_h + int(line_h*0.35)

    img.save(out_path, format="PNG")
    return out_path

# ---------- ORCHESTRATOR ----------
async def animated_card_reveal(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    chat_id = update.effective_chat.id
    style = random.choice(FRAME_STYLES)

    inner_width = compute_inner_width(text)
    body_lines  = wrap_card_text(text, inner_width)
    pad_top, pad_bottom = compute_square_padding(inner_width, len(body_lines))

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    visual_blank = build_card([""]*(pad_top + len(body_lines) + pad_bottom), style, inner_width, 0, 0)
    msg = await context.bot.send_message(chat_id, fence(visual_blank), parse_mode="HTML", reply_markup=KB)
    LAST_TEXT_CACHE[(msg.chat_id, msg.message_id)] = fence(visual_blank)
    LAST_CARD_PER_CHAT[chat_id] = text  # for sharing

    if random.random() < RARE_EVENT_CHANCE:
        await reveal_void(msg, style, inner_width, body_lines, pad_top, pad_bottom, context)
    else:
        anim = random.choice(["lines", "drip", "lines", "lines"])
        if anim == "lines":
            await reveal_lines(msg, style, inner_width, body_lines, pad_top, pad_bottom, context)
        else:
            await reveal_drip(msg, style, inner_width, body_lines, pad_top, pad_bottom, context)

# ---------- COMMANDS & CALLBACKS ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not load_deck():
        return await update.message.reply_text("The deck is empty. Add lines to antagonist_strategies.txt.")
    await update.message.reply_text(
        "Welcome. Type /draw to receive your first card."
    )

async def draw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    card = pick_card()
    await animated_card_reveal(update, context, card)

async def on_draw_again(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q:
        await q.answer()
        card = pick_card()
        fake_update = Update(update.update_id, message=update.effective_message)
        await animated_card_reveal(fake_update, context, card)

async def on_share_last(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    await q.answer()
    chat_id = q.message.chat_id
    text = LAST_CARD_PER_CHAT.get(chat_id)
    if not text:
        return await q.message.reply_text("Draw a card first, then share.")
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
    out_path = OUT_DIR / f"antagonist_{chat_id}_{random.randint(1000,9999)}.png"
    render_share_image(text, out_path)
    with open(out_path, "rb") as f:
        await q.message.reply_photo(InputFile(f), caption="")

# ---------- ERROR HANDLER ----------
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    try:
        log.error("[ERROR] %r", context.error)
    except Exception:
        log.error("[ERROR] unknown")

# ---------- APP ----------
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("draw", draw))
    app.add_handler(CallbackQueryHandler(on_draw_again, pattern="^draw_again$"))
    app.add_handler(CallbackQueryHandler(on_share_last, pattern="^share_last$"))
    app.add_error_handler(on_error)
    log.info("Starting Antagonist Strategies…")
    app.run_polling()

if __name__ == "__main__":
    main()
