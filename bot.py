import os
import sqlite3
import logging

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# =========================
# НАСТРОЙКИ
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CHANNEL_ID = os.getenv("CHANNEL_ID", "")

DB_PATH = "/app/data/submissions.db"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================
# БАЗА
# =========================

def init_db():
    os.makedirs("/app/data", exist_ok=True)

    conn = sqlite3.connect(DB_PATH)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            first_name TEXT,
            status TEXT DEFAULT 'pending'
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS submission_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            submission_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL
        )
    """)

    conn.commit()
    conn.close()


def create_submission(user):
    conn = sqlite3.connect(DB_PATH)

    cursor = conn.execute(
        """
        INSERT INTO submissions
        (user_id, username, first_name)
        VALUES (?, ?, ?)
        """,
        (
            user.id,
            user.username,
            user.first_name,
        ),
    )

    submission_id = cursor.lastrowid

    conn.commit()
    conn.close()

    return submission_id


def add_message(submission_id, message_id):
    conn = sqlite3.connect(DB_PATH)

    conn.execute(
        """
        INSERT INTO submission_messages
        (submission_id, message_id)
        VALUES (?, ?)
        """,
        (submission_id, message_id),
    )

    conn.commit()
    conn.close()


def get_submission(submission_id):
    conn = sqlite3.connect(DB_PATH)

    row = conn.execute(
        """
        SELECT id, user_id, username, first_name, status
        FROM submissions
        WHERE id = ?
        """,
        (submission_id,),
    ).fetchone()

    conn.close()

    return row


def get_submission_messages(submission_id):
    conn = sqlite3.connect(DB_PATH)

    rows = conn.execute(
        """
        SELECT message_id
        FROM submission_messages
        WHERE submission_id = ?
        ORDER BY id
        """,
        (submission_id,),
    ).fetchall()

    conn.close()

    return [row[0] for row in rows]


def set_status(submission_id, status):
    conn = sqlite3.connect(DB_PATH)

    conn.execute(
        """
        UPDATE submissions
        SET status = ?
        WHERE id = ?
        """,
        (status, submission_id),
    )

    conn.commit()
    conn.close()


# =========================
# КЛАВИАТУРА
# =========================

def main_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📨 Предложить пост")]
        ],
        resize_keyboard=True,
    )


# =========================
# START
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    text = (
        "🤨 НЕ НОРМАЛЬНО\n\n"
        "Есть что-то, что заслуживает попасть в канал?\n\n"
        "Присылай сюда:\n"
        "📸 фото\n"
        "🎥 видео\n"
        "💬 переписку\n"
        "🤡 историю\n"
        "🚗 странное объявление\n"
        "🐈 своего кота\n\n"
        "Всё самое НЕ НОРМАЛЬНОЕ мы попробуем опубликовать.\n\n"
        "Спасибо, что помогаете нам поддерживать этот бардак. 🤝"
    )

    await update.message.reply_text(
        text,
        reply_markup=main_keyboard(),
    )


# =========================
# ПОЛУЧИТЬ ID
# =========================

async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Твой Telegram ID:\n\n{update.effective_user.id}"
    )


# =========================
# НАЧАЛО ПРЕДЛОЖКИ
# =========================

async def start_submission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["submission_id"] = create_submission(
        update.effective_user
    )

    context.user_data["collecting"] = True

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Готово",
                    callback_data="finish_submission"
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ Отмена",
                    callback_data="cancel_submission"
                )
            ],
        ]
    )

    await update.message.reply_text(
        "📨 Отлично, присылай материал.\n\n"
        "Можно отправить несколько сообщений подряд: "
        "текст, фотографии, видео и т.д.\n\n"
        "Когда закончишь, нажми кнопку «✅ Готово».",
        reply_markup=keyboard,
    )


# =========================
# ПОЛУЧЕНИЕ МАТЕРИАЛА
# =========================

async def collect_message(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not context.user_data.get("collecting"):
        return

    submission_id = context.user_data.get("submission_id")

    if not submission_id:
        return

    add_message(
        submission_id,
        update.message.message_id
    )

    await update.message.reply_text(
        "📎 Получил. Если есть ещё материал, присылай."
    )


# =========================
# ЗАВЕРШЕНИЕ
# =========================

async def finish_submission(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    submission_id = context.user_data.get("submission_id")

    if not submission_id:
        await query.edit_message_text(
            "Предложка уже завершена или отсутствует."
        )
        return

    messages = get_submission_messages(submission_id)

    if not messages:
        await query.edit_message_text(
            "Ты пока ничего не прислал 😐"
        )
        return

    submission = get_submission(submission_id)

    user_id = submission[1]
    username = submission[2]
    first_name = submission[3]

    username_text = (
        f"@{username}"
        if username
        else "username отсутствует"
    )

    admin_text = (
        "📨 НОВАЯ ПРЕДЛОЖКА\n\n"
        f"👤 Автор: {first_name}\n"
        f"🔗 {username_text}\n"
        f"🆔 ID: {user_id}\n\n"
        f"📎 Материалов: {len(messages)}\n"
        f"📝 Заявка №{submission_id}"
    )

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ ОПУБЛИКОВАТЬ",
                    callback_data=f"publish:{submission_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ ОТКЛОНИТЬ",
                    callback_data=f"reject:{submission_id}"
                )
            ],
        ]
    )

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=admin_text,
        reply_markup=keyboard,
    )

    # Пересылаем сам материал админу
    for message_id in messages:
        await context.bot.copy_message(
            chat_id=ADMIN_ID,
            from_chat_id=user_id,
            message_id=message_id,
        )

    set_status(submission_id, "moderation")

    context.user_data.clear()

    await query.edit_message_text(
        "✅ Получили!\n\n"
        "Редакция НЕ НОРМАЛЬНО проверит предложение.\n"
        "Если оно достаточно НЕ НОРМАЛЬНОЕ, появится в канале. 😐"
    )


# =========================
# ОТМЕНА
# =========================

async def cancel_submission(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    submission_id = context.user_data.get("submission_id")

    if submission_id:
        set_status(submission_id, "cancelled")

    context.user_data.clear()

    await query.edit_message_text(
        "Окей, отменили 😐"
    )


# =========================
# МОДЕРАЦИЯ
# =========================

async def moderation(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    if query.from_user.id != ADMIN_ID:
        await query.answer(
            "У тебя нет доступа.",
            show_alert=True
        )
        return

    await query.answer()

    data = query.data

    if data.startswith("publish:"):
        submission_id = int(data.split(":")[1])

        submission = get_submission(submission_id)

        if not submission:
            await query.edit_message_text(
                "❌ Заявка не найдена."
            )
            return

        if submission[4] != "moderation":
            await query.edit_message_text(
                "Эта заявка уже обработана."
            )
            return

        messages = get_submission_messages(submission_id)

        for message_id in messages:
            await context.bot.copy_message(
                chat_id=CHANNEL_ID,
                from_chat_id=ADMIN_ID,
                message_id=message_id,
            )

        set_status(submission_id, "published")

        await query.edit_message_text(
            f"✅ Заявка №{submission_id} опубликована."
        )

        try:
            await context.bot.send_message(
                chat_id=submission[1],
                text=(
                    "🎉 Твоя предложка опубликована!\n\n"
                    "Спасибо за вклад в НЕ НОРМАЛЬНО 😐"
                ),
            )
        except Exception:
            pass

    elif data.startswith("reject:"):
        submission_id = int(data.split(":")[1])

        submission = get_submission(submission_id)

        if not submission:
            await query.edit_message_text(
                "❌ Заявка не найдена."
            )
            return

        set_status(submission_id, "rejected")

        await query.edit_message_text(
            f"❌ Заявка №{submission_id} отклонена."
        )

        try:
            await context.bot.send_message(
                chat_id=submission[1],
                text=(
                    "😐 В этот раз предложка не прошла.\n"
                    "Но можешь попробовать ещё."
                ),
            )
        except Exception:
            pass


# =========================
# ЗАПУСК
# =========================

def main():

    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN не найден."
        )

    if not ADMIN_ID:
        raise RuntimeError(
            "ADMIN_ID не найден."
        )

    if not CHANNEL_ID:
        raise RuntimeError(
            "CHANNEL_ID не найден."
        )

    init_db()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("myid", myid)
    )

    application.add_handler(
        MessageHandler(
            filters.Regex("^📨 Предложить пост$"),
            start_submission,
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            finish_submission,
            pattern="^finish_submission$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            cancel_submission,
            pattern="^cancel_submission$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            moderation,
            pattern="^(publish|reject):",
        )
    )

    application.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND,
            collect_message,
        )
    )

    logger.info("Бот запущен!")

    application.run_polling()


if __name__ == "__main__":
    main()
