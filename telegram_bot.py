"""
telegram_bot.py — Main entry point (requests-based polling)
=============================================================
python-telegram-bot এর httpx স্ট্যাক কিছু Hugging Face Space network এ
ConnectTimeout দেয়। এই ভার্সন raw `requests` লাইব্রেরি দিয়ে Telegram
Bot API এর সাথে কথা বলে — যা সাধারণত অনেক বেশি reliable।

Hugging Face Secrets:
  TELEGRAM_BOT_TOKEN   — BotFather থেকে নেওয়া Token
  TELEGRAM_CHAT_ID     — আপনার Chat ID (কমা দিয়ে একাধিক)
"""

import os
import time
import logging
import threading
import requests
from concurrent.futures import ThreadPoolExecutor
from agents import MyAgent
import memory
from tools import WORKSPACE

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is missing! Add it to HF Space secrets.")

API_BASE_ROOT = os.getenv("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")
API_BASE = f"{API_BASE_ROOT}/bot{TOKEN}"

_raw_ids = os.getenv("TELEGRAM_CHAT_ID", "")
AUTHORIZED_IDS = {cid.strip() for cid in _raw_ids.split(",") if cid.strip()}

SESSION = requests.Session()
TIMEOUT = (15, 40)  # (connect, read)

executor = ThreadPoolExecutor(max_workers=4)


def is_authorized(chat_id) -> bool:
    if not AUTHORIZED_IDS:
        return True
    return str(chat_id) in AUTHORIZED_IDS


def api_call(method: str, **params):
    """Call Telegram Bot API with retries on connection errors."""
    url = f"{API_BASE}/{method}"
    for attempt in range(5):
        try:
            resp = SESSION.post(url, json=params, timeout=TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            wait = min(2 ** attempt, 30)
            logger.warning("API call '%s' failed (attempt %d): %s — retrying in %ds",
                            method, attempt + 1, e, wait)
            time.sleep(wait)
    logger.error("API call '%s' failed after retries.", method)
    return None


def send_message(chat_id, text: str, parse_mode: str | None = None):
    result = None
    for chunk in _split_message(text):
        params = {"chat_id": chat_id, "text": chunk}
        if parse_mode:
            params["parse_mode"] = parse_mode
        result = api_call("sendMessage", **params)
    return result


def edit_message(chat_id, message_id, text: str, parse_mode: str | None = None):
    params = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if parse_mode:
        params["parse_mode"] = parse_mode
    return api_call("editMessageText", **params)


def delete_message(chat_id, message_id):
    return api_call("deleteMessage", chat_id=chat_id, message_id=message_id)


def send_chat_action(chat_id, action="typing"):
    return api_call("sendChatAction", chat_id=chat_id, action=action)


# ── Continuous "typing" indicator ────────────────────────────────────────────
# Telegram's chat action expires after ~5 seconds, so we refresh it
# periodically while the agent is working (shows "agent working..." +
# the animated dots indicator at the top of the chat on Telegram).

class TypingIndicator:
    """Keeps the Telegram 'typing…' indicator alive in the background."""

    def __init__(self, chat_id, action="working", interval=10):
        self.chat_id = chat_id
        self.action = action
        self.interval = interval
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)

    def _run(self):
        while not self._stop.is_set():
            send_chat_action(self.chat_id, self.action)
            self._stop.wait(self.interval)


# ── Status message animation ─────────────────────────────────────────────────

_WORKING_FRAMES = [
    "😎 *agent:* I am working sir, please wait sometime ⏳",
    "😎 *agent:* I am working sir, please wait sometime ⏳.",
    "😎 *agent:* I am working sir, please wait sometime ⏳..",
    "😎 *agent:* I am working sir, please wait sometime ⏳...",
]


