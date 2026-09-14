import os

from telebot import types

# Mini App (webapp/index.html) manzili — buni serverda quyidagi
# environment variable orqali beriladi:
#   export MINI_APP_URL="https://sizning-mini-app-manzilingiz"
# Bo'sh bo'lsa, "📱 Mini App" tugmasi hech qayerda ko'rinmaydi —
# ya'ni hali sozlamagan bo'lsangiz ham bot xatosiz ishlayveradi.
MINI_APP_URL = os.getenv("MINI_APP_URL", "").strip()
MINI_APP_LABEL = "📱 Mini App"

ROLE_TEACHER = "👨‍🏫 O'qituvchi"
ROLE_STUDENT = "🎓 O'quvchi"

SEND_QUESTION = "❓ Savol yuborish"
MY_QUESTIONS = "🗂 Mening savollarim"
ACTIVE_CHAT = "💬 Faol suhbat"
MY_RATINGS = "⭐ Baholarim"
PROFILE = "👤 Profilim"

CHOOSE_SUBJECT = "📚 Fan tanlash"
FREE = "✅ Bo'shman"
BUSY = "⏳ Bandman"
TEACHER_RATING = "🏆 Ustozlar reytingi"

END_CHAT = "🔚 Suhbatni yakunlash"
REDIRECT_QUESTION = "⚠️ Javob bera olmayman"
COMPLAINT = "🚩 Shikoyat qilish"
BACK = "⬅️ Orqaga"

# --- Admin panel tugmalari ---
ADMIN_PENDING_APPS = "📋 Kutayotgan arizalar"
ADMIN_ADD_TEACHER = "➕ Ustoz qo'shish"
ADMIN_TEACHERS_LIST = "📚 Ustozlar ro'yxati"
ADMIN_LOGOUT = "🚪 Chiqish"

SUBJECTS = {
    "🧮 Matematika": "Matematika",
    "⚛️ Fizika": "Fizika",
    "💻 Informatika": "Informatika",
    "🇬🇧 Ingliz tili": "Ingliz tili",
    "✍️ Ona tili": "Ona tili",
    "🏛️ Tarix": "Tarix",
    "📌 Boshqa fan": "Boshqa fan",
}

btn = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
btn.add(types.KeyboardButton(ROLE_STUDENT), types.KeyboardButton(ROLE_TEACHER))

oquvchi_menu = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
oquvchi_menu.add(
    types.KeyboardButton(SEND_QUESTION),
    types.KeyboardButton(MY_QUESTIONS),
    types.KeyboardButton(ACTIVE_CHAT),
    types.KeyboardButton(MY_RATINGS),
    types.KeyboardButton(PROFILE),
)

oqituvchi_menu = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
oqituvchi_menu.add(
    types.KeyboardButton(CHOOSE_SUBJECT),
    types.KeyboardButton(FREE),
    types.KeyboardButton(BUSY),
    types.KeyboardButton(TEACHER_RATING),
    types.KeyboardButton(PROFILE),
)

chat_menu = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
chat_menu.add(
    types.KeyboardButton(END_CHAT),
    types.KeyboardButton(REDIRECT_QUESTION),
    types.KeyboardButton(COMPLAINT),
)

savollar_menu = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
savollar_menu.add(*[types.KeyboardButton(name) for name in SUBJECTS], types.KeyboardButton(BACK))

admin_menu = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
admin_menu.add(
    types.KeyboardButton(ADMIN_PENDING_APPS),
    types.KeyboardButton(ADMIN_ADD_TEACHER),
    types.KeyboardButton(ADMIN_TEACHERS_LIST),
    types.KeyboardButton(ADMIN_LOGOUT),
)


def _attach_webapp_button(menu):
    """MINI_APP_URL to'g'ri (https://...) bo'lsa, menyuga alohida qatorda
    Mini App'ni ochadigan tugma qo'shadi. Aks holda menyuni o'zgartirmaydi."""
    if MINI_APP_URL.startswith("https://"):
        menu.add(types.KeyboardButton(MINI_APP_LABEL, web_app=types.WebAppInfo(url=MINI_APP_URL)))
    return menu


_attach_webapp_button(oquvchi_menu)
_attach_webapp_button(oqituvchi_menu)
