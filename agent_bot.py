import os, json, logging, math, httpx
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

for v in ("HTTP_PROXY","HTTPS_PROXY","http_proxy","https_proxy","ALL_PROXY","all_proxy"):
    os.environ.pop(v, None)

from dotenv import load_dotenv
import anthropic
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

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
        "Привіт! Я бізнес-асистент на Claude.\n\n"
        "Вмію:\n🔢 Рахувати\n📝 Зберігати нотатки\n🌐 Читати сайти\n📅 Казати дату і час\n\n"
        "Команди: /notes /reset"
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
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("notes", notes_cmd))
    app.add_handler(MessageHandler(filters.PHOTO, photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))
    app.run_polling()

if __name__ == "__main__":
    main()
