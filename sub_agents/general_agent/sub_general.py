"""
sub_github.py — GitHub Sub-Agent (Mission-Locked, 3-Layer Memory)
=====================================================================
এই sub-agent টি মূল agent (agents.py এর MyAgent) এর সাথে গঠনগতভাবে
একদম একই — একই ধরনের LLM resolution, একই ধরনের Agent-তৈরি প্যাটার্ন —
কিন্তু দুটো গুরুত্বপূর্ণ পার্থক্য নিয়ে:

  ১. এটি শুধুমাত্র GitHub-সম্পর্কিত tools পাবে (repo, issues, Actions,
     auto-fixer) — internet search, file system, password manager,
     vault ইত্যাদি কিছুই নেই। Scope ইচ্ছাকৃতভাবে সংকুচিত।

  ২. এটি কখনো সরাসরি ব্যবহারকারীর (Telegram ইত্যাদি) সাথে কথা বলে না।
     এর একমাত্র "ক্লায়েন্ট" মূল agent — তাই backstory এবং expected_output
     ভাষা সবসময় "report back to the orchestrating agent" এর প্রতি
     নির্দেশিত, ব্যবহারকারীর প্রতি নয়।

মেমোরি/কনটেক্সট ডিজাইন (sub_memory.py থেকে):
  [LAYER 1] MISSION   — প্রজেক্টের মূল লক্ষ্য + প্রত্যাশিত ফাইনাল আউটপুট।
                        প্রতি কলে backstory এর সবচেয়ে উপরে বসে, যাতে
                        sub-agent কখনো scope থেকে সরে না যায়।
  [LAYER 2] SUBTASKS   — মিশন ভাঙা ছোট কাজ ও তাদের স্ট্যাটাস।
  [LAYER 3] PROGRESS   — কী হয়েছিল, কোথায় ভুল হয়েছিল, কেন হয়েছিল, এবং
                        কীভাবে ভবিষ্যতে আরও ভালো করা যায় — পরবর্তী রান-এ
                        একই ভুল এড়াতে।

এই তিন লেয়ার build_sub_context() দিয়ে একত্র হয়ে backstory তে inject
হয় — ঠিক মূল agent এর build_smart_context() যেভাবে Layer 2+3 inject
করে তার মতোই প্যাটার্নে, কিন্তু sub-agent এর নিজের namespace এ।

মূল agent এর সাথে সংযোগ (future — এখনই করা হচ্ছে না):
  মূল agent (agents.py) ভবিষ্যতে CrewAI delegation বা একটি `tool` এর
  মাধ্যমে এই SubAgent কে কল করবে। এই ফাইলটি সেই ইন্টিগ্রেশনকে সহজ
  করার জন্যই `run_mission()` নামে একটি single, সরল entry-point
  function এক্সপোজ করে — মূল agent শুধু mission_id, goal, final_output
  ও instruction পাঠালেই sub-agent চলতে পারবে। agents.py এখনো এই ফাইল
  import করে না — ইচ্ছাকৃতভাবে, যেমন অনুরোধ করা হয়েছিল।
"""

import os
import logging

from crewai import Agent, Task, Crew, Process, LLM
from .sub_memory import*

from tools import (
    internet_search,
    news_search,
    image_search,
    browse_webpage,
    get_page_links,
    http_get,
    read_file,
    write_file,
    append_file,
    delete_file,
    list_files,
    calculator,
    get_current_datetime,
    summarize_text,
    save_lesson,
)
from memory import (
    vault_get,
    
)

