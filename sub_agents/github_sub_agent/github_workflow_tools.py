"""
github_workflow_tools.py — GitHub Actions Workflow & Build Control Tools
=========================================================================
github_actions_tools.py শুধু *existing* run monitor/rerun/cancel করে।
এই ফাইলটি তার পরিপূরক — যা এখনো coverage এ ছিল না:

  • workflow_dispatch — কোনো ব্রাউজারে গিয়ে "Run workflow" বাটনে ক্লিক
    করার সমতুল্য, agent কে নতুন build/deploy/任何 manual-trigger
    workflow চালানোর ক্ষমতা দেয়।
  • workflow list/enable/disable — কোন workflow files আছে, কোনটা চালু/
    বন্ধ আছে তা দেখা ও নিয়ন্ত্রণ করা।
  • job-level logs — পুরো run এর zip log না নিয়ে নির্দিষ্ট job এর
    ধাপ-ভিত্তিক (step-by-step) তথ্য দেখা, কোন step এ ঠিক ব্যর্থ হয়েছে
    বোঝার জন্য আরও নির্দিষ্ট।
  • artifacts — workflow run এর output (build artifact) লিস্ট ও
    ডাউনলোড লিংক বের করা।
  • billing/usage — workflow কতটা compute সময় খরচ করছে তা দেখা।

GitHub Personal Access Token প্রয়োজন (repo + workflow scope)।
"""

import os
import json
import requests
from crewai.tools import tool

GITHUB_TOKEN = os.getenv("GITHUB_ACCESS_TOKEN", "")
GITHUB_API_BASE = "https://api.github.com"


def _get_headers():
    if not GITHUB_TOKEN:
        return {"Accept": "application/vnd.github.v3+json"}
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }


def _handle_response(response):
    if response.status_code in (200, 201):
        return response.json()
    elif response.status_code == 204:
        return {"success": True, "message": "Operation successful"}
    elif response.status_code == 401:
        return {"error": "Unauthorized! Check your GITHUB_ACCESS_TOKEN"}
    elif response.status_code == 404:
        return {"error": "Workflow or resource not found"}
    elif response.status_code == 422:
        return {"error": f"Validation failed (check ref/inputs): {response.text[:300]}"}
    else:
        return {"error": f"GitHub API error: {response.status_code}", "details": response.text[:300]}


# ─────────────────────────────────────────────
#  WORKFLOW LISTING & FILE INFO
# ─────────────────────────────────────────────

@tool("github_list_workflows")
def github_list_workflows(repo_full_name: str) -> str:
    """
    Repository এর সব workflow file (.github/workflows/*.yml) লিস্ট
    করে, প্রতিটার ID, নাম, ও state (active/disabled) সহ।

    Args:
        repo_full_name: Full name "username/repo-name"

    Returns:
        Workflow তালিকা — workflow_dispatch এর জন্য workflow_id জানতে
        এটা দরকার হয়
    """
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/actions/workflows"
        response = requests.get(url, headers=_get_headers(), params={"per_page": 50})
        data = _handle_response(response)
        if "error" in data:
            return f"❌ {data['error']}"
        workflows = data.get("workflows", [])
        if not workflows:
            return "📭 No workflows found in this repository."
        result = f"⚙️ *Workflows* for {repo_full_name}\n\n"
        for wf in workflows:
            state_emoji = "🟢" if wf["state"] == "active" else "🔴"
            result += f"{state_emoji} **{wf['name']}** (id: {wf['id']})\n"
            result += f"   📄 {wf['path']} | State: {wf['state']}\n\n"
        return result
    except Exception as e:
        return f"❌ Error: {str(e)}"


@tool("github_trigger_workflow")
def github_trigger_workflow(repo_full_name: str, workflow_id: str, ref: str = "main",
                             inputs_json: str = "") -> str:
    """
    একটি workflow ম্যানুয়ালি ট্রিগার করে — যেমন ওয়েবসাইটে গিয়ে "Run
    workflow" বাটনে ক্লিক করার সমতুল্য। এটা দিয়ে build, deploy, test —
    যেকোনো workflow_dispatch-সক্ষম workflow চালানো যায়।

    ⚠️ এই workflow ফাইলে অবশ্যই `on: workflow_dispatch:` ট্রিগার
    সংজ্ঞায়িত থাকতে হবে, নাহলে এই কল ব্যর্থ হবে।

    Args:
        repo_full_name: Full name "username/repo-name"
        workflow_id: workflow file এর নাম (যেমন "build.yml") অথবা
                     numeric ID (github_list_workflows থেকে পাওয়া)
        ref: কোন branch/tag থেকে চালাতে হবে (default: "main")
        inputs_json: workflow এ ডিফাইন করা input গুলোর JSON string
                     (যেমন '{"environment": "production"}')। কোনো
                     input না থাকলে ফাঁকা রাখুন।

    Returns:
        ট্রিগার সফল হলো কিনা তার নিশ্চিতকরণ
    """
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        inputs = json.loads(inputs_json) if inputs_json.strip() else {}
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/actions/workflows/{workflow_id}/dispatches"
        payload = {"ref": ref, "inputs": inputs}
        response = requests.post(url, headers=_get_headers(), json=payload)
        if response.status_code == 204:
            return (f"✅ Workflow '{workflow_id}' triggered on branch '{ref}'!\n"
                    f"ℹ️ Use github_list_workflow_runs to find the new run_id and monitor it "
                    f"(it can take a few seconds to appear).")
        result = _handle_response(response)
        return f"❌ {result.get('error', 'Failed to trigger workflow')}"
    except json.JSONDecodeError:
        return "❌ Error: inputs_json is not valid JSON"
    except Exception as e:
        return f"❌ Error: {str(e)}"


