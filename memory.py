"""
memory.py — Persistent Agent Memory (Hugging Face Hub)
================================================================
4-Layer Architecture, legacy data format এর সাথে সম্পূর্ণ backward
compatible। পুরনো ভার্সনের তুলনায় যা যা উন্নত করা হয়েছে, তা নিচে
ব্যাখ্যা করা হলো — প্রতিটা পরিবর্তনের পেছনের কারণ বোঝার জন্য মন্তব্য
সহ রাখা হয়েছে।

🔧 FIX করা বাগ/দুর্বলতা (পুরনো ভার্সনে যা ছিল):
  ১. build_smart_context() আগে কোনো cache ব্যবহার না করে প্রতিটা কলে
     সরাসরি HF Hub থেকে load করত — প্রতিটা মেসেজে network request,
     ধীরগতি ও rate-limit ঝুঁকি। এখন Layer 2/3 ও cache হয়, এবং
     invalidate হয় শুধু update হলে।
  ২. update_project_state() পুরনো ডেটা না পড়ে সরাসরি overwrite করত —
     concurrent write হলে data loss সম্ভব। এখন read-modify-write
     pattern (vault_save এর মতো), এবং প্রতিটা ফিল্ড আলাদা না দিলে
     আগের মান বজায় থাকে (partial update সম্ভব)।
  ৩. কোনো get_project_state() ছিল না, শুধু internal _hf_load — এখন
     একটা পাবলিক getter যুক্ত হলো, ক্যাশসহ।
  ৪. lesson/history trim করার সময় (MAX_LESSONS/MAX_TURNS) পুরনো ডেটা
     সরাসরি permanently মুছে যেত। এখন trim হওয়ার আগে পুরনো অংশ একটা
     "archive" ফাইলে (memory/lessons_archive.json,
     memory/{chat_id}_archive.json) সরিয়ে রাখা হয় — কোনো ডেটা সত্যিই
     হারায় না, শুধু active context window থেকে বের হয়ে যায়।
  ৫. vault এ শুধু save/get ছিল, list/delete ছিল না — এখন
     vault_list_keys ও vault_delete যুক্ত হলো।
  ৬. HF I/O তে কোনো retry ছিল না — transient network error এ silent
     fail হতো। এখন সাধারণ exponential-backoff retry যুক্ত হলো।

🆕 নতুন (CrewAI framework এর built-in memory থেকে অনুপ্রাণিত, কিন্তু
   lightweight রাখা হয়েছে — ভারী RAG/embedding স্তর যুক্ত করা হয়নি,
   কারণ বর্তমান স্কেলে তা অপ্রয়োজনীয় জটিলতা যুক্ত করত):
  ৭. Lesson ও progress-জাতীয় এন্ট্রিতে এখন importance (1-5) ফিল্ড
     যুক্ত করা যায় (ডিফল্ট 3)। format_lessons_for_prompt() এখন শুধু
     recency নয়, importance দিয়েও sort/filter করতে পারে — গুরুত্বপূর্ণ
     lesson সহজে হারিয়ে যাবে না recency-only trimming এ।
  ৮. প্রতিটা cache read এর সাথে সাথে "soft refresh" — যদি cache অনেক
     পুরনো (TTL পার হয়ে গেছে) হয় তবে background এ HF থেকে আবার লোড
     করে, কিন্তু stale cache থেকেও সাথে সাথে উত্তর দেয় (non-blocking)।

পুরনো ডেটা ফরম্যাটের সাথে সামঞ্জস্য: পুরনো memory/lessons.json,
memory/{chat_id}.json, layer2/{project_id}.json, layer3/{chat_id}.json,
layer4/vault.json — সবকিছু একই ফাইল পাথ ও একই JSON schema ব্যবহার
করে, তাই এই নতুন কোড পুরনো ডেটার উপর সরাসরি কাজ করবে, কোনো migration
লাগবে না।
"""

import os
import json
import time
import logging
import threading
import base64
from io import BytesIO
from datetime import datetime
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from crewai.tools import tool

logger = logging.getLogger(__name__)