class StatusAnimator:
    """Periodically edits a status message to show a 'thinking' animation."""

    def __init__(self, chat_id, message_id, interval=8):
        self.chat_id = chat_id
        self.message_id = message_id
        self.interval = interval
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)

    def _run(self):
        i = 0
        while not self._stop.is_set():
            frame = _WORKING_FRAMES[i % len(_WORKING_FRAMES)]
            edit_message(self.chat_id, self.message_id, frame, parse_mode="Markdown")
            i += 1
            self._stop.wait(self.interval)


# ── File download (incoming) ─────────────────────────────────────────────────

INCOMING_DIR = os.path.join(WORKSPACE, "incoming")
os.makedirs(INCOMING_DIR, exist_ok=True)


def download_telegram_file(file_id: str, suggested_name: str | None = None) -> str | None:
    """Download a file from Telegram by file_id into the agent's workspace.
    Returns the local file path, or None on failure."""
    try:
        info = api_call("getFile", file_id=file_id)
        if not info or not info.get("ok"):
            return None
        file_path = info["result"]["file_path"]
        url = f"{API_BASE_ROOT}/file/bot{TOKEN}/{file_path}"

        local_name = suggested_name or os.path.basename(file_path)
        local_path = os.path.join(INCOMING_DIR, local_name)

        resp = SESSION.get(url, timeout=(15, 60))
        resp.raise_for_status()
        with open(local_path, "wb") as f:
            f.write(resp.content)

        return local_path
    except Exception as e:
        logger.warning("Failed to download Telegram file %s: %s", file_id, e)
        return None


# ── File upload (outgoing) ───────────────────────────────────────────────────

def send_document(chat_id, file_path: str, caption: str | None = None):
    """Send a local file to the user as a Telegram document."""
    try:
        url = f"{API_BASE}/sendDocument"
        with open(file_path, "rb") as f:
            files = {"document": (os.path.basename(file_path), f)}
            data = {"chat_id": chat_id}
            if caption:
                data["caption"] = caption[:1024]
            resp = SESSION.post(url, data=data, files=files, timeout=(15, 120))
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning("Failed to send document %s: %s", file_path, e)
        return None


def send_voice(chat_id, file_path: str, caption: str | None = None):
    """Send a local audio file to the user as a Telegram voice message."""
    try:
        url = f"{API_BASE}/sendVoice"
        with open(file_path, "rb") as f:
            files = {"voice": (os.path.basename(file_path), f)}
            data = {"chat_id": chat_id}
            if caption:
                data["caption"] = caption[:1024]
            resp = SESSION.post(url, data=data, files=files, timeout=(15, 120))
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning("Failed to send voice %s: %s", file_path, e)
        return None


def send_output_files(chat_id, text: str):
    """Scan the agent's response for file paths in the workspace and send any
    that exist as Telegram documents (so the user receives the actual file)."""
    import re
    candidates = set()

    # Absolute workspace paths mentioned in the text
    for m in re.finditer(r"(/[^\s`'\"]+)", text):
        candidates.add(m.group(1))
    # Backtick/code-quoted filenames
    for m in re.finditer(r"[`'\"]([^\s`'\"]+\.\w{1,8})[`'\"]", text):
        candidates.add(m.group(1))

    sent = set()
    for c in candidates:
        path = c if os.path.isabs(c) else os.path.join(WORKSPACE, c)
        path = os.path.realpath(path)
        if (
            path.startswith(os.path.realpath(WORKSPACE))
            and os.path.isfile(path)
            and path not in sent
        ):
            send_document(chat_id, path)
            sent.add(path)

    return sent


def set_my_commands():
    commands = [
        {"command": "start", "description": "Show welcome message"},
        {"command": "help", "description": "Show available tools and usage"},
        {"command": "model", "description": "Show current LLM model info"},
        {"command": "clear", "description": "Clear conversation context"},
    ]
    return api_call("setMyCommands", commands=commands)


def _split_message(text: str, limit: int = 4096) -> list[str]:
    parts = []
    while len(text) > limit:
        split_at = text.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        parts.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    parts.append(text)
    return parts


# ── Command text ─────────────────────────────────────────────────────────────

