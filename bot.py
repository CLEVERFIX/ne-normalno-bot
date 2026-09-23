import os
import sqlite3
import logging
from typing import Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.error import TelegramError


# ============================================================
# НАСТРОЙКИ
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CHANNEL_ID = os.getenv("CHANNEL_ID", "")

DATA_DIR = os.getenv("DATA_DIR", "/app/data")
DB_PATH = os.path.join(DATA_DIR, "submissions.db")


# ============================================================
# ЛОГИ
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ============================================================
# БАЗА ДАННЫХ
# ============================================================

def get_db():
    os.makedirs(DATA_DIR, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            first_name TEXT,
            status TEXT NOT NULL DEFAULT 'collecting',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS submission_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            submission_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            FOREIGN KEY(submission_id)
                REFERENCES submissions(id)
        )
    """)

    conn.commit()
    conn.close()

    logger.info("База данных инициализирована: %s", DB_PATH)


def create_submission(user) -> int:
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO submissions
        (user_id, username, first_name, status)
        VALUES (?, ?, ?, 'collecting')
        """,
        (
            user.id,
            user.username,
            user.first_name,
        ),
    )

    submission_id = cur.lastrowid

    conn.commit()
    conn.close()

    return submission_id


def add_submission_message(
    submission_id: int,
    message_id: int,
):
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO submission_messages
        (submission_id, message_id)
        VALUES (?, ?)
        """,
        (
            submission_id,
            message_id,
        ),
    )

    conn.commit()
    conn.close()


def set_submission_status(
    submission_id: int,
    status: str,
):
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE submissions
        SET status = ?
        WHERE id = ?
        """,
        (
            status,
            submission_id,
        ),
    )

    conn.commit()
    conn.close()


def get_submission(
    submission_id: int,
):
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT *
        FROM submissions
        WHERE id = ?
        """,
        (submission_id,),
    )

    row = cur.fetchone()

    conn.close()

    return row


def get_submission_messages(
    submission_id: int,
):
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT message_id
        FROM submission_messages
        WHERE submission_id = ?
        ORDER BY id
        """,
        (submission_id,),
    )

    rows = cur.fetchall()

    conn.close()

    return [row["message_id"] for row in rows]


# ============================================================
# КНОПКИ
# ============================================================

def submission_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Отправить предложку",
                callback_data="finish_submission",
            )
        ],
        [
            InlineKeyboardButton(
                "❌ Отменить",
                callback_data="cancel_submission",
            )
        ],
    ])


def moderation_keyboard(submission_id: int):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Опубликовать",
                callback_data=f"publish:{submission_id}",
            ),
            InlineKeyboardButton(
                "❌ Отклонить",
                callback_data=f"reject:{submission_id}",
            ),
        ]
    ])


# ============================================================
# /START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user

    logger.info(
        "START: user_id=%s username=%s",
        user.id,
        user.username,
    )

    # Специально показываем ID пользователя.
    # Это удобно для диагностики.
    await update.message.reply_text(
        "Привет! 👋\n\n"
        "Здесь можно предложить новость для НЕНОРМАЛЬНО.\n\n"
        "Нажми кнопку ниже и отправь материал.\n\n"
        f"Твой Telegram ID: {user.id}",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "📰 Предложить новость",
                    callback_data="start_submission",
                )
            ]
        ]),
    )


# ============================================================
# НАЧАЛО ПРЕДЛОЖКИ
# ============================================================

