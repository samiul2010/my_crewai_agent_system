---
title: My AI Agent CrewAI
emoji: 🤖
colorFrom: pink
colorTo: gray
sdk: docker
pinned: false
---

# 🤖 My AI Agent — CrewAI + Universal LLM

CrewAI-based AI agent যা Hugging Face Spaces এ Docker দিয়ে চলে এবং Telegram Bot হিসেবে ব্যবহার করা যায়।

## ✨ Features

| Feature | Details |
|---|---|
| **Framework** | CrewAI |
| **LLM Support** | OpenAI, Anthropic Claude, Google Gemini, OpenRouter, HuggingFace Inference, Groq, Mistral, Cohere, যেকোনো Custom OpenAI-compatible endpoint |
| **Interface** | Telegram Bot |
| **Search** | DuckDuckGo Web, News, Image |
| **Browsing** | Webpage reader, Link extractor, HTTP GET |
| **Files** | Read, Write, Append, Delete, List |
| **Utilities** | Calculator, Datetime, Text summarizer |
| **Deployment** | Hugging Face Spaces (Docker SDK) |

---

## 🔐 Hugging Face Space Secrets

Space Settings → Variables and Secrets → **New Secret** এ যান।

### ✅ Required Secrets

| Secret Name | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/botfather) থেকে পাওয়া token |
| `TELEGRAM_CHAT_ID` | আপনার Chat ID — [@userinfobot](https://t.me/userinfobot) দিয়ে জানুন। একাধিক হলে কমা দিন: `123,456` |
| `LLM_API_KEY` | আপনার LLM provider এর API key |
| `LLM_MODEL` | মডেল string (নিচে দেখুন) |

### ⚙️ Optional Secrets

| Secret Name | Default | Description |
|---|---|---|
| `LLM_BASE_URL` | (provider default) | Custom OpenAI-compatible endpoint URL |
| `LLM_TEMPERATURE` | `0.7` | মডেলের creativity (0.0 – 1.0) |
| `LLM_MAX_TOKENS` | `4096` | সর্বোচ্চ response length |
| `AGENT_MAX_ITER` | `8` | Agent এর সর্বোচ্চ iteration |
| `AGENT_MAX_EXEC_SECONDS` | `180` | CrewAI Agent এর নিজস্ব execution time limit |
| `AGENT_HARD_TIMEOUT_SECONDS` | `240` | পুরো টাস্কের জন্য সর্বোচ্চ সময় — এর বেশি হলে বট নিজে থামিয়ে দেবে (hang প্রতিরোধ) |
| `TELEGRAM_API_BASE` | `https://api.telegram.org` | Cloudflare Worker proxy URL (নিচে দেখুন) |
| `HF_TOKEN` | — | **Persistent Memory** এর জন্য (নিচে দেখুন) + HF Inference API |
| `MEMORY_REPO_ID` | `<username>/agent-memory` | মেমোরি রাখার dataset repo (auto-created) |
| `MEMORY_MAX_TURNS` | `20` | প্রতি chat এ কতগুলো past exchange মনে রাখবে |
| `MEMORY_MAX_LESSONS` | `100` | সর্বোচ্চ কতগুলো lesson/experience মনে রাখবে |

---

## 🧠 Persistent Memory (Hugging Face Dataset)

`HF_TOKEN` সেট করলে এজেন্ট স্বয়ংক্রিয়ভাবে একটি **প্রাইভেট HF dataset** (`<username>/agent-memory`) তৈরি করে এবং তাতে:

1. **প্রতিটি চ্যাটের কথোপকথন ইতিহাস** — restart হলেও মনে থাকবে (`/clear` দিয়ে রিসেট করা যায়)
2. **Lessons learned** — অতীত error, fix, শর্টকাট, ইউজার preference — এজেন্ট নিজেই `save_lesson` টুল দিয়ে এগুলো লিখে রাখে এবং প্রতিটি নতুন টাস্কের আগে পড়ে নেয়, যাতে একই ভুল আবার না করে

### HF_TOKEN পাওয়ার উপায়
https://huggingface.co/settings/tokens → **New token** → role: **Write** → copy → `HF_TOKEN` secret এ পেস্ট করুন।

> মেমোরি repo `https://huggingface.co/datasets/<username>/agent-memory` এ (private) তৈরি হবে — চাইলে সরাসরি দেখতে/এডিট করতে পারবেন।

---

## 🖥️ Status Web UI

Space এর **App** ট্যাবে গেলে একটি সিম্পল status পেজ দেখা যাবে:
- 🟢 Agent is running / 🟡 Starting / 🔴 Error
- বট username, মডেল, চালু হওয়ার সময়, কতগুলো মেসেজ হ্যান্ডেল হয়েছে

(পোর্ট `7860`, 10 সেকেন্ডে auto-refresh হয়)

---

## 🌐 Telegram Connection Timeout সমস্যা — Cloudflare Worker সমাধান

কিছু Hugging Face Space এর network থেকে `api.telegram.org` এ সরাসরি কানেকশন **টাইমআউট** হয় (firewall block)। সমাধান: **Cloudflare Workers** দিয়ে একটি ফ্রি proxy বসানো।

### ধাপ ১ — Worker তৈরি করুন
1. https://workers.cloudflare.com → ফ্রি অ্যাকাউন্ট খুলুন → **Create Worker**
2. এই repo এর `cloudflare-worker.js` ফাইলের কোড কপি করে Worker editor এ পেস্ট করুন
3. **Save and Deploy** — আপনি একটি URL পাবেন, যেমন:
   ```
   https://tg-proxy.yourname.workers.dev
   ```

### ধাপ ২ — HF Space Secret যুক্ত করুন
| Secret Name | Value |
|---|---|
| `TELEGRAM_API_BASE` | `https://tg-proxy.yourname.workers.dev` |

### ধাপ ৩ — Space Restart করুন
ব্যাস! এখন সব Telegram API call Cloudflare Worker এর মাধ্যমে যাবে, যা HF এর network এ ব্লকড নয়।

> 💡 `TELEGRAM_API_BASE` সেট না করলে স্বাভাবিকভাবে সরাসরি `https://api.telegram.org` ব্যবহার হবে।

---

## 🤖 LLM মডেল পরিবর্তন

`LLM_MODEL` secret পরিবর্তন করলেই মডেল বদলে যাবে।

### Provider → LLM_MODEL উদাহরণ

```
# OpenAI
LLM_MODEL = openai/gpt-4o
LLM_MODEL = openai/gpt-4o-mini

# Anthropic Claude
LLM_MODEL = anthropic/claude-3-5-sonnet-20241022
LLM_MODEL = anthropic/claude-3-5-haiku-20241022

# Google Gemini
LLM_MODEL = gemini/gemini-2.5-pro
LLM_MODEL = gemini/gemini-2.5-flash

# OpenRouter (সব মডেল)
LLM_MODEL = openrouter/google/gemma-2-27b-it
LLM_MODEL = openrouter/meta-llama/llama-3.1-70b-instruct
LLM_MODEL = openrouter/anthropic/claude-3.5-sonnet

# HuggingFace Inference API
LLM_MODEL = huggingface/mistralai/Mistral-7B-Instruct-v0.3
LLM_MODEL = huggingface/Qwen/Qwen2.5-72B-Instruct

# Groq (Free & Fast)
LLM_MODEL = groq/llama-3.1-70b-versatile
LLM_MODEL = groq/mixtral-8x7b-32768

# Mistral AI
LLM_MODEL = mistral/mistral-large-latest

# DeepSeek
LLM_MODEL = deepseek/deepseek-chat
LLM_MODEL = deepseek/deepseek-reasoner

# Cohere
LLM_MODEL = cohere/command-r-plus

# Custom/Local (Ollama, vLLM, LM Studio ইত্যাদি)
LLM_MODEL = openai/my-custom-model
LLM_BASE_URL = http://your-server:8080/v1
```

---

## 🛠️ Telegram Commands

| Command | Description |
|---|---|
| `/start` | Welcome message |
| `/help` | সব tools এর তালিকা |
| `/model` | বর্তমান LLM দেখুন |
| `/clear` | Context রিসেট করুন |

---

## 📁 File Structure

```
.
├── telegram_bot.py    # Main entry point, Telegram handler
├── agents.py          # CrewAI Agent + Universal LLM config
├── tools.py           # সব tools (search, browse, file, utility)
├── requirements.txt   # Python dependencies
├── Dockerfile         # HF Spaces deployment config
└── README.md
```

---

## 🚀 Quick Deploy Steps

1. এই repo fork করুন বা নিজের HF Space এ upload করুন
2. Space Settings → Secrets এ Required Secrets যোগ করুন
3. Space restart করুন — bot চালু হয়ে যাবে!
4. Telegram এ গিয়ে আপনার bot কে message করুন ✅

---

## 🛡️ Hang / Hallucination ফিক্স (2026-06-13)

বট বারবার hang/freeze হয়ে যাওয়া এবং কাজ না করেও "করে দিয়েছি" বলে দেওয়ার সমস্যা সমাধান করা হয়েছে:

### Hang ফিক্স
- `github_watch_workflow`, `github_monitor_all`, `github_auto_monitor`, `github_stream_logs` —
  এগুলো আগে `while True` / `time.sleep()` লুপে আটকে যেত (কোনো কোনো ক্ষেত্রে অসীম সময়)।
  এখন সবগুলো **single snapshot** রিটার্ন করে — একবার চেক করে সাথে সাথে রিপোর্ট দেয়, পুনরায়
  জিজ্ঞেস করলে আবার চেক করবে।
- সব GitHub API কলে `timeout` যুক্ত করা হয়েছে (network hang প্রতিরোধ)।
- নতুন **hard timeout** (`AGENT_HARD_TIMEOUT_SECONDS`, ডিফল্ট 240s) — কোনো টাস্ক এর বেশি সময়
  নিলে বট নিজেই থামিয়ে ইউজারকে জানিয়ে দেবে, পুরো বট আটকে থাকবে না।

### Hallucination ফিক্স
- এজেন্টের backstory তে কঠোর নিয়ম যুক্ত করা হয়েছে: টুল কল করে সফল রেজাল্ট না পেলে
  "করে দিয়েছি" বলা নিষেধ — ব্যর্থ হলে honestly বলতে হবে।
- মেমোরি context ছোট করা হয়েছে (২০ → ৬ turns, প্রতি লেসন/মেসেজ truncate করা) — অনেক বড়
  prompt ছোট মডেলকে (Gemini Flash, Gemma) confuse করে JSON malformed action দিচ্ছিল।

> দীর্ঘ সময়ের কাজ (যেমন একটি GitHub Action শেষ হওয়ার জন্য অপেক্ষা) এখন একবারে শেষ হবে না —
> এজেন্ট স্ট্যাটাস জানাবে এবং পরে আবার চেক করতে বলবে। এটাই স্বাভাবিক ও bot-safe behavior।