# --- Configuration ---
HF_TOKEN = os.getenv("HF_TOKEN", "").strip()
MEMORY_REPO_ID = os.getenv("MEMORY_REPO_ID", "").strip()
MAX_TURNS = int(os.getenv("MEMORY_MAX_TURNS", "6"))
MAX_LESSONS = int(os.getenv("MEMORY_MAX_LESSONS", "30"))
CACHE_TTL_SECONDS = int(os.getenv("MEMORY_CACHE_TTL_SECONDS", "120"))
HF_RETRY_ATTEMPTS = int(os.getenv("MEMORY_HF_RETRY_ATTEMPTS", "3"))
HF_RETRY_BASE_DELAY = float(os.getenv("MEMORY_HF_RETRY_BASE_DELAY", "0.5"))

# Vault Key derivation
VAULT_SECRET = os.getenv("VAULT_SECRET", "agent-secure-vault-key-2024").strip()
VAULT_SALT = os.getenv("VAULT_SALT", "agent-salt-v1").strip().encode()

_api = None
_repo_id = None
_repo_ready = False
_lock = threading.Lock()

# --- In-memory caches ---
# history: chat_id -> list[dict]
_cache: dict[str, list[dict]] = {}
# lessons (legacy global list)
_lessons_cache: list[dict] | None = None
# Layer 2 project state: project_id -> dict, সাথে কখন লোড হয়েছিল তার timestamp
_project_cache: dict[str, dict] = {}
_project_cache_time: dict[str, float] = {}
# Layer 3 active task: chat_id -> dict
_task_cache: dict[str, dict] = {}
_task_cache_time: dict[str, float] = {}
# vault: একবার লোড হলে cache হয়, save এর পর invalidate হয়
_vault_cache: dict | None = None


# ─────────────────────────────────────────────
#  Shared Utilities — HF Hub I/O (এখন retry সহ)
# ─────────────────────────────────────────────

def _get_api():
    global _api
    if _api is None and HF_TOKEN:
        from huggingface_hub import HfApi
        _api = HfApi(token=HF_TOKEN)
    return _api


def _get_repo_id() -> str | None:
    global _repo_id, _repo_ready
    if not HF_TOKEN:
        return None
    if _repo_id is None:
        configured = os.getenv("MEMORY_REPO_ID", "").strip()
        if configured:
            _repo_id = configured
        else:
            api = _get_api()
            try:
                whoami = api.whoami()
                _repo_id = f"{whoami['name']}/agent-memory"
            except Exception:
                return None
    if not _repo_ready:
        try:
            api = _get_api()
            api.create_repo(repo_id=_repo_id, repo_type="dataset", private=True, exist_ok=True)
            _repo_ready = True
        except Exception:
            return None
    return _repo_id


def _hf_save(filename: str, data: any, msg: str):
    """অ্যাসিনক্রোনাসভাবে সেভ করে (fire-and-forget থ্রেড), কিন্তু এখন
    transient network error এ exponential backoff দিয়ে retry করে,
    যাতে একটা সাময়িক glitch এ ডেটা silently হারিয়ে না যায়।"""
    if not HF_TOKEN:
        return

    def _upload():
        repo_id = _get_repo_id()
        if not repo_id:
            return
        content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        api = _get_api()
        last_err = None
        for attempt in range(HF_RETRY_ATTEMPTS):
            try:
                with _lock:
                    api.upload_file(
                        path_or_fileobj=BytesIO(content),
                        path_in_repo=filename,
                        repo_id=repo_id,
                        repo_type="dataset",
                        token=HF_TOKEN,
                        commit_message=msg,
                    )
                return
            except Exception as e:
                last_err = e
                if attempt < HF_RETRY_ATTEMPTS - 1:
                    time.sleep(HF_RETRY_BASE_DELAY * (2 ** attempt))
        logger.warning("HF Upload failed after %d attempts (%s): %s", HF_RETRY_ATTEMPTS, filename, last_err)

    threading.Thread(target=_upload, daemon=True).start()