async def start_submission(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    user = update.effective_user

    # Если уже есть незаконченная предложка,
    # не создаём новую.
    if context.user_data.get("submission_id"):
        await query.edit_message_text(
            "Ты уже собираешь предложку.\n\n"
            "Просто отправь материал сюда.\n"
            "Когда закончишь, нажми «Отправить предложку»."
        )
        return

    submission_id = create_submission(user)

    context.user_data["submission_id"] = submission_id

    logger.info(
        "Создана предложка #%s от user_id=%s",
        submission_id,
        user.id,
    )

    await query.edit_message_text(
        "📰 Предложка создана.\n\n"
        "Теперь отправь сюда новость, текст, фото, видео "
        "или другой материал.\n\n"
        "Можно отправить несколько сообщений.\n"
        "Когда закончишь, нажми кнопку «Отправить предложку».",
        reply_markup=submission_keyboard(),
    )


# ============================================================
# ПОЛУЧЕНИЕ МАТЕРИАЛА
# ============================================================

async def collect_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    submission_id = context.user_data.get("submission_id")

    # Если пользователь сейчас ничего не предлагает,
    # просто не вмешиваемся.
    if not submission_id:
        return

    message = update.message

    if not message:
        return

    logger.info(
        "Получен материал: submission=%s user=%s message_id=%s",
        submission_id,
        update.effective_user.id,
        message.message_id,
    )

    add_submission_message(
        submission_id,
        message.message_id,
    )

    await message.reply_text(
        "✅ Получил.\n"
        "Можешь отправить ещё материал или нажать "
        "«Отправить предложку».",
        reply_markup=submission_keyboard(),
    )


# ============================================================
# ЗАВЕРШЕНИЕ ПРЕДЛОЖКИ
# ============================================================

async def finish_submission(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    submission_id = context.user_data.get("submission_id")

    if not submission_id:
        await query.edit_message_text(
            "У тебя сейчас нет активной предложки."
        )
        return

    messages = get_submission_messages(submission_id)

    if not messages:
        await query.edit_message_text(
            "❗ Ты ещё ничего не отправил.\n\n"
            "Сначала отправь новость или материал."
        )
        return

    submission = get_submission(submission_id)

    if not submission:
        await query.edit_message_text(
            "❌ Заявка не найдена."
        )
        context.user_data.pop("submission_id", None)
        return

    set_submission_status(
        submission_id,
        "moderation",
    )

    username = submission["username"]
    first_name = submission["first_name"]
    user_id = submission["user_id"]

    if username:
        user_display = f"@{username}"
    else:
        user_display = first_name or "Без имени"

    admin_text = (
        "📰 НОВАЯ ПРЕДЛОЖКА\n\n"
        f"ID заявки: #{submission_id}\n"
        f"Отправитель: {user_display}\n"
        f"Telegram ID: {user_id}\n"
        f"Материалов: {len(messages)}"
    )

    logger.info(
        "Отправляем предложку #%s админу %s",
        submission_id,
        ADMIN_ID,
    )

    try:
        # Сначала отправляем карточку модерации.
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=admin_text,
            reply_markup=moderation_keyboard(
                submission_id
            ),
        )

        # Затем копируем все сообщения пользователя админу.
        for message_id in messages:
            await context.bot.copy_message(
                chat_id=ADMIN_ID,
                from_chat_id=user_id,
                message_id=message_id,
            )

        logger.info(
            "Предложка #%s успешно отправлена админу",
            submission_id,
        )

        await query.edit_message_text(
            "✅ Предложка отправлена на модерацию!\n\n"
            "Спасибо ❤️"
        )

        context.user_data.pop(
            "submission_id",
            None,
        )

    except TelegramError as e:
        logger.exception(
            "Ошибка отправки предложки #%s админу",
            submission_id,
        )

        await query.edit_message_text(
            "❌ Не удалось отправить предложку.\n\n"
            "Попробуй ещё раз чуть позже."
        )


# ============================================================
# ОТМЕНА ПРЕДЛОЖКИ
# ============================================================

async def cancel_submission(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    submission_id = context.user_data.get("submission_id")

    if submission_id:
        set_submission_status(
            submission_id,
            "cancelled",
        )

    context.user_data.pop(
        "submission_id",
        None,
    )

    await query.edit_message_text(
        "❌ Предложка отменена."
    )


# ============================================================
# МОДЕРАЦИЯ
# ============================================================

async def moderation(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    # ВАЖНО:
    # проверяем именно Telegram ID пользователя,
    # который нажал кнопку.
    if query.from_user.id != ADMIN_ID:
        await query.answer(
            "У тебя нет доступа.",
            show_alert=True,
        )
        return

    await query.answer()

    data = query.data

    logger.info(
        "Модерация: admin=%s action=%s",
        query.from_user.id,
        data,
    )

    # --------------------------------------------------------
    # ПУБЛИКАЦИЯ
    # --------------------------------------------------------

    if data.startswith("publish:"):
        submission_id = int(
            data.split(":")[1]
        )

        submission = get_submission(
            submission_id
        )

        if not submission:
            await query.edit_message_text(
                "❌ Заявка не найдена."
            )
            return

        if submission["status"] != "moderation":
            await query.edit_message_text(
                "Эта заявка уже обработана."
            )
            return

        messages = get_submission_messages(
            submission_id
        )

        if not messages:
            await query.edit_message_text(
                "❌ В заявке нет сообщений."
            )
            return

        try:
            for message_id in messages:
                await context.bot.copy_message(
                    chat_id=CHANNEL_ID,
                    from_chat_id=ADMIN_ID,
                    message_id=message_id,
                )

            set_submission_status(
                submission_id,
                "published",
            )

            logger.info(
                "Заявка #%s опубликована",
                submission_id,
            )

            await query.edit_message_text(
                f"✅ Заявка #{submission_id} опубликована."
            )

        except TelegramError as e:
            logger.exception(
                "Ошибка публикации заявки #%s",
                submission_id,
            )

            await query.edit_message_text(
                "❌ Не удалось опубликовать.\n\n"
                f"Ошибка Telegram: {e}"
            )

        return

    # --------------------------------------------------------
    # ОТКЛОНЕНИЕ
    # --------------------------------------------------------

    if data.startswith("reject:"):
        submission_id = int(
            data.split(":")[1]
        )

        submission = get_submission(
            submission_id
        )

        if not submission:
            await query.edit_message_text(
                "❌ Заявка не найдена."
            )
            return

        if submission["status"] != "moderation":
            await query.edit_message_text(
                "Эта заявка уже обработана."
            )
            return

        set_submission_status(
            submission_id,
            "rejected",
        )

        logger.info(
            "Заявка #%s отклонена",
            submission_id,
        )

        await query.edit_message_text(
            f"❌ Заявка #{submission_id} отклонена."
        )

        return


# ============================================================
# ТЕСТ АДМИНА
# ============================================================

async def test_admin(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user

    # Команду может вызвать только администратор.
    if user.id != ADMIN_ID:
        await update.message.reply_text(
            "Нет доступа."
        )
        return

    logger.info(
        "Запущен тест ADMIN_ID=%s",
        ADMIN_ID,
    )

    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                "🧪 Тест модерации\n\n"
                "Если ты видишь это сообщение, "
                "ADMIN_ID настроен правильно.\n\n"
                f"ADMIN_ID: {ADMIN_ID}\n"
                f"CHANNEL_ID: {CHANNEL_ID}"
            ),
        )

        await update.message.reply_text(
            "✅ Тест отправлен."
        )

    except TelegramError as e:
        logger.exception(
            "Тест ADMIN_ID завершился ошибкой"
        )

        await update.message.reply_text(
            "❌ Telegram не дал отправить сообщение:\n\n"
            f"{e}"
        )


# ============================================================
# ОБЩИЕ ОШИБКИ
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    logger.exception(
        "Необработанная ошибка:",
        exc_info=context.error,
    )


# ============================================================
# MAIN
# ============================================================

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

    logger.info(
        "Запуск бота..."
    )

    logger.info(
        "ADMIN_ID = %s",
        ADMIN_ID,
    )

    logger.info(
        "CHANNEL_ID = %s",
        CHANNEL_ID,
    )

    init_db()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # Команды
    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    application.add_handler(
        CommandHandler(
            "test_admin",
            test_admin,
        )
    )

    # Кнопки
    application.add_handler(
        CallbackQueryHandler(
            start_submission,
            pattern=r"^start_submission$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            finish_submission,
            pattern=r"^finish_submission$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            cancel_submission,
            pattern=r"^cancel_submission$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            moderation,
            pattern=r"^(publish|reject):\d+$",
        )
    )

    # Любые сообщения пользователя.
    # Сюда попадут текст, фото, видео, документы и т.д.
    application.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND,
            collect_message,
        )
    )

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "INFO: Бот запущен"
    )

    logger.info(
        "INFO: Ожидание сообщений"
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
