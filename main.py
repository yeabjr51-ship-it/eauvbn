import os
import logging
import sqlite3
import random
import html
import time
from typing import Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    Update,
)
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.filters import StateFilter

# ---------- CONFIG (read from environment) ----------
API_TOKEN = os.getenv("API_TOKEN")
if not API_TOKEN:
    raise SystemExit("Environment variable API_TOKEN is required")

CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1003234117416"))
CONFESSION_NAME = os.getenv("CONFESSION_NAME", "EAU Confession")
CONFESSION_COOLDOWN = int(os.getenv("CONFESSION_COOLDOWN", "30"))
COMMENT_COOLDOWN = int(os.getenv("COMMENT_COOLDOWN", "10"))

BAD_WORDS = {"badword1", "badword2"}
AVATAR_EMOJIS = [
    "🗿","👤","👽","🤖","👻","🦊","🐼","🐵","🐥","🦄","😺","😎","🫥","🪄","🧋"
]

DB_PATH = os.getenv("DB_PATH", "eaubot.db")
WEBHOOK_BASE = os.getenv("WEBHOOK_BASE")  # e.g. https://your-app.onrender.com

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- Bot & Dispatcher ----------
storage = MemoryStorage()
bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=storage)

BOT_USERNAME: Optional[str] = None

# ---------- Database helpers ----------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS confessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            channel_message_id INTEGER,
            author_id INTEGER
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            confession_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            avatar TEXT,
            timestamp INTEGER NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()

def db_execute(query, params=(), fetch=False, many=False):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if many:
        c.executemany(query, params)
        conn.commit()
        conn.close()
        return None
    c.execute(query, params)
    if fetch:
        rows = c.fetchall()
        conn.commit()
        conn.close()
        return rows
    conn.commit()
    conn.close()
    return None

# Rate-limits
_last_confession = {}
_last_comment = {}

class AddCommentState(StatesGroup):
    waiting_for_comment = State()

# ---------- Helpers ----------
def check_profanity(text: str) -> bool:
    t = text.lower()
    for w in BAD_WORDS:
        if w in t:
            return True
    return False

def format_confession_message(conf_id: int, text: str) -> str:
    t = html.escape(text)
    return f"👀 <b>{CONFESSION_NAME} #{conf_id}</b>\n\n{t}\n\n#Other"

def build_channel_keyboard(conf_id: int, comment_count: int, bot_username: str):
    view_url = f"https://t.me/{bot_username}?start=view_{conf_id}"
    add_url = f"https://t.me/{bot_username}?start=add_{conf_id}"
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton(f"👀 Browse Comments ({comment_count})", url=view_url),
        InlineKeyboardButton("➕ Add Comment", url=add_url),
    )
    return kb

def build_comment_page_keyboard(conf_id: int, page: int, total_pages: int):
    kb = InlineKeyboardMarkup(row_width=2)
    if page > 1:
        kb.row(InlineKeyboardButton("⬅️ Prev", callback_data=f"page:{conf_id}:{page-1}"))
    if page < total_pages:
        kb.insert(InlineKeyboardButton("Next ➡️", callback_data=f"page:{conf_id}:{page+1}"))
    kb.add(InlineKeyboardButton("➕ Add Comment", url=f"https://t.me/{BOT_USERNAME}?start=add_{conf_id}"))
    return kb

def get_top_menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    kb.add(KeyboardButton("📝 Confess"))
    kb.add(KeyboardButton("👀 Browse Confessions"))
    return kb

# ---------- Handlers ----------
@dp.message.register(Command(commands=["start"]))
async def cmd_start(message: Message, state: FSMContext):
    global BOT_USERNAME
    text = "Welcome to EAU Confessions — send an anonymous confession and I'll post it.\n\n"
    await message.answer(text, reply_markup=get_top_menu())

    args = message.get_args()
    if args:
        if args.startswith("view_"):
            try:
                conf_id = int(args.split("_", 1)[1])
                await send_comments_page(message.chat.id, conf_id, page=1)
                return
            except Exception:
                pass
        if args.startswith("add_"):
            try:
                conf_id = int(args.split("_", 1)[1])
                await message.answer("Send your comment:")
                await state.update_data(confession_id=conf_id)
                await state.set_state(AddCommentState.waiting_for_comment)
                return
            except Exception:
                pass

@dp.message.register(Command(commands=["help"]))
async def cmd_help(message: Message):
    await message.answer("Use the buttons in the channel to interact with confessions.")

@dp.message.register(lambda message: message.text in ["📝 Confess", "👀 Browse Confessions"])
async def top_menu_buttons(message: Message):
    if message.text == "📝 Confess":
        await message.answer("Send your confession now.", reply_markup=ReplyKeyboardRemove())
    else:
        await message.answer("Browse confessions:", reply_markup=ReplyKeyboardRemove())
        await message.answer("https://t.me/eauvents")

@dp.message.register()
async def receive_confession(message: Message):
    if message.chat.type != "private":
        return
    uid = message.from_user.id
    now = time.time()
    last = _last_confession.get(uid, 0)
    if now - last < CONFESSION_COOLDOWN:
        await message.reply(f"Wait {int(CONFESSION_COOLDOWN - (now-last))}s before sending another confession.")
        return

    text = message.text.strip() if message.text else (message.caption.strip() if message.caption else "")
    if not text:
        await message.reply("Empty confession.")
        return
    if check_profanity(text):
        await message.reply("Your confession contains banned words.")
        return

    ts = int(time.time())
    db_execute("INSERT INTO confessions (text, timestamp, author_id) VALUES (?, ?, ?)", (text, ts, uid))
    conf_id = db_execute("SELECT id FROM confessions ORDER BY id DESC LIMIT 1", fetch=True)[0][0]
    formatted = format_confession_message(conf_id, text)

    try:
        sent = await bot.send_message(
            CHANNEL_ID,
            formatted,
            reply_markup=build_channel_keyboard(conf_id, 0, BOT_USERNAME),
        )
        db_execute("UPDATE confessions SET channel_message_id=? WHERE id=?", (sent.message_id, conf_id))
    except Exception:
        await message.reply("Bot cannot post in channel.")
        return

    _last_confession[uid] = now
    await message.reply(f"Posted as {CONFESSION_NAME} #{conf_id}")

