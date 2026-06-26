"""
github_pulls_tools.py — GitHub Pull Request Tools
=================================================
Agent GitHub Pull Request এর সম্পূর্ণ lifecycle পরিচালনা করতে পারে:
তৈরি করা, লিস্ট/দেখা, merge করা, review করা, কমেন্ট করা, ফাইলের diff
দেখা। existing github_tools.py এর সাথে একই কনভেনশন (headers, error
format, emoji prefix) মেনে লেখা হয়েছে যাতে agent এর কাছে সব GitHub
tool একই রকম দেখায় ও আচরণ করে।

GitHub Personal Access Token প্রয়োজন (repo scope)।
"""

import os
import requests
from crewai.tools import tool

GITHUB_TOKEN = os.getenv("GITHUB_ACCESS_TOKEN", "")
GITHUB_API_BASE = "https://api.github.com"


def _get_headers():
    """GitHub API request headers"""
    if not GITHUB_TOKEN:
        return {"Accept": "application/vnd.github.v3+json"}
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }


def _handle_response(response):
    """Handle GitHub API response — github_tools.py এর সাথে অভিন্ন কনভেনশন"""
    if response.status_code in (200, 201):
        return response.json()
    elif response.status_code == 204:
        return {"success": True, "message": "Operation successful"}
    elif response.status_code == 401:
        return {"error": "Unauthorized! Check your GITHUB_ACCESS_TOKEN"}
    elif response.status_code == 404:
        return {"error": "Repository, PR, or resource not found"}
    elif response.status_code == 422:
        return {"error": f"Validation failed: {response.text[:300]}"}
    else:
        return {"error": f"GitHub API error: {response.status_code}", "details": response.text[:300]}


# ─────────────────────────────────────────────
#  CREATE / LIST / GET
# ─────────────────────────────────────────────

@tool("github_create_pull_request")
def github_create_pull_request(repo_full_name: str, title: str, head_branch: str,
                                base_branch: str = "main", body: str = "",
                                draft: bool = False) -> str:
    """
    একটি নতুন Pull Request তৈরি করে।

    Args:
        repo_full_name: Full name "username/repo-name"
        title: PR এর টাইটেল
        head_branch: যে branch থেকে merge করতে চান (যেমন "feature-x")
        base_branch: যে branch এ merge করতে চান (default: "main")
        body: PR description (optional)
        draft: True হলে draft PR হিসেবে তৈরি হবে

    Returns:
        PR নাম্বার ও URL সহ confirmation
    """
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/pulls"
        data = {"title": title, "head": head_branch, "base": base_branch, "body": body, "draft": draft}
        response = requests.post(url, headers=_get_headers(), json=data)
        result = _handle_response(response)
        if "error" in result:
            return f"❌ {result['error']}"
        return (f"✅ Pull Request created!\n🔗 {result['html_url']}\n"
                f"📌 #{result['number']}: {title}\n"
                f"🌿 {head_branch} → {base_branch}{' (draft)' if draft else ''}")
    except Exception as e:
        return f"❌ Error creating PR: {str(e)}"


@tool("github_list_pull_requests")
def github_list_pull_requests(repo_full_name: str, state: str = "open") -> str:
    """
    Repository এর Pull Request লিস্ট করে।

    Args:
        repo_full_name: Full name "username/repo-name"
        state: 'open', 'closed', or 'all' (default: 'open')

    Returns:
        PR লিস্ট, নাম্বার, টাইটেল, branch তথ্য সহ
    """
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/pulls"
        response = requests.get(url, headers=_get_headers(), params={"state": state, "per_page": 20})
        data = _handle_response(response)
        if "error" in data:
            return f"❌ {data['error']}"
        if not data:
            return f"📭 No {state} pull requests found."
        result = f"🔀 *Pull Requests* ({state}) for {repo_full_name}\n\n"
        for pr in data:
            result += f"#{pr['number']} **{pr['title']}**\n"
            result += f"   🌿 {pr['head']['ref']} → {pr['base']['ref']}\n"
            result += f"   👤 {pr['user']['login']} | 🔗 {pr['html_url']}\n\n"
        return result
    except Exception as e:
        return f"❌ Error: {str(e)}"


