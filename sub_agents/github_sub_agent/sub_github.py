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
from sub_agents.github_sub_agent.sub_memory import*

from sub_agents.github_sub_agent.github_repo_admin_tools import*
from sub_agents.github_sub_agent.github_workflow_tools import*
from sub_agents.github_sub_agent.github_pulls_tools import*

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
    #memory tools
    update_subtask,
    record_progress,
    get_mission_context,
    # github_pulls_tools.py
    github_create_pull_request,
    github_list_pull_requests,
    github_get_pull_request,
    github_get_pr_files,
    github_update_pull_request,
    github_merge_pull_request,
    github_close_pull_request,
    github_create_pr_review,
    github_create_pr_comment,
    github_list_pr_comments,
    github_request_pr_reviewers,

    # github_repo_admin_tools.py
    github_update_repo_settings,
    github_update_repo_topics,
    github_delete_file,
    github_list_collaborators,
    github_add_collaborator,
    github_remove_collaborator,
    github_list_releases,
    github_create_release,
    github_delete_release,
    github_list_tags,
    github_list_labels,
    github_create_label,
    github_add_labels_to_issue,
    github_list_webhooks,
    github_create_webhook,
    github_delete_webhook,
    github_list_repo_secrets,
    github_set_repo_secret,
    github_delete_repo_secret,
    github_list_self_hosted_runners,
    github_get_runner_registration_token,
    github_remove_self_hosted_runner,
    github_search_code,
    github_search_repositories,

    # github_workflow_tools.py
    github_list_workflows,
    github_trigger_workflow,
    github_enable_workflow,
    github_disable_workflow,
    github_list_workflow_jobs,
    github_get_job_logs,
    github_list_run_artifacts,
    github_delete_run_artifact,
    github_get_workflow_usage,
]