@dp.message(StateFilter(AddCommentState.waiting_for_comment))
async def process_comment(message: Message, state: FSMContext):
    data = await state.get_data()
    confession_id = data.get("confession_id")
    if not confession_id:
        await message.reply("Session expired.")
        await state.clear()
        return

    uid = message.from_user.id
    now = time.time()
    last = _last_comment.get(uid, 0)
    if now - last < COMMENT_COOLDOWN:
        await message.reply(f"Wait {int(COMMENT_COOLDOWN - (now-last))}s before commenting again.")
        await state.clear()
        return

    text = message.text.strip() if message.text else ""
    if not text:
        await message.reply("Comment canceled.")
        await state.clear()
        return

    if check_profanity(text):
        await message.reply("Your comment contains banned words.")
        await state.clear()
        return

    avatar = random.choice(AVATAR_EMOJIS)
    ts = int(time.time())
    db_execute(
        "INSERT INTO comments (confession_id, text, avatar, timestamp) VALUES (?, ?, ?, ?)",
        (confession_id, text, avatar, ts),
    )

    rows = db_execute("SELECT channel_message_id FROM confessions WHERE id=?", (confession_id,), fetch=True)
    if rows and rows[0][0]:
        ch_msg = rows[0][0]
        cnt = db_execute("SELECT COUNT(*) FROM comments WHERE confession_id=?", (confession_id,), fetch=True)[0][0]
        try:
            await bot.edit_message_reply_markup(
                chat_id=CHANNEL_ID, message_id=ch_msg, reply_markup=build_channel_keyboard(confession_id, cnt, BOT_USERNAME)
            )
        except Exception:
            pass

    _last_comment[uid] = now
    await message.reply("Comment added!")
    await state.clear()

async def send_comments_page(chat_id: int, confession_id: int, page: int = 1, edit_message_id: int = None):
    PAGE_SIZE = 4
    conf = db_execute("SELECT id, text FROM confessions WHERE id=?", (confession_id,), fetch=True)
    if not conf:
        await bot.send_message(chat_id, "Confession not found.")
        return

    conf_text = conf[0][1]
    total = db_execute("SELECT COUNT(*) FROM comments WHERE confession_id=?", (confession_id,), fetch=True)[0][0]
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * PAGE_SIZE

    rows = db_execute(
        "SELECT id, text, avatar, timestamp FROM comments WHERE confession_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
        (confession_id, PAGE_SIZE, offset),
        fetch=True,
    )

    body = f"👀 <b>{CONFESSION_NAME} #{confession_id}</b>\n\n{html.escape(conf_text)}\n\n"
    body += f"💬 Comments (page {page}/{total_pages}):\n\n"

    for r in rows:
        cid, ctext, avatar, ts = r
        snippet = html.escape(ctext if len(ctext) <= 250 else ctext[:247] + "...")
        body += f"{avatar} <b>Comment #{cid}</b>\n{snippet}\n\n"

    kb = build_comment_page_keyboard(confession_id, page, total_pages)

    if edit_message_id:
        try:
            await bot.edit_message_text(text=body, chat_id=chat_id, message_id=edit_message_id, reply_markup=kb)
            return
        except Exception:
            pass

    await bot.send_message(chat_id, body, reply_markup=kb)

@dp.callback_query.register(lambda c: c.data and c.data.startswith("page:"))
async def callback_page(call: CallbackQuery):
    await call.answer()
    try:
        _, conf, pg = call.data.split(":")
        await send_comments_page(call.from_user.id, int(conf), int(pg), edit_message_id=call.message.message_id)
    except Exception:
        pass

# ---------- FastAPI app + webhook ----------
app = FastAPI()

@app.on_event("startup")
async def on_startup():
    global BOT_USERNAME
    init_db()
    me = await bot.get_me()
    BOT_USERNAME = me.username
    logger.info("Bot started as %s", BOT_USERNAME)

    # Set webhook if WEBHOOK_BASE provided
    if WEBHOOK_BASE:
        webhook_url = f"{WEBHOOK_BASE.rstrip('/')}" + f"/webhook/{API_TOKEN}"
        try:
            await bot.set_webhook(webhook_url)
            logger.info("Webhook set to %s", webhook_url)
        except Exception as e:
            logger.exception("Failed to set webhook: %s", e)

@app.on_event("shutdown")
async def on_shutdown():
    try:
        await bot.delete_webhook()
    except Exception:
        pass
    await bot.session.close()

@app.get("/", response_class=PlainTextResponse)
async def root():
    return "OK"

@app.post("/webhook/{token}")
async def telegram_webhook(token: str, request: Request):
    if token != API_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid token")

    data = await request.json()
    try:
        update = Update(**data)
    except Exception:
        return {"ok": False}

    try:
        await dp.process_update(update)
    except Exception:
        logger.exception("Error processing update")
    return {"ok": True}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), log_level="info")