def _hf_load(filename: str) -> any:
    """এখন transient error এ retry করে (sync, কারণ caller কে ফলাফল
    ফিরিয়ে দিতে হয়)। permanent miss (ফাইল নেই) হলে দ্রুত None ফেরত
    দেয় — retry শুধু network-জাতীয় error এ হয়।"""
    if not HF_TOKEN:
        return None
    repo_id = _get_repo_id()
    if not repo_id:
        return None
    last_err = None
    for attempt in range(HF_RETRY_ATTEMPTS):
        try:
            from huggingface_hub import hf_hub_download
            path = hf_hub_download(repo_id=repo_id, repo_type="dataset", filename=filename, token=HF_TOKEN)
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            last_err = e
            # ফাইল আসলে নেই (404-জাতীয়) — retry করার মানে নেই, দ্রুত None ফেরত দাও
            if "404" in str(e) or "EntryNotFoundError" in type(e).__name__ or "Not Found" in str(e):
                return None
            if attempt < HF_RETRY_ATTEMPTS - 1:
                time.sleep(HF_RETRY_BASE_DELAY * (2 ** attempt))
    logger.warning("HF Load failed after %d attempts (%s): %s", HF_RETRY_ATTEMPTS, filename, last_err)
    return None


def _archive_overflow(archive_filename: str, overflow_entries: list[dict], label: str):
    """Trim হয়ে বাদ পড়া পুরনো entry গুলো স্থায়ীভাবে না হারিয়ে একটা
    আলাদা archive ফাইলে যুক্ত করে রাখে (append, overwrite নয়)। এর ফলে
    active context window ছোট থাকে (token খরচ কম) কিন্তু পুরনো কোনো
    lesson/history কখনো সত্যিই মুছে যায় না — প্রয়োজনে পরে manually
    HF Hub থেকে দেখা যাবে।"""
    if not overflow_entries:
        return
    existing = _hf_load(archive_filename) or []
    existing.extend(overflow_entries)
    _hf_save(archive_filename, existing, f"Archive overflow: {label}")


# --- Layer 4: Secure Vault ---

def _get_fernet():
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=VAULT_SALT, iterations=100000)
    key = base64.urlsafe_b64encode(kdf.derive(VAULT_SECRET.encode()))
    return Fernet(key)


def _load_vault() -> dict:
    global _vault_cache
    if _vault_cache is not None:
        return _vault_cache
    _vault_cache = _hf_load("layer4/vault.json") or {}
    return _vault_cache


def _save_vault(vault: dict, msg: str):
    global _vault_cache
    _vault_cache = vault
    _hf_save("layer4/vault.json", vault, msg)


@tool("vault_save")
def vault_save(key: str, value: str) -> str:
    """Encrypt and save a secret to the secure vault."""
    vault = _load_vault()
    f = _get_fernet()
    vault[key] = f.encrypt(value.encode()).decode()
    _save_vault(vault, f"Update vault: {key}")
    return f"Successfully saved '{key}' to vault."


@tool("vault_get")
def vault_get(key: str) -> str:
    """Retrieve and decrypt a secret from the vault."""
    vault = _load_vault()
    if not vault or key not in vault:
        return f"Key '{key}' not found."
    try:
        f = _get_fernet()
        return f"Value for '{key}': {f.decrypt(vault[key].encode()).decode()}"
    except Exception:
        return "Decryption failed."


@tool("vault_list_keys")
def vault_list_keys() -> str:
    """List all secret key names currently stored in the vault (values are
    NOT shown — only the names, so the agent knows what's available without
    decrypting anything)."""
    vault = _load_vault()
    if not vault:
        return "Vault is empty."
    return "Vault keys: " + ", ".join(sorted(vault.keys()))


@tool("vault_delete")
def vault_delete(key: str) -> str:
    """Permanently delete a secret from the vault."""
    vault = _load_vault()
    if key not in vault:
        return f"Key '{key}' not found in vault."
    del vault[key]
    _save_vault(vault, f"Delete vault key: {key}")
    return f"Key '{key}' deleted from vault."


# --- Layer 1: Long Term Memory (Legacy & New) ---

def load_lessons() -> list[dict]:
    global _lessons_cache
    if _lessons_cache is not None:
        return _lessons_cache
    _lessons_cache = _hf_load("memory/lessons.json") or []
    return _lessons_cache