@tool("github_enable_workflow")
def github_enable_workflow(repo_full_name: str, workflow_id: str) -> str:
    """
    একটি disabled workflow পুনরায় সক্রিয় করে।

    Args:
        repo_full_name: Full name "username/repo-name"
        workflow_id: workflow file নাম বা numeric ID
    """
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/actions/workflows/{workflow_id}/enable"
        response = requests.put(url, headers=_get_headers())
        if response.status_code == 204:
            return f"✅ Workflow '{workflow_id}' enabled."
        return f"❌ Failed to enable: {response.status_code}"
    except Exception as e:
        return f"❌ Error: {str(e)}"


@tool("github_disable_workflow")
def github_disable_workflow(repo_full_name: str, workflow_id: str) -> str:
    """
    একটি workflow নিষ্ক্রিয় করে — নতুন কোনো trigger event এ এটা আর
    চলবে না, যতক্ষণ না আবার enable করা হয়।

    Args:
        repo_full_name: Full name "username/repo-name"
        workflow_id: workflow file নাম বা numeric ID
    """
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/actions/workflows/{workflow_id}/disable"
        response = requests.put(url, headers=_get_headers())
        if response.status_code == 204:
            return f"✅ Workflow '{workflow_id}' disabled."
        return f"❌ Failed to disable: {response.status_code}"
    except Exception as e:
        return f"❌ Error: {str(e)}"


# ─────────────────────────────────────────────
#  JOB-LEVEL DETAILS (step-by-step, run-level log এর চেয়ে বেশি নির্দিষ্ট)
# ─────────────────────────────────────────────

@tool("github_list_workflow_jobs")
def github_list_workflow_jobs(repo_full_name: str, run_id: int) -> str:
    """
    একটি workflow run এর সব job ও তাদের প্রতিটা step এর status দেখায় —
    ঠিক কোন step এ ব্যর্থ হয়েছে বোঝার জন্য এটা পুরো run-level log এর
    চেয়ে অনেক নির্দিষ্ট ও পড়া সহজ।

    Args:
        repo_full_name: Full name "username/repo-name"
        run_id: workflow run ID

    Returns:
        প্রতিটা job ও তার ভেতরের step-ভিত্তিক status
    """
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/actions/runs/{run_id}/jobs"
        response = requests.get(url, headers=_get_headers())
        data = _handle_response(response)
        if "error" in data:
            return f"❌ {data['error']}"
        jobs = data.get("jobs", [])
        if not jobs:
            return f"📭 No jobs found for run #{run_id}."

        result = f"⚙️ *Jobs for Run #{run_id}*\n\n"
        for job in jobs:
            job_emoji = "✅" if job["conclusion"] == "success" else ("❌" if job["conclusion"] == "failure" else "🔄")
            result += f"{job_emoji} **{job['name']}** (job_id: {job['id']}) — {job['status']}/{job.get('conclusion', '...')}\n"
            for step in job.get("steps", []):
                step_emoji = "✅" if step["conclusion"] == "success" else ("❌" if step["conclusion"] == "failure" else ("⏭️" if step["conclusion"] == "skipped" else "🔄"))
                result += f"   {step_emoji} [{step['number']}] {step['name']}\n"
            result += "\n"
        return result
    except Exception as e:
        return f"❌ Error: {str(e)}"


