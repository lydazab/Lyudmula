import os, logging, httpx, re
from dotenv import load_dotenv
import anthropic
from bs4 import BeautifulSoup
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ConversationHandler, filters, ContextTypes,
)

for v in ("HTTP_PROXY","HTTPS_PROXY","http_proxy","https_proxy","ALL_PROXY","all_proxy"):
    os.environ.pop(v, None)

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)

claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

# Conversation states
WAIT_DESCRIPTION, WAIT_PROFILE = range(2)
user_sessions: dict[int, dict] = {}

ANALYZE_PROMPT = """Ти — HR-аналітик. Проаналізуй LinkedIn профіль кандидата на відповідність вакансії.

ВАКАНСІЯ / КРИТЕРІЇ ВІДБОРУ:
{description}

ПРОФІЛЬ КАНДИДАТА:
{profile}

Дай структурований аналіз у форматі:

🎯 ВІДПОВІДНІСТЬ: X/10

✅ СИЛЬНІ СТОРОНИ:
• (список що збігається з вакансією)

❌ НЕДОЛІКИ / ЧОГО НЕ ВИСТАЧАЄ:
• (список що відсутнє або не відповідає)

📋 ДОСВІД:
• (ключові посади і роки)

🛠 НАВИЧКИ:
• (релевантні навички)

💬 ВИСНОВОК:
(2-3 речення — варто запрошувати на співбесіду чи ні і чому)"""


def fetch_linkedin(url: str) -> str:
    """Спроба отримати текст публічного LinkedIn профілю."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        r = httpx.get(url, headers=headers, timeout=15, follow_redirects=True)
        if r.status_code != 200:
            return ""
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        text = " ".join(soup.get_text(separator="\n").split())
        # LinkedIn часто блокує — перевіряємо чи є корисний вміст
        if len(text) < 300 or "join linkedin" in text.lower():
            return ""
        return text[:5000]
    except Exception:
        return ""


def analyze_profile(description: str, profile_text: str) -> str:
    resp = claude.messages.create(
        model="claude-opus-4-7",
        max_tokens=1500,
        messages=[{"role": "user", "content": ANALYZE_PROMPT.format(
            description=description,
            profile=profile_text,
        )}],
    )
    return resp.content[0].text


async def linkedin_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_sessions[uid] = {}
    await update.message.reply_text(
        "📋 *Аналіз LinkedIn профілю*\n\n"
        "Крок 1 з 2: Надішли мені опис вакансії або критерії відбору.\n\n"
        "_Наприклад: «Шукаємо Python-розробника з 3+ роками досвіду, "
        "знанням Django/FastAPI, досвідом роботи з PostgreSQL»_",
        parse_mode="Markdown",
    )
    return WAIT_DESCRIPTION


async def got_description(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_sessions[uid]["description"] = update.message.text
    await update.message.reply_text(
        "✅ Опис вакансії збережено.\n\n"
        "Крок 2 з 2: Тепер надішли профіль кандидата — одним із способів:\n\n"
        "🔗 *URL профілю* (linkedin.com/in/...)\n"
        "📝 *Текст профілю* — скопіюй і встав весь текст зі сторінки LinkedIn",
        parse_mode="Markdown",
    )
    return WAIT_PROFILE


async def got_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    description = user_sessions.get(uid, {}).get("description", "")

    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    await update.message.reply_text("⏳ Аналізую профіль...")

    profile_text = ""

    # Якщо це URL — спробуємо завантажити
    if re.match(r"https?://(www\.)?linkedin\.com/in/", text):
        profile_text = fetch_linkedin(text)
        if not profile_text:
            await update.message.reply_text(
                "⚠️ LinkedIn заблокував автоматичне завантаження.\n\n"
                "Відкрий профіль у браузері → виділи весь текст (Ctrl+A) → "
                "скопіюй і надішли мені як повідомлення.",
            )
            return WAIT_PROFILE
    else:
        profile_text = text

    if len(profile_text) < 100:
        await update.message.reply_text("❌ Замало тексту. Надішли більше інформації про кандидата.")
        return WAIT_PROFILE

    try:
        result = analyze_profile(description, profile_text)
        await update.message.reply_text(result)
        # Пропонуємо проаналізувати ще одного
        await update.message.reply_text(
            "Надішли наступний профіль або /cancel щоб завершити.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return WAIT_PROFILE
    except Exception:
        log.exception("analyze error")
        await update.message.reply_text("Помилка аналізу. Спробуй ще раз.")
        return WAIT_PROFILE


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_sessions.pop(uid, None)
    await update.message.reply_text("Аналіз завершено.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


def main():
    log.info("LinkedIn analyzer bot started")
    app = ApplicationBuilder().token(os.environ["TELEGRAM_BOT_TOKEN"]).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("linkedin", linkedin_start)],
        states={
            WAIT_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_description)],
            WAIT_PROFILE: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_profile)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text(
        "Привіт! Надішли /linkedin щоб розпочати аналіз профілів."
    )))
    app.run_polling()


if __name__ == "__main__":
    main()