WELCOME_TEXT = (
    "👋 *Hello! I am your AI Agent.*\n\n"
    "Send me any question or task and I'll get to work.\n\n"
    "📌 *Commands:*\n"
    "/start — Show this message\n"
    "/help — Detailed help\n"
    "/model — Show current LLM model\n"
    "/clear — Clear conversation hint"
)

HELP_TEXT = (
    "🤖 *AI Agent — Help*\n\n"
    "*What I can do:*\n"
    "• 🔍 Search the web (DuckDuckGo)\n"
    "• 📰 Search news\n"
    "• 🖼️ Search images\n"
    "• 🌐 Browse any webpage\n"
    "• 📂 Read/write files (and send them back to you)\n"
    "• 🧮 Calculate math\n"
    "• 🕐 Get current time\n"
    "• 📝 Summarize text\n"
    "• 🔐 Manage passwords\n"
    "• 🐙 GitHub repos, issues & Actions\n\n"
    "*Files & Voice:*\n"
    "📎 Send me any document, photo, voice note, or audio file — I'll receive it and "
    "can work with it (read, analyze, process).\n"
    "📤 If I create or modify a file for you, I'll send it back automatically.\n\n"
    "*How to use:*\n"
    "Just send me a message in plain language!\n\n"
    "_Example: Search for the latest AI news and summarize_"
)


def handle_command(command: str, chat_id, agent_factory):
    if command == "/start":
        send_message(chat_id, WELCOME_TEXT, parse_mode="Markdown")
    elif command == "/help":
        send_message(chat_id, HELP_TEXT, parse_mode="Markdown")
    elif command == "/model":
        model = os.getenv("LLM_MODEL", "auto-detected")
        base_url = os.getenv("LLM_BASE_URL", "provider default")
        send_message(
            chat_id,
            f"🤖 *Current LLM Config*\n\nModel: `{model}`\nBase URL: `{base_url}`\n\n"
            f"_Change via HF Space Secrets: LLM_MODEL, LLM_BASE_URL, LLM_API_KEY_",
            parse_mode="Markdown",
        )
    elif command == "/clear":
        memory.clear_history(chat_id)
        send_message(chat_id, "🗑️ Context cleared. Start fresh!")
    else:
        send_message(chat_id, "Unknown command. Try /help")


