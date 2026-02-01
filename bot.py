import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional
import os
from dotenv import load_dotenv
load_dotenv()

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ChatPermissions,
)
from telegram.constants import ChatType
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# -------------------- CONFIG --------------------
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Falta TELEGRAM_BOT_TOKEN. Ponlo en .env o como variable de entorno.")
BOT_USERNAME = "TecsoPro"  # opcional (sin @)

DB_PATH = "bot.db"

MIN_WARN_LIMIT = 1
MAX_WARN_LIMIT = 20
MAX_MUTE_MINUTES = 7 * 24 * 60  # 7 días

TEMP_LIMIT_KEY = "temp_warn_limit"
STATE_KEY = "state"  # para flujos de botones (add/remove word)

# estados
STATE_NONE = None
STATE_ADD_BW = "await_add_banned_word"
STATE_REMOVE_BW = "await_remove_banned_word"


# -------------------- DB CONNECTION --------------------
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# -------------------- MIGRATIONS --------------------
def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
    return cur.fetchone() is not None


def get_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table_name})")
    return {row[1] for row in cur.fetchall()}


def ensure_columns(conn: sqlite3.Connection, table_name: str, required: dict[str, str]):
    existing = get_columns(conn, table_name)
    cur = conn.cursor()
    for col, definition in required.items():
        if col not in existing:
            cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {col} {definition};")


def init_db():
    conn = db()
    cur = conn.cursor()

    # chats config por grupo
    cur.execute("""
    CREATE TABLE IF NOT EXISTS chats (
        chat_id INTEGER PRIMARY KEY,
        warn_limit INTEGER NOT NULL DEFAULT 3,
        log_chat_id INTEGER
    )
    """)

    # warns
    cur.execute("""
    CREATE TABLE IF NOT EXISTS warns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        warned_by INTEGER NOT NULL DEFAULT 0,
        reason TEXT,
        created_at TEXT NOT NULL
    )
    """)

    # bans
    cur.execute("""
    CREATE TABLE IF NOT EXISTS bans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        banned_by INTEGER NOT NULL DEFAULT 0,
        reason TEXT,
        created_at TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 'manual' -- manual | autowarn | banned_word
    )
    """)

    # unbans (audit)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS unbans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        unbanned_by INTEGER NOT NULL DEFAULT 0,
        reason TEXT,
        created_at TEXT NOT NULL
    )
    """)

    # banned words por grupo
    cur.execute("""
    CREATE TABLE IF NOT EXISTS banned_words (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        word TEXT NOT NULL,
        created_by INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )
    """)

    # MIGRATIONS (si DB vieja)
    if table_exists(conn, "chats"):
        ensure_columns(conn, "chats", {
            "warn_limit": "INTEGER NOT NULL DEFAULT 3",
            "log_chat_id": "INTEGER",
        })

    if table_exists(conn, "warns"):
        ensure_columns(conn, "warns", {
            "warned_by": "INTEGER NOT NULL DEFAULT 0",
            "reason": "TEXT",
            "created_at": "TEXT",
        })
        cur.execute("UPDATE warns SET created_at = COALESCE(created_at, ?)", (datetime.now(timezone.utc).isoformat(),))

    if table_exists(conn, "bans"):
        ensure_columns(conn, "bans", {
            "banned_by": "INTEGER NOT NULL DEFAULT 0",
            "reason": "TEXT",
            "created_at": "TEXT",
            "source": "TEXT NOT NULL DEFAULT 'manual'",
        })
        cur.execute("UPDATE bans SET created_at = COALESCE(created_at, ?)", (datetime.now(timezone.utc).isoformat(),))
        cur.execute("UPDATE bans SET source = COALESCE(source, 'manual')")

    if table_exists(conn, "unbans"):
        ensure_columns(conn, "unbans", {
            "unbanned_by": "INTEGER NOT NULL DEFAULT 0",
            "reason": "TEXT",
            "created_at": "TEXT",
        })
        cur.execute("UPDATE unbans SET created_at = COALESCE(created_at, ?)", (datetime.now(timezone.utc).isoformat(),))

    if table_exists(conn, "banned_words"):
        ensure_columns(conn, "banned_words", {
            "word": "TEXT NOT NULL",
            "created_by": "INTEGER NOT NULL DEFAULT 0",
            "created_at": "TEXT",
        })
        cur.execute("UPDATE banned_words SET created_at = COALESCE(created_at, ?)", (datetime.now(timezone.utc).isoformat(),))

    conn.commit()
    conn.close()


# -------------------- DB HELPERS --------------------
def ensure_chat(chat_id: int):
    conn = db()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO chats(chat_id) VALUES (?)", (chat_id,))
    conn.commit()
    conn.close()


def get_warn_limit(chat_id: int) -> int:
    ensure_chat(chat_id)
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT warn_limit FROM chats WHERE chat_id = ?", (chat_id,))
    row = cur.fetchone()
    conn.close()
    return int(row["warn_limit"])


def set_warn_limit(chat_id: int, limit: int):
    ensure_chat(chat_id)
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE chats SET warn_limit = ? WHERE chat_id = ?", (limit, chat_id))
    conn.commit()
    conn.close()


def get_log_chat_id(chat_id: int) -> Optional[int]:
    ensure_chat(chat_id)
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT log_chat_id FROM chats WHERE chat_id = ?", (chat_id,))
    row = cur.fetchone()
    conn.close()
    val = row["log_chat_id"]
    return int(val) if val is not None else None


def set_log_chat_id(chat_id: int, log_chat_id: Optional[int]):
    ensure_chat(chat_id)
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE chats SET log_chat_id = ? WHERE chat_id = ?", (log_chat_id, chat_id))
    conn.commit()
    conn.close()


def add_warn(chat_id: int, user_id: int, warned_by: int, reason: Optional[str]):
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO warns(chat_id, user_id, warned_by, reason, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (chat_id, user_id, warned_by, reason, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()


def count_warns(chat_id: int, user_id: int) -> int:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM warns WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
    row = cur.fetchone()
    conn.close()
    return int(row["c"])


def remove_last_warn(chat_id: int, user_id: int) -> bool:
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id FROM warns
        WHERE chat_id = ? AND user_id = ?
        ORDER BY id DESC
        LIMIT 1
    """, (chat_id, user_id))
    row = cur.fetchone()
    if not row:
        conn.close()
        return False
    cur.execute("DELETE FROM warns WHERE id = ?", (int(row["id"]),))
    conn.commit()
    conn.close()
    return True


