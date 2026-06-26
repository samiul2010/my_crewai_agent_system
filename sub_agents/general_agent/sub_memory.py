"""
sub_memory.py — Persistent Memory for Sub-Agents (3-Layer, Namespaced)
======================================================================
এই মডিউলটি মূল agent এর memory.py থেকে সম্পূর্ণ আলাদা এবং স্বাধীন। একই
HF Hub dataset repo ব্যবহার করে (storage খরচ বাঁচাতে), কিন্তু নিচের
AGENT_NAMESPACE ভেরিয়েবলের মান অনুযায়ী একটা পৃথক ফোল্ডারের নিচে ফাইল
রাখে।

🔧 কীভাবে একাধিক sub-agent এ একই ফাইল পুনর্ব্যবহার করবেন:
  এই একটাই sub_memory.py ফাইল সব sub-agent ব্যবহার করবে। প্রতিটা
  sub-agent এর নিজের ফাইলে (যেমন sub_github.py) import করার ঠিক পরে
  নিচের মতো namespace override করে দিন:

      import sub_memory
      sub_memory.AGENT_NAMESPACE = "github"   # ← শুধু এই লাইন বদলালেই
                                                 সম্পূর্ণ নতুন, আলাদা
                                                 HF ফোল্ডার (এবং তাই
                                                 আলাদা মেমোরি স্পেস)
                                                 ব্যবহার হবে।

  এর ফলে HF dataset repo তে ডেটা এভাবে সাজানো থাকবে:
      sub-agents/github/layer1/<mission_id>.json
      sub-agents/browser/layer1/<mission_id>.json
      sub-agents/whatsapp/layer1/<mission_id>.json
      ... ইত্যাদি, namespace অনুযায়ী আলাদা।

  নিচের module-level ডিফল্ট (AGENT_NAMESPACE = "default") শুধু তখনই
  ব্যবহার হবে যদি কোনো sub-agent ফাইল এটা override না করে — production
  এ প্রতিটা sub-agent ফাইলে এটা override করা আবশ্যক, নাহলে সব sub-agent
  এর মেমোরি একসাথে মিশে যাবে।

৩-লেয়ার ডিজাইন (মূল agent এর ৪-লেয়ারের চেয়ে ছোট, কারণ sub-agent এর
কোনো vault/credential প্রয়োজন নেই — credential ব্যবস্থাপনা সবসময়
মূল agent এর দায়িত্বে থাকে):

  [LAYER 1] MISSION   — sub-agent কে যে প্রজেক্ট/টাস্কের জন্য ডাকা হয়েছে
                         তার মূল লক্ষ্য ও প্রত্যাশিত ফাইনাল আউটপুট।
                         প্রতিটা নতুন কলে এটাই প্রথমে context এ যায়, যাতে
                         sub-agent কখনো লক্ষ্য থেকে সরে না যায়।

  [LAYER 2] SUBTASKS   — মিশনটি ভাঙা সাব-টাস্কগুলোর তালিকা ও তাদের
                         স্ট্যাটাস (pending/in_progress/done/blocked)।

  [LAYER 3] PROGRESS   — প্রতিটা সাব-টাস্ক কতটা এগিয়েছে, কী ভুল হয়েছিল,
                         কেন হয়েছিল, এবং কীভাবে আরও ভালোভাবে করা যায় —
                         এই lesson/history স্তরটাই মূল agent এর Layer 1
                         (lessons) এর সমতুল্য, sub-agent এর জন্য।

ব্যবহারের নিয়ম (mission drift আটকানোর জন্য):
  build_sub_context() সবসময় MISSION ব্লককে সবচেয়ে উপরে রাখে, তারপর
  SUBTASKS, তারপর PROGRESS — এই ক্রম ইচ্ছাকৃত। LLM সাধারণত prompt এর
  শুরুর তথ্যকে বেশি ওজন দেয়, তাই "মূল লক্ষ্য" বারবার সবার উপরে দেখানো
  sub-agent কে scope creep থেকে রক্ষা করে।
"""