@tool("github_get_job_logs")
def github_get_job_logs(repo_full_name: str, job_id: int) -> str:
    """
    একটি নির্দিষ্ট job এর (পুরো run নয়) plaintext log আনে — যখন ঠিক
    কোন job এ সমস্যা সেটা জানা থাকে (github_list_workflow_jobs থেকে
    job_id নিয়ে), পুরো run এর zip না খুলে সরাসরি সেই job এর log
    দেখার জন্য এটা বেশি দ্রুত ও focused।

    Args:
        repo_full_name: Full name "username/repo-name"
        job_id: job ID (github_list_workflow_jobs থেকে পাওয়া)

    Returns:
        Job এর plaintext log (truncated)
    """
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/actions/jobs/{job_id}/logs"
        response = requests.get(url, headers=_get_headers())
        if response.status_code == 200:
            logs_text = response.text
            error_lines = [l.strip()[:300] for l in logs_text.split("\n")
                            if any(kw in l.lower() for kw in ["error", "fail", "exception", "traceback"])]
            result = f"📋 *Logs for Job #{job_id}*\n\n"
            if error_lines:
                result += "### ❌ Errors Found:\n```\n" + "\n".join(error_lines[:15]) + "\n```\n\n"
            result += f"### 📄 Full Logs (first 3000 chars):\n```\n{logs_text[:3000]}\n```"
            if len(logs_text) > 3000:
                result += f"\n... (truncated, total {len(logs_text)} chars)"
            return result
        elif response.status_code == 404:
            return f"❌ Job #{job_id} not found or logs expired"
        return f"❌ Could not fetch logs: {response.status_code}"
    except Exception as e:
        return f"❌ Error: {str(e)}"


# ─────────────────────────────────────────────
#  ARTIFACTS (build outputs)
# ─────────────────────────────────────────────

@tool("github_list_run_artifacts")
def github_list_run_artifacts(repo_full_name: str, run_id: int) -> str:
    """
    একটি workflow run এর artifacts (build output, যেমন compiled binary,
    test report, coverage report) লিস্ট করে।

    Args:
        repo_full_name: Full name "username/repo-name"
        run_id: workflow run ID

    Returns:
        Artifact নাম, সাইজ, ও ডাউনলোড URL এর তালিকা
    """
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/actions/runs/{run_id}/artifacts"
        response = requests.get(url, headers=_get_headers())
        data = _handle_response(response)
        if "error" in data:
            return f"❌ {data['error']}"
        artifacts = data.get("artifacts", [])
        if not artifacts:
            return f"📭 No artifacts found for run #{run_id}."
        result = f"📦 *Artifacts for Run #{run_id}*\n\n"
        for art in artifacts:
            expired = " (⚠️ expired)" if art.get("expired") else ""
            size_mb = art["size_in_bytes"] / (1024 * 1024)
            result += f"📁 **{art['name']}**{expired}\n"
            result += f"   Size: {size_mb:.2f} MB | id: {art['id']}\n"
            result += f"   🔗 {art['archive_download_url']}\n\n"
        return result
    except Exception as e:
        return f"❌ Error: {str(e)}"


@tool("github_delete_run_artifact")
def github_delete_run_artifact(repo_full_name: str, artifact_id: int) -> str:
    """
    একটি নির্দিষ্ট artifact ডিলিট করে (storage খরচ কমাতে)।

    Args:
        repo_full_name: Full name "username/repo-name"
        artifact_id: artifact ID (github_list_run_artifacts থেকে পাওয়া)
    """
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/actions/artifacts/{artifact_id}"
        response = requests.delete(url, headers=_get_headers())
        if response.status_code == 204:
            return f"✅ Artifact #{artifact_id} deleted."
        return f"❌ Failed to delete: {response.status_code}"
    except Exception as e:
        return f"❌ Error: {str(e)}"


# ─────────────────────────────────────────────
#  USAGE / BILLING
# ─────────────────────────────────────────────

@tool("github_get_workflow_usage")
def github_get_workflow_usage(repo_full_name: str, workflow_id: str) -> str:
    """
    একটি workflow বর্তমান billing cycle এ কত billable minutes খরচ
    করেছে তা দেখায় (GitHub-hosted runner সহ private repo এর জন্য)।

    Args:
        repo_full_name: Full name "username/repo-name"
        workflow_id: workflow file নাম বা numeric ID
    """
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/actions/workflows/{workflow_id}/timing"
        response = requests.get(url, headers=_get_headers())
        data = _handle_response(response)
        if "error" in data:
            return f"❌ {data['error']}"
        billable = data.get("billable", {})
        if not billable:
            return f"📊 No billable usage recorded for workflow '{workflow_id}' (likely a public repo or free usage)."
        result = f"📊 *Billable usage for workflow '{workflow_id}'*\n\n"
        for os_name, info in billable.items():
            minutes = info.get("total_ms", 0) / 60000
            result += f"💻 {os_name}: {minutes:.1f} minutes\n"
        return result
    except Exception as e:
        return f"❌ Error: {str(e)}"
