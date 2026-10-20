import hashlib
import hmac
import json
import os
import sqlite3
import threading
import time
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlparse

from telebot import TeleBot, types

from btns import (
    ACTIVE_CHAT,
    ADMIN_ADD_TEACHER,
    ADMIN_LOGOUT,
    ADMIN_PENDING_APPS,
    ADMIN_TEACHERS_LIST,
    BACK,
    BUSY,
    CHOOSE_SUBJECT,
    COMPLAINT,
    END_CHAT,
    FREE,
    MINI_APP_LABEL,
    MINI_APP_URL,
    MY_QUESTIONS,
    MY_RATINGS,
    PROFILE,
    REDIRECT_QUESTION,
    ROLE_STUDENT,
    ROLE_TEACHER,
    SEND_QUESTION,
    SUBJECTS,
    TEACHER_RATING,
    admin_menu,
    btn,
    chat_menu,
    oqituvchi_menu,
    oquvchi_menu,
    savollar_menu,
)

TOKEN = os.getenv("BOT_TOKEN", "").strip()
DB_NAME = os.getenv("BOT_DB", "bot.db")
WEBAPP_HOST = os.getenv("WEBAPP_HOST", "0.0.0.0")
WEBAPP_PORT = int(os.getenv("PORT", os.getenv("WEBAPP_PORT", "8080")))
WEBAPP_DIR = Path(__file__).resolve().parent / "webapp"
WEBAPP_ORIGIN = os.getenv("WEBAPP_ORIGIN", MINI_APP_URL).rstrip("/")

# --- Admin panel sozlamalari ---
# Admin panelga faqat shu login/parol bilan kirish mumkin.
ADMIN_LOGIN = os.getenv("ADMIN_LOGIN", "admin1234")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "1234admin")

ADMIN_CONTACT_USERNAME = "hoshimov_kamronbek"


STALE_CHAT_MINUTES = int(os.getenv("STALE_CHAT_MINUTES", "20"))
STALE_CHAT_CHECK_SECONDS = 60

bot = TeleBot(token=TOKEN)


class SessionDict(dict):


    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        save_session(key, value)

    def pop(self, key, *default):
        result = super().pop(key, *default)
        delete_session(key)
        return result


user_states = SessionDict()
active_chats = {}


def now():
    return datetime.now().isoformat(timespec="seconds")


def hash_password(password):
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def is_valid_password(password):
    return (
        len(password) >= 8
        and any(ch.isupper() for ch in password)
        and any(ch.isdigit() for ch in password)
        and not password.isdigit()
    )


def get_db():
    db = sqlite3.connect(DB_NAME)
    db.row_factory = sqlite3.Row
    return db


def ensure_column(db, table, column, definition):
    columns = [row["name"] for row in db.execute(f"PRAGMA table_info({table})")]
    if column not in columns:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                name TEXT,
                role TEXT,
                created_at TEXT
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS teachers (
                user_id INTEGER PRIMARY KEY,
                subject TEXT,
                is_free INTEGER DEFAULT 1,
                rating_total INTEGER DEFAULT 0,
                rating_count INTEGER DEFAULT 0,
                helped_count INTEGER DEFAULT 0
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER,
                subject TEXT,
                question_type TEXT,
                question_text TEXT,
                photo_id TEXT,
                status TEXT DEFAULT 'new',
                teacher_id INTEGER,
                created_at TEXT
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question_id INTEGER,
                student_id INTEGER,
                teacher_id INTEGER,
                status TEXT DEFAULT 'active',
                started_at TEXT,
                ended_at TEXT
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS ratings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER UNIQUE,
                student_id INTEGER,
                teacher_id INTEGER,
                score INTEGER,
                created_at TEXT
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS question_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question_id INTEGER,
                teacher_id INTEGER,
                message_id INTEGER,
                created_at TEXT
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                created_at TEXT
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS teacher_applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                name TEXT,
                subject TEXT,
                doc_type TEXT,
                file_id TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT,
                reviewed_at TEXT,
                reviewed_by INTEGER
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                user_id INTEGER PRIMARY KEY,
                state_json TEXT,
                updated_at TEXT
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS complaints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reporter_id INTEGER,
                reported_id INTEGER,
                chat_id INTEGER,
                text TEXT,
                created_at TEXT
            )
        """)

        ensure_column(db, "questions", "created_at", "TEXT")
        ensure_column(db, "teachers", "rating_total", "INTEGER DEFAULT 0")
        ensure_column(db, "teachers", "rating_count", "INTEGER DEFAULT 0")
        ensure_column(db, "teachers", "helped_count", "INTEGER DEFAULT 0")
        ensure_column(db, "users", "login", "TEXT")
        ensure_column(db, "users", "password_hash", "TEXT")
        ensure_column(db, "chats", "reminded", "INTEGER DEFAULT 0")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_login ON users(login)")


def save_user(message, role=None, name=None, login=None, password_hash=None):
    user = message.from_user
    user_name = name or user.first_name or user.username or str(user.id)
    with get_db() as db:
        db.execute("""
            INSERT INTO users (user_id, name, role, login, password_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                name = COALESCE(excluded.name, users.name),
                role = COALESCE(excluded.role, users.role),
                login = COALESCE(excluded.login, users.login),
                password_hash = COALESCE(excluded.password_hash, users.password_hash)
        """, (user.id, user_name, role, login, password_hash, now()))


def get_user(user_id):
    with get_db() as db:
        return db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()


def get_user_by_login(login):
    with get_db() as db:
        return db.execute("SELECT * FROM users WHERE login = ?", (login,)).fetchone()


def migrate_user_id(old_id, new_id):
    """O'quvchi/ustoz boshqa Telegram hisobidan /login orqali profilini
    tikladi. Barcha jadvallardagi eski user_id'ni yangisiga ko'chiradi,
    shu bilan savollar, suhbatlar va reyting tarixi saqlanib qoladi."""
    if old_id == new_id:
        return True

    with get_db() as db:
        already_exists = db.execute("SELECT 1 FROM users WHERE user_id = ?", (new_id,)).fetchone()
        if already_exists:
            return False

        db.execute("UPDATE users SET user_id = ? WHERE user_id = ?", (new_id, old_id))
        db.execute("UPDATE teachers SET user_id = ? WHERE user_id = ?", (new_id, old_id))
        db.execute("UPDATE questions SET student_id = ? WHERE student_id = ?", (new_id, old_id))
        db.execute("UPDATE questions SET teacher_id = ? WHERE teacher_id = ?", (new_id, old_id))
        db.execute("UPDATE chats SET student_id = ? WHERE student_id = ?", (new_id, old_id))
        db.execute("UPDATE chats SET teacher_id = ? WHERE teacher_id = ?", (new_id, old_id))
        db.execute("UPDATE ratings SET student_id = ? WHERE student_id = ?", (new_id, old_id))
        db.execute("UPDATE ratings SET teacher_id = ? WHERE teacher_id = ?", (new_id, old_id))
        db.execute("UPDATE question_notifications SET teacher_id = ? WHERE teacher_id = ?", (new_id, old_id))
        db.execute("UPDATE teacher_applications SET user_id = ? WHERE user_id = ?", (new_id, old_id))
        db.execute("UPDATE admins SET user_id = ? WHERE user_id = ?", (new_id, old_id))

    return True


def delete_teacher(user_id):
    with get_db() as db:
        db.execute("DELETE FROM teachers WHERE user_id = ?", (user_id,))


# ------------------ Sessiyalarni saqlash (bot qayta ishga tushsa ham) ------------------

def save_session(user_id, state):
    with get_db() as db:
        db.execute("""
            INSERT INTO sessions (user_id, state_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                state_json = excluded.state_json,
                updated_at = excluded.updated_at
        """, (user_id, json.dumps(state), now()))


def delete_session(user_id):
    with get_db() as db:
        db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def load_all_sessions():
    with get_db() as db:
        rows = db.execute("SELECT user_id, state_json FROM sessions").fetchall()
    result = {}
    for row in rows:
        try:
            result[row["user_id"]] = json.loads(row["state_json"])
        except (TypeError, ValueError):
            continue
    return result


def restore_runtime_state():
    """Bot ishga tushganda oldingi sessiyalarni va faol suhbatlarni
    xotiraga qaytadan yuklaydi — shu tufayli server qayta ishga tushsa
    ham (crash, deploy) foydalanuvchilar hech narsani yo'qotmaydi."""
    for uid, state in load_all_sessions().items():
        dict.__setitem__(user_states, uid, state)

    with get_db() as db:
        rows = db.execute("SELECT student_id, teacher_id FROM chats WHERE status = 'active'").fetchall()
    for row in rows:
        active_chats[row["student_id"]] = row["teacher_id"]
        active_chats[row["teacher_id"]] = row["student_id"]

    print(f"[UyVazifa] {len(user_states)} sessiya va {len(rows)} faol suhbat tiklandi.")


def delete_account(user_id):
    """Foydalanuvchining profilini va unga bog'liq barcha ma'lumotlarni
    (ustozlik, savollar, suhbatlar, baholar, arizalar) butunlay o'chiradi."""
    with get_db() as db:
        db.execute("DELETE FROM ratings WHERE student_id = ? OR teacher_id = ?", (user_id, user_id))
        db.execute("DELETE FROM chats WHERE student_id = ? OR teacher_id = ?", (user_id, user_id))
        db.execute("DELETE FROM question_notifications WHERE teacher_id = ?", (user_id,))
        db.execute("DELETE FROM questions WHERE student_id = ? OR teacher_id = ?", (user_id, user_id))
        db.execute("DELETE FROM teacher_applications WHERE user_id = ?", (user_id,))
        db.execute("DELETE FROM teachers WHERE user_id = ?", (user_id,))
        db.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
        db.execute("DELETE FROM users WHERE user_id = ?", (user_id,))

    active_chats.pop(user_id, None)
    user_states.pop(user_id, None)


# ------------------ Admin yordamchi funksiyalari ------------------

def save_admin(user_id, username=None):
    with get_db() as db:
        db.execute("""
            INSERT INTO admins (user_id, username, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = COALESCE(excluded.username, admins.username)
        """, (user_id, username, now()))


def is_admin(user_id):
    with get_db() as db:
        row = db.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,)).fetchone()
        return row is not None