@tool("github_get_pull_request")
def github_get_pull_request(repo_full_name: str, pr_number: int) -> str:
    """
    একটি নির্দিষ্ট Pull Request এর বিস্তারিত তথ্য (mergeable কিনা, conflict
    আছে কিনা, কতগুলো commit/changed files, ইত্যাদি) দেখায়।

    Args:
        repo_full_name: Full name "username/repo-name"
        pr_number: PR নাম্বার

    Returns:
        PR এর বিস্তারিত অবস্থা
    """
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/pulls/{pr_number}"
        response = requests.get(url, headers=_get_headers())
        data = _handle_response(response)
        if "error" in data:
            return f"❌ {data['error']}"

        mergeable = data.get("mergeable")
        mergeable_str = "✅ Yes" if mergeable is True else ("❌ No (conflicts)" if mergeable is False else "⏳ Checking...")

        return (f"🔀 *PR #{pr_number}: {data['title']}*\n\n"
                f"📝 State: {data['state']}{' (draft)' if data.get('draft') else ''}\n"
                f"🌿 {data['head']['ref']} → {data['base']['ref']}\n"
                f"👤 Author: {data['user']['login']}\n"
                f"🔄 Mergeable: {mergeable_str}\n"
                f"📊 +{data.get('additions', 0)} / -{data.get('deletions', 0)} lines, "
                f"{data.get('changed_files', 0)} files, {data.get('commits', 0)} commits\n"
                f"💬 Comments: {data.get('comments', 0)} | Review comments: {data.get('review_comments', 0)}\n"
                f"🔗 {data['html_url']}")
    except Exception as e:
        return f"❌ Error: {str(e)}"


@tool("github_get_pr_files")
def github_get_pr_files(repo_full_name: str, pr_number: int) -> str:
    """
    একটি Pull Request এ কোন কোন ফাইল পরিবর্তন হয়েছে এবং কীভাবে
    (added/modified/removed, কত লাইন +/-) তা দেখায়।

    Args:
        repo_full_name: Full name "username/repo-name"
        pr_number: PR নাম্বার

    Returns:
        পরিবর্তিত ফাইলের তালিকা ও সংক্ষিপ্ত diff stats
    """
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/pulls/{pr_number}/files"
        response = requests.get(url, headers=_get_headers(), params={"per_page": 50})
        data = _handle_response(response)
        if "error" in data:
            return f"❌ {data['error']}"
        if not data:
            return "📭 No changed files found."
        result = f"📁 *Changed files in PR #{pr_number}* ({len(data)})\n\n"
        for f in data[:30]:
            status_emoji = {"added": "🟢", "modified": "🟡", "removed": "🔴", "renamed": "🔵"}.get(f["status"], "⚪")
            result += f"{status_emoji} {f['filename']} (+{f['additions']}/-{f['deletions']})\n"
        return result
    except Exception as e:
        return f"❌ Error: {str(e)}"


# ─────────────────────────────────────────────
#  UPDATE / MERGE / CLOSE
# ─────────────────────────────────────────────

@tool("github_update_pull_request")
def github_update_pull_request(repo_full_name: str, pr_number: int, title: str = "",
                                body: str = "", state: str = "", base_branch: str = "") -> str:
    """
    একটি Pull Request আপডেট করে (title, description, state, বা base branch)।
    যা দেওয়া হবে না, তা অপরিবর্তিত থাকবে।

    Args:
        repo_full_name: Full name "username/repo-name"
        pr_number: PR নাম্বার
        title: (optional) নতুন title
        body: (optional) নতুন description
        state: (optional) 'open' বা 'closed'
        base_branch: (optional) নতুন base branch

    Returns:
        Confirmation message
    """
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/pulls/{pr_number}"
        payload = {}
        if title:
            payload["title"] = title
        if body:
            payload["body"] = body
        if state:
            payload["state"] = state
        if base_branch:
            payload["base"] = base_branch
        if not payload:
            return "⚠️ No fields provided to update."
        response = requests.patch(url, headers=_get_headers(), json=payload)
        result = _handle_response(response)
        if "error" in result:
            return f"❌ {result['error']}"
        return f"✅ PR #{pr_number} updated successfully."
    except Exception as e:
        return f"❌ Error: {str(e)}"


@tool("github_merge_pull_request")
def github_merge_pull_request(repo_full_name: str, pr_number: int,
                               merge_method: str = "merge", commit_title: str = "") -> str:
    """
    একটি Pull Request merge করে।

    Args:
        repo_full_name: Full name "username/repo-name"
        pr_number: PR নাম্বার
        merge_method: 'merge', 'squash', or 'rebase' (default: 'merge')
        commit_title: (optional) merge commit এর টাইটেল

    Returns:
        Merge সফল হলো কিনা তার নিশ্চিতকরণ, বা ব্যর্থ হলে কারণ (যেমন conflict)
    """
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/pulls/{pr_number}/merge"
        payload = {"merge_method": merge_method}
        if commit_title:
            payload["commit_title"] = commit_title
        response = requests.put(url, headers=_get_headers(), json=payload)
        result = _handle_response(response)
        if response.status_code == 405:
            return f"❌ PR #{pr_number} is not mergeable (conflicts or checks failing). {response.text[:200]}"
        if response.status_code == 409:
            return f"❌ Merge conflict — head branch was modified, SHA mismatch."
        if "error" in result:
            return f"❌ {result['error']}"
        return f"✅ PR #{pr_number} merged successfully using '{merge_method}' method!\n🔗 SHA: {result.get('sha', 'N/A')}"
    except Exception as e:
        return f"❌ Error: {str(e)}"


