import os
os.environ.setdefault("HF_HOME", "/tmp/hf_cache")
os.makedirs(os.environ["HF_HOME"], exist_ok=True)

# এরপর বাকি সব import (crewai, memory, sub_github, ইত্যাদি)

"""
agents.py — Universal LLM Agent
================================
সমস্ত LLM প্রোভাইডার সাপোর্ট করে।
Hugging Face Secrets দিয়ে কনফিগার করুন:

  LLM_API_KEY   — আপনার API key
  LLM_BASE_URL  — Custom base URL (optional, OpenAI-compatible endpoints এর জন্য)
  LLM_MODEL     — Full model string (নিচে উদাহরণ দেখুন)

────────────────────────────────────────────
Provider → LLM_MODEL এর উদাহরণ
────────────────────────────────────────────
OpenAI          → openai/gpt-4o  |  openai/gpt-4o-mini
Anthropic       → anthropic/claude-3-5-sonnet-20241022
Google Gemini   → gemini/gemini-2.5-pro  |  gemini/gemini-2.5-flash
OpenRouter      → openrouter/google/gemma-3-27b-it (যেকোনো OR মডেল)
HF Inference    → huggingface/mistralai/Mistral-7B-Instruct-v0.3
Mistral AI      → mistral/mistral-large-latest
Groq            → groq/llama-3.1-70b-versatile
Cohere          → cohere/command-r-plus
DeepSeek        → deepseek/deepseek-chat  |  deepseek/deepseek-reasoner
Custom/Local    → openai/custom  (LLM_BASE_URL সহ)
────────────────────────────────────────────
"""

import os
import logging
from crewai import Agent, LLM,Crew,Task,Process
from crewai.tools import tool
from sub_agents.github_sub_agent.sub_github import GitHubSubAgent
from sub_agents.general_agent.sub_general import general_agent
from sub_agents.media_download.sub_download import downloader_agent
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
    vault_save, vault_get,
    save_lesson_v2,
    update_project_state,
    update_active_task,
    build_smart_context
)




logger = logging.getLogger(__name__)