import os
import json
import logging
import threading
from io import BytesIO
from datetime import datetime
from crewai.tools import tool

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
#  🔑 এই একটি ভেরিয়েবল পরিবর্তন করেই সম্পূর্ণ আলাদা sub-agent এর জন্য
#  আলাদা HF Hub ফোল্ডার (= আলাদা মেমোরি স্পেস) তৈরি হয়ে যায়।
#  প্রতিটা sub-agent ফাইল import করার পরেই এটা override করবে:
#      import sub_memory
#      sub_memory.AGENT_NAMESPACE = "github"
# ═══════════════════════════════════════════════════════════════
AGENT_NAMESPACE = "general_sub_agent"


# --- Configuration (মূল agent এর HF env গুলোই পুনর্ব্যবহার করা হচ্ছে) ---
HF_TOKEN = os.getenv("HF_TOKEN", "").strip()
# চাইলে সব sub-agent কে সম্পূর্ণ আলাদা dataset repo তেও রাখা যায়;
# কিন্তু একই repo + namespace-ভিত্তিক ফোল্ডার ডিফল্ট, কারণ এক জায়গায়
# সব ব্যাকআপ রাখা সহজ। ভিন্ন repo চাইলে SUB_MEMORY_REPO_ID সেট করুন।
SUB_MEMORY_REPO_ID = os.getenv("SUB_MEMORY_REPO_ID", "").strip() or os.getenv("MEMORY_REPO_ID", "").strip()
SUB_MAX_PROGRESS_ENTRIES = int(os.getenv("SUB_MEMORY_MAX_PROGRESS", "40"))

_api = None
_repo_id = None
_repo_ready = False
_lock = threading.Lock()

# in-memory cache, namespace অনুযায়ী আলাদা — যাতে একই প্রসেসে একাধিক
# sub-agent (আলাদা namespace) চললেও তাদের cache ভুলবশত মিশে না যায়।
# cache key = f"{AGENT_NAMESPACE}::{mission_id}"
_mission_cache: dict[str, dict] = {}
_subtasks_cache: dict[str, list[dict]] = {}
_progress_cache: dict[str, list[dict]] = {}


def _cache_key(mission_id: str) -> str:
    return f"{AGENT_NAMESPACE}::{mission_id}"


def _namespaced_path(filename: str) -> str:
    """বর্তমান AGENT_NAMESPACE অনুযায়ী HF Hub এর ভেতরের পাথ তৈরি করে।
    namespace পরিবর্তন হলে এই function এর আউটপুটও বদলে যায়, তাই কলিং
    কোড কিছু না জেনেই ভিন্ন ফোল্ডারে read/write করে।"""
    return f"sub-agents/{AGENT_NAMESPACE}/{filename}"


# ─────────────────────────────────────────────
#  HF Hub I/O (মূল memory.py এর মতই pattern, কিন্তু namespace-ভিত্তিক)
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
        if SUB_MEMORY_REPO_ID:
            _repo_id = SUB_MEMORY_REPO_ID
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
    """বর্তমান AGENT_NAMESPACE অনুযায়ী পাথে অ্যাসিনক্রোনাসভাবে সেভ করে —
    fire-and-forget থ্রেড, যাতে sub-agent এর কাজ HF আপলোডের জন্য ব্লক
    না হয়।"""
    if not HF_TOKEN:
        return
    full_path = _namespaced_path(filename)
    namespace_snapshot = AGENT_NAMESPACE  # থ্রেড শুরুর সময়ের namespace ধরে রাখা

    def _upload():
        repo_id = _get_repo_id()
        if not repo_id:
            return
        try:
            content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            api = _get_api()
            with _lock:
                api.upload_file(
                    path_or_fileobj=BytesIO(content),
                    path_in_repo=full_path,
                    repo_id=repo_id,
                    repo_type="dataset",
                    token=HF_TOKEN,
                    commit_message=f"[{namespace_snapshot}] {msg}",
                )
        except Exception as e:
            logger.warning("Sub-memory HF upload failed (%s): %s", full_path, e)

    threading.Thread(target=_upload, daemon=True).start()


