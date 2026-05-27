import os, json, logging, math, httpx, re, base64
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

for v in ("HTTP_PROXY","HTTPS_PROXY","http_proxy","https_proxy","ALL_PROXY","all_proxy"):
    os.environ.pop(v, None)

from dotenv import load_dotenv
import anthropic
from bs4 import BeautifulSoup
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ConversationHandler, filters, ContextTypes,
)

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)

claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
history: dict[int, list[dict]] = {}
NOTES_DIR = Path(__file__).parent / "notes"
NOTES_DIR.mkdir(exist_ok=True)

SYSTEM_PROMPT = """Ти — бізнес-асистент. Відповідаєш українською, коротко і по суті.
Маєш інструменти: calculate, save_note, list_notes, delete_note, get_datetime, read_url.
Використовуй їх коли потрібно — без зайвих пояснень."""

TOOLS = [
    {
        "name": "calculate",
        "description": "Виконує математичні розрахунки. Підтримує +,-,*,/,**,sqrt,sin,cos,log тощо.",
        "input_schema": {
            "type": "object",
            "properties": {"expression": {"type": "string", "description": "Математичний вираз"}},
            "required": ["expression"],
        },
    },
    {
        "name": "save_note",
        "description": "Зберігає нотатку для користувача.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Назва нотатки"},
                "content": {"type": "string", "description": "Текст нотатки"},
            },
            "required": ["title", "content"],
        },
    },
    {
        "name": "list_notes",
        "description": "Показує список нотаток користувача.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "delete_note",
        "description": "Видаляє нотатку за назвою.",
        "input_schema": {
            "type": "object",
            "properties": {"title": {"type": "string", "description": "Назва нотатки для видалення"}},
            "required": ["title"],
        },
    },
    {
        "name": "get_datetime",
        "description": "Повертає поточну дату і час українською мовою.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "read_url",
        "description": "Читає веб-сторінку і повертає текстовий вміст.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "URL сторінки"}},
            "required": ["url"],
        },
    },
]

MONTHS_UA = ["січня","лютого","березня","квітня","травня","червня",
              "липня","серпня","вересня","жовтня","листопада","грудня"]
DAYS_UA = ["понеділок","вівторок","середа","четвер","п'ятниця","субота","неділя"]

def _notes_file(uid: int) -> Path:
    return NOTES_DIR / f"{uid}.json"

def _load_notes(uid: int) -> dict:
    f = _notes_file(uid)
    return json.loads(f.read_text()) if f.exists() else {}

def _save_notes(uid: int, notes: dict):
    _notes_file(uid).write_text(json.dumps(notes, ensure_ascii=False, indent=2))

def tool_calculate(expression: str) -> str:
    try:
        allowed = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")}
        allowed.update({"abs": abs, "round": round})
        result = eval(expression, {"__builtins__": {}}, allowed)
        return f"{expression} = {result}"
    except Exception as e:
        return f"Помилка: {e}"

def tool_save_note(uid: int, title: str, content: str) -> str:
    notes = _load_notes(uid)
    notes[title] = {"content": content, "created": datetime.now().isoformat()}
    _save_notes(uid, notes)
    return f"Нотатку «{title}» збережено."

def tool_list_notes(uid: int) -> str:
    notes = _load_notes(uid)
    if not notes:
        return "Нотаток немає."
    lines = [f"📝 Твої нотатки ({len(notes)} шт.):"]
    for title, data in notes.items():
        lines.append(f"• {title}: {data['content'][:80]}")
    return "\n".join(lines)

def tool_delete_note(uid: int, title: str) -> str:
    notes = _load_notes(uid)
    if title in notes:
        del notes[title]
        _save_notes(uid, notes)
        return f"Нотатку «{title}» видалено."
    return f"Нотатку «{title}» не знайдено."

def tool_get_datetime() -> str:
    now = datetime.now(ZoneInfo("Europe/Kyiv"))
    return (f"{DAYS_UA[now.weekday()]}, {now.day} {MONTHS_UA[now.month-1]} {now.year} р., "
            f"{now.strftime('%H:%M')} (Київ)")

