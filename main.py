# main.py
import logging
import sqlite3
import asyncio
import random
import html
import time
from datetime import datetime
from typing import Optional

from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import StatesGroup, State

# ---------- CONFIG ----------
API_TOKEN = "8541392648:AAEORiUdbDPTo8BdFx9W_-NP1zZiY3tLyYI"
CHANNEL_ID = -1003234117416
CONFESSION_NAME = "EAU Confession"
CONFESSION_COOLDOWN = 30
COMMENT_COOLDOWN = 10

BAD_WORDS = {"badword1", "badword2"}
AVATAR_EMOJIS = ["🗿","👤","👽","🤖","👻","🦊","🐼","🐵","🐥","🦄","😺","😎","🫥","🪄","🧋"]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Bot / Dispatcher will be created in main()
bot: Optional[Bot] = None
dp: Optional[Dispatcher] = None 

BOT_USERNAME: Optional[str] = None

DB_PATH = "eaubot.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS confessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        text TEXT NOT NULL,
        timestamp INTEGER NOT NULL,
        channel_message_id INTEGER,
        author_id INTEGER
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        confession_id INTEGER NOT NULL,
        text TEXT NOT NULL,
        avatar TEXT,
        timestamp INTEGER NOT NULL
    )
    """)
    conn.commit()
    conn.close()

def db_execute(query: str, params=(), fetch: bool = False, many: bool = False):
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

_last_confession: dict = {}
_last_comment: dict = {}

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
        InlineKeyboardButton("➕ Add Comment", url=add_url)
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

# ---------- Top Menu ----------
def get_top_menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    kb.add(KeyboardButton("📝 Confess"))
    kb.add(KeyboardButton("👀 Browse Confessions"))
    return kb

# ---------- Handlers (aiogram v3 style) ----------
async def cmd_start(message: types.Message, state: FSMContext):
    global BOT_USERNAME
    # Clear state in case the user was stuck
    await state.clear()
    
    text = "Welcome to EAU Confessions — send an anonymous confession and I'll post it.\n\n"
    await message.answer(text, reply_markup=get_top_menu())

    if message.get_args():
        arg = message.get_args()
        if arg.startswith("view_"):
            try:
                conf_id = int(arg.split("_",1)[1])
                await send_comments_page(message.chat.id, conf_id, page=1, edit_message_id=None)
                return
            except:
                pass
        if arg.startswith("add_"):
            try:
                conf_id = int(arg.split("_",1)[1])
                await message.answer("Send your comment:")
                await state.update_data(confession_id=conf_id)
                await state.set_state(AddCommentState.waiting_for_comment)
                return
            except:
                pass

async def cmd_help(message: types.Message):
    await message.answer("Use the buttons in the channel to interact with confessions.")

async def cmd_stop(message: types.Message, state: FSMContext):
    """Allows user to exit any active state (e.g., adding a comment)."""
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Nothing to stop. You're not in any active process.", reply_markup=get_top_menu())
        return

    await state.clear()
    await message.answer("Operation cancelled. You can now use the main menu.", reply_markup=get_top_menu())

async def top_menu_buttons(message: types.Message):
    if message.text == "📝 Confess":
        await message.answer("Send your confession now.", reply_markup=types.ReplyKeyboardRemove())
    else:
        await message.answer("Browse confessions:", reply_markup=types.ReplyKeyboardRemove())
        await message.answer("https://t.me/eauvents")

async def receive_confession(message: types.Message):
    # Only accept in private chats
    if message.chat.type != "private":
        return
    uid = message.from_user.id
    now = time.time()
    last = _last_confession.get(uid, 0)
    if now - last < CONFESSION_COOLDOWN:
        await message.reply(f"Wait {int(CONFESSION_COOLDOWN - (now-last))}s before sending another confession.")
        return

    text = ""
    if message.text:
        text = message.text.strip()
    elif message.caption:
        text = message.caption.strip()

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
            chat_id=CHANNEL_ID,
            text=formatted,
            parse_mode=ParseMode.HTML,
            reply_markup=build_channel_keyboard(conf_id, 0, BOT_USERNAME)
        )
        db_execute("UPDATE confessions SET channel_message_id=? WHERE id=?", (sent.message_id, conf_id))
    except Exception as e:
        logger.exception("Failed to post confession to channel")
        await message.reply("Bot cannot post in channel.")
        return

    _last_confession[uid] = now
    await message.reply(f"Posted as {CONFESSION_NAME} #{conf_id}")

# ---------- Add Comment ----------
async def process_comment(message: types.Message, state: FSMContext):
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

    text = (message.text or "").strip()
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
        (confession_id, text, avatar, ts)
    )

    rows = db_execute("SELECT channel_message_id FROM confessions WHERE id=?", (confession_id,), fetch=True)
    if rows and rows[0][0]:
        ch_msg = rows[0][0]
        cnt = db_execute("SELECT COUNT(*) FROM comments WHERE confession_id=?", (confession_id,), fetch=True)[0][0]
        try:
            # v3 edit_message_reply_markup uses bot method with keywords
            await bot.edit_message_reply_markup(chat_id=CHANNEL_ID, message_id=ch_msg, reply_markup=build_channel_keyboard(confession_id, cnt, BOT_USERNAME))
        except Exception:
            logger.exception("Failed to update channel message keyboard")

    _last_comment[uid] = now
    await message.reply("Comment added!")
    await state.clear()

# ---------- View Comments ----------
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
        (confession_id, PAGE_SIZE, offset), fetch=True)

    body = f"👀 <b>{CONFESSION_NAME} #{confession_id}</b>\n\n{html.escape(conf_text)}\n\n"
    body += f"💬 Comments (page {page}/{total_pages}):\n\n"

    for r in rows:
        cid, ctext, avatar, ts = r
        snippet = html.escape(ctext if len(ctext) <= 250 else ctext[:247] + "...")
        body += f"{avatar} <b>Comment #{cid}</b>\n{snippet}\n\n"

    kb = build_comment_page_keyboard(conf_id, page, total_pages)

    if edit_message_id:
        try:
            await bot.edit_message_text(text=body, chat_id=chat_id, message_id=edit_message_id, parse_mode=ParseMode.HTML, reply_markup=kb)
            return
        except Exception:
            logger.exception("Failed to edit message text")

    await bot.send_message(chat_id, body, parse_mode=ParseMode.HTML, reply_markup=kb)

# ---------- Callback Page ----------
async def callback_page(call: types.CallbackQuery):
    await call.answer()
    try:
        _, conf, pg = call.data.split(":")
        await send_comments_page(call.from_user.id, int(conf), int(pg), edit_message_id=call.message.message_id)
    except Exception:
        logger.exception("Failed to handle page callback")
        await call.message.reply("Could not load that page.")

# ---------- Startup ----------
async def on_startup():
    global BOT_USERNAME
    init_db()
    me = await bot.get_me()
    BOT_USERNAME = me.username
    logger.info("Bot started as @%s", BOT_USERNAME)

async def main():
    global bot, dp
    bot = Bot(
        token=API_TOKEN, 
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # Explicitly register all handlers
    dp.message.register(cmd_start, Command(commands=["start"]))
    dp.message.register(cmd_help, Command(commands=["help"]))
    dp.message.register(cmd_stop, Command(commands=["stop"]))
    dp.message.register(top_menu_buttons, lambda m: m.text in ["📝 Confess", "👀 Browse Confessions"])
    dp.message.register(receive_confession, lambda m: True)
    
    # Register FSM state filter
    dp.message.register(process_comment, AddCommentState.waiting_for_comment)
    
    dp.callback_query.register(callback_page, lambda c: c.data and c.data.startswith("page:"))

    # run startup tasks
    await on_startup()

    # NOTE: Webhook deletion is omitted as requested.
    
    # start long-polling
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped")