def clear_warns(chat_id: int, user_id: int) -> int:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM warns WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
    count = int(cur.fetchone()["c"])
    cur.execute("DELETE FROM warns WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
    conn.commit()
    conn.close()
    return count


def list_warns(chat_id: int, user_id: int, limit: int = 10):
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, reason, warned_by, created_at
        FROM warns
        WHERE chat_id = ? AND user_id = ?
        ORDER BY id DESC
        LIMIT ?
    """, (chat_id, user_id, limit))
    rows = cur.fetchall()
    conn.close()
    return rows


def add_ban(chat_id: int, user_id: int, banned_by: int, reason: Optional[str], source: str):
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO bans(chat_id, user_id, banned_by, reason, created_at, source)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (chat_id, user_id, banned_by, reason, datetime.now(timezone.utc).isoformat(), source))
    conn.commit()
    conn.close()


def add_unban(chat_id: int, user_id: int, unbanned_by: int, reason: Optional[str]):
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO unbans(chat_id, user_id, unbanned_by, reason, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (chat_id, user_id, unbanned_by, reason, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()


def bw_add(chat_id: int, word: str, created_by: int) -> bool:
    w = normalize_word(word)
    if not w:
        return False
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM banned_words WHERE chat_id = ? AND word = ?", (chat_id, w))
    if cur.fetchone():
        conn.close()
        return False
    cur.execute("""
        INSERT INTO banned_words(chat_id, word, created_by, created_at)
        VALUES (?, ?, ?, ?)
    """, (chat_id, w, created_by, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    return True


def bw_remove(chat_id: int, word: str) -> bool:
    w = normalize_word(word)
    if not w:
        return False
    conn = db()
    cur = conn.cursor()
    cur.execute("DELETE FROM banned_words WHERE chat_id = ? AND word = ?", (chat_id, w))
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return changed


def bw_list(chat_id: int) -> list[str]:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT word FROM banned_words WHERE chat_id = ? ORDER BY word ASC", (chat_id,))
    words = [r["word"] for r in cur.fetchall()]
    conn.close()
    return words


# -------------------- HELPERS --------------------
def is_group(update: Update) -> bool:
    return bool(update.effective_chat and update.effective_chat.type in (ChatType.GROUP, ChatType.SUPERGROUP))


async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: Optional[int] = None) -> bool:
    chat = update.effective_chat
    if not chat:
        return False
    uid = user_id if user_id is not None else (update.effective_user.id if update.effective_user else None)
    if uid is None:
        return False
    member = await context.bot.get_chat_member(chat.id, uid)
    return member.status in ("administrator", "creator")


def target_user_id_from_reply(update: Update) -> Optional[int]:
    msg = update.effective_message
    if msg and msg.reply_to_message and msg.reply_to_message.from_user:
        return msg.reply_to_message.from_user.id
    return None


def clamp(n: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, n))


def normalize_word(word: str) -> str:
    w = (word or "").strip().lower()
    # simple: no espacios
    w = w.replace("\n", " ").strip()
    if not w:
        return ""
    # si te mandan "palabra,": recorta signos comunes
    w = w.strip(" \t.,;:!?\"'()[]{}<>")
    return w


async def send_modlog(context: ContextTypes.DEFAULT_TYPE, group_chat_id: int, text: str):
    log_chat_id = get_log_chat_id(group_chat_id)
    if not log_chat_id:
        return
    try:
        await context.bot.send_message(chat_id=log_chat_id, text=text)
    except Exception:
        return


async def maybe_autoban_after_warn(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, target_id: int, actor_id: int, source: str):
    total = count_warns(chat_id, target_id)
    limit = get_warn_limit(chat_id)
    if total < limit:
        return

    try:
        await context.bot.ban_chat_member(chat_id=chat_id, user_id=target_id)
        add_ban(chat_id, target_id, actor_id, f"Auto-ban por {limit} warns", source=source)
        await update.effective_message.reply_text(f"⛔ Usuario {target_id} baneado por alcanzar {limit} warns.")
        await send_modlog(
            context,
            chat_id,
            f"⛔ AUTO-BAN\nGrupo: {chat_id}\nActor: {actor_id}\nUsuario: {target_id}\nMotivo: alcanzó {limit} warns\nSource: {source}"
        )
    except Exception as e:
        await update.effective_message.reply_text(f"⚠️ Llegó al límite, pero no pude banear: {e}")
        await send_modlog(context, chat_id, f"⚠️ ERROR AUTO-BAN\nGrupo: {chat_id}\nUsuario: {target_id}\nError: {e}")


# -------------------- MENUS (CONFIG COMPLETO) --------------------
def main_config_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚠️ Warn limit", callback_data="cfg:menu:warn")],
        [InlineKeyboardButton("🚫 Banned words", callback_data="cfg:menu:bw")],
        [InlineKeyboardButton("🧾 Mod-log", callback_data="cfg:menu:log")],
        [InlineKeyboardButton("✖️ Cerrar", callback_data="cfg:close")],
    ])


def warn_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➖ 1", callback_data="cfg:warn:dec"),
            InlineKeyboardButton("➕ 1", callback_data="cfg:warn:inc"),
        ],
        [
            InlineKeyboardButton("Set 3", callback_data="cfg:warn:set:3"),
            InlineKeyboardButton("Set 5", callback_data="cfg:warn:set:5"),
            InlineKeyboardButton("Set 7", callback_data="cfg:warn:set:7"),
        ],
        [
            InlineKeyboardButton("✅ Guardar", callback_data="cfg:warn:save"),
            InlineKeyboardButton("⬅️ Atrás", callback_data="cfg:back"),
        ],
    ])


def bw_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 Ver lista", callback_data="cfg:bw:view")],
        [InlineKeyboardButton("➕ Agregar palabra", callback_data="cfg:bw:add")],
        [InlineKeyboardButton("➖ Quitar palabra", callback_data="cfg:bw:remove")],
        [InlineKeyboardButton("⬅️ Atrás", callback_data="cfg:back")],
    ])


def log_menu_keyboard(is_on: bool) -> InlineKeyboardMarkup:
    status_btn = InlineKeyboardButton("✅ Activar aquí", callback_data="cfg:log:on_here") if not is_on else InlineKeyboardButton("❌ Desactivar", callback_data="cfg:log:off")
    return InlineKeyboardMarkup([
        [status_btn],
        [InlineKeyboardButton("🧪 Probar log", callback_data="cfg:log:test")],
        [InlineKeyboardButton("⬅️ Atrás", callback_data="cfg:back")],
    ])


def config_header_text(chat_id: int) -> str:
    wl = get_warn_limit(chat_id)
    log_id = get_log_chat_id(chat_id)
    bw_count = len(bw_list(chat_id))
    return (
        "⚙️ *Configuración del bot*\n\n"
        f"• Warn limit: *{wl}*\n"
        f"• Banned words: *{bw_count}*\n"
        f"• Mod-log: *{'ON' if log_id else 'OFF'}*\n\n"
        "Selecciona una opción:"
    )


def warn_menu_text(chat_id: int, temp_limit: int) -> str:
    saved = get_warn_limit(chat_id)
    return (
        "⚠️ *Warn limit*\n\n"
        f"• Guardado: *{saved}*\n"
        f"• Editando: *{temp_limit}*\n\n"
        "Cuando un usuario llega al límite → ⛔ auto-ban."
    )


def bw_view_text(chat_id: int) -> str:
    words = bw_list(chat_id)
    if not words:
        return "🚫 *Banned words*\n\nLista vacía."
    preview = words[:50]
    text = "🚫 *Banned words*\n\n" + "\n".join([f"• `{w}`" for w in preview])
    if len(words) > 50:
        text += f"\n\n(+{len(words)-50} más)"
    return text


def log_menu_text(chat_id: int) -> str:
    log_id = get_log_chat_id(chat_id)
    return (
        "🧾 *Mod-log*\n\n"
        f"Estado: *{'ON' if log_id else 'OFF'}*\n"
        f"Log chat_id: `{log_id}`\n\n" if log_id else
        "🧾 *Mod-log*\n\nEstado: *OFF*\n\n"
        "Si lo activas aquí, los logs se enviarán a este mismo grupo.\n"
        "Si quieres un canal/grupo separado, te lo hago con un flujo guiado después."
    )


# -------------------- PRIVATE MENU --------------------
def pm_keyboard() -> InlineKeyboardMarkup:
    add_group_url = None
    if BOT_USERNAME and BOT_USERNAME != "TU_BOT_USERNAME_AQUI":
        add_group_url = f"https://t.me/{BOT_USERNAME}?startgroup=1"

    row1 = [
        InlineKeyboardButton("📘 Comandos", callback_data="pm:help"),
        InlineKeyboardButton("⚙️ Configurar", callback_data="pm:configinfo"),
    ]
    row2 = []
    if add_group_url:
        row2.append(InlineKeyboardButton("➕ Añadir a un grupo", url=add_group_url))
    row2.append(InlineKeyboardButton("🛡️ Permisos", callback_data="pm:perms"))
    return InlineKeyboardMarkup([row1, row2])


def pm_intro_text() -> str:
    return (
        "👋 Soy un bot de moderación para grupos.\n\n"
        "Funciones:\n"
        "• warns + auto-ban\n"
        "• banned words (borra + warn automático)\n"
        "• mute/ban/unban\n"
        "• mod-log\n\n"
        "👉 En un grupo usa /config para abrir el menú."
    )


def pm_help_text() -> str:
    return (
        "📘 Comandos (admins)\n\n"
        "• /config → menú completo con botones\n"
        "• /warn (reply) <razón>\n"
        "• /warns (reply)\n"
        "• /unwarn (reply)\n"
        "• /clearwarns (reply)\n"
        "• /mute (reply) <minutos> <razón opcional>\n"
        "• /ban (reply) <razón>\n"
        "• /unban <user_id>  (o reply)\n"
    )


def pm_config_info_text() -> str:
    return "En el grupo escribe /config para abrir el menú. Solo admins pueden usarlo."


def pm_perms_text() -> str:
    return (
        "🛡️ Permisos del bot\n\n"
        "Para que TODO funcione, el bot debe ser admin y tener:\n"
        "• Delete messages (banned words)\n"
        "• Ban users (ban/auto-ban)\n"
        "• Restrict members (mute)\n"
    )


# -------------------- COMMANDS --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat and chat.type == ChatType.PRIVATE:
        return await update.effective_message.reply_text(pm_intro_text(), reply_markup=pm_keyboard())
    await update.effective_message.reply_text("🤖 Bot activo. Admins: /config")


async def config_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_group(update):
        return await update.effective_message.reply_text("Este comando solo funciona en grupos.")
    if not await is_admin(update, context):
        return await update.effective_message.reply_text("❌ Solo administradores pueden configurar.")

    chat_id = update.effective_chat.id
    context.chat_data[TEMP_LIMIT_KEY] = get_warn_limit(chat_id)
    context.chat_data[STATE_KEY] = STATE_NONE

    await update.effective_message.reply_text(
        config_header_text(chat_id),
        reply_markup=main_config_keyboard(),
        parse_mode="Markdown",
    )


async def warn_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_group(update):
        return await update.effective_message.reply_text("Solo en grupos.")
    if not await is_admin(update, context):
        return await update.effective_message.reply_text("❌ Solo admins.")

    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    target_id = target_user_id_from_reply(update)
    if not target_id:
        return await update.effective_message.reply_text("Responde al mensaje del usuario: /warn <razón>")

    reason = " ".join(context.args).strip() if context.args else None
    add_warn(chat_id, target_id, admin_id, reason)

    total = count_warns(chat_id, target_id)
    limit = get_warn_limit(chat_id)
    await update.effective_message.reply_text(f"⚠️ Warn añadido. {total}/{limit}\nUsuario: {target_id}\nRazón: {reason or '(sin razón)'}")

    await send_modlog(context, chat_id, f"⚠️ WARN | admin {admin_id} → user {target_id} | {total}/{limit} | {reason or '(sin razón)'}")
    await maybe_autoban_after_warn(update, context, chat_id, target_id, admin_id, source="autowarn")


async def warns_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_group(update):
        return await update.effective_message.reply_text("Solo en grupos.")
    if not await is_admin(update, context):
        return await update.effective_message.reply_text("❌ Solo admins.")

    chat_id = update.effective_chat.id
    target_id = target_user_id_from_reply(update)
    if not target_id:
        return await update.effective_message.reply_text("Responde al mensaje del usuario: /warns")

    total = count_warns(chat_id, target_id)
    limit = get_warn_limit(chat_id)
    rows = list_warns(chat_id, target_id, limit=10)

    if not rows:
        return await update.effective_message.reply_text("✅ Este usuario no tiene warns.")

    lines = [f"📋 Warns de {target_id}: {total}/{limit}\n"]
    for r in rows:
        reason = r["reason"] if r["reason"] else "(sin razón)"
        lines.append(f"• #{r['id']} — {reason}")
    await update.effective_message.reply_text("\n".join(lines))


async def unwarn_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_group(update):
        return await update.effective_message.reply_text("Solo en grupos.")
    if not await is_admin(update, context):
        return await update.effective_message.reply_text("❌ Solo admins.")

    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    target_id = target_user_id_from_reply(update)
    if not target_id:
        return await update.effective_message.reply_text("Responde al mensaje del usuario: /unwarn")

    if not remove_last_warn(chat_id, target_id):
        return await update.effective_message.reply_text("✅ Ese usuario no tiene warns para quitar.")

    total = count_warns(chat_id, target_id)
    limit = get_warn_limit(chat_id)
    await update.effective_message.reply_text(f"✅ Warn quitado. {total}/{limit}\nUsuario: {target_id}")
    await send_modlog(context, chat_id, f"✅ UNWARN | admin {admin_id} → user {target_id} | {total}/{limit}")


async def clearwarns_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_group(update):
        return await update.effective_message.reply_text("Solo en grupos.")
    if not await is_admin(update, context):
        return await update.effective_message.reply_text("❌ Solo admins.")

    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    target_id = target_user_id_from_reply(update)
    if not target_id:
        return await update.effective_message.reply_text("Responde al mensaje del usuario: /clearwarns")

    deleted = clear_warns(chat_id, target_id)
    await update.effective_message.reply_text(f"🧹 Warns borrados: {deleted}\nUsuario: {target_id}")
    await send_modlog(context, chat_id, f"🧹 CLEARWARNS | admin {admin_id} → user {target_id} | borrados {deleted}")


async def mute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_group(update):
        return await update.effective_message.reply_text("Solo en grupos.")
    if not await is_admin(update, context):
        return await update.effective_message.reply_text("❌ Solo admins.")

    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    target_id = target_user_id_from_reply(update)
    if not target_id:
        return await update.effective_message.reply_text("Responde al mensaje del usuario: /mute <minutos> <razón opcional>")

    if not context.args or not context.args[0].isdigit():
        return await update.effective_message.reply_text("Uso: /mute <minutos> <razón opcional>")

    minutes = clamp(int(context.args[0]), 1, MAX_MUTE_MINUTES)
    reason = " ".join(context.args[1:]).strip() if len(context.args) > 1 else None
    until_date = datetime.now(timezone.utc) + timedelta(minutes=minutes)

    try:
        await context.bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=target_id,
            permissions=ChatPermissions(
                can_send_messages=False,
                can_send_audios=False,
                can_send_documents=False,
                can_send_photos=False,
                can_send_videos=False,
                can_send_video_notes=False,
                can_send_voice_notes=False,
                can_send_polls=False,
                can_send_other_messages=False,
                can_add_web_page_previews=False,
                can_change_info=False,
                can_invite_users=False,
                can_pin_messages=False,
                can_manage_topics=False,
            ),
            until_date=until_date,
        )
        await update.effective_message.reply_text(f"🔇 Mute {minutes} min\nUsuario: {target_id}\nRazón: {reason or '(sin razón)'}")
        await send_modlog(context, chat_id, f"🔇 MUTE | admin {admin_id} → user {target_id} | {minutes} min | {reason or '(sin razón)'}")
    except Exception as e:
        await update.effective_message.reply_text(f"⚠️ No pude silenciar: {e}")


async def ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_group(update):
        return await update.effective_message.reply_text("Solo en grupos.")
    if not await is_admin(update, context):
        return await update.effective_message.reply_text("❌ Solo admins.")

    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    target_id = target_user_id_from_reply(update)
    if not target_id:
        return await update.effective_message.reply_text("Responde al mensaje del usuario: /ban <razón>")

    reason = " ".join(context.args).strip() if context.args else None
    try:
        await context.bot.ban_chat_member(chat_id=chat_id, user_id=target_id)
        add_ban(chat_id, target_id, admin_id, reason, source="manual")
        await update.effective_message.reply_text(f"⛔ Ban aplicado\nUsuario: {target_id}\nRazón: {reason or '(sin razón)'}")
        await send_modlog(context, chat_id, f"⛔ BAN | admin {admin_id} → user {target_id} | {reason or '(sin razón)'}")
    except Exception as e:
        await update.effective_message.reply_text(f"⚠️ No pude banear: {e}")


async def unban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_group(update):
        return await update.effective_message.reply_text("Solo en grupos.")
    if not await is_admin(update, context):
        return await update.effective_message.reply_text("❌ Solo admins.")

    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id

    # por reply o por user_id
    target_id = target_user_id_from_reply(update)
    if target_id is None:
        if context.args and context.args[0].isdigit():
            target_id = int(context.args[0])
        else:
            return await update.effective_message.reply_text("Uso: /unban <user_id>  (o respondiendo a un mensaje)")

    reason = " ".join(context.args[1:]).strip() if (context.args and len(context.args) > 1) else None

    try:
        await context.bot.unban_chat_member(chat_id=chat_id, user_id=target_id)
        add_unban(chat_id, target_id, admin_id, reason)
        await update.effective_message.reply_text(f"✅ Unban aplicado\nUsuario: {target_id}\nRazón: {reason or '(sin razón)'}")
        await send_modlog(context, chat_id, f"✅ UNBAN | admin {admin_id} → user {target_id} | {reason or '(sin razón)'}")
    except Exception as e:
        await update.effective_message.reply_text(f"⚠️ No pude desbanear: {e}")


# -------------------- BANNED WORDS ENFORCEMENT --------------------
async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Detecta banned words, borra mensaje, da warn y autoban si corresponde."""
    if not update.effective_chat or update.effective_chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    if not update.effective_message or not update.effective_message.text:
        return

    chat_id = update.effective_chat.id
    user = update.effective_user
    if not user:
        return

    # no castigar admins/owner
    if await is_admin(update, context, user_id=user.id):
        return

    text = update.effective_message.text.lower()
    words = bw_list(chat_id)
    if not words:
        return

    hit = None
    for w in words:
        if w and w in text:
            hit = w
            break
    if not hit:
        return

    # 1) borrar mensaje
    try:
        await update.effective_message.delete()
    except Exception:
        # si no se puede borrar, seguimos con warn (pero ideal tener permiso delete)
        pass

    # 2) warn automático
    reason = f"banned word: {hit}"
    add_warn(chat_id, user.id, warned_by=0, reason=reason)  # 0 = automático

    total = count_warns(chat_id, user.id)
    limit = get_warn_limit(chat_id)

    # aviso breve en el chat
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🚫 Mensaje eliminado. ⚠️ Warn {total}/{limit} para {user.id} (palabra prohibida: {hit})",
        )
    except Exception:
        pass

    await send_modlog(context, chat_id, f"🚫 BANNED WORD | user {user.id} | hit '{hit}' | warn {total}/{limit}")

    # 3) autoban si llega al límite
    # actor_id=0 (automático) y source='banned_word'
    await maybe_autoban_after_warn(update, context, chat_id, user.id, actor_id=0, source="banned_word")