def tool_read_url(url: str) -> str:
    try:
        r = httpx.get(url, timeout=15, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script","style","nav","footer","header"]):
            tag.decompose()
        text = " ".join(soup.get_text(separator=" ").split())
        return text[:4000] if len(text) > 4000 else text
    except Exception as e:
        return f"Помилка читання: {e}"

def run_tool(uid: int, name: str, inp: dict) -> str:
    if name == "calculate":
        return tool_calculate(inp["expression"])
    if name == "save_note":
        return tool_save_note(uid, inp["title"], inp["content"])
    if name == "list_notes":
        return tool_list_notes(uid)
    if name == "delete_note":
        return tool_delete_note(uid, inp["title"])
    if name == "get_datetime":
        return tool_get_datetime()
    if name == "read_url":
        return tool_read_url(inp["url"])
    return "Невідомий інструмент."

async def run_agent(uid: int, messages: list) -> str:
    while True:
        resp = claude.messages.create(
            model="claude-opus-4-7",
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )
        if resp.stop_reason == "tool_use":
            tool_results = []
            assistant_content = resp.content
            for block in resp.content:
                if block.type == "tool_use":
                    result = run_tool(uid, block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
            messages.append({"role": "assistant", "content": assistant_content})
            messages.append({"role": "user", "content": tool_results})
        else:
            for block in resp.content:
                if hasattr(block, "text"):
                    return block.text
            return "Немає відповіді."

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привіт! Я HR-асистент на Claude.\n\n"
        "👔 *Робота з кандидатами:*\n"
        "/linkedin — аналіз профілю\n"
        "/reject — лист відмови\n"
        "/invite — запрошення на співбесіду\n\n"
        "🛠 *Інше:*\n"
        "/notes — мої нотатки\n"
        "/reset — очистити історію\n\n"
        "Або просто пиши — я відповім 🙂",
        parse_mode="Markdown",
    )

async def reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    history.pop(update.effective_user.id, None)
    await update.message.reply_text("Історію очищено.")

async def notes_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text(tool_list_notes(uid))

async def chat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    history.setdefault(uid, []).append({"role": "user", "content": update.message.text})
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    try:
        text = await run_agent(uid, history[uid])
        history[uid].append({"role": "assistant", "content": text})
        history[uid] = history[uid][-30:]
        await update.message.reply_text(text)
    except Exception:
        log.exception("error")
        await update.message.reply_text("Помилка. Спробуй ще раз.")

# ── LinkedIn analyzer ────────────────────────────────────────────────────────
LI_WAIT_DESC, LI_WAIT_PROFILE = range(10, 12)
REJ_WAIT_INFO, REJ_WAIT_REASON = range(20, 22)
INV_WAIT_INFO, INV_WAIT_SLOTS = range(30, 32)
hr_sessions: dict[int, dict] = {}
li_sessions: dict[int, dict] = {}

LI_PROMPT = """Ти — HR-аналітик. Проаналізуй LinkedIn профіль кандидата на відповідність вакансії.

ВАКАНСІЯ / КРИТЕРІЇ:
{description}

ПРОФІЛЬ КАНДИДАТА:
{profile}

Дай структурований аналіз:

🎯 ВІДПОВІДНІСТЬ: X/10

✅ СИЛЬНІ СТОРОНИ:
• (що збігається з вакансією)

❌ ЧОГО НЕ ВИСТАЧАЄ:
• (що відсутнє або не відповідає)

📋 ДОСВІД:
• (ключові посади і роки)

🛠 НАВИЧКИ:
• (релевантні навички)

💬 ВИСНОВОК:
(2-3 речення — запрошувати на співбесіду чи ні і чому)"""

def _fetch_linkedin(url: str) -> str:
    try:
        r = httpx.get(url, timeout=15, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.8",
        })
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script","style","nav","footer","header","aside"]):
            tag.decompose()
        text = " ".join(soup.get_text(separator="\n").split())
        if len(text) < 300 or "join linkedin" in text.lower():
            return ""
        return text[:5000]
    except Exception:
        return ""

async def li_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    li_sessions[update.effective_user.id] = {}
    await update.message.reply_text(
        "📋 *Аналіз LinkedIn профілю*\n\n"
        "Крок 1/2 — Надішли опис вакансії або критерії відбору.\n\n"
        "_Наприклад: «Python-розробник 3+ роки, Django, PostgreSQL, англійська»_\n\n"
        "/cancel — скасувати",
        parse_mode="Markdown",
    )
    return LI_WAIT_DESC

async def li_got_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    li_sessions[update.effective_user.id]["description"] = update.message.text
    await update.message.reply_text(
        "✅ Вакансію збережено.\n\n"
        "Крок 2/2 — Надішли профіль кандидата:\n\n"
        "🔗 *URL* (linkedin.com/in/...)\n"
        "📝 *або текст* — скопіюй зі сторінки LinkedIn і встав сюди\n\n"
        "Можна аналізувати кількох кандидатів підряд — просто надсилай наступний профіль.",
        parse_mode="Markdown",
    )
    return LI_WAIT_PROFILE