def _build_llm() -> LLM:
    """
    HF Secrets থেকে LLM তৈরি করে।
    যেকোনো OpenAI-compatible, Anthropic, Gemini, HF Inference API গ্রহণ করে।
    """
    api_key = (
        os.getenv("LLM_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or os.getenv("OPENROUTER_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
        or os.getenv("HF_TOKEN")
        or "no-key"
    )

    base_url = os.getenv("LLM_BASE_URL", "").strip() or None

    # ── Model string resolution ──────────────────────────────────────────
    model = os.getenv("LLM_MODEL", "").strip()

    if not model:
        # API key দেখে স্বয়ংক্রিয়ভাবে প্রোভাইডার বের করো
        if api_key.startswith("sk-ant-"):
            model = "anthropic/claude-3-5-haiku-20241022"
        elif api_key.startswith("AIza"):
            model = "gemini/gemini-2.5-flash"
        elif os.getenv("OPENROUTER_API_KEY"):
            model = "openrouter/openai/gpt-4o-mini"
        elif api_key.startswith("hf_"):
            model = "huggingface/mistralai/Mistral-7B-Instruct-v0.3"
        elif os.getenv("DEEPSEEK_API_KEY"):
            model = "deepseek/deepseek-chat"
        elif base_url:
            model = "openai/custom-model"
        else:
            model = "openai/gpt-4o-mini"  # Default fallback
        logger.warning("LLM_MODEL not set — using auto-detected: %s", model)

    # ── Special provider routing ─────────────────────────────────────────
    # OpenRouter: base_url ঠিক করো
    if model.startswith("openrouter/") and not base_url:
        base_url = "https://openrouter.ai/api/v1"
        if not os.getenv("OPENROUTER_API_KEY"):
            logger.warning("LLM_MODEL is openrouter/* but OPENROUTER_API_KEY not set.")

    # Gemini: google API key সেট করো
    if model.startswith("gemini/"):
        google_key = os.getenv("GOOGLE_API_KEY") or os.getenv("LLM_API_KEY")
        if google_key:
            os.environ.setdefault("GOOGLE_API_KEY", google_key)

    # HuggingFace Inference API
    if model.startswith("huggingface/") and not base_url:
        base_url = "https://api-inference.huggingface.co/v1"

    # Groq
    if model.startswith("groq/") and not base_url:
        base_url = "https://api.groq.com/openai/v1"

    # Mistral
    if model.startswith("mistral/") and not base_url:
        base_url = "https://api.mistral.ai/v1"

    # DeepSeek
    if model.startswith("deepseek/") and not base_url:
        base_url = "https://api.deepseek.com/v1"

    # Cohere (LiteLLM handles natively — no base_url needed)

    logger.info("LLM configured → model=%s | base_url=%s", model, base_url or "(provider default)")

    llm_kwargs = {
        "model": model,
        "api_key": api_key,
        "temperature": float(os.getenv("LLM_TEMPERATURE", "0.7")),
        "max_tokens": int(os.getenv("LLM_MAX_TOKENS", "4096")),
    }
    if base_url:
        llm_kwargs["base_url"] = base_url

    return LLM(**llm_kwargs)


# All available tools grouped for easy editing



class MyAgent:
    """
    Universal CrewAI Agent Factory.
    একবার তৈরি হলে main_agent() দিয়ে Agent নিন।
    """

    def __init__(self):
        self.llm = _build_llm()

    def main_agent(self, memory_context: str = "", chat_id: str = "", project_id: str = "default", query: str = "") -> Agent:
        smart_ctx = build_smart_context(chat_id, project_id)  
        combined_context = "\n\n".join(b for b in [memory_context, smart_ctx] if b)
        backstory = (
            "You are the central Manager Agent of a multi-agent AI ecosystem created and owned by Samiul. "
            "Your purpose is to coordinate specialized agents, manage complex tasks, maintain project awareness, "
            "and ensure high-quality results for every request.\n\n"

            "ABOUT YOUR CREATOR:\n"
            "Your creator is Samiul, an independent AI builder and automation enthusiast. "
            "He is building a personal AI ecosystem using CrewAI, Hugging Face Spaces, Gemma models, "
            "custom memory systems, RAG, LoRA adaptation, GitHub automation, Telegram integration, "
            "and multiple specialized AI agents. He values automation, efficiency, accuracy, continuous "
            "learning, and systems that improve over time. He prefers solutions that minimize manual work "
            "and maximize autonomous task completion.\n\n"
            
            "YOUR ROLE:\n"
            "You are not a specialist worker. You are the manager, planner, coordinator, and decision-maker "
            "of the entire agent ecosystem. Your responsibility is to understand user objectives, select the "
            "most suitable agents, delegate work, review outputs, combine results, and deliver the final answer.\n\n"
            
            "AGENT MANAGEMENT RULES:\n"
            "1. Analyze every request carefully before taking action.\n"
            "2. Delegate specialized work to the most appropriate agent.\n"
            "3. Coordinate multiple agents when necessary.\n"
            "4. Review and validate all agent outputs before presenting them.\n"
            "5. Focus on completing tasks efficiently and accurately.\n"
            "6. Avoid unnecessary user intervention whenever possible.\n\n"
            
            "TRUTHFULNESS RULES:\n"
            "1. Never claim a task was completed unless an agent actually completed it.\n"
            "2. Never invent results, files, URLs, workflow runs, repository states, or tool outputs.\n"
            "3. If information is unavailable or uncertain, state that clearly.\n"
            "4. Accuracy is more important than sounding confident.\n\n"
            
            "MEMORY AND LEARNING:\n"
            "You operate within an advanced memory ecosystem. Preserve important project concepts, "
            "architecture decisions, lessons learned, recurring user preferences, and operational knowledge. "
            "Help the overall system improve continuously and avoid repeating past mistakes.\n\n"
            
            "WORKING PHILOSOPHY:\n"
            "Think strategically, act efficiently, coordinate intelligently, and prioritize successful task completion. "
            "Your mission is to help Samiul build a capable, reliable, continuously improving AI ecosystem.\n\n"
            
            "COMMUNICATION STYLE:\n"
            "Be professional, concise, practical, and action-oriented. Provide clear decisions, clear reasoning, "
            "and clear final results."
        )
        if combined_context:
            backstory += f"\n\n### YOUR CURRENT CONTEXT (FROM MEMORY):\n{combined_context}"

        return Agent(
            role="Personal AI Assistant",
            goal=(
            "Serve as the primary AI assistant and manager for the user. "
            "Understand user requests, coordinate specialized agents, assign tasks, "
            "review their outputs, maintain project awareness, and deliver accurate final results. "
            "Focus on completing tasks efficiently without requiring unnecessary user intervention."
            "তুমি কখনোই নিজের নিজেকে দিয়ে কাজ করাবে না বা নিজেই নিজেকে কল করবে না তুমি অন্য সকল এজেন্টদের ডাকবে এবং কল করবে কাজ করাবে"
            ),
            backstory=backstory,
            llm=self.llm,
            verbose=True,
            allow_delegation=True,
            max_iter=int(os.getenv("AGENT_MAX_ITER", "8")),
            max_rpm=int(os.getenv("AGENT_MAX_RPM", "20")),
            #max_execution_time=int(os.getenv("AGENT_MAX_EXEC_SECONDS", "180")),
        )
    def run(self, instruction: str, memory_context: str = "", chat_id: str = "", project_id: str = "default") -> str:        
    
        main = self.main_agent(memory_context=memory_context, chat_id=chat_id, project_id=project_id)
        task = Task(
            description=instruction,
            expected_output=
                "উপরের নির্দেশনা (description) সম্পূর্ণরূপে অনুসরণ করুন। "
                "প্রতিটি ধাপ সঠিকভাবে সম্পন্ন করুন এবং কোনো কিছু বাদ দেবেন না। "
                "আউটপুট এমনভাবে দিন যেন ব্যবহারকারী তা সরাসরি ব্যবহার করতে পারে। "
                "যদি ফাইল তৈরি/এডিট/ডিলিট করতে বলা হয়ে থাকে, তাহলে ফাইলের সম্পূর্ণ পাথ ও পরিবর্তনের বিবরণ দিন। "
                "যদি ডেটা বা তথ্য চাওয়া হয়ে থাকে, তাহলে তা সঠিক ও সংগঠিত আকারে উপস্থাপন করুন। "
                "যদি কোনো নির্দিষ্ট ফরম্যাট (JSON, Markdown, Table, ইত্যাদি) চাওয়া হয়ে থাকে, তাহলে ঠিক সেই ফরম্যাটে উত্তর দিন। "
                "কাজ শেষে একটি সংক্ষিপ্ত সারাংশ দিন যা থেকে বোঝা যায় কী করা হয়েছে। "
                "কোনো ত্রুটি বা সমস্যা থাকলে তার স্পষ্ট কারণ ও সম্ভাব্য সমাধান উল্লেখ করুন।"
    )
        #object agent 
        github_sub_agent = GitHubSubAgent().sub_agent(mission_id=chat_id)
        general_sub_agent=general_agent().sub_agent(mission_id=chat_id)
        downloade_sub_agent=downloader_agent().sub_agent(mission_id=chat_id)
        
        #all agents 
        ALL_AGENTS=[
            github_sub_agent,
            general_sub_agent,
            downloade_sub_agent,
         
        ]

        crew = Crew(
            agents=ALL_AGENTS,
            tasks=[task],
            process=Process.hierarchical,
            manager_agent=main,
            verbose=True
        )
    
        return str(crew.kickoff())        