def get_admin_ids():
    with get_db() as db:
        return [row["user_id"] for row in db.execute("SELECT user_id FROM admins").fetchall()]


def resolve_admin_contact_id():
    """@hoshimov_kamronbek bilan bot avval xabarlashgan bo'lsa, uning chat_id'sini topishga urinadi."""
    try:
        chat = bot.get_chat(f"@{ADMIN_CONTACT_USERNAME}")
        return chat.id
    except Exception:
        return None


# ------------------ Ustoz arizalari (diplom tasdiqlash) ------------------

def save_teacher_application(user_id, name, subject, doc_type, file_id):
    with get_db() as db:
        cursor = db.execute("""
            INSERT INTO teacher_applications (user_id, name, subject, doc_type, file_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, name, subject, doc_type, file_id, now()))
        return cursor.lastrowid


def get_application(app_id):
    with get_db() as db:
        return db.execute("""
            SELECT * FROM teacher_applications WHERE id = ?
        """, (app_id,)).fetchone()


def get_latest_application(user_id):
    with get_db() as db:
        return db.execute("""
            SELECT * FROM teacher_applications
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 1
        """, (user_id,)).fetchone()


def get_pending_applications():
    with get_db() as db:
        return db.execute("""
            SELECT * FROM teacher_applications WHERE status = 'pending' ORDER BY id
        """).fetchall()


def update_application_status(app_id, status, reviewed_by):
    with get_db() as db:
        cursor = db.execute("""
            UPDATE teacher_applications
            SET status = ?, reviewed_at = ?, reviewed_by = ?
            WHERE id = ? AND status = 'pending'
        """, (status, now(), reviewed_by, app_id))
        return cursor.rowcount > 0


def save_teacher(user_id, subject=None, is_free=1):
    with get_db() as db:
        db.execute("""
            INSERT INTO teachers (user_id, subject, is_free)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                subject = COALESCE(excluded.subject, teachers.subject),
                is_free = excluded.is_free
        """, (user_id, subject, is_free))


def set_teacher_free(user_id, is_free):
    with get_db() as db:
        db.execute("UPDATE teachers SET is_free = ? WHERE user_id = ?", (is_free, user_id))


def save_question(student_id, subject, question_type, question_text=None, photo_id=None):
    with get_db() as db:
        cursor = db.execute("""
            INSERT INTO questions
                (student_id, subject, question_type, question_text, photo_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (student_id, subject, question_type, question_text, photo_id, now()))
        return cursor.lastrowid


def save_question_notification(question_id, teacher_id, message_id):
    with get_db() as db:
        db.execute("""
            INSERT INTO question_notifications (question_id, teacher_id, message_id, created_at)
            VALUES (?, ?, ?, ?)
        """, (question_id, teacher_id, message_id, now()))


def get_question_notifications(question_id, exclude_teacher_id=None):
    with get_db() as db:
        return db.execute("""
            SELECT teacher_id, message_id
            FROM question_notifications
            WHERE question_id = ? AND teacher_id != ?
        """, (question_id, exclude_teacher_id or 0)).fetchall()


def delete_question_notifications(question_id):
    with get_db() as db:
        db.execute("DELETE FROM question_notifications WHERE question_id = ?", (question_id,))


def remove_question_messages(question_id, exclude_teacher_id=None):
    for notification in get_question_notifications(question_id, exclude_teacher_id):
        try:
            bot.delete_message(notification["teacher_id"], notification["message_id"])
        except Exception:
            pass

    delete_question_notifications(question_id)


def find_free_teachers(subject, student_id=None):
    with get_db() as db:
        cursor = db.execute("""
            SELECT user_id
            FROM teachers
            WHERE subject = ? AND is_free = 1 AND user_id != ?
            ORDER BY rating_count DESC, rating_total DESC
        """, (subject, student_id or 0))
        return [row["user_id"] for row in cursor.fetchall()]


def get_question(question_id):
    with get_db() as db:
        return db.execute("""
            SELECT id, student_id, subject, question_type, question_text, photo_id, status, teacher_id
            FROM questions
            WHERE id = ?
        """, (question_id,)).fetchone()


def get_active_chat(user_id):
    with get_db() as db:
        return db.execute("""
            SELECT *
            FROM chats
            WHERE status = 'active' AND (student_id = ? OR teacher_id = ?)
            ORDER BY id DESC
            LIMIT 1
        """, (user_id, user_id)).fetchone()


def accept_question(question_id, teacher_id):
    with get_db() as db:
        cursor = db.execute("""
            UPDATE questions
            SET status = 'accepted', teacher_id = ?
            WHERE id = ? AND status = 'new'
        """, (teacher_id, question_id))
        if cursor.rowcount == 0:
            return None

        question = db.execute("""
            SELECT student_id
            FROM questions
            WHERE id = ?
        """, (question_id,)).fetchone()

        db.execute("UPDATE teachers SET is_free = 0 WHERE user_id = ?", (teacher_id,))
        chat_cursor = db.execute("""
            INSERT INTO chats (question_id, student_id, teacher_id, started_at)
            VALUES (?, ?, ?, ?)
        """, (question_id, question["student_id"], teacher_id, now()))

        return question["student_id"], chat_cursor.lastrowid


def close_chat(user_id):
    chat = get_active_chat(user_id)
    if not chat:
        return None

    with get_db() as db:
        db.execute("""
            UPDATE chats
            SET status = 'closed', ended_at = ?
            WHERE id = ?
        """, (now(), chat["id"]))
        db.execute("""
            UPDATE questions
            SET status = 'closed'
            WHERE id = ?
        """, (chat["question_id"],))
        db.execute("""
            UPDATE teachers
            SET is_free = 1, helped_count = helped_count + 1
            WHERE user_id = ?
        """, (chat["teacher_id"],))

    active_chats.pop(chat["student_id"], None)
    active_chats.pop(chat["teacher_id"], None)
    return chat


def redirect_chat(teacher_id):
    """Ustoz 'Javob bera olmayman' deganda chaqiriladi: joriy suhbat
    yopiladi, lekin savol YECHILGAN deb belgilanmaydi — u qaytadan
    'new' holatiga o'tadi va boshqa bo'sh ustozlarga yuboriladi."""
    chat = get_active_chat(teacher_id)
    if not chat or chat["teacher_id"] != teacher_id:
        return None

    with get_db() as db:
        db.execute("""
            UPDATE chats
            SET status = 'redirected', ended_at = ?
            WHERE id = ?
        """, (now(), chat["id"]))
        db.execute("""
            UPDATE questions
            SET status = 'new', teacher_id = NULL
            WHERE id = ?
        """, (chat["question_id"],))
        db.execute("""
            UPDATE teachers
            SET is_free = 1
            WHERE user_id = ?
        """, (teacher_id,))

    active_chats.pop(chat["student_id"], None)
    active_chats.pop(chat["teacher_id"], None)
    return chat


def get_student_questions(student_id, limit=10):
    with get_db() as db:
        return db.execute("""
            SELECT subject, question_type, question_text, status, created_at
            FROM questions
            WHERE student_id = ?
            ORDER BY id DESC
            LIMIT ?
        """, (student_id, limit)).fetchall()


def get_oldest_pending_question(subject, exclude_teacher_id=None):
    """Berilgan fan bo'yicha eng eski 'new' (hali qabul qilinmagan)
    savolni topadi — ustoz 'Bo'shman' bosganda ishlatiladi."""
    with get_db() as db:
        return db.execute("""
            SELECT id, student_id, subject, question_type, question_text, photo_id
            FROM questions
            WHERE subject = ? AND status = 'new'
            ORDER BY id ASC
            LIMIT 1
        """, (subject,)).fetchone()


def get_all_teachers():
    with get_db() as db:
        return db.execute("""
            SELECT t.user_id, t.subject, t.is_free, t.rating_total, t.rating_count, t.helped_count, u.name
            FROM teachers t
            LEFT JOIN users u ON u.user_id = t.user_id
            ORDER BY u.name
        """).fetchall()


def save_complaint(reporter_id, reported_id, chat_id, text):
    with get_db() as db:
        db.execute("""
            INSERT INTO complaints (reporter_id, reported_id, chat_id, text, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (reporter_id, reported_id, chat_id, text, now()))


def get_stale_active_chats(threshold_minutes):
    with get_db() as db:
        rows = db.execute("SELECT * FROM chats WHERE status = 'active' AND reminded = 0").fetchall()

    stale = []
    threshold_seconds = threshold_minutes * 60
    for row in rows:
        try:
            started = datetime.fromisoformat(row["started_at"])
        except (TypeError, ValueError):
            continue
        if (datetime.now() - started).total_seconds() >= threshold_seconds:
            stale.append(row)
    return stale


def mark_chat_reminded(chat_id):
    with get_db() as db:
        db.execute("UPDATE chats SET reminded = 1 WHERE id = ?", (chat_id,))


def save_rating(chat_id, student_id, teacher_id, score):
    with get_db() as db:
        try:
            db.execute("""
                INSERT INTO ratings (chat_id, student_id, teacher_id, score, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (chat_id, student_id, teacher_id, score, now()))
        except sqlite3.IntegrityError:
            return False

        db.execute("""
            UPDATE teachers
            SET rating_total = rating_total + ?, rating_count = rating_count + 1
            WHERE user_id = ?
        """, (score, teacher_id))
        return True


def get_teacher_rating(user_id):
    with get_db() as db:
        return db.execute("""
            SELECT subject, rating_total, rating_count, helped_count, is_free
            FROM teachers
            WHERE user_id = ?
        """, (user_id,)).fetchone()


def get_user_role(user_id):
    with get_db() as db:
        user = db.execute("SELECT role FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return user["role"] if user else None


def get_user_name(user_id):
    with get_db() as db:
        user = db.execute("SELECT name FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return user["name"] if user and user["name"] else "Noma'lum"


def get_leaderboard():
    with get_db() as db:
        return db.execute("""
            SELECT u.name, t.subject, t.rating_total, t.rating_count, t.helped_count
            FROM teachers t
            LEFT JOIN users u ON u.user_id = t.user_id
            WHERE t.rating_count > 0
            ORDER BY CAST(t.rating_total AS REAL) / t.rating_count DESC, t.helped_count DESC
            LIMIT 10
        """).fetchall()


def send_question_to_teachers(question_id, student_id, subject, question_type, question_text=None, photo_id=None):
    teachers = find_free_teachers(subject, student_id)

    if not teachers:
        bot.send_message(
            student_id,
            "Hozircha bu fan bo'yicha bo'sh ustoz topilmadi. Savolingiz saqlandi.",
        )
        return

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🤝 Yordam beraman", callback_data=f"accept:{question_id}"))

    for teacher_id in teachers:
        text = (
            "Yangi yordam so'rovi\n"
            f"Fan: {subject}\n"
            f"Savol: {question_text if question_type == 'text' else 'rasm ko`rinishida'}\n\n"
            "O'quvchiga yordam berishni xohlaysizmi?"
        )
        if question_type == "photo":
            sent = bot.send_photo(teacher_id, photo_id, caption=text, reply_markup=markup)
        else:
            sent = bot.send_message(teacher_id, text, reply_markup=markup)
        save_question_notification(question_id, teacher_id, sent.message_id)

    bot.send_message(student_id, "Savolingiz saqlandi. Bo'sh ustozlarga xabar yuborildi.")


# ------------------ Mini App API ------------------

def get_mini_app_user(init_data):
    """Telegram WebApp initData imzosini tekshiradi va undagi userni qaytaradi."""
    fields = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = fields.pop("hash", "")
    if not received_hash:
        return None

    check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", TOKEN.encode("utf-8"), hashlib.sha256).digest()
    expected_hash = hmac.new(secret, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(received_hash, expected_hash):
        return None

    try:
        user = json.loads(fields.get("user", "{}"))
        return user if user.get("id") else None
    except (TypeError, ValueError):
        return None


def ensure_mini_app_user(user):
    user_id = int(user["id"])
    name = " ".join(part for part in (user.get("first_name"), user.get("last_name")) if part).strip()
    name = name or user.get("username") or str(user_id)
    with get_db() as db:
        db.execute(
            """
            INSERT INTO users (user_id, name, role, created_at)
            VALUES (?, ?, 'student', ?)
            ON CONFLICT(user_id) DO NOTHING
            """,
            (user_id, name, now()),
        )
    return user_id


def mini_app_snapshot(user):
    user_id = ensure_mini_app_user(user)
    with get_db() as db:
        profile = db.execute("SELECT name, role FROM users WHERE user_id = ?", (user_id,)).fetchone()
        teacher = db.execute(
            """
            SELECT subject, is_free, rating_total, rating_count, helped_count
            FROM teachers WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        questions = db.execute(
            """
            SELECT id, subject, question_text, status, created_at
            FROM questions
            WHERE student_id = ?
            ORDER BY id DESC
            LIMIT 30
            """,
            (user_id,),
        ).fetchall()
    snapshot = {
        "user": {"id": user_id, "name": profile["name"], "role": profile["role"]},
        "questions": [dict(row) for row in questions],
    }
    if teacher:
        rating = teacher["rating_total"] / teacher["rating_count"] if teacher["rating_count"] else None
        snapshot["teacher"] = {
            "subject": teacher["subject"],
            "is_free": bool(teacher["is_free"]),
            "rating": rating,
            "helped_count": teacher["helped_count"],
        }
    return snapshot


class MiniAppRequestHandler(BaseHTTPRequestHandler):
    server_version = "NomiyoMiniApp/1.0"

    def log_message(self, _format, *_args):
        return

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if WEBAPP_ORIGIN:
            self.send_header("Access-Control-Allow-Origin", WEBAPP_ORIGIN)
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Telegram-Init-Data")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 10000:
                return None
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (TypeError, ValueError, UnicodeDecodeError):
            return None

    def telegram_user(self):
        user = get_mini_app_user(self.headers.get("X-Telegram-Init-Data", ""))
        if not user:
            self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "Telegram tasdiqlashi topilmadi."})
        return user

    def serve_static(self, path):
        relative_path = "index.html" if path == "/" else unquote(path).lstrip("/")
        candidate = (WEBAPP_DIR / relative_path).resolve()
        if WEBAPP_DIR not in candidate.parents or not candidate.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        mime_types = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}
        body = candidate.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime_types.get(candidate.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/bootstrap":
            user = self.telegram_user()
            if user:
                self.send_json(HTTPStatus.OK, mini_app_snapshot(user))
            return
        self.serve_static(path)

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        if WEBAPP_ORIGIN:
            self.send_header("Access-Control-Allow-Origin", WEBAPP_ORIGIN)
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Telegram-Init-Data")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ("/api/questions", "/api/teacher/availability"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        user = self.telegram_user()
        if not user:
            return
        payload = self.read_json() or {}
        if path == "/api/teacher/availability":
            user_id = ensure_mini_app_user(user)
            is_free = payload.get("is_free")
            if not isinstance(is_free, bool):
                self.send_json(HTTPStatus.BAD_REQUEST, {"error": "Holat noto'g'ri."})
                return
            with get_db() as db:
                updated = db.execute(
                    "UPDATE teachers SET is_free = ? WHERE user_id = ?", (int(is_free), user_id)
                ).rowcount
                teacher = db.execute("SELECT subject FROM teachers WHERE user_id = ?", (user_id,)).fetchone()
            if not updated:
                self.send_json(HTTPStatus.FORBIDDEN, {"error": "Ustoz profili topilmadi."})
                return
            if is_free and teacher:
                notify_free_teacher_of_backlog(user_id, teacher["subject"])
            self.send_json(HTTPStatus.OK, {"is_free": is_free})
            return

        subject = str(payload.get("subject", "")).strip()
        question_text = str(payload.get("question_text", "")).strip()
        if subject not in set(SUBJECTS.values()) or not 5 <= len(question_text) <= 1200:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "Savol yoki fan noto'g'ri."})
            return

        user_id = ensure_mini_app_user(user)
        question_id = save_question(user_id, subject, "text", question_text=question_text)
        send_question_to_teachers(question_id, user_id, subject, "text", question_text=question_text)
        with get_db() as db:
            question = db.execute(
                "SELECT id, subject, question_text, status, created_at FROM questions WHERE id = ?", (question_id,)
            ).fetchone()
        self.send_json(HTTPStatus.CREATED, {"question": dict(question)})

    def do_PUT(self):
        if urlparse(self.path).path != "/api/profile":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        user = self.telegram_user()
        if not user:
            return
        payload = self.read_json() or {}
        name = str(payload.get("name", "")).strip()
        if not 1 <= len(name) <= 60:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": "Ism 1 dan 60 belgigacha bo'lishi kerak."})
            return
        user_id = ensure_mini_app_user(user)
        with get_db() as db:
            db.execute("UPDATE users SET name = ? WHERE user_id = ?", (name, user_id))
        self.send_json(HTTPStatus.OK, {"name": name})


def start_mini_app_server():
    httpd = ThreadingHTTPServer((WEBAPP_HOST, WEBAPP_PORT), MiniAppRequestHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"[UyVazifa] Mini App API http://{WEBAPP_HOST}:{WEBAPP_PORT} manzilida ishga tushdi.")


def notify_teacher_about_question(teacher_id, question):
    """Bitta ustozga (masalan, endigina 'Bo'shman' bosgan ustozga)
    kutib turgan bitta savol haqida xabar beradi."""
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🤝 Yordam beraman", callback_data=f"accept:{question['id']}"))

    text = (
        "🔔 Sizni kutayotgan savol topildi!\n"
        f"Fan: {question['subject']}\n"
        f"Savol: {question['question_text'] if question['question_type'] == 'text' else 'rasm ko`rinishida'}\n\n"
        "O'quvchiga yordam berishni xohlaysizmi?"
    )
    if question["question_type"] == "photo":
        sent = bot.send_photo(teacher_id, question["photo_id"], caption=text, reply_markup=markup)
    else:
        sent = bot.send_message(teacher_id, text, reply_markup=markup)
    save_question_notification(question["id"], teacher_id, sent.message_id)


def notify_free_teacher_of_backlog(teacher_id, subject):
    """Ustoz 'Bo'shman' tugmasini bosganda chaqiriladi: agar shu fan
    bo'yicha kutib turgan (hali hech kim javob bermagan) savol bo'lsa,
    ustozga darhol xabar yuboradi."""
    if not subject:
        return
    question = get_oldest_pending_question(subject)
    if question:
        notify_teacher_about_question(teacher_id, question)


def requeue_question_broadcast(question_id, exclude_teacher_id):
    """Savol boshqa ustozga o'tkazilganda (redirect) chaqiriladi:
    savolni shu fan bo'yicha qolgan bo'sh ustozlarga qayta yuboradi."""
    question = get_question(question_id)
    if not question:
        return False

    teachers = find_free_teachers(question["subject"])
    teachers = [t for t in teachers if t != exclude_teacher_id]

    if not teachers:
        return False

    for teacher_id in teachers:
        notify_teacher_about_question(teacher_id, question)
    return True


def send_application_to_admins(app_id):
    application = get_application(app_id)
    if not application:
        return

    admin_ids = set(get_admin_ids())
    contact_id = resolve_admin_contact_id()
    if contact_id:
        admin_ids.add(contact_id)

    if not admin_ids:
        return

    caption = (
        "🆕 Ustozlikka ariza\n"
        f"Ism: {application['name']}\n"
        f"Fan: {application['subject']}\n"
        f"Foydalanuvchi ID: {application['user_id']}\n\n"
        "Ilova qilingan hujjat ustozning bilim/diplomini isbotlaydi. "
        "Tasdiqlaysizmi?"
    )
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("✅ Tasdiqlayman", callback_data=f"tapp_approve:{application['id']}"),
        types.InlineKeyboardButton("❌ Rad etaman", callback_data=f"tapp_reject:{application['id']}"),
    )

    for admin_id in admin_ids:
        try:
            if application["doc_type"] == "photo":
                bot.send_photo(admin_id, application["file_id"], caption=caption, reply_markup=markup)
            else:
                bot.send_document(admin_id, application["file_id"], caption=caption, reply_markup=markup)
        except Exception:
            pass


def send_to_partner(msg, partner_id):
    if msg.content_type == "text":
        bot.send_message(partner_id, msg.text, reply_markup=chat_menu)
    elif msg.content_type == "photo":
        bot.send_photo(partner_id, msg.photo[-1].file_id, caption=msg.caption, reply_markup=chat_menu)
    elif msg.content_type == "document":
        bot.send_document(partner_id, msg.document.file_id, caption=msg.caption, reply_markup=chat_menu)


def send_profile(user_id):
    name = get_user_name(user_id)
    teacher = get_teacher_rating(user_id)
    if teacher:
        rating = "hali baho yo'q"
        if teacher["rating_count"]:
            rating = f"{teacher['rating_total'] / teacher['rating_count']:.1f}/5"
        status = "bo'sh" if teacher["is_free"] else "band"
        bot.send_message(
            user_id,
            "Profil:\n"
            f"Ism: {name}\n"
            f"Rol: o'qituvchi\n"
            f"Fan: {teacher['subject'] or 'tanlanmagan'}\n"
            f"Holat: {status}\n"
            f"Reyting: {rating}\n"
            f"Yordam bergan savollar: {teacher['helped_count']}",
            reply_markup=oqituvchi_menu,
        )
    else:
        application = get_latest_application(user_id)
        if application and application["status"] == "pending":
            bot.send_message(
                user_id,
                f"Profil:\nIsm: {name}\nRol: ustozlikka ariza (⏳ kutilmoqda)\nFan: {application['subject']}",
                reply_markup=btn,
            )
        elif application and application["status"] == "rejected":
            bot.send_message(
                user_id,
                f"Profil:\nIsm: {name}\nRol: o'quvchi\n"
                "Ustozlikka arizangiz rad etilgan edi. Qaytadan urinib ko'rishingiz mumkin.",
                reply_markup=btn,
            )
        else:
            bot.send_message(user_id, f"Profil:\nIsm: {name}\nRol: o'quvchi", reply_markup=oquvchi_menu)


def send_leaderboard(user_id):
    rows = get_leaderboard()
    if not rows:
        bot.send_message(user_id, "Hali baholangan ustozlar yo'q.")
        return

    lines = ["Ustozlar reytingi:"]
    for index, row in enumerate(rows, start=1):
        rating = row["rating_total"] / row["rating_count"]
        lines.append(f"{index}. {row['name'] or 'Ustoz'} | {row['subject']} | {rating:.1f}/5")
    bot.send_message(user_id, "\n".join(lines))


def ask_rating(student_id, chat_id):
    markup = types.InlineKeyboardMarkup(row_width=5)
    buttons = [
        types.InlineKeyboardButton(f"⭐ {score}", callback_data=f"rate:{chat_id}:{score}")
        for score in range(1, 6)
    ]
    markup.add(*buttons)
    bot.send_message(student_id, "Ustozning yordami sizga qanchalik foydali bo'ldi?", reply_markup=markup)


@bot.message_handler(commands=["start"])
def start(msg: types.Message):
    user_id = msg.from_user.id
    existing = get_user(user_id)

    # Bu Telegram hisobida allaqachon profil bor — botni o'chirib qo'ygan
    # yoki qayta ishga tushirgan bo'lsa ham, ma'lumotlari saqlanib qoladi.
    if existing and existing["name"]:
        user_states.pop(user_id, None)
        role = existing["role"]
        if role == "teacher":
            bot.send_message(user_id, f"Xush kelibsiz, {existing['name']}!", reply_markup=oqituvchi_menu)
        elif role == "student":
            bot.send_message(user_id, f"Xush kelibsiz, {existing['name']}!", reply_markup=oquvchi_menu)
        else:
            bot.send_message(
                user_id,
                f"Xush kelibsiz, {existing['name']}! Kim sifatida davom etasiz?",
                reply_markup=btn,
            )
        return

    user_states.pop(user_id, None)
    user_states[user_id] = {"step": "waiting_full_name"}
    bot.send_message(
        msg.chat.id,
        "Assalomu alaykum! Iltimos, ism familiyangizni yozing.",
        reply_markup=types.ReplyKeyboardRemove(),
    )


@bot.message_handler(commands=["app"])
def open_mini_app(msg: types.Message):
    user_id = msg.from_user.id

    if not MINI_APP_URL.startswith("https://"):
        bot.send_message(
            user_id,
            "Mini App hali sozlanmagan. Server administratori MINI_APP_URL "
            "muhit o'zgaruvchisini o'rnatishi kerak.",
        )
        return

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(MINI_APP_LABEL, web_app=types.WebAppInfo(url=MINI_APP_URL)))
    bot.send_message(user_id, "Ilovani quyidagi tugma orqali oching:", reply_markup=markup)


@bot.message_handler(commands=["login"])
def login_entry(msg: types.Message):
    user_id = msg.from_user.id

    if get_user(user_id):
        bot.send_message(
            user_id,
            "Sizda shu Telegram hisobida allaqachon profil mavjud. /start buyrug'i bilan davom etishingiz mumkin.",
        )
        return

    user_states[user_id] = {"step": "recover_waiting_login"}
    bot.send_message(
        user_id,
        "🔑 Profilni tiklash\nAvvalgi hisobingiz login (foydalanuvchi nomi)ni kiriting:",
        reply_markup=types.ReplyKeyboardRemove(),
    )


@bot.message_handler(commands=["delete"])
def delete_entry(msg: types.Message):
    user_id = msg.from_user.id

    if not get_user(user_id):
        bot.send_message(user_id, "Sizda o'chiriladigan profil topilmadi.")
        return

    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("✅ Ha, o'chirilsin", callback_data="delacc_confirm"),
        types.InlineKeyboardButton("❌ Bekor qilish", callback_data="delacc_cancel"),
    )
    bot.send_message(
        user_id,
        "⚠️ Diqqat! Profilingiz, ustozlik ma'lumotlari, savollar va baholar tarixi "
        "BUTUNLAY o'chib ketadi va uni qaytarib bo'lmaydi.\n\n"
        "Rostdan ham o'chirishni xohlaysizmi?",
        reply_markup=markup,
    )


@bot.message_handler(commands=["admin"])
def admin_entry(msg: types.Message):
    user_id = msg.from_user.id

    if is_admin(user_id):
        user_states[user_id] = {"role": "admin"}
        bot.send_message(user_id, "Admin panelga xush kelibsiz.", reply_markup=admin_menu)
        return

    user_states[user_id] = {"step": "admin_waiting_login"}
    bot.send_message(
        user_id,
        "🔐 Admin panelga kirish\nLoginni kiriting:",
        reply_markup=types.ReplyKeyboardRemove(),
    )


@bot.message_handler(commands=["help"])
def help_entry(msg: types.Message):
    user_id = msg.from_user.id
    role = get_user_role(user_id)

    lines = [
        "🤖 UyVazifa bot buyruqlari:\n",
        "/start — botni ishga tushirish / bosh menyuga qaytish",
        "/app — Mini App'ni ochish",
        "/login — profilni boshqa hisobdan tiklash",
        "/delete — profilni butunlay o'chirish",
        "/help — shu yordam matni",
    ]

    if role == "student":
        lines.append("\n🎓 O'quvchi menyusi: savol yuborish, savollaringizni ko'rish, faol suhbat, baholar, profil.")
    elif role == "teacher":
        lines.append("\n👨‍🏫 Ustoz menyusi: fan tanlash, bo'sh/band holat, faol suhbat, reyting, profil.")
        lines.append("Faol suhbatda: agar javob bera olmasangiz, \"⚠️ Javob bera olmayman\" tugmasini bosing — savol boshqa ustozga yuboriladi.")

    if is_admin(user_id):
        lines.append("\n🔐 Admin: /admin buyrug'i orqali panelga kirasiz — arizalarni ko'rish, ustoz qo'shish/o'chirish.")

    bot.send_message(user_id, "\n".join(lines))


@bot.message_handler(content_types=["web_app_data"])
def web_app_data_handler(msg: types.Message):
    user_id = msg.from_user.id

    try:
        payload = json.loads(msg.web_app_data.data or "{}")
    except (TypeError, ValueError):
        bot.send_message(user_id, "Mini App ma'lumoti noto'g'ri keldi. Iltimos, qaytadan urinib ko'ring.")
        return

    if payload.get("action") != "submit_question":
        bot.send_message(user_id, "Mini App'dan noma'lum amal keldi.")
        return

    subject = str(payload.get("subject", "")).strip()
    question_text = str(payload.get("question_text", "")).strip()
    valid_subjects = set(SUBJECTS.values())

    if subject not in valid_subjects:
        bot.send_message(user_id, "Fan noto'g'ri tanlangan. Iltimos, Mini App orqali qaytadan tanlang.")
        return

    if len(question_text) < 5:
        bot.send_message(user_id, "Savol juda qisqa. Iltimos, savolni batafsilroq yozing.")
        return

    if len(question_text) > 1200:
        question_text = question_text[:1200]

    if not get_user(user_id):
        save_user(msg, role="student")

    question_id = save_question(user_id, subject, "text", question_text=question_text)
    send_question_to_teachers(question_id, user_id, subject, "text", question_text=question_text)
    bot.send_message(user_id, "Mini App orqali yuborilgan savolingiz qabul qilindi.", reply_markup=oquvchi_menu)


@bot.message_handler(content_types=["text"])
def message_handler(msg: types.Message):
    user_id = msg.from_user.id
    text = msg.text
    state = user_states.get(user_id, {})

    # --- Admin login/parol tekshiruvi ---
    if state.get("step") == "admin_waiting_login":
        if text.strip() != ADMIN_LOGIN:
            user_states.pop(user_id, None)
            bot.send_message(user_id, "❌ Login xato. Qaytadan /admin buyrug'ini yuboring.", reply_markup=btn)
            return
        user_states[user_id] = {"step": "admin_waiting_password"}
        bot.send_message(user_id, "Parolni kiriting:")
        return

    if state.get("step") == "admin_waiting_password":
        if text.strip() != ADMIN_PASSWORD:
            user_states.pop(user_id, None)
            bot.send_message(user_id, "❌ Parol xato. Qaytadan /admin buyrug'ini yuboring.", reply_markup=btn)
            return
        save_admin(user_id, msg.from_user.username)
        user_states[user_id] = {"role": "admin"}
        bot.send_message(user_id, "✅ Tasdiqlandi! Admin panelga xush kelibsiz.", reply_markup=admin_menu)
        return

    # --- Admin: ustozni qo'lda qo'shish ---
    if state.get("step") == "admin_waiting_add_teacher":
        parts = text.strip().split(maxsplit=1)
        if len(parts) != 2 or not parts[0].isdigit():
            bot.send_message(
                user_id,
                "Noto'g'ri format. Iltimos: `<telegram_id> <Fan nomi>` ko'rinishida yuboring.\n"
                "Masalan: 123456789 Matematika",
            )
            return

        new_teacher_id, subject = int(parts[0]), parts[1].strip()
        save_teacher(new_teacher_id, subject=subject, is_free=1)
        with get_db() as db:
            db.execute("""
                INSERT INTO users (user_id, role, created_at)
                VALUES (?, 'teacher', ?)
                ON CONFLICT(user_id) DO UPDATE SET role = 'teacher'
            """, (new_teacher_id, now()))

        user_states[user_id] = {"role": "admin"}
        bot.send_message(user_id, f"✅ {new_teacher_id} endi '{subject}' fani bo'yicha ustoz.", reply_markup=admin_menu)
        try:
            bot.send_message(
                new_teacher_id,
                f"🎉 Tabriklaymiz! Admin sizni '{subject}' fani bo'yicha ustoz sifatida qo'shdi.",
                reply_markup=oqituvchi_menu,
            )
        except Exception:
            pass
        return

    # --- Admin menyu tugmalari ---
    if is_admin(user_id):
        if text == ADMIN_PENDING_APPS:
            pending = get_pending_applications()
            if not pending:
                bot.send_message(user_id, "Hozircha kutayotgan arizalar yo'q.", reply_markup=admin_menu)
            else:
                for application in pending:
                    send_application_to_admins(application["id"])
            return
        if text == ADMIN_ADD_TEACHER:
            user_states[user_id] = {"role": "admin", "step": "admin_waiting_add_teacher"}
            bot.send_message(
                user_id,
                "Ustozning Telegram ID raqami va fanini yuboring.\n"
                "Format: `<telegram_id> <Fan nomi>`\nMasalan: 123456789 Matematika",
            )
            return
        if text == ADMIN_TEACHERS_LIST:
            teachers = get_all_teachers()
            if not teachers:
                bot.send_message(user_id, "Hozircha ustozlar yo'q.", reply_markup=admin_menu)
            else:
                bot.send_message(user_id, f"Jami {len(teachers)} ustoz:", reply_markup=admin_menu)
                for teacher in teachers:
                    rating = "hali baho yo'q"
                    if teacher["rating_count"]:
                        rating = f"{teacher['rating_total'] / teacher['rating_count']:.1f}/5"
                    status = "bo'sh" if teacher["is_free"] else "band"
                    caption = (
                        f"👤 {teacher['name'] or 'Noma’lum'}\n"
                        f"Fan: {teacher['subject'] or '-'}\n"
                        f"Holat: {status}\n"
                        f"Reyting: {rating} ({teacher['helped_count']} savol)"
                    )
                    del_markup = types.InlineKeyboardMarkup()
                    del_markup.add(
                        types.InlineKeyboardButton("❌ O'chirish", callback_data=f"admin_delteacher:{teacher['user_id']}")
                    )
                    bot.send_message(user_id, caption, reply_markup=del_markup)
            return
        if text == ADMIN_LOGOUT:
            user_states.pop(user_id, None)
            bot.send_message(user_id, "Admin paneldan chiqdingiz.", reply_markup=btn)
            return

    if text == END_CHAT:
        chat = close_chat(user_id)
        if not chat:
            bot.send_message(user_id, "Hozir faol suhbat yo'q.")
            return

        partner_id = chat["teacher_id"] if user_id == chat["student_id"] else chat["student_id"]
        bot.send_message(partner_id, "Suhbat yakunlandi.")
        bot.send_message(user_id, "Suhbat yakunlandi.")
        ask_rating(chat["student_id"], chat["id"])
        return

    if text == REDIRECT_QUESTION:
        chat = get_active_chat(user_id)
        if not chat or chat["teacher_id"] != user_id:
            bot.send_message(user_id, "Bu funksiya faqat ustoz uchun, faol suhbat davomida ishlaydi.")
            return

        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("✅ Ha", callback_data=f"redir_yes:{chat['id']}"),
            types.InlineKeyboardButton("❌ Yo'q", callback_data=f"redir_no:{chat['id']}"),
        )
        bot.send_message(
            user_id,
            "Savolni boshqa bo'sh ustozga yuboraylikmi?",
            reply_markup=markup,
        )
        return

    if text == COMPLAINT:
        if user_id not in active_chats:
            bot.send_message(user_id, "Shikoyat qilish uchun avval faol suhbatda bo'lishingiz kerak.")
            return
        user_states[user_id] = {**state, "step": "waiting_complaint", "complaint_partner": active_chats[user_id]}
        bot.send_message(
            user_id,
            "🚩 Shikoyatingizni yozing — bu adminga yuboriladi. Suhbat davom etadi, xohlasangiz bekor qilish uchun \"⬅️ Orqaga\" yozing.",
        )
        return

    if state.get("step") == "waiting_complaint" and text != BACK:
        partner_id = state.get("complaint_partner")
        chat = get_active_chat(user_id)
        save_complaint(user_id, partner_id, chat["id"] if chat else None, text)

        admin_ids = set(get_admin_ids())
        contact_id = resolve_admin_contact_id()
        if contact_id:
            admin_ids.add(contact_id)
        reporter_name = get_user_name(user_id)
        for admin_id in admin_ids:
            try:
                bot.send_message(
                    admin_id,
                    "🚩 Yangi shikoyat!\n"
                    f"Shikoyatchi: {reporter_name} (ID: {user_id})\n"
                    f"Kimga: ID {partner_id}\n\n"
                    f"Matn: {text}",
                )
            except Exception:
                pass

        user_states[user_id] = {"role": state.get("role")}
        bot.send_message(user_id, "✅ Shikoyatingiz adminga yuborildi. Rahmat!", reply_markup=chat_menu)
        return

    if user_id in active_chats and text != BACK:
        send_to_partner(msg, active_chats[user_id])
        return

    if state.get("step") == "waiting_full_name":
        full_name = text.strip()
        if len(full_name.split()) < 2:
            bot.send_message(user_id, "Iltimos, ism va familiyangizni to'liq yozing.")
            return

        user_states[user_id] = {"full_name": full_name, "step": "waiting_login"}
        bot.send_message(
            user_id,
            "Rahmat! Endi o'zingizga login (foydalanuvchi nomi) o'ylab toping "
            "(kamida 3 belgi, probelsiz). Bu login kelajakda profilingizni "
            "tiklash uchun kerak bo'ladi, agar botni bilmasdan o'chirib qo'ysangiz "
            "yoki telefon almashtirsangiz.",
        )
        return

    if state.get("step") == "waiting_login":
        login_value = text.strip()
        if len(login_value) < 3 or " " in login_value:
            bot.send_message(user_id, "Login kamida 3 belgidan iborat va probelsiz bo'lishi kerak. Qaytadan kiriting:")
            return
        if get_user_by_login(login_value):
            bot.send_message(user_id, "Bu login band. Boshqa login kiriting:")
            return

        user_states[user_id] = {"full_name": state.get("full_name"), "login": login_value, "step": "waiting_password"}
        bot.send_message(
            user_id,
            "Ajoyib! Endi parol o'ylab toping: kamida 8 ta belgi, kamida 1 ta katta harf "
            "va kamida 1 ta son bo'lsin. Parol faqat sonlardan iborat bo'lmasin.",
        )
        return

    if state.get("step") == "waiting_password":
        password_value = text.strip()
        if not is_valid_password(password_value):
            bot.send_message(
                user_id,
                "Parol berilgan talablarga mos emas. Kamida 8 ta belgi, kamida 1 ta katta harf "
                "va kamida 1 ta son ishlating. Parol faqat sonlardan iborat bo'lmasin. Qaytadan kiriting:",
            )
            return

        save_user(
            msg,
            name=state.get("full_name"),
            login=state.get("login"),
            password_hash=hash_password(password_value),
        )
        user_states[user_id] = {"full_name": state.get("full_name")}
        bot.send_message(
            user_id,
            "✅ Ro'yxatdan o'tish yakunlandi! Login/parolingizni eslab qoling — "
            "profilingizni tiklash kerak bo'lsa, /login buyrug'i orqali foydalanasiz.\n\n"
            "Endi kim sifatida kirdingiz?",
            reply_markup=btn,
        )
        return

    if state.get("step") == "recover_waiting_login":
        row = get_user_by_login(text.strip())
        if not row:
            user_states.pop(user_id, None)
            bot.send_message(user_id, "Bunday login topilmadi. /login bilan qaytadan urinib ko'ring.", reply_markup=btn)
            return

        user_states[user_id] = {"step": "recover_waiting_password", "recover_user_id": row["user_id"]}
        bot.send_message(user_id, "Parolni kiriting:")
        return

    if state.get("step") == "recover_waiting_password":
        old_id = state.get("recover_user_id")
        row = get_user(old_id) if old_id else None

        if not row or not row["password_hash"] or hash_password(text.strip()) != row["password_hash"]:
            user_states.pop(user_id, None)
            bot.send_message(user_id, "❌ Parol xato. /login bilan qaytadan urinib ko'ring.", reply_markup=btn)
            return

        migrated = migrate_user_id(old_id, user_id)
        user_states.pop(user_id, None)

        if not migrated:
            bot.send_message(
                user_id,
                "Profilni tiklab bo'lmadi: bu Telegram hisobida allaqachon boshqa profil mavjud.",
                reply_markup=btn,
            )
            return

        restored = get_user(user_id)
        role = restored["role"] if restored else None
        if role == "teacher":
            bot.send_message(user_id, f"✅ Profilingiz tiklandi! Xush kelibsiz, {restored['name']}.", reply_markup=oqituvchi_menu)
        elif role == "student":
            bot.send_message(user_id, f"✅ Profilingiz tiklandi! Xush kelibsiz, {restored['name']}.", reply_markup=oquvchi_menu)
        else:
            bot.send_message(user_id, f"✅ Profilingiz tiklandi! Xush kelibsiz, {restored['name']}.", reply_markup=btn)
        return

    if state.get("step") == "waiting_question" and text != BACK:
        subject = state["subject"]
        question_id = save_question(user_id, subject, "text", question_text=text)
        user_states.pop(user_id, None)
        send_question_to_teachers(question_id, user_id, subject, "text", question_text=text)
        return

    if text == ROLE_STUDENT:
        save_user(msg, role="student", name=state.get("full_name"))
        delete_teacher(user_id)
        user_states[user_id] = {"role": "student", "full_name": state.get("full_name")}
        bot.send_message(user_id, "O'quvchi bo'limiga xush kelibsiz!", reply_markup=oquvchi_menu)
    elif text == ROLE_TEACHER:
        # Ustoz bo'lish endi to'g'ridan-to'g'ri emas: avval fan tanlanadi,
        # so'ng diplom/hujjat yuboriladi va admin tasdig'idan keyingina
        # foydalanuvchi haqiqiy ustoz bo'ladi.
        existing_teacher = get_teacher_rating(user_id)
        if existing_teacher:
            user_states[user_id] = {"role": "teacher", "full_name": state.get("full_name")}
            bot.send_message(user_id, "Siz allaqachon tasdiqlangan ustozsiz.", reply_markup=oqituvchi_menu)
            return

        save_user(msg, role="teacher_pending", name=state.get("full_name"))
        user_states[user_id] = {
            "role": "teacher_pending",
            "full_name": state.get("full_name"),
            "step": "waiting_teacher_subject",
        }
        bot.send_message(
            user_id,
            "Ustoz sifatida ro'yxatdan o'tish uchun avval qaysi fandan yordam bera olishingizni tanlang.",
            reply_markup=savollar_menu,
        )
    elif text == CHOOSE_SUBJECT:
        user_states[user_id] = {"role": "teacher", "step": "choose_subject", "full_name": state.get("full_name")}
        bot.send_message(user_id, "Qaysi fandan yordam berasiz?", reply_markup=savollar_menu)
    elif text == FREE:
        set_teacher_free(user_id, 1)
        bot.send_message(user_id, "Siz bo'sh holatga o'tdingiz.", reply_markup=oqituvchi_menu)
        teacher = get_teacher_rating(user_id)
        if teacher:
            notify_free_teacher_of_backlog(user_id, teacher["subject"])
    elif text == BUSY:
        set_teacher_free(user_id, 0)
        bot.send_message(user_id, "Siz band holatga o'tdingiz.", reply_markup=oqituvchi_menu)
    elif text == SEND_QUESTION:
        user_states[user_id] = {"role": "student", "step": "choose_subject"}
        bot.send_message(user_id, "Qaysi fandan savol yubormoqchisiz?", reply_markup=savollar_menu)
    elif text == MY_QUESTIONS:
        rows = get_student_questions(user_id)
        if not rows:
            bot.send_message(user_id, "Hozircha savolingiz yo'q.", reply_markup=oquvchi_menu)
        else:
            status_labels = {"new": "⏳ Kutilmoqda", "accepted": "💬 Faol suhbat", "closed": "✅ Yechildi", "redirected": "🔄 Boshqa ustozga o'tkazildi"}
            lines = ["🗂 Sizning savollaringiz:\n"]
            for row in rows:
                preview = row["question_text"] if row["question_type"] == "text" else "📷 rasm"
                preview = (preview[:60] + "…") if preview and len(preview) > 60 else preview
                status_text = status_labels.get(row["status"], row["status"])
                lines.append(f"• [{row['subject']}] {preview}\n  {status_text} | {row['created_at']}")
            bot.send_message(user_id, "\n".join(lines), reply_markup=oquvchi_menu)
    elif text == ACTIVE_CHAT:
        chat = get_active_chat(user_id)
        message = "Faol suhbat mavjud." if chat else "Hozir faol suhbat yo'q."
        bot.send_message(user_id, message, reply_markup=chat_menu if chat else oquvchi_menu)
    elif text == MY_RATINGS:
        teacher = get_teacher_rating(user_id)
        if not teacher or not teacher["rating_count"]:
            bot.send_message(user_id, "Sizda hali baholar yo'q.")
        else:
            rating = teacher["rating_total"] / teacher["rating_count"]
            bot.send_message(user_id, f"Sizning reytingingiz: {rating:.1f}/5")
    elif text == TEACHER_RATING:
        send_leaderboard(user_id)
    elif text == PROFILE:
        send_profile(user_id)
    elif text == BACK:
        user_states.pop(user_id, None)
        role = get_user_role(user_id)
        if role == "teacher":
            bot.send_message(user_id, "O'qituvchi menyusi", reply_markup=oqituvchi_menu)
        elif role == "student":
            bot.send_message(user_id, "O'quvchi menyusi", reply_markup=oquvchi_menu)
        else:
            bot.send_message(user_id, "Asosiy menyu", reply_markup=btn)
    elif text in SUBJECTS and state.get("step") == "waiting_teacher_subject":
        subject = SUBJECTS[text]
        user_states[user_id] = {
            "role": "teacher_pending",
            "full_name": state.get("full_name"),
            "step": "waiting_teacher_document",
            "subject": subject,
        }
        bot.send_message(
            user_id,
            "Endi ustozligingizni isbotlash uchun diplom, sertifikat yoki o'qiganingizni "
            "tasdiqlaydigan rasm/PDF hujjatini yuboring.",
            reply_markup=types.ReplyKeyboardRemove(),
        )
    elif text in SUBJECTS:
        subject = SUBJECTS[text]
        if state.get("role") == "teacher":
            save_teacher(user_id, subject=subject, is_free=1)
            user_states[user_id] = {"role": "teacher", "full_name": state.get("full_name")}
            bot.send_message(
                user_id,
                f"Siz {subject} fani uchun bo'sh ustoz sifatida saqlandingiz.",
                reply_markup=oqituvchi_menu,
            )
        else:
            user_states[user_id] = {"role": "student", "step": "waiting_question", "subject": subject}
            bot.send_message(
                user_id,
                "Savolingizni matn yoki rasm ko'rinishida yuboring.",
                reply_markup=oquvchi_menu,
            )
    else:
        bot.send_message(user_id, "Iltimos, menyudan birini tanlang.", reply_markup=btn)


def submit_teacher_application(msg, doc_type, file_id):
    user_id = msg.from_user.id
    state = user_states.get(user_id, {})
    subject = state.get("subject")
    name = state.get("full_name") or get_user_name(user_id)

    app_id = save_teacher_application(user_id, name, subject, doc_type, file_id)
    user_states.pop(user_id, None)
    bot.send_message(
        user_id,
        "✅ Arizangiz qabul qilindi! Admin hujjatingizni ko'rib chiqib, tez orada javob beradi.",
        reply_markup=btn,
    )
    send_application_to_admins(app_id)


@bot.message_handler(content_types=["photo"])
def photo_handler(msg: types.Message):
    user_id = msg.from_user.id

    if user_id in active_chats:
        send_to_partner(msg, active_chats[user_id])
        return

    state = user_states.get(user_id, {})

    if state.get("step") == "waiting_teacher_document":
        submit_teacher_application(msg, "photo", msg.photo[-1].file_id)
        return

    if state.get("step") != "waiting_question":
        bot.send_message(user_id, "Rasm savol yuborish uchun avval fan tanlang.", reply_markup=savollar_menu)
        return

    subject = state["subject"]
    photo_id = msg.photo[-1].file_id
    question_id = save_question(user_id, subject, "photo", photo_id=photo_id)
    user_states.pop(user_id, None)
    send_question_to_teachers(question_id, user_id, subject, "photo", photo_id=photo_id)


@bot.message_handler(content_types=["document"])
def document_handler(msg: types.Message):
    user_id = msg.from_user.id

    if user_id in active_chats:
        send_to_partner(msg, active_chats[user_id])
        return

    state = user_states.get(user_id, {})

    if state.get("step") == "waiting_teacher_document":
        submit_teacher_application(msg, "document", msg.document.file_id)
        return

    bot.send_message(
        user_id,
        "Hozir hujjat kutilmagan. Ustozlikka ariza topshirish uchun avval \"O'qituvchi\" tugmasini bosing.",
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("accept:"))
def accept_help(call: types.CallbackQuery):
    question_id = int(call.data.split(":")[1])
    teacher_id = call.from_user.id
    question = get_question(question_id)

    if not question:
        bot.answer_callback_query(call.id, "Savol topilmadi.")
        return

    accepted = accept_question(question_id, teacher_id)
    if not accepted:
        bot.answer_callback_query(call.id, "Bu savolga boshqa ustoz javob bermoqda.")
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        return

    student_id, _chat_id = accepted
    active_chats[student_id] = teacher_id
    active_chats[teacher_id] = student_id
    remove_question_messages(question_id)

    bot.answer_callback_query(call.id, "Suhbat boshlandi.")
    bot.send_message(
        teacher_id,
        "Siz o'quvchi bilan bog'landingiz. Xabar yozishingiz mumkin.",
        reply_markup=chat_menu,
    )
    bot.send_message(
        student_id,
        "Ustoz topildi. Endi ustoz bilan yozishishingiz mumkin.",
        reply_markup=chat_menu,
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("rate:"))
def rate_teacher(call: types.CallbackQuery):
    _, chat_id_raw, score_raw = call.data.split(":")
    chat_id = int(chat_id_raw)
    score = int(score_raw)
    student_id = call.from_user.id

    with get_db() as db:
        chat = db.execute("""
            SELECT *
            FROM chats
            WHERE id = ? AND student_id = ?
        """, (chat_id, student_id)).fetchone()

    if not chat:
        bot.answer_callback_query(call.id, "Bu baho sizga tegishli emas.")
        return

    if save_rating(chat_id, student_id, chat["teacher_id"], score):
        bot.answer_callback_query(call.id, "Baho saqlandi.")
        bot.send_message(student_id, "Rahmat! Bahoyingiz saqlandi.", reply_markup=oquvchi_menu)
        bot.send_message(chat["teacher_id"], f"O'quvchi sizga {score}/5 baho berdi.", reply_markup=oqituvchi_menu)
    else:
        bot.answer_callback_query(call.id, "Bu suhbat baholab bo'lingan.")


@bot.callback_query_handler(func=lambda call: call.data.startswith("tapp_approve:"))
def approve_teacher_application(call: types.CallbackQuery):
    app_id = int(call.data.split(":")[1])
    reviewer_id = call.from_user.id
    application = get_application(app_id)

    if not application:
        bot.answer_callback_query(call.id, "Ariza topilmadi.")
        return

    updated = update_application_status(app_id, "approved", reviewer_id)
    if not updated:
        bot.answer_callback_query(call.id, "Bu ariza allaqachon ko'rib chiqilgan.")
        return

    save_teacher(application["user_id"], subject=application["subject"], is_free=1)
    with get_db() as db:
        db.execute("UPDATE users SET role = 'teacher' WHERE user_id = ?", (application["user_id"],))

    bot.answer_callback_query(call.id, "Tasdiqlandi.")
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except Exception:
        pass
    try:
        bot.send_message(
            application["user_id"],
            f"🎉 Tabriklaymiz! '{application['subject']}' fani bo'yicha ustozligingiz tasdiqlandi.",
            reply_markup=oqituvchi_menu,
        )
    except Exception:
        pass


@bot.callback_query_handler(func=lambda call: call.data.startswith("tapp_reject:"))
def reject_teacher_application(call: types.CallbackQuery):
    app_id = int(call.data.split(":")[1])
    reviewer_id = call.from_user.id
    application = get_application(app_id)

    if not application:
        bot.answer_callback_query(call.id, "Ariza topilmadi.")
        return

    updated = update_application_status(app_id, "rejected", reviewer_id)
    if not updated:
        bot.answer_callback_query(call.id, "Bu ariza allaqachon ko'rib chiqilgan.")
        return

    with get_db() as db:
        db.execute("UPDATE users SET role = NULL WHERE user_id = ?", (application["user_id"],))

    bot.answer_callback_query(call.id, "Rad etildi.")
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except Exception:
        pass
    try:
        bot.send_message(
            application["user_id"],
            "❌ Afsuski, ustozlikka arizangiz rad etildi. Hujjatlaringizni tekshirib qaytadan "
            "urinib ko'rishingiz mumkin.",
            reply_markup=btn,
        )
    except Exception:
        pass


@bot.callback_query_handler(func=lambda call: call.data in ("delacc_confirm", "delacc_cancel"))
def delete_account_callback(call: types.CallbackQuery):
    user_id = call.from_user.id

    if call.data == "delacc_cancel":
        bot.answer_callback_query(call.id, "Bekor qilindi.")
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception:
            pass
        bot.send_message(user_id, "O'chirish bekor qilindi. Profilingiz saqlanib qoldi.")
        return

    close_chat(user_id)
    delete_account(user_id)

    bot.answer_callback_query(call.id, "Profil o'chirildi.")
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except Exception:
        pass
    bot.send_message(
        user_id,
        "🗑 Profilingiz butunlay o'chirildi. Qaytadan ro'yxatdan o'tish uchun /start buyrug'ini yuboring.",
        reply_markup=types.ReplyKeyboardRemove(),
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("redir_yes:") or call.data.startswith("redir_no:"))
def redirect_question_callback(call: types.CallbackQuery):
    teacher_id = call.from_user.id
    action, chat_id_raw = call.data.split(":")
    chat_id = int(chat_id_raw)

    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except Exception:
        pass

    if action == "redir_no":
        bot.answer_callback_query(call.id, "Bekor qilindi.")
        return

    chat = get_active_chat(teacher_id)
    if not chat or chat["id"] != chat_id or chat["teacher_id"] != teacher_id:
        bot.answer_callback_query(call.id, "Bu suhbat allaqachon yopilgan.")
        return

    student_id = chat["student_id"]
    question_id = chat["question_id"]
    redirect_chat(teacher_id)

    bot.answer_callback_query(call.id, "Savol boshqa ustozga yuborilmoqda.")
    bot.send_message(teacher_id, "Suhbat yakunlandi, savol boshqa ustozga yuborildi.", reply_markup=oqituvchi_menu)
    bot.send_message(
        student_id,
        "⚠️ Ustoz hozir yordam bera olmadi. Savolingiz boshqa bo'sh ustozga yuborilmoqda...",
    )

    found = requeue_question_broadcast(question_id, exclude_teacher_id=teacher_id)
    if not found:
        bot.send_message(
            student_id,
            "Hozircha bu fan bo'yicha boshqa bo'sh ustoz topilmadi. "
            "Bo'sh ustoz paydo bo'lishi bilan sizga xabar beriladi.",
        )


@bot.callback_query_handler(func=lambda call: call.data.startswith("admin_delteacher:"))
def admin_delete_teacher_callback(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Sizda ruxsat yo'q.")
        return

    teacher_id = int(call.data.split(":")[1])
    delete_teacher(teacher_id)
    with get_db() as db:
        db.execute("UPDATE users SET role = NULL WHERE user_id = ?", (teacher_id,))

    bot.answer_callback_query(call.id, "Ustoz o'chirildi.")
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except Exception:
        pass
    try:
        bot.send_message(
            teacher_id,
            "Sizning ustozlik profilingiz admin tomonidan o'chirildi. Savol bo'lsa, admin bilan bog'laning.",
            reply_markup=btn,
        )
    except Exception:
        pass


def check_stale_chats():
    """Fon vazifasi: uzoq vaqt davom etayotgan faol suhbatlar haqida
    ikkala tomonga ham eslatma yuboradi."""
    while True:
        try:
            for chat in get_stale_active_chats(STALE_CHAT_MINUTES):
                mark_chat_reminded(chat["id"])
                reminder = (
                    f"⏰ Bu suhbat {STALE_CHAT_MINUTES} daqiqadan ko'proq davom etmoqda. "
                    "Agar muammo hal bo'lgan bo'lsa, \"🔚 Suhbatni yakunlash\" tugmasini bosing."
                )
                try:
                    bot.send_message(chat["student_id"], reminder)
                except Exception:
                    pass
                try:
                    bot.send_message(chat["teacher_id"], reminder)
                except Exception:
                    pass
        except Exception as exc:
            print(f"[UyVazifa] check_stale_chats xatosi: {exc}")
        time.sleep(STALE_CHAT_CHECK_SECONDS)


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is required")
    init_db()
    restore_runtime_state()
    start_mini_app_server()
    threading.Thread(target=check_stale_chats, daemon=True).start()
    bot.polling(none_stop=True)