def handle_text(text: str, chat_id, agent_factory, attached_file: str | None = None, file_kind: str | None = None):
    status = send_message(chat_id, _WORKING_FRAMES[0], parse_mode="Markdown")
    status_msg_id = None
    if status and status.get("ok"):
        status_msg_id = status["result"]["message_id"]

    typing = TypingIndicator(chat_id, "typing")
    animator = StatusAnimator(chat_id, status_msg_id) if status_msg_id else None

    typing.__enter__()
    if animator:
        animator.__enter__()

    try:
        # Build memory context: past lessons (global) + this chat's history
        lessons_block = memory.format_lessons_for_prompt()
        history_block = memory.format_history_for_prompt(chat_id)
        memory_context = "\n\n".join(b for b in [lessons_block, history_block] if b)

        task_description = text
        if attached_file:
            task_description = (
                f"{text}\n\n"
                f"[The user also sent a {file_kind or 'file'} attachment, saved at this "
                f"path on the local filesystem: {attached_file} — use read_file or other "
                f"appropriate tools to inspect/process it if relevant to the request.]"
            )

        # ── এজেন্ট orchestration (Crew/Task/multi-agent) সম্পূর্ণ agents.py
        # এর ভেতরে থাকে — telegram_bot.py শুধু instruction পাঠায় ও ফলাফল
        # ফেরত পায়, কীভাবে কাজটা ভেতরে সম্পন্ন হলো তা নিয়ে কিছু জানে না ──
        HARD_TIMEOUT = int(os.getenv("AGENT_HARD_TIMEOUT_SECONDS", "240"))
        kickoff_result = {}

        def _run_crew():
            try:
                kickoff_result["value"] = agent_factory.run(
                    instruction=task_description,
                    memory_context=memory_context,
                    chat_id=str(chat_id),
                )
            except Exception as exc:
                kickoff_result["error"] = exc

        worker = threading.Thread(target=_run_crew, daemon=True)
        worker.start()
        worker.join(timeout=HARD_TIMEOUT)

        if worker.is_alive():
            result_text = (
                "⏰ This task is taking too long and was stopped (timeout "
                f"{HARD_TIMEOUT}s). It may involve a long-running operation "
                "(e.g. watching a GitHub Actions run). Try asking me to check "
                "the status again, or break the task into smaller steps."
            )
            memory.add_lesson(
                topic="long_running_task",
                lesson=f"Task '{text[:100]}' exceeded {HARD_TIMEOUT}s and was abandoned "
                       f"— consider using single-snapshot tools instead of watch/monitor loops.",
            )
        elif "error" in kickoff_result:
            raise kickoff_result["error"]
        else:
            result_text = str(kickoff_result.get("value", "")).strip() or "✅ Task completed (no output returned)."

        if animator:
            animator.__exit__(None, None, None)
        typing.__exit__(None, None, None)

        if status_msg_id:
            delete_message(chat_id, status_msg_id)
        send_message(chat_id, result_text)

        # If the agent mentions any output files in the workspace, send them too
        send_output_files(chat_id, result_text)

        # Persist this exchange for future context
        memory.append_exchange(chat_id, text, result_text)

    except Exception as e:
        if animator:
            animator.__exit__(None, None, None)
        typing.__exit__(None, None, None)

        logger.exception("Error processing request from chat %s", chat_id)
        error_text = f"❌ An error occurred:\n{str(e)[:500]}\n\nPlease try again or rephrase your request."
        if status_msg_id:
            edit_message(chat_id, status_msg_id, error_text)
        else:
            send_message(chat_id, error_text)

        try:
            memory.add_lesson(
                topic=f"error_{type(e).__name__}",
                lesson=f"Task '{text[:100]}' failed with: {str(e)[:300]}",
            )
        except Exception:
            pass


# ── Status web UI (for Hugging Face Space "App" tab) ─────────────────────────

BOT_STATUS = {
    "state": "starting",          # starting | running | error
    "bot_username": None,
    "model": os.getenv("LLM_MODEL", "auto-detected"),
    "started_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
    "messages_handled": 0,
}


def _run_status_server():
    try:
        from flask import Flask, jsonify
    except ImportError:
        logger.warning("Flask not installed — status web UI disabled.")
        return

    app = Flask(__name__)

    @app.route("/")
    def index():
        s = BOT_STATUS
        color = {"starting": "#f39c12", "running": "#2ecc71", "error": "#e74c3c"}.get(s["state"], "#999")
        label = {"starting": "Starting…", "running": "Agent is running ✅", "error": "Error ⚠️"}.get(s["state"], s["state"])
        return f"""
        <html>
        <head>
          <title>AI Agent Status</title>
          <meta http-equiv="refresh" content="10">
          <style>
            body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; background:#0f1115; color:#eee;
                    display:flex; align-items:center; justify-content:center; height:100vh; margin:0; }}
            .card {{ background:#1b1e26; padding:32px 40px; border-radius:16px; box-shadow:0 4px 24px rgba(0,0,0,.4);
                     text-align:center; min-width:320px; }}
            .dot {{ display:inline-block; width:14px; height:14px; border-radius:50%; background:{color};
                    margin-right:8px; box-shadow:0 0 10px {color}; }}
            h1 {{ font-size:20px; margin-bottom:4px; }}
            .row {{ display:flex; justify-content:space-between; margin:8px 0; font-size:14px; color:#aaa; }}
            .row b {{ color:#eee; }}
          </style>
        </head>
        <body>
          <div class="card">
            <h1><span class="dot"></span>{label}</h1>
            <div class="row"><span>Bot</span><b>@{s["bot_username"] or "—"}</b></div>
            <div class="row"><span>Model</span><b>{s["model"]}</b></div>
            <div class="row"><span>Started</span><b>{s["started_at"]}</b></div>
            <div class="row"><span>Messages handled</span><b>{s["messages_handled"]}</b></div>
          </div>
        </body>
        </html>
        """

    @app.route("/health")
    def health():
        return jsonify(BOT_STATUS)

    port = int(os.getenv("PORT", "7860"))
    app.run(host="0.0.0.0", port=port)