from password_manager import (
    password_save,
    password_get,
    password_list,
    password_delete,
    password_update,
    password_search,
    password_stats,
    password_autofill,
    password_sync_now,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
#  LLM — মূল agent এর _build_llm() এর সাথে একই প্যাটার্ন, কিন্তু
#  SUB_ প্রিফিক্স যুক্ত env var ব্যবহার করে, যাতে চাইলে sub-agent কে
#  ভিন্ন (সস্তা/দ্রুত) মডেলে চালানো যায় মূল agent থেকে আলাদা করে।
#  কোনো SUB_ var সেট না থাকলে মূল agent এর সাধারণ var-এ fallback করে,
#  যাতে আলাদা কনফিগ ছাড়াই কাজ চলে।
# ─────────────────────────────────────────────

def _build_llm() -> LLM:
    api_key = (
        os.getenv("SUB_LLM_API_KEY")
        or os.getenv("SUB_OPENAI_API_KEY")
        or os.getenv("SUB_ANTHROPIC_API_KEY")
        or os.getenv("SUB_GOOGLE_API_KEY")
        or os.getenv("SUB_OPENROUTER_API_KEY")
        or os.getenv("SUB_DEEPSEEK_API_KEY")
        or os.getenv("SUB_HF_TOKEN")
        or os.getenv("LLM_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or os.getenv("OPENROUTER_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
        or os.getenv("HF_TOKEN")
        or "no-key"
    )

    base_url = os.getenv("SUB_LLM_BASE_URL", "").strip() or os.getenv("LLM_BASE_URL", "").strip() or None
    model = os.getenv("SUB_LLM_MODEL", "").strip() or os.getenv("SUB_LLM_MODEL", "").strip()

    if not model:
        if api_key.startswith("sk-ant-"):
            model = "anthropic/claude-3-5-haiku-20241022"
        elif api_key.startswith("AIza"):
            model = "gemini/gemini-2.5-flash"
        elif os.getenv("SUB_OPENROUTER_API_KEY") or os.getenv("OPENROUTER_API_KEY"):
            model = "openrouter/openai/gpt-4o-mini"
        elif api_key.startswith("hf_"):
            model = "huggingface/mistralai/Mistral-7B-Instruct-v0.3"
        elif os.getenv("SUB_DEEPSEEK_API_KEY") or os.getenv("DEEPSEEK_API_KEY"):
            model = "deepseek/deepseek-chat"
        elif base_url:
            model = "openai/custom-model"
        else:
            model = "openai/gpt-4o-mini"
        logger.warning("SUB_LLM_MODEL not set — using auto-detected: %s", model)

    if model.startswith("openrouter/") and not base_url:
        base_url = "https://openrouter.ai/api/v1"
    if model.startswith("gemini/"):
        google_key = os.getenv("SUB_GOOGLE_API_KEY") or os.getenv("GOOGLE_API_KEY") or api_key
        if google_key:
            os.environ.setdefault("GOOGLE_API_KEY", google_key)
    if model.startswith("huggingface/") and not base_url:
        base_url = "https://api-inference.huggingface.co/v1"
    if model.startswith("groq/") and not base_url:
        base_url = "https://api.groq.com/openai/v1"
    if model.startswith("mistral/") and not base_url:
        base_url = "https://api.mistral.ai/v1"
    if model.startswith("deepseek/") and not base_url:
        base_url = "https://api.deepseek.com/v1"

    logger.info("Sub-agent LLM configured -> model=%s | base_url=%s", model, base_url or "(provider default)")

    llm_kwargs = {
        "model": model,
        "api_key": api_key,
        "temperature": float(os.getenv("SUB_LLM_TEMPERATURE", "0.4")),
        "max_tokens": int(os.getenv("SUB_LLM_MAX_TOKENS", "4096")),
    }
    if base_url:
        llm_kwargs["base_url"] = base_url

    return LLM(**llm_kwargs)


# ─────────────────────────────────────────────
#  TOOLS — শুধু GitHub + নিজের memory tools। কোনো internet_search,
#  file system, password, vault tool নেই — scope ইচ্ছাকৃতভাবে সংকুচিত।
# ─────────────────────────────────────────────

ALL_TOOLS = [
    #general tools 
    internet_search,
    news_search,
    image_search,
    browse_webpage,
    get_page_links,
    http_get,
    read_file,
    write_file,
    append_file,
    delete_file,
    list_files,
    calculator,
    get_current_datetime,
    summarize_text,
    #Password Manager tools 
    password_save,
    password_get,
    password_list,
    password_delete,
    password_update,
    password_search,
    password_stats,
    password_autofill,
    password_sync_now,
    #present lesson
    vault_get, 
    
    
]


class general_agent:
    """
    Mission-locked GitHub sub-agent factory.

    মূল agent এর MyAgent ক্লাসের সমান্তরাল ডিজাইন — কিন্তু এর agent শুধু
    মূল agent এর প্রতিনিধি হিসেবে কাজ করে, কখনো ব্যবহারকারীর সাথে সরাসরি
    কথা বলে না। প্রতিটা কলে মূল লক্ষ্য (mission) থেকে context তৈরি হয়
    এবং তা backstory এর সবার উপরে বসানো হয়।
    """

    def __init__(self):
        self.llm1 = _build_llm()

    def sub_agent(self, mission_id: str) -> Agent:
        """
        Mission context (Layer 1+2+3) লোড করে একটি Agent তৈরি করে।
        মিশন আগে থেকে set_mission() দিয়ে সেট করা থাকতে হবে (নিচে
        ensure_mission() / run_mission() দেখুন) — sub_agent() নিজে
        মিশনের লক্ষ্য তৈরি করে না, শুধু পড়ে।
        """
        context = build_sub_context(mission_id)

        backstory = (
            "You are an advanced General AI Agent designed to assist users with a wide variety of digital tasks. You have access to web search, news search, webpage browsing, image search, file management, knowledge retrieval, password management, calculation, text summarization, and system utility tools.\n\n"

            "Your responsibility is to understand the users objective, determine the best approach, and use the most appropriate tools to accomplish the task. You should gather relevant information, verify facts when possible, analyze results, and provide clear, accurate, and actionable responses.\n"

            "You operate with a strong focus on efficiency, reliability, security, and privacy. When sensitive data such as passwords or personal information is involved, you must handle it carefully and only perform actions that are necessary for the user's request.\n\n"

            "You are resourceful, methodical, and proactive. Before taking action, you evaluate available information and select the optimal tool or sequence of tools. You avoid unnecessary tool calls, minimize errors, and always aim to deliver the highest quality outcome.\n\n"

            "Your role is not only to answer questions but also to act as a capable digital operator that can search, organize, retrieve, analyze, and manage information on behalf of the user while maintaining accuracy, safety, and operational efficiency."
            )


        if context:
            backstory += f"\n\n{context}"
        else:
            backstory += (
                "\n\n⚠️ কোনো mission context পাওয়া যায়নি — এই মিশনের জন্য "
                "set_mission() এখনো কল করা হয়নি। কাজ শুরুর আগে মূল agent কে "
                "জানান যে মিশনের লক্ষ্য নির্ধারিত নয়।"
            )

        return Agent(
            role="General Agent",
            goal=(
                "Serve as a highly capable general-purpose AI assistant that can research information\n"
                " browse the web, analyze content, manage files, access stored knowledge, handle password management tasks, and complete user requests accurately, efficiently, and securely by intelligently selecting and using available tools.s."
            ),
            backstory=backstory,
            tools=ALL_TOOLS,
            llm=self.llm1,
            verbose=True,
            allow_delegation=False,
            max_iter=int(os.getenv("SUB_AGENT_MAX_ITER", "8")),
            max_rpm=int(os.getenv("SUB_AGENT_MAX_RPM", "20")),
            #max_execution_time=int(os.getenv("SUB_AGENT_MAX_EXEC_SECONDS", "180")),
        )
        

"""def general_task(instruction: str, agent: Agent) -> Task:
    return Task(
        description=instruction,
        agent=Agent,
        expected_output="A complete, accurate, and well-structured response that directly addresses the user's request. The output should include relevant findings, analysis, recommendations, or actions performed using available tools when necessary. Information should be concise, factual, actionable, and easy to understand. Any tool usage, retrieved data, or completed operations should be clearly reflected in the final response."
        )"""
        