def _hf_load(filename: str) -> any:
    if not HF_TOKEN:
        return None
    repo_id = _get_repo_id()
    if not repo_id:
        return None
    full_path = _namespaced_path(filename)
    try:
        from huggingface_hub import hf_hub_download
        path = hf_hub_download(repo_id=repo_id, repo_type="dataset", filename=full_path, token=HF_TOKEN)
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ─────────────────────────────────────────────
#  [LAYER 1] MISSION — মূল লক্ষ্য ও প্রত্যাশিত ফাইনাল আউটপুট
# ─────────────────────────────────────────────

def set_mission(mission_id: str, goal: str, final_output: str, constraints: str = "") -> dict:
    """
    মিশনের মূল লক্ষ্য নির্ধারণ/আপডেট করে। সাধারণত মূল agent এই sub-agent কে
    ডাকার আগে একবার এটা সেট করবে (বা প্রথম কলেই সেট করে দেবে)।

    Args:
        mission_id: এই কাজের ইউনিক আইডি (যেমন repo নাম বা project_id)
        goal: এই মিশনের মূল উদ্দেশ্য কী
        final_output: কাজ শেষে চূড়ান্ত আউটপুট কী রকম/কী হওয়া উচিত
        constraints: কোনো hard constraint থাকলে (optional)
    """
    data = {
        "mission_id": mission_id,
        "goal": goal,
        "final_output": final_output,
        "constraints": constraints,
        "updated_at": datetime.now().isoformat(),
    }
    _mission_cache[_cache_key(mission_id)] = data
    _hf_save(f"layer1/{mission_id}.json", data, f"Set mission: {mission_id}")
    return data


def get_mission(mission_id: str) -> dict:
    key = _cache_key(mission_id)
    if key in _mission_cache:
        return _mission_cache[key]
    data = _hf_load(f"layer1/{mission_id}.json") or {}
    _mission_cache[key] = data
    return data


# ─────────────────────────────────────────────
#  [LAYER 2] SUBTASKS — মিশন ভাঙা ছোট কাজের তালিকা ও স্ট্যাটাস
# ─────────────────────────────────────────────

def load_subtasks(mission_id: str) -> list[dict]:
    key = _cache_key(mission_id)
    if key in _subtasks_cache:
        return _subtasks_cache[key]
    _subtasks_cache[key] = _hf_load(f"layer2/{mission_id}.json") or []
    return _subtasks_cache[key]


def save_subtasks(mission_id: str, subtasks: list[dict]):
    _subtasks_cache[_cache_key(mission_id)] = subtasks
    _hf_save(f"layer2/{mission_id}.json", subtasks, f"Update subtasks: {mission_id}")


def upsert_subtask(mission_id: str, subtask_id: str, description: str,
                    status: str = "pending", note: str = "") -> list[dict]:
    """
    একটা সাব-টাস্ক যোগ করে বা (একই subtask_id থাকলে) আপডেট করে।
    status: 'pending' | 'in_progress' | 'done' | 'blocked'
    """
    subtasks = load_subtasks(mission_id)
    subtasks = [s for s in subtasks if s.get("id") != subtask_id]
    subtasks.append({
        "id": subtask_id,
        "description": description,
        "status": status,
        "note": note,
        "updated_at": datetime.now().isoformat(),
    })
    save_subtasks(mission_id, subtasks)
    return subtasks


# ─────────────────────────────────────────────
#  [LAYER 3] PROGRESS — কী হয়েছে, কেন হয়েছে, কীভাবে ভালো করা যায়
# ─────────────────────────────────────────────

def load_progress(mission_id: str) -> list[dict]:
    key = _cache_key(mission_id)
    if key in _progress_cache:
        return _progress_cache[key]
    _progress_cache[key] = _hf_load(f"layer3/{mission_id}.json") or []
    return _progress_cache[key]