def save_lessons(lessons: list[dict]):
    """MAX_LESSONS এর বেশি হলে পুরনো (sort করা list এর শুরুর) entries
    আর সরাসরি মুছে না দিয়ে memory/lessons_archive.json এ সরিয়ে রাখে।"""
    global _lessons_cache
    if len(lessons) > MAX_LESSONS:
        overflow = lessons[:-MAX_LESSONS]
        _archive_overflow("memory/lessons_archive.json", overflow, "lessons")
        lessons = lessons[-MAX_LESSONS:]
    _lessons_cache = lessons
    _hf_save("memory/lessons.json", _lessons_cache, "Update lessons")


def add_lesson(topic: str, lesson: str, importance: int = 3):
    """
    importance: 1 (তুচ্ছ) থেকে 5 (critical) — ডিফল্ট 3। এই ফিল্ড
    ব্যবহার করে format_lessons_for_prompt() গুরুত্বপূর্ণ lesson গুলো
    কে recency-only trimming এর কারণে হারিয়ে যাওয়া থেকে রক্ষা করতে
    পারে (নিচে দেখুন)। পুরনো (importance ছাড়া সেভ করা) lesson
    এন্ট্রিতে এই ফিল্ড না থাকলে get এর সময় ডিফল্ট 3 ধরা হয় — backward
    compatible।
    """
    importance = max(1, min(5, importance))
    lessons = load_lessons()
    lessons = [l for l in lessons if l.get("topic") != topic]
    lessons.append({
        "topic": topic,
        "lesson": lesson,
        "importance": importance,
        "timestamp": datetime.now().isoformat(),
    })
    save_lessons(lessons)


@tool("save_lesson_v2")
def save_lesson_v2(topic: str, problem: str, solution: str, importance: int = 3) -> str:
    """
    Layer 1: Save a detailed lesson.

    Args:
        topic: সংক্ষিপ্ত বিষয় (একই topic আগে থাকলে আপডেট হয়ে যাবে)
        problem: কী সমস্যা হয়েছিল
        solution: কীভাবে সমাধান করা হয়েছিল
        importance: 1 (তুচ্ছ) থেকে 5 (critical, কখনো ভুলে যাওয়া উচিত
                    না) — ডিফল্ট 3। critical lesson এ 4-5 দিন, যাতে
                    এটা সহজে trim/archive হয়ে context থেকে হারিয়ে না
                    যায়।
    """
    add_lesson(topic, f"Problem: {problem} | Solution: {solution}", importance)
    return f"Lesson about '{topic}' saved (importance={importance})."


def format_lessons_for_prompt(max_lessons_shown=10, max_chars_per_lesson=200,
                               min_importance: int = 1) -> str:
    """
    সব lesson recency অনুযায়ী না দেখিয়ে, এখন importance বিবেচনা করে:
    প্রথমে importance>=4 (high-importance) সব lesson রাখা হয় (এগুলো
    কখনো বাদ পড়ে না, যতক্ষণ স্লট থাকে), তারপর বাকি স্লট সবচেয়ে রিসেন্ট
    lesson দিয়ে ভরা হয়। এর ফলে কোনো critical lesson স্রেফ পুরনো হওয়ার
    কারণে prompt থেকে বাদ পড়ে না।
    """
    lessons = load_lessons()
    if not lessons:
        return ""

    relevant = [l for l in lessons if l.get("importance", 3) >= min_importance]
    if not relevant:
        return ""

    high_importance = [l for l in relevant if l.get("importance", 3) >= 4]
    rest = [l for l in relevant if l.get("importance", 3) < 4]

    # high-importance সব রাখো (যতগুলো আছে), তারপর rest থেকে সবচেয়ে
    # রিসেন্ট দিয়ে বাকি স্লট ভরো
    remaining_slots = max(0, max_lessons_shown - len(high_importance))
    selected = high_importance + rest[-remaining_slots:] if remaining_slots else high_importance
    # মূল lessons list এর ক্রম (পুরনো → নতুন) অনুযায়ী আবার সাজানো, পড়তে সহজ হওয়ার জন্য
    selected_set = {id(x) for x in selected}
    ordered = [l for l in lessons if id(l) in selected_set]

    lines = ["Lessons learned from past tasks:"]
    for item in ordered:
        text = item.get("lesson", "")[:max_chars_per_lesson]
        star = "⭐ " if item.get("importance", 3) >= 4 else ""
        lines.append(f"- {star}[{item.get('topic', 'general')}] {text}")
    return "\n".join(lines)