@tool("github_close_pull_request")
def github_close_pull_request(repo_full_name: str, pr_number: int) -> str:
    """
    একটি Pull Request merge না করে বন্ধ করে দেয়।

    Args:
        repo_full_name: Full name "username/repo-name"
        pr_number: PR নাম্বার
    """
    return github_update_pull_request(repo_full_name, pr_number, state="closed")


# ─────────────────────────────────────────────
#  REVIEWS & COMMENTS
# ─────────────────────────────────────────────

@tool("github_create_pr_review")
def github_create_pr_review(repo_full_name: str, pr_number: int, body: str,
                             event: str = "COMMENT") -> str:
    """
    একটি Pull Request এ review জমা দেয়।

    Args:
        repo_full_name: Full name "username/repo-name"
        pr_number: PR নাম্বার
        body: Review এর মূল কন্টেন্ট
        event: 'APPROVE', 'REQUEST_CHANGES', or 'COMMENT' (default: 'COMMENT')

    Returns:
        Confirmation message
    """
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/pulls/{pr_number}/reviews"
        payload = {"body": body, "event": event.upper()}
        response = requests.post(url, headers=_get_headers(), json=payload)
        result = _handle_response(response)
        if "error" in result:
            return f"❌ {result['error']}"
        return f"✅ Review submitted on PR #{pr_number} ({event.upper()})"
    except Exception as e:
        return f"❌ Error: {str(e)}"


@tool("github_create_pr_comment")
def github_create_pr_comment(repo_full_name: str, pr_number: int, body: str) -> str:
    """
    একটি Pull Request এ সাধারণ (issue-style) কমেন্ট যুক্ত করে — নির্দিষ্ট
    কোনো লাইনে নয়, পুরো PR-এর আলোচনায়।

    Args:
        repo_full_name: Full name "username/repo-name"
        pr_number: PR নাম্বার (issue নাম্বার হিসেবেও কাজ করে)
        body: কমেন্টের টেক্সট

    Returns:
        Confirmation message with URL
    """
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/issues/{pr_number}/comments"
        response = requests.post(url, headers=_get_headers(), json={"body": body})
        result = _handle_response(response)
        if "error" in result:
            return f"❌ {result['error']}"
        return f"✅ Comment added to PR #{pr_number}\n🔗 {result.get('html_url', '')}"
    except Exception as e:
        return f"❌ Error: {str(e)}"


@tool("github_list_pr_comments")
def github_list_pr_comments(repo_full_name: str, pr_number: int) -> str:
    """
    একটি Pull Request এর সব কমেন্ট লিস্ট করে।

    Args:
        repo_full_name: Full name "username/repo-name"
        pr_number: PR নাম্বার

    Returns:
        কমেন্টের তালিকা, author ও content সহ
    """
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/issues/{pr_number}/comments"
        response = requests.get(url, headers=_get_headers(), params={"per_page": 30})
        data = _handle_response(response)
        if "error" in data:
            return f"❌ {data['error']}"
        if not data:
            return f"📭 No comments on PR #{pr_number}."
        result = f"💬 *Comments on PR #{pr_number}* ({len(data)})\n\n"
        for c in data:
            result += f"👤 **{c['user']['login']}**: {c['body'][:200]}\n\n"
        return result
    except Exception as e:
        return f"❌ Error: {str(e)}"


@tool("github_request_pr_reviewers")
def github_request_pr_reviewers(repo_full_name: str, pr_number: int, reviewers: str) -> str:
    """
    Pull Request এ review এর জন্য একাধিক ব্যবহারকারী অনুরোধ করে।

    Args:
        repo_full_name: Full name "username/repo-name"
        pr_number: PR নাম্বার
        reviewers: কমা-সেপারেটেড username (যেমন "alice,bob")

    Returns:
        Confirmation message
    """
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/pulls/{pr_number}/requested_reviewers"
        usernames = [r.strip() for r in reviewers.split(",") if r.strip()]
        response = requests.post(url, headers=_get_headers(), json={"reviewers": usernames})
        result = _handle_response(response)
        if "error" in result:
            return f"❌ {result['error']}"
        return f"✅ Requested review from: {', '.join(usernames)} on PR #{pr_number}"
    except Exception as e:
        return f"❌ Error: {str(e)}"