def save_progress(mission_id: str, entries: list[dict]):
    trimmed = entries[-SUB_MAX_PROGRESS_ENTRIES:]
    _progress_cache[_cache_key(mission_id)] = trimmed
    _hf_save(f"layer3/{mission_id}.json", trimmed, f"Update progress: {mission_id}")


def add_progress_note(mission_id: str, subtask_id: str, what_happened: str,
                       what_went_wrong: str = "", how_to_improve: str = "") -> str:
    """
    মূল agent এর 'lesson' ধারণার সমতুল্য — sub-agent এর জন্য। প্রতিটা সাব-টাস্ক
    শেষে (সফল হোক বা ব্যর্থ) এটা কল করে কী ঘটেছিল রেকর্ড করা উচিত, যাতে
    ভবিষ্যতে একই মিশনে (বা পরবর্তী রান-এ) একই ভুল আবার না হয়।
    """
    entries = load_progress(mission_id)
    entries.append({
        "subtask_id": subtask_id,
        "what_happened": what_happened,
        "what_went_wrong": what_went_wrong,
        "how_to_improve": how_to_improve,
        "timestamp": datetime.now().isoformat(),
    })
    save_progress(mission_id, entries)
    return f"Progress note recorded for subtask '{subtask_id}'."


# ─────────────────────────────────────────────
#  CONTEXT BUILDER — তিন লেয়ারকে নির্দিষ্ট ক্রমে combine করে
# ─────────────────────────────────────────────

def build_sub_context(mission_id: str, max_progress_shown: int = 8,
                       max_chars_per_note: int = 220) -> str:
    """
    Sub-agent এর backstory এর জন্য চূড়ান্ত context string তৈরি করে।
    ক্রম ইচ্ছাকৃতভাবে fixed: MISSION সবার আগে, তারপর SUBTASKS, তারপর
    PROGRESS — যাতে মূল লক্ষ্য সবসময় সবচেয়ে বেশি গুরুত্ব পায় এবং
    sub-agent মূল উদ্দেশ্য থেকে সরে না যায়।
    """
    blocks = []

    mission = get_mission(mission_id)
    if mission:
        blocks.append(
            "### [LAYER 1] MISSION (এটাই আপনার একমাত্র লক্ষ্য — কখনো এর বাইরে যাবেন না)\n"
            f"- Goal: {mission.get('goal', '')}\n"
            f"- Expected final output: {mission.get('final_output', '')}\n"
            f"- Constraints: {mission.get('constraints', 'None')}"
        )

    subtasks = load_subtasks(mission_id)
    if subtasks:
        lines = ["### [LAYER 2] SUBTASKS"]
        for s in subtasks:
            lines.append(f"- [{s.get('status', 'pending')}] {s.get('id')}: {s.get('description')}"
                         + (f" — note: {s.get('note')}" if s.get("note") else ""))
        blocks.append("\n".join(lines))

    progress = load_progress(mission_id)
    if progress:
        lines = ["### [LAYER 3] PROGRESS / LESSONS (আগের রান থেকে)"]
        for p in progress[-max_progress_shown:]:
            entry = f"- [{p.get('subtask_id')}] {p.get('what_happened', '')[:max_chars_per_note]}"
            if p.get("what_went_wrong"):
                entry += f" | ভুল হয়েছিল: {p['what_went_wrong'][:max_chars_per_note]}"
            if p.get("how_to_improve"):
                entry += f" | উন্নতির উপায়: {p['how_to_improve'][:max_chars_per_note]}"
            lines.append(entry)
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def clear_mission(mission_id: str):
    """কোনো মিশন সম্পূর্ণ রিসেট করতে চাইলে (নতুন প্রজেক্টের জন্য পুনর্ব্যবহার)।"""
    key = _cache_key(mission_id)
    _mission_cache.pop(key, None)
    _subtasks_cache.pop(key, None)
    _progress_cache.pop(key, None)
    _hf_save(f"layer1/{mission_id}.json", {}, f"Clear mission: {mission_id}")
    _hf_save(f"layer2/{mission_id}.json", [], f"Clear subtasks: {mission_id}")
    _hf_save(f"layer3/{mission_id}.json", [], f"Clear progress: {mission_id}")