def main():
    # Start status web UI in background
    threading.Thread(target=_run_status_server, daemon=True).start()

    logger.info("Initialising agent and LLM...")
    agent_factory = MyAgent()
    logger.info("Agent and LLM initialised successfully.")

    # Verify bot token works (retry until success — handles slow startup network)
    me = None
    for attempt in range(10):
        me = api_call("getMe")
        if me and me.get("ok"):
            break
        logger.warning("getMe failed, retrying... (%d/10)", attempt + 1)
        time.sleep(5)

    if not me or not me.get("ok"):
        logger.error("Could not connect to Telegram API after retries. Will keep retrying in main loop.")
        BOT_STATUS["state"] = "error"
    else:
        logger.info("Connected as @%s", me["result"].get("username"))
        BOT_STATUS["bot_username"] = me["result"].get("username")
        BOT_STATUS["state"] = "running"

    set_my_commands()

    logger.info("🤖 Telegram bot started. Polling for messages…")

    offset = None
    while True:
        try:
            params = {"timeout": 30}
            if offset is not None:
                params["offset"] = offset
            resp = SESSION.post(f"{API_BASE}/getUpdates", json=params, timeout=(15, 40))
            resp.raise_for_status()
            data = resp.json()

            if not data.get("ok"):
                logger.warning("getUpdates returned not ok: %s", data)
                time.sleep(3)
                continue

            for update in data.get("result", []):
                offset = update["update_id"] + 1
                message = update.get("message")
                if not message:
                    continue

                chat_id = message["chat"]["id"]

                if not is_authorized(chat_id):
                    send_message(chat_id, "⛔ Unauthorized access.")
                    continue

                text = (message.get("text") or message.get("caption") or "").strip()

                # ── Incoming file attachments (document / photo / voice / audio) ──
                attached_file = None
                file_kind = None

                if "document" in message:
                    doc = message["document"]
                    attached_file = download_telegram_file(doc["file_id"], doc.get("file_name"))
                    file_kind = "document"
                elif "photo" in message:
                    # Telegram sends multiple sizes — take the largest
                    largest = message["photo"][-1]
                    attached_file = download_telegram_file(largest["file_id"], f"photo_{largest['file_id']}.jpg")
                    file_kind = "photo"
                elif "voice" in message:
                    voice = message["voice"]
                    attached_file = download_telegram_file(voice["file_id"], f"voice_{voice['file_id']}.ogg")
                    file_kind = "voice message"
                    if not text:
                        text = "(The user sent a voice message. Transcribe/listen if possible, or let them know you received it and ask what they'd like you to do with it.)"
                elif "audio" in message:
                    audio = message["audio"]
                    attached_file = download_telegram_file(audio["file_id"], audio.get("file_name") or f"audio_{audio['file_id']}.mp3")
                    file_kind = "audio file"

                if attached_file and not text:
                    text = f"The user sent a {file_kind}. Please review it and respond appropriately."

                if not text and not attached_file:
                    continue

                if text.startswith("/"):
                    command = text.split()[0].split("@")[0]  # strip bot username
                    executor.submit(handle_command, command, chat_id, agent_factory)
                else:
                    BOT_STATUS["messages_handled"] += 1
                    executor.submit(handle_text, text, chat_id, agent_factory, attached_file, file_kind)

        except requests.exceptions.RequestException as e:
            logger.warning("Polling network error: %s — retrying in 5s", e)
            time.sleep(5)
        except Exception:
            logger.exception("Unexpected error in polling loop")
            time.sleep(5)


if __name__ == "__main__":
    main()