# -------------------- CALLBACKS (MENÚ COMPLETO + PM) --------------------
async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()

    data = query.data or ""
    chat = query.message.chat if query.message else None

    # PM menu
    if chat and chat.type == ChatType.PRIVATE and data.startswith("pm:"):
        if data == "pm:help":
            return await query.edit_message_text(pm_help_text(), reply_markup=pm_keyboard())
        if data == "pm:configinfo":
            return await query.edit_message_text(pm_config_info_text(), reply_markup=pm_keyboard())
        if data == "pm:perms":
            return await query.edit_message_text(pm_perms_text(), reply_markup=pm_keyboard())
        return

    # Config menus (solo grupos)
    if not chat or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return

    # seguridad: solo admins pueden usar los botones del menú
    fake_update = Update(update.update_id, callback_query=query)
    if not await is_admin(fake_update, context):
        return await query.edit_message_text("❌ Solo administradores pueden usar este menú.")

    chat_id = chat.id

    # cerrar
    if data == "cfg:close":
        context.chat_data[STATE_KEY] = STATE_NONE
        return await query.edit_message_text("✅ Menú cerrado.")

    # volver
    if data == "cfg:back":
        context.chat_data[STATE_KEY] = STATE_NONE
        return await query.edit_message_text(
            config_header_text(chat_id),
            reply_markup=main_config_keyboard(),
            parse_mode="Markdown",
        )

    # abrir sub-menus
    if data == "cfg:menu:warn":
        temp = context.chat_data.get(TEMP_LIMIT_KEY, get_warn_limit(chat_id))
        return await query.edit_message_text(
            warn_menu_text(chat_id, temp),
            reply_markup=warn_menu_keyboard(),
            parse_mode="Markdown",
        )

    if data == "cfg:menu:bw":
        context.chat_data[STATE_KEY] = STATE_NONE
        return await query.edit_message_text(
            "🚫 *Banned words*\n\nElige una opción:",
            reply_markup=bw_menu_keyboard(),
            parse_mode="Markdown",
        )

    if data == "cfg:menu:log":
        is_on = bool(get_log_chat_id(chat_id))
        return await query.edit_message_text(
            log_menu_text(chat_id),
            reply_markup=log_menu_keyboard(is_on),
            parse_mode="Markdown",
        )

    # warn limit adjustments
    if data.startswith("cfg:warn:"):
        temp = int(context.chat_data.get(TEMP_LIMIT_KEY, get_warn_limit(chat_id)))

        if data == "cfg:warn:inc":
            temp = clamp(temp + 1, MIN_WARN_LIMIT, MAX_WARN_LIMIT)
        elif data == "cfg:warn:dec":
            temp = clamp(temp - 1, MIN_WARN_LIMIT, MAX_WARN_LIMIT)
        elif data.startswith("cfg:warn:set:"):
            try:
                v = int(data.split(":")[-1])
                temp = clamp(v, MIN_WARN_LIMIT, MAX_WARN_LIMIT)
            except ValueError:
                pass
        elif data == "cfg:warn:save":
            set_warn_limit(chat_id, clamp(temp, MIN_WARN_LIMIT, MAX_WARN_LIMIT))
            context.chat_data[TEMP_LIMIT_KEY] = get_warn_limit(chat_id)
            return await query.edit_message_text(
                config_header_text(chat_id),
                reply_markup=main_config_keyboard(),
                parse_mode="Markdown",
            )

        context.chat_data[TEMP_LIMIT_KEY] = temp
        return await query.edit_message_text(
            warn_menu_text(chat_id, temp),
            reply_markup=warn_menu_keyboard(),
            parse_mode="Markdown",
        )

    # banned words actions
    if data == "cfg:bw:view":
        return await query.edit_message_text(
            bw_view_text(chat_id),
            reply_markup=bw_menu_keyboard(),
            parse_mode="Markdown",
        )

    if data == "cfg:bw:add":
        context.chat_data[STATE_KEY] = STATE_ADD_BW
        return await query.edit_message_text(
            "➕ Envíame la palabra a *agregar* (un solo texto).\n\nEj: `spam`\n\n(Escribe la palabra ahora en el chat)",
            reply_markup=bw_menu_keyboard(),
            parse_mode="Markdown",
        )

    if data == "cfg:bw:remove":
        context.chat_data[STATE_KEY] = STATE_REMOVE_BW
        return await query.edit_message_text(
            "➖ Envíame la palabra a *quitar*.\n\nEj: `spam`\n\n(Escribe la palabra ahora en el chat)",
            reply_markup=bw_menu_keyboard(),
            parse_mode="Markdown",
        )

    # log actions
    if data == "cfg:log:on_here":
        set_log_chat_id(chat_id, chat_id)
        return await query.edit_message_text(
            log_menu_text(chat_id),
            reply_markup=log_menu_keyboard(True),
            parse_mode="Markdown",
        )

    if data == "cfg:log:off":
        set_log_chat_id(chat_id, None)
        return await query.edit_message_text(
            log_menu_text(chat_id),
            reply_markup=log_menu_keyboard(False),
            parse_mode="Markdown",
        )

    if data == "cfg:log:test":
        await send_modlog(context, chat_id, f"✅ LOGTEST OK | grupo {chat_id}")
        return await query.edit_message_text(
            log_menu_text(chat_id) + "\n\n✅ Envié un mensaje de prueba al log.",
            reply_markup=log_menu_keyboard(bool(get_log_chat_id(chat_id))),
            parse_mode="Markdown",
        )