async def li_got_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    description = li_sessions.get(uid, {}).get("description", "")
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    await update.message.reply_text("⏳ Аналізую...")

    if re.match(r"https?://(www\.)?linkedin\.com/in/", text):
        profile_text = _fetch_linkedin(text)
        if not profile_text:
            await update.message.reply_text(
                "⚠️ LinkedIn заблокував автозавантаження.\n\n"
                "Відкрий профіль у браузері → Ctrl+A → скопіюй текст → надішли мені."
            )
            return LI_WAIT_PROFILE
    else:
        profile_text = text

    if len(profile_text) < 80:
        await update.message.reply_text("❌ Замало тексту. Скопіюй більше інформації з профілю.")
        return LI_WAIT_PROFILE

    try:
        resp = claude.messages.create(
            model="claude-opus-4-7", max_tokens=1500,
            messages=[{"role": "user", "content": LI_PROMPT.format(
                description=description, profile=profile_text
            )}],
        )
        await update.message.reply_text(resp.content[0].text)
        await update.message.reply_text("Надішли наступний профіль або /cancel щоб завершити.")
        return LI_WAIT_PROFILE
    except Exception:
        log.exception("li analyze error")
        await update.message.reply_text("Помилка аналізу. Спробуй ще раз.")
        return LI_WAIT_PROFILE