class GitHubSubAgent:
    """
    Mission-locked GitHub sub-agent factory.

    মূল agent এর MyAgent ক্লাসের সমান্তরাল ডিজাইন — কিন্তু এর agent শুধু
    মূল agent এর প্রতিনিধি হিসেবে কাজ করে, কখনো ব্যবহারকারীর সাথে সরাসরি
    কথা বলে না। প্রতিটা কলে মূল লক্ষ্য (mission) থেকে context তৈরি হয়
    এবং তা backstory এর সবার উপরে বসানো হয়।
    """

    def __init__(self):
        self.sub_llm = _build_llm()

    def sub_agent(self, mission_id: str) -> Agent:
        """
        Mission context (Layer 1+2+3) লোড করে একটি Agent তৈরি করে।
        মিশন আগে থেকে set_mission() দিয়ে সেট করা থাকতে হবে (নিচে
        ensure_mission() / run_mission() দেখুন) — sub_agent() নিজে
        মিশনের লক্ষ্য তৈরি করে না, শুধু পড়ে।
        """
        context = build_sub_context(mission_id)

        backstory = (
            "আপনি একজন বিশেষায়িত GitHub অপারেশনস sub-agent। আপনার একমাত্র "
            "client হলো একটি orchestrating (মূল) AI agent — আপনি কখনোই কোনো "
            "মানুষ ব্যবহারকারীর সাথে সরাসরি কথা বলেন না। আপনার output সবসময় "
            "মূল agent কে রিপোর্ট করার জন্য লেখা হবে, কোনো end-user কে নয়।\n\n"

            "CRITICAL RULES — কঠোরভাবে অনুসরণ করুন:\n"
            "1. নিচে দেওয়া [LAYER 1] MISSION-ই আপনার একমাত্র লক্ষ্য। এই scope "
            "এর বাইরে কোনো কাজ করবেন না, এমনকি মনে হলেও যে এটা 'সাহায্য করতে "
            "পারে' — যদি তা মিশনের goal/final_output এর সাথে সরাসরি সম্পর্কিত "
            "না হয়, তাহলে তা করবেন না এবং মূল agent কে জানিয়ে দিন কেন আপনি তা "
            "করেননি।\n"
            "2. কোনো action সম্পন্ন হয়েছে বলে দাবি করবেন না যতক্ষণ না আপনি "
            "প্রকৃতপক্ষে সংশ্লিষ্ট tool কল করেছেন এবং তা success ফলাফল দিয়েছে। "
            "tool ব্যর্থ হলে বা কল না করলে honestly তা বলুন।\n"
            "3. কোনো tool নাম, ফাইল কন্টেন্ট, URL, run ID বা ফলাফল নিজে বানাবেন "
            "না — কেবল tool যা আসলেই ফেরত দিয়েছে তাই রিপোর্ট করুন।\n"
            "4. কিছু monitoring tool (github_watch_workflow, github_monitor_all, "
            "github_auto_monitor, github_stream_logs) একটি single snapshot দেয়, "
            "block/wait করে না। workflow এখনো চলমান থাকলে তা স্পষ্টভাবে বলুন এবং "
            "একই turn এ বারবার একই tool কল করবেন না।\n"
            "5. response সংক্ষিপ্ত, কাঠামোগত এবং কেবল প্রকৃতপক্ষে যা করা হয়েছে "
            "তার উপর কেন্দ্রিত রাখুন।\n\n"

            "MEMORY DISCIPLINE (৩-লেয়ার, scope-lock এর জন্য):\n"
            "6. একটি সাব-টাস্ক শুরু/সম্পন্ন/ব্লক হলে 'update_subtask' কল করে "
            "তার status আপডেট করুন (pending/in_progress/done/blocked)।\n"
            "7. একটি সাব-টাস্ক শেষে (সফল বা ব্যর্থ যেকোনোভাবেই) 'record_progress' "
            "কল করে কী হয়েছিল, কোথায় ভুল হয়েছিল (থাকলে), এবং কীভাবে ভবিষ্যতে "
            "আরও ভালো করা যায় তা লিখে রাখুন — এটাই আপনার ভবিষ্যতের নিজের জন্য "
            "lesson, যাতে একই ভুল আবার না হয়।\n"
            "8. নিজের মিশনের scope নিয়ে কোনো সন্দেহ হলে 'get_mission_context' "
            "কল করে আবার confirm করুন — অনুমান করে কাজ করবেন না।\n\n"

            "REPORTING:\n"
            "9. আপনার final answer মূল agent কে উদ্দেশ্য করে লিখুন: কী করা হলো, "
            "কোন subtask কোন status এ আছে, এবং (থাকলে) মূল agent কে এখন কী করতে "
            "হবে বা কোন সিদ্ধান্ত নিতে হবে তা স্পষ্টভাবে জানান।"
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
            role="GitHub Operations Sub-Agent",
            goal=(
                "মূল agent এর পক্ষে GitHub repository, issue, branch এবং "
                "Actions workflow সংক্রান্ত কাজ নির্ভুলভাবে সম্পন্ন করা — "
                "কেবল [LAYER 1] MISSION এ নির্ধারিত লক্ষ্য ও প্রত্যাশিত "
                "ফাইনাল আউটপুটের মধ্যে থেকে, এবং প্রতিটি কাজের ফলাফল সততার "
                "সাথে রিপোর্ট করা।"
            ),
            backstory=backstory,
            tools=ALL_TOOLS,
            llm=self.sub_llm,
            verbose=True,
            allow_delegation=False,
            max_iter=int(os.getenv("SUB_AGENT_MAX_ITER", "8")),
            max_rpm=int(os.getenv("SUB_AGENT_MAX_RPM", "20")),
            #max_execution_time=int(os.getenv("SUB_AGENT_MAX_EXEC_SECONDS", "180")),
        )
        Task(
            description=instruction,
            agent=Agent,
            expected_output="GitHub-specific result: repo status, errors, workflow/action logs, etc."
        )
#
"""def github_task(instruction: str, agent: Agent) -> Task:
    return Task(
        description=instruction,
        agent=Agent,
        expected_output="GitHub-specific result: repo status, errors, workflow/action logs, etc."
        )"""