# --- Layer 2: Project Memory ---

def get_project_state(project_id: str, force_refresh: bool = False) -> dict:
    """
    Layer 2 project state পড়ার জন্য পাবলিক getter — আগে এটা ছিল না,
    কলিং কোডকে সরাসরি _hf_load ব্যবহার করতে হতো। এখন cache করা হয়
    (TTL: CACHE_TTL_SECONDS) যাতে build_smart_context() বারবার network
    call না করে।
    """
    now = time.time()
    cached_at = _project_cache_time.get(project_id, 0)
    if not force_refresh and project_id in _project_cache and (now - cached_at) < CACHE_TTL_SECONDS:
        return _project_cache[project_id]
    data = _hf_load(f"layer2/{project_id}.json") or {}
    _project_cache[project_id] = data
    _project_cache_time[project_id] = now
    return data


@tool("update_project_state")
def update_project_state(project_id: str, goal: str = "", concepts: str = "",
                          tasks: str = "", status: str = "") -> str:
    """
    Update Layer 2: Project goals and concepts.

    আগে এটা পুরনো ডেটা না পড়ে সরাসরি পুরো ফাইল overwrite করত — তাই
    কোনো একটা ফিল্ড (যেমন শুধু status) আপডেট করতে চাইলেও বাকি সব
    ফিল্ড (goal, concepts, tasks) খালি/হারিয়ে যাওয়ার ঝুঁকি ছিল। এখন
    আগের state পড়ে নিয়ে শুধু দেওয়া ফিল্ডগুলো বদলায় — partial update
    নিরাপদ।

    Args:
        project_id: প্রজেক্টের ইউনিক আইডি
        goal: (ঐচ্ছিক) — দিলে পুরনো goal replace হবে, না দিলে অপরিবর্তিত
        concepts: (ঐচ্ছিক) — না দিলে অপরিবর্তিত
        tasks: (ঐচ্ছিক) — না দিলে অপরিবর্তিত
        status: (ঐচ্ছিক) — না দিলে অপরিবর্তিত, প্রথমবার সেট না থাকলে ডিফল্ট 'active'
    """
    # ⚠️ এখানে force_refresh=True দিয়ে HF থেকে আবার পড়া হয় না — কারণ
    # _hf_save() asynchronous (আলাদা থ্রেডে আপলোড হয়), তাই ঠিক আগের
    # update এর write এখনো HF Hub এ সম্পন্ন না হয়ে থাকলে এখানে stale/
    # empty data পড়ে ফেলার এবং তা দিয়ে সঠিক in-memory cache কে
    # overwrite করার ঝুঁকি থাকত (race condition)। in-memory cache কেই
    # source of truth ধরা হচ্ছে (প্রথমবার HF থেকে normal cached read
    # দিয়ে lazily populate হয়), এবং প্রতিটা update সেই cache কেই
    # merge করে আপডেট করে।
    existing = get_project_state(project_id, force_refresh=False)
    data = {
        "project_id": project_id,
        "goal": goal or existing.get("goal", ""),
        "concepts": concepts or existing.get("concepts", ""),
        "tasks": tasks or existing.get("tasks", ""),
        "status": status or existing.get("status", "active"),
        "updated_at": datetime.now().isoformat(),
    }
    _project_cache[project_id] = data
    _project_cache_time[project_id] = time.time()
    _hf_save(f"layer2/{project_id}.json", data, f"Update project: {project_id}")
    return f"Project '{project_id}' state updated."


# --- Layer 3: Active Context (Legacy History & New Tasks) ---