async def li_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    li_sessions.pop(update.effective_user.id, None)
    await update.message.reply_text("Аналіз завершено.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# ── Reject handler ────────────────────────────────────────────────────────────
REJECT_PROMPT = """Ти — досвідчений HR-рекрутер. Напиши персоналізований лист-відмову кандидату.

Кандидат: {name}
Вакансія: {position}
Причина відмови (для внутрішнього використання): {reason}

Вимоги до листа:
- Тепло і з повагою, без канцеляризмів
- Подякуй за час і інтерес
- НЕ вказуй конкретну причину відмови — лише м'яко повідом що обрали іншого
- Залиш двері відкритими для майбутніх вакансій
- Довжина: 5-7 речень
- Мова: українська
- Формат: готовий до відправки лист (без теми листа)"""

INVITE_PROMPT = """Ти — досвідчений HR-рекрутер. Напиши персоналізоване запрошення на співбесіду.

Кандидат: {name}
Вакансія: {position}
Доступні слоти для співбесіди: {slots}

Вимоги до листа:
- Енергійно і привітно
- Подякуй за резюме/профіль
- Запропонуй обрати зручний слот із наданих
- Вкажи що співбесіда займе ~1 годину
- Попроси підтвердити вибір часу
- Довжина: 6-8 речень
- Мова: українська
- Формат: готовий до відправки лист"""

async def rej_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    hr_sessions[update.effective_user.id] = {"type": "reject"}
    await update.message.reply_text(
        "✉️ *Лист відмови*\n\n"
        "Крок 1/2 — Напиши ім'я кандидата і вакансію.\n\n"
        "_Наприклад: «Іван Петренко, вакансія Python-розробник»_\n\n"
        "/cancel — скасувати",
        parse_mode="Markdown",
    )
    return REJ_WAIT_INFO

async def rej_got_info(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    hr_sessions[update.effective_user.id]["info"] = update.message.text
    await update.message.reply_text(
        "Крок 2/2 — Чому не підійшов кандидат?\n\n"
        "_Це тільки для мене, у лист не потрапить.\n"
        "Наприклад: «Мало досвіду», «Не влаштували зарплатні очікування», «Обрали сильнішого кандидата»_",
        parse_mode="Markdown",
    )
    return REJ_WAIT_REASON

async def rej_got_reason(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    info = hr_sessions.get(uid, {}).get("info", "")
    reason = update.message.text
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    await update.message.reply_text("⏳ Готую лист...")
    try:
        parts = info.split(",", 1)
        name = parts[0].strip() if parts else info
        position = parts[1].strip() if len(parts) > 1 else "вакансія"
        resp = claude.messages.create(
            model="claude-opus-4-7", max_tokens=800,
            messages=[{"role": "user", "content": REJECT_PROMPT.format(
                name=name, position=position, reason=reason
            )}],
        )
        await update.message.reply_text("📩 Готовий лист:\n\n" + resp.content[0].text)
        await update.message.reply_text(
            "Можеш скопіювати і відправити кандидату.\n"
            "Ще один? Напиши /reject або /cancel щоб завершити."
        )
    except Exception:
        log.exception("reject error")
        await update.message.reply_text("Помилка. Спробуй ще раз.")
    hr_sessions.pop(uid, None)
    return ConversationHandler.END

# ── Invite handler ─────────────────────────────────────────────────────────────
async def inv_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    hr_sessions[update.effective_user.id] = {"type": "invite"}
    await update.message.reply_text(
        "📅 *Запрошення на співбесіду*\n\n"
        "Крок 1/2 — Напиши ім'я кандидата і вакансію.\n\n"
        "_Наприклад: «Марія Коваленко, вакансія маркетолог»_\n\n"
        "/cancel — скасувати",
        parse_mode="Markdown",
    )
    return INV_WAIT_INFO

async def inv_got_info(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    hr_sessions[update.effective_user.id]["info"] = update.message.text
    await update.message.reply_text(
        "Крок 2/2 — Напиши доступні слоти для зустрічі.\n\n"
        "_Наприклад: «Вівторок 10:00, середа 14:00 або 16:00, п'ятниця 11:00»_",
        parse_mode="Markdown",
    )
    return INV_WAIT_SLOTS

async def inv_got_slots(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    info = hr_sessions.get(uid, {}).get("info", "")
    slots = update.message.text
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    await update.message.reply_text("⏳ Готую запрошення...")
    try:
        parts = info.split(",", 1)
        name = parts[0].strip() if parts else info
        position = parts[1].strip() if len(parts) > 1 else "вакансія"
        resp = claude.messages.create(
            model="claude-opus-4-7", max_tokens=800,
            messages=[{"role": "user", "content": INVITE_PROMPT.format(
                name=name, position=position, slots=slots
            )}],
        )
        await update.message.reply_text("📩 Готове запрошення:\n\n" + resp.content[0].text)
        await update.message.reply_text(
            "Можеш скопіювати і відправити кандидату.\n"
            "Ще одне? Напиши /invite або /cancel щоб завершити."
        )
    except Exception:
        log.exception("invite error")
        await update.message.reply_text("Помилка. Спробуй ще раз.")
    hr_sessions.pop(uid, None)
    return ConversationHandler.END

async def hr_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    hr_sessions.pop(update.effective_user.id, None)
    await update.message.reply_text("Скасовано.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# ── Photo handler ─────────────────────────────────────────────────────────────
async def photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    try:
        file = await update.message.photo[-1].get_file()
        img_bytes = bytes(await file.download_as_bytearray())
        import base64
        img_b64 = base64.standard_b64encode(img_bytes).decode()
        caption = update.message.caption or "Що на цьому фото?"
        resp = claude.messages.create(
            model="claude-opus-4-7",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}},
                {"type": "text", "text": caption},
            ]}],
        )
        await update.message.reply_text(resp.content[0].text)
    except Exception:
        log.exception("photo error")
        await update.message.reply_text("Не вдалося обробити фото.")

def main():
    log.info("Agent bot started with tools")
    app = ApplicationBuilder().token(os.environ["TELEGRAM_BOT_TOKEN"]).build()

    li_conv = ConversationHandler(
        entry_points=[CommandHandler("linkedin", li_start)],
        states={
            LI_WAIT_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, li_got_desc)],
            LI_WAIT_PROFILE: [MessageHandler(filters.TEXT & ~filters.COMMAND, li_got_profile)],
        },
        fallbacks=[CommandHandler("cancel", li_cancel)],
    )

    rej_conv = ConversationHandler(
        entry_points=[CommandHandler("reject", rej_start)],
        states={
            REJ_WAIT_INFO: [MessageHandler(filters.TEXT & ~filters.COMMAND, rej_got_info)],
            REJ_WAIT_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, rej_got_reason)],
        },
        fallbacks=[CommandHandler("cancel", hr_cancel)],
    )

    inv_conv = ConversationHandler(
        entry_points=[CommandHandler("invite", inv_start)],
        states={
            INV_WAIT_INFO: [MessageHandler(filters.TEXT & ~filters.COMMAND, inv_got_info)],
            INV_WAIT_SLOTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, inv_got_slots)],
        },
        fallbacks=[CommandHandler("cancel", hr_cancel)],
    )

    app.add_handler(li_conv)
    app.add_handler(rej_conv)
    app.add_handler(inv_conv)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("notes", notes_cmd))
    app.add_handler(MessageHandler(filters.PHOTO, photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))
    app.run_polling()

if __name__ == "__main__":
    main()