# -------------------- STATE INPUT HANDLER (ADD/REMOVE WORDS) --------------------
async def handle_state_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cuando el admin pulsa botones para add/remove, aquí leemos la palabra que escribe."""
    if not is_group(update):
        return
    if not update.effective_message or not update.effective_message.text:
        return

    state = context.chat_data.get(STATE_KEY, STATE_NONE)
    if state not in (STATE_ADD_BW, STATE_REMOVE_BW):
        return

    # solo admins pueden completar el flujo
    if not await is_admin(update, context):
        return

    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    word = normalize_word(update.effective_message.text)

    if not word:
        context.chat_data[STATE_KEY] = STATE_NONE
        return await update.effective_message.reply_text("❌ Palabra inválida. Intenta de nuevo desde /config.")

    if state == STATE_ADD_BW:
        ok = bw_add(chat_id, word, admin_id)
        context.chat_data[STATE_KEY] = STATE_NONE
        if ok:
            await update.effective_message.reply_text(f"✅ Agregada: {word}")
            await send_modlog(context, chat_id, f"➕ BANNED WORD ADD | admin {admin_id} | '{word}'")
        else:
            await update.effective_message.reply_text("⚠️ Esa palabra ya estaba en la lista (o inválida).")
        return

    if state == STATE_REMOVE_BW:
        ok = bw_remove(chat_id, word)
        context.chat_data[STATE_KEY] = STATE_NONE
        if ok:
            await update.effective_message.reply_text(f"✅ Quitada: {word}")
            await send_modlog(context, chat_id, f"➖ BANNED WORD REMOVE | admin {admin_id} | '{word}'")
        else:
            await update.effective_message.reply_text("⚠️ Esa palabra no estaba en la lista.")
        return


# -------------------- MAIN --------------------
def main():
    init_db()

    app = Application.builder().token(TOKEN).build()

    # base
    app.add_handler(CommandHandler("start", start))

    # config/menu
    app.add_handler(CommandHandler("config", config_cmd))
    app.add_handler(CallbackQueryHandler(callbacks))

    # moderation
    app.add_handler(CommandHandler("warn", warn_cmd))
    app.add_handler(CommandHandler("warns", warns_cmd))
    app.add_handler(CommandHandler("unwarn", unwarn_cmd))
    app.add_handler(CommandHandler("clearwarns", clearwarns_cmd))
    app.add_handler(CommandHandler("mute", mute_cmd))
    app.add_handler(CommandHandler("ban", ban_cmd))
    app.add_handler(CommandHandler("unban", unban_cmd))

    # state input (para add/remove palabras)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_state_input), group=0)

    # enforcement banned words (mensajes normales)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_group_message), group=1)

    print("🤖 Bot iniciado...")
    app.run_polling()


if __name__ == "__main__":
    main()