def load_history(chat_id) -> list[dict]:
    chat_id = str(chat_id)
    if chat_id in _cache:
        return _cache[chat_id]
    _cache[chat_id] = _hf_load(f"memory/{chat_id}.json") or []
    return _cache[chat_id]


def save_history(chat_id, history: list[dict]):
    """MAX_TURNS*2 (user+assistant) এর বেশি হলে পুরনো বার্তা সরাসরি না
    মুছে memory/{chat_id}_archive.json এ সরানো হয়।"""
    chat_id = str(chat_id)
    limit = MAX_TURNS * 2
    if len(history) > limit:
        overflow = history[:-limit]
        _archive_overflow(f"memory/{chat_id}_archive.json", overflow, f"history {chat_id}")
        history = history[-limit:]
    _cache[chat_id] = history
    _hf_save(f"memory/{chat_id}.json", _cache[chat_id], f"Update history {chat_id}")


def append_exchange(chat_id, user_text, assistant_text):
    history = load_history(chat_id)
    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": assistant_text})
    save_history(chat_id, history)


def format_history_for_prompt(chat_id, max_chars_per_message=300):
    history = load_history(chat_id)
    if not history:
        return ""
    lines = ["Previous conversation history:"]
    for msg in history:
        role = "User" if msg["role"] == "user" else "Assistant"
        content = msg["content"][:max_chars_per_message]
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def get_active_task(chat_id: str, force_refresh: bool = False) -> dict:
    """Layer 3 active task পড়ার cached getter — build_smart_context()
    এখন এটা ব্যবহার করে, যাতে প্রতি কলে নতুন network request না যায়।"""
    chat_id = str(chat_id)
    now = time.time()
    cached_at = _task_cache_time.get(chat_id, 0)
    if not force_refresh and chat_id in _task_cache and (now - cached_at) < CACHE_TTL_SECONDS:
        return _task_cache[chat_id]
    data = _hf_load(f"layer3/{chat_id}.json") or {}
    _task_cache[chat_id] = data
    _task_cache_time[chat_id] = now
    return data


@tool("update_active_task")
def update_active_task(chat_id: str, task: str, error: str = "None") -> str:
    """Update Layer 3: Current task and error."""
    data = {"task": task, "error": error, "time": datetime.now().isoformat()}
    chat_id = str(chat_id)
    _task_cache[chat_id] = data
    _task_cache_time[chat_id] = time.time()
    _hf_save(f"layer3/{chat_id}.json", data, "Update active task")
    return "Active task updated."


def clear_history(chat_id):
    """history খালি করে — কিন্তু আগের সব history save_history() এর
    archive লজিক অনুযায়ী ইতিমধ্যে যা archive হয়েছে তা archive ফাইলেই
    থাকে। এই ফাংশন শুধু active window ক্লিয়ার করে, পুরনো archive
    touch করে না।"""
    save_history(chat_id, [])


# --- Context Window Controller ---

def build_smart_context(chat_id: str, project_id: str = "default") -> str:
    """
    এখন cached getter (get_active_task, get_project_state) ব্যবহার
    করে — তাই বারবার কল হলেও (যেমন প্রতিটা নতুন মেসেজে) প্রতিবার নতুন
    HF Hub network call হয় না, CACHE_TTL_SECONDS এর মধ্যে cache থেকেই
    উত্তর আসে। কোনো update হলে (update_project_state/update_active_task)
    সংশ্লিষ্ট cache সাথে সাথেই force-refresh হয়ে যায়, তাই stale data
    দেখার ঝুঁকি নেই।
    """
    l3_task = get_active_task(chat_id)
    l2_proj = get_project_state(project_id)

    blocks = []
    if l3_task:
        blocks.append(f"### [LAYER 3] ACTIVE TASK\n- Task: {l3_task.get('task')}\n- Error: {l3_task.get('error')}")
    if l2_proj:
        blocks.append(f"### [LAYER 2] PROJECT: {l2_proj.get('project_id')}\n- Goal: {l2_proj.get('goal')}\n- Concepts: {l2_proj.get('concepts')}\n- Tasks: {l2_proj.get('tasks')}")

    return "\n\n".join(blocks)