# ─────────────────────────────────────────────
#  CrewAI TOOLS — sub-agent নিজে এগুলো কল করতে পারবে রানটাইমে।
#  (set_mission ইচ্ছাকৃতভাবে tool হিসেবে এক্সপোজ করা হয়নি — মিশনের মূল
#  লক্ষ্য কেবল কলিং কোড/মূল agent থেকেই সেট হওয়া উচিত, sub-agent নিজে
#  নিজের লক্ষ্য বদলাতে পারবে না। এটাই "লক্ষ্য থেকে সরে না যাওয়া"
#  নিয়মের কোড-লেভেল গ্যারান্টি।)
#
#  ⚠️ গুরুত্বপূর্ণ: এই @tool wrapped function গুলো module import এর
#  সময় তৈরি হয় — কিন্তু ভেতরের update_subtask()/add_progress_note()
#  ফাংশনগুলো রানটাইমে AGENT_NAMESPACE পড়ে, তাই sub-agent ফাইলে
#  namespace override করার পরেই agent তৈরি করলে এই tool গুলো সঠিক
#  namespace এ read/write করবে।
# ─────────────────────────────────────────────

@tool("update_subtask")
def update_subtask(mission_id: str, subtask_id: str, description: str,
                    status: str = "pending", note: str = "") -> str:
    """
    Layer 2: একটা সাব-টাস্ক যোগ/আপডেট করুন। মিশনকে ছোট সাব-টাস্কে ভাগ করার
    পর প্রতিটার স্ট্যাটাস ট্র্যাক করতে এটা ব্যবহার করুন।

    Args:
        mission_id: যে মিশনের অধীনে এই সাব-টাস্ক
        subtask_id: ছোট, ইউনিক আইডি (যেমন 'fix-requirements', 'rerun-ci')
        description: এই সাব-টাস্কটি কী করে তার সংক্ষিপ্ত বিবরণ
        status: 'pending' | 'in_progress' | 'done' | 'blocked'
        note: অতিরিক্ত প্রসঙ্গ (optional)
    """
    upsert_subtask(mission_id, subtask_id, description, status, note)
    return f"Subtask '{subtask_id}' এর status এখন '{status}'।"


@tool("record_progress")
def record_progress(mission_id: str, subtask_id: str, what_happened: str,
                     what_went_wrong: str = "", how_to_improve: str = "") -> str:
    """
    Layer 3: একটা সাব-টাস্কে কী ঘটেছিল তা রেকর্ড করুন — সফল হোক বা ব্যর্থ।
    এটা পরবর্তী রান-এর জন্য "lesson" হিসেবে কাজ করে, যাতে একই ভুল আবার
    না হয়।

    Args:
        mission_id: যে মিশনের অধীনে
        subtask_id: কোন সাব-টাস্ক সম্পর্কে
        what_happened: কী করা হয়েছিল / কী ফলাফল এসেছিল
        what_went_wrong: কোনো সমস্যা/এরর হলে তার বিবরণ (optional)
        how_to_improve: ভবিষ্যতে আরও ভালো করার উপায় (optional)
    """
    return add_progress_note(mission_id, subtask_id, what_happened, what_went_wrong, how_to_improve)


@tool("get_mission_context")
def get_mission_context(mission_id: str) -> str:
    """
    বর্তমান মিশনের সম্পূর্ণ context (Layer 1+2+3) আবার দেখতে চাইলে এটা
    কল করুন — যদি কোনো কারণে নিজের মূল লক্ষ্য বা অগ্রগতি নিয়ে নিশ্চিত
    না থাকেন।
    """
    ctx = build_sub_context(mission_id)
    return ctx or "এই মিশনের জন্য কোনো context পাওয়া যায়নি।"
