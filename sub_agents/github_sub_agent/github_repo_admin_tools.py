"""
github_repo_admin_tools.py — GitHub Repository Administration Tools
======================================================================
github_tools.py এ মূল repo/issue/file/branch coverage আছে। এই ফাইলে
বাকি যে administrative/advanced কাজগুলো ওয়েবসাইটের Settings/Releases/
Collaborators ট্যাবে গিয়ে করা হয়, সেগুলো API দিয়ে covered:

  • Repository settings আপডেট (description, topics, default branch,
    visibility, features চালু/বন্ধ)
  • Content delete (ফাইল মুছে ফেলা — github_tools.py এ শুধু create/
    update আছে)
  • Collaborators (যুক্ত করা, লিস্ট করা, বাদ দেওয়া, permission সেট করা)
  • Releases ও Tags (নতুন রিলিজ পাবলিশ করা, লিস্ট করা, ডিলিট করা)
  • Labels (issue/PR এর জন্য)
  • Webhooks (তৈরি, লিস্ট, ডিলিট)
  • Repository (Actions) Secrets — encrypted secret তৈরি, লিস্ট, ডিলিট
  • Self-hosted runners — list/remove (registration token দিয়ে নতুন
    runner যুক্ত করার প্রক্রিয়া physical মেশিনে কমান্ড চালানো লাগে, তাই
    এখানে শুধু token generation ও existing runner management করা যায়)
  • Code/Repository search (গোটা GitHub এ বা নির্দিষ্ট repo এর মধ্যে
    কোড খোঁজা)

GitHub Personal Access Token প্রয়োজন। কিছু endpoint এ admin scope
লাগবে (secrets, webhooks, collaborators, runners)।
"""

import os
import base64
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
    elif response.status_code == 403:
        return {"error": "Forbidden — token may lack required scope/permission for this action"}
    elif response.status_code == 404:
        return {"error": "Repository or resource not found"}
    elif response.status_code == 422:
        return {"error": f"Validation failed: {response.text[:300]}"}
    else:
        return {"error": f"GitHub API error: {response.status_code}", "details": response.text[:300]}


# ─────────────────────────────────────────────
#  REPOSITORY SETTINGS
# ─────────────────────────────────────────────

@tool("github_update_repo_settings")
def github_update_repo_settings(repo_full_name: str, description: str = "", homepage: str = "",
                                 private: str = "", default_branch: str = "",
                                 has_issues: str = "", has_wiki: str = "",
                                 has_projects: str = "") -> str:
    """
    Repository এর settings আপডেট করে (যা সাধারণত ওয়েবসাইটের Settings
    ট্যাবে করা হয়)। যা ফাঁকা রাখবেন তা অপরিবর্তিত থাকবে।

    Args:
        repo_full_name: Full name "username/repo-name"
        description: (optional) নতুন description
        homepage: (optional) homepage URL
        private: (optional) "true" বা "false" — repo private/public করতে
        default_branch: (optional) নতুন default branch নাম
        has_issues: (optional) "true"/"false" — Issues ফিচার চালু/বন্ধ
        has_wiki: (optional) "true"/"false" — Wiki ফিচার চালু/বন্ধ
        has_projects: (optional) "true"/"false" — Projects ফিচার চালু/বন্ধ

    Returns:
        Confirmation message
    """
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        payload = {}
        if description:
            payload["description"] = description
        if homepage:
            payload["homepage"] = homepage
        if private:
            payload["private"] = private.lower() == "true"
        if default_branch:
            payload["default_branch"] = default_branch
        if has_issues:
            payload["has_issues"] = has_issues.lower() == "true"
        if has_wiki:
            payload["has_wiki"] = has_wiki.lower() == "true"
        if has_projects:
            payload["has_projects"] = has_projects.lower() == "true"
        if not payload:
            return "⚠️ No settings provided to update."

        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}"
        response = requests.patch(url, headers=_get_headers(), json=payload)
        result = _handle_response(response)
        if "error" in result:
            return f"❌ {result['error']}"
        return f"✅ Repository settings updated: {', '.join(payload.keys())}"
    except Exception as e:
        return f"❌ Error: {str(e)}"


@tool("github_update_repo_topics")
def github_update_repo_topics(repo_full_name: str, topics: str) -> str:
    """
    Repository এর topics (tags, যেমন "python, ai, automation") সেট করে।

    Args:
        repo_full_name: Full name "username/repo-name"
        topics: কমা-সেপারেটেড topic তালিকা (যেমন "python,ai,bot")
    """
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        topic_list = [t.strip().lower() for t in topics.split(",") if t.strip()]
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/topics"
        response = requests.put(url, headers=_get_headers(), json={"names": topic_list})
        result = _handle_response(response)
        if "error" in result:
            return f"❌ {result['error']}"
        return f"✅ Topics set: {', '.join(topic_list)}"
    except Exception as e:
        return f"❌ Error: {str(e)}"


@tool("github_delete_file")
def github_delete_file(repo_full_name: str, file_path: str, commit_message: str = "Delete file via AI Agent",
                        branch: str = "main") -> str:
    """
    Repository থেকে একটি ফাইল ডিলিট করে। github_tools.py এর
    github_create_file শুধু তৈরি/আপডেট করে, ডিলিট করতে পারে না —
    এটা সেই gap পূরণ করে।

    Args:
        repo_full_name: Full name "username/repo-name"
        file_path: যে ফাইল ডিলিট করতে হবে তার পাথ
        commit_message: কমিট মেসেজ
        branch: branch নাম (default: "main")

    Returns:
        Confirmation message
    """
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        # প্রথমে ফাইলের বর্তমান SHA জানতে হবে, GitHub এর delete API তে এটা required
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/contents/{file_path}"
        get_resp = requests.get(url, headers=_get_headers(), params={"ref": branch})
        get_data = _handle_response(get_resp)
        if "error" in get_data:
            return f"❌ Could not find file to delete: {get_data['error']}"
        sha = get_data.get("sha")
        if not sha:
            return "❌ File SHA not found — is this path a directory?"

        payload = {"message": commit_message, "sha": sha, "branch": branch}
        del_resp = requests.delete(url, headers=_get_headers(), json=payload)
        result = _handle_response(del_resp)
        if "error" in result:
            return f"❌ {result['error']}"
        return f"✅ File '{file_path}' deleted from branch '{branch}'."
    except Exception as e:
        return f"❌ Error: {str(e)}"


# ─────────────────────────────────────────────
#  COLLABORATORS
# ─────────────────────────────────────────────

@tool("github_list_collaborators")
def github_list_collaborators(repo_full_name: str) -> str:
    """Repository এর সব collaborator ও তাদের permission level লিস্ট করে।"""
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/collaborators"
        response = requests.get(url, headers=_get_headers(), params={"per_page": 50})
        data = _handle_response(response)
        if "error" in data:
            return f"❌ {data['error']}"
        if not data:
            return "📭 No collaborators found."
        result = f"👥 *Collaborators* for {repo_full_name}\n\n"
        for c in data:
            perms = c.get("permissions", {})
            level = "admin" if perms.get("admin") else ("write" if perms.get("push") else "read")
            result += f"👤 {c['login']} — {level}\n"
        return result
    except Exception as e:
        return f"❌ Error: {str(e)}"


@tool("github_add_collaborator")
def github_add_collaborator(repo_full_name: str, username: str, permission: str = "push") -> str:
    """
    Repository এ একজন collaborator যুক্ত করে (invitation পাঠায়)।

    Args:
        repo_full_name: Full name "username/repo-name"
        username: যাকে যুক্ত করতে চান
        permission: 'pull' (read), 'push' (write), 'admin', 'maintain', or 'triage'
    """
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/collaborators/{username}"
        response = requests.put(url, headers=_get_headers(), json={"permission": permission})
        result = _handle_response(response)
        if "error" in result:
            return f"❌ {result['error']}"
        return f"✅ Invitation sent to '{username}' with '{permission}' permission."
    except Exception as e:
        return f"❌ Error: {str(e)}"


@tool("github_remove_collaborator")
def github_remove_collaborator(repo_full_name: str, username: str) -> str:
    """Repository থেকে একজন collaborator সরিয়ে দেয়।"""
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/collaborators/{username}"
        response = requests.delete(url, headers=_get_headers())
        if response.status_code == 204:
            return f"✅ '{username}' removed as collaborator."
        return f"❌ Failed: {response.status_code}"
    except Exception as e:
        return f"❌ Error: {str(e)}"


# ─────────────────────────────────────────────
#  RELEASES & TAGS
# ─────────────────────────────────────────────

@tool("github_list_releases")
def github_list_releases(repo_full_name: str) -> str:
    """Repository এর সব release লিস্ট করে।"""
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/releases"
        response = requests.get(url, headers=_get_headers(), params={"per_page": 20})
        data = _handle_response(response)
        if "error" in data:
            return f"❌ {data['error']}"
        if not data:
            return "📭 No releases found."
        result = f"🏷️ *Releases* for {repo_full_name}\n\n"
        for r in data:
            draft = " (draft)" if r.get("draft") else ""
            pre = " (pre-release)" if r.get("prerelease") else ""
            result += f"📦 **{r['tag_name']}**{draft}{pre} — {r.get('name', '')}\n"
            result += f"   🔗 {r['html_url']}\n\n"
        return result
    except Exception as e:
        return f"❌ Error: {str(e)}"


@tool("github_create_release")
def github_create_release(repo_full_name: str, tag_name: str, name: str = "",
                           body: str = "", draft: bool = False, prerelease: bool = False,
                           target_commitish: str = "main") -> str:
    """
    একটি নতুন release publish করে (একটা নতুন version বের করার সমতুল্য)।

    Args:
        repo_full_name: Full name "username/repo-name"
        tag_name: tag নাম (যেমন "v1.0.0")
        name: release টাইটেল (না দিলে tag_name ব্যবহার হবে)
        body: release notes
        draft: True হলে draft release হবে
        prerelease: True হলে pre-release হিসেবে চিহ্নিত হবে
        target_commitish: কোন branch/commit থেকে tag তৈরি হবে

    Returns:
        Release URL সহ confirmation
    """
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/releases"
        payload = {
            "tag_name": tag_name,
            "name": name or tag_name,
            "body": body,
            "draft": draft,
            "prerelease": prerelease,
            "target_commitish": target_commitish,
        }
        response = requests.post(url, headers=_get_headers(), json=payload)
        result = _handle_response(response)
        if "error" in result:
            return f"❌ {result['error']}"
        return f"✅ Release '{tag_name}' published!\n🔗 {result['html_url']}"
    except Exception as e:
        return f"❌ Error: {str(e)}"


@tool("github_delete_release")
def github_delete_release(repo_full_name: str, release_id: int) -> str:
    """একটি release ডিলিট করে (এর tag টা ডিলিট হয় না, শুধু release entry)।"""
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/releases/{release_id}"
        response = requests.delete(url, headers=_get_headers())
        if response.status_code == 204:
            return f"✅ Release #{release_id} deleted."
        return f"❌ Failed: {response.status_code}"
    except Exception as e:
        return f"❌ Error: {str(e)}"


@tool("github_list_tags")
def github_list_tags(repo_full_name: str) -> str:
    """Repository এর সব git tag লিস্ট করে।"""
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/tags"
        response = requests.get(url, headers=_get_headers(), params={"per_page": 30})
        data = _handle_response(response)
        if "error" in data:
            return f"❌ {data['error']}"
        if not data:
            return "📭 No tags found."
        result = f"🏷️ *Tags* for {repo_full_name}\n\n"
        for t in data:
            result += f"• {t['name']} ({t['commit']['sha'][:7]})\n"
        return result
    except Exception as e:
        return f"❌ Error: {str(e)}"


# ─────────────────────────────────────────────
#  LABELS
# ─────────────────────────────────────────────

@tool("github_list_labels")
def github_list_labels(repo_full_name: str) -> str:
    """Repository এর সব issue/PR label লিস্ট করে।"""
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/labels"
        response = requests.get(url, headers=_get_headers(), params={"per_page": 50})
        data = _handle_response(response)
        if "error" in data:
            return f"❌ {data['error']}"
        if not data:
            return "📭 No labels found."
        result = f"🏷️ *Labels* for {repo_full_name}\n\n"
        for l in data:
            result += f"• {l['name']} (#{l['color']})\n"
        return result
    except Exception as e:
        return f"❌ Error: {str(e)}"


@tool("github_create_label")
def github_create_label(repo_full_name: str, name: str, color: str = "ededed", description: str = "") -> str:
    """
    একটি নতুন label তৈরি করে।

    Args:
        repo_full_name: Full name "username/repo-name"
        name: label নাম
        color: hex color code, '#' ছাড়া (যেমন "ff0000")
        description: (optional) label description
    """
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/labels"
        payload = {"name": name, "color": color.lstrip("#"), "description": description}
        response = requests.post(url, headers=_get_headers(), json=payload)
        result = _handle_response(response)
        if "error" in result:
            return f"❌ {result['error']}"
        return f"✅ Label '{name}' created."
    except Exception as e:
        return f"❌ Error: {str(e)}"


@tool("github_add_labels_to_issue")
def github_add_labels_to_issue(repo_full_name: str, issue_number: int, labels: str) -> str:
    """
    একটি issue/PR এ label যুক্ত করে।

    Args:
        repo_full_name: Full name "username/repo-name"
        issue_number: issue বা PR নাম্বার
        labels: কমা-সেপারেটেড label নাম (যেমন "bug,urgent")
    """
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        label_list = [l.strip() for l in labels.split(",") if l.strip()]
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/issues/{issue_number}/labels"
        response = requests.post(url, headers=_get_headers(), json={"labels": label_list})
        result = _handle_response(response)
        if "error" in result:
            return f"❌ {result['error']}"
        return f"✅ Labels added to #{issue_number}: {', '.join(label_list)}"
    except Exception as e:
        return f"❌ Error: {str(e)}"


# ─────────────────────────────────────────────
#  WEBHOOKS
# ─────────────────────────────────────────────

@tool("github_list_webhooks")
def github_list_webhooks(repo_full_name: str) -> str:
    """Repository এর সব webhook লিস্ট করে।"""
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/hooks"
        response = requests.get(url, headers=_get_headers())
        data = _handle_response(response)
        if "error" in data:
            return f"❌ {data['error']}"
        if not data:
            return "📭 No webhooks found."
        result = f"🪝 *Webhooks* for {repo_full_name}\n\n"
        for h in data:
            active = "🟢" if h.get("active") else "🔴"
            result += f"{active} id:{h['id']} → {h['config'].get('url', 'N/A')}\n"
            result += f"   Events: {', '.join(h.get('events', []))}\n\n"
        return result
    except Exception as e:
        return f"❌ Error: {str(e)}"


@tool("github_create_webhook")
def github_create_webhook(repo_full_name: str, payload_url: str, events: str = "push",
                           secret: str = "") -> str:
    """
    একটি নতুন webhook তৈরি করে — repository তে কোনো event ঘটলে একটি
    external URL এ notification পাঠাতে।

    Args:
        repo_full_name: Full name "username/repo-name"
        payload_url: যে URL এ webhook event পাঠানো হবে
        events: কমা-সেপারেটেড event তালিকা (যেমন "push,pull_request")
        secret: (optional) webhook payload sign করার জন্য secret
    """
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        event_list = [e.strip() for e in events.split(",") if e.strip()]
        config = {"url": payload_url, "content_type": "json"}
        if secret:
            config["secret"] = secret
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/hooks"
        payload = {"name": "web", "active": True, "events": event_list, "config": config}
        response = requests.post(url, headers=_get_headers(), json=payload)
        result = _handle_response(response)
        if "error" in result:
            return f"❌ {result['error']}"
        return f"✅ Webhook created (id: {result['id']}) for events: {', '.join(event_list)}"
    except Exception as e:
        return f"❌ Error: {str(e)}"


@tool("github_delete_webhook")
def github_delete_webhook(repo_full_name: str, hook_id: int) -> str:
    """একটি webhook ডিলিট করে।"""
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/hooks/{hook_id}"
        response = requests.delete(url, headers=_get_headers())
        if response.status_code == 204:
            return f"✅ Webhook #{hook_id} deleted."
        return f"❌ Failed: {response.status_code}"
    except Exception as e:
        return f"❌ Error: {str(e)}"


# ─────────────────────────────────────────────
#  REPOSITORY (ACTIONS) SECRETS
#  ⚠️ এই secrets GitHub Actions workflow এর জন্য — memory.py এর vault
#  থেকে আলাদা ব্যাপার। GitHub এর secret encryption এর জন্য libsodium
#  sealed box প্রয়োজন, তাই 'pynacl' প্যাকেজ লাগবে।
# ─────────────────────────────────────────────

@tool("github_list_repo_secrets")
def github_list_repo_secrets(repo_full_name: str) -> str:
    """
    Repository এর GitHub Actions secrets লিস্ট করে (নাম শুধু, মান
    encrypted/hidden — GitHub কখনো secret এর মান ফেরত দেয় না)।
    """
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/actions/secrets"
        response = requests.get(url, headers=_get_headers())
        data = _handle_response(response)
        if "error" in data:
            return f"❌ {data['error']}"
        secrets = data.get("secrets", [])
        if not secrets:
            return "📭 No Actions secrets found."
        result = f"🔐 *Actions Secrets* for {repo_full_name}\n\n"
        for s in secrets:
            result += f"🔑 {s['name']} (updated: {s['updated_at'][:10]})\n"
        return result
    except Exception as e:
        return f"❌ Error: {str(e)}"


@tool("github_set_repo_secret")
def github_set_repo_secret(repo_full_name: str, secret_name: str, secret_value: str) -> str:
    """
    একটি GitHub Actions secret তৈরি/আপডেট করে — workflow এর ভেতরে
    `${{ secrets.SECRET_NAME }}` দিয়ে ব্যবহার করা যাবে।

    ⚠️ requires the 'pynacl' package (pip install pynacl) for the
    libsodium sealed-box encryption GitHub API mandates.

    Args:
        repo_full_name: Full name "username/repo-name"
        secret_name: secret এর নাম (convention: UPPER_SNAKE_CASE)
        secret_value: secret এর মান (plaintext — এই function নিজেই encrypt করে)
    """
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        try:
            from nacl import encoding, public
        except ImportError:
            return "❌ Error: 'pynacl' package required. Run: pip install pynacl"

        # প্রথমে repo এর public key আনতে হবে encryption এর জন্য
        key_url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/actions/secrets/public-key"
        key_resp = requests.get(key_url, headers=_get_headers())
        key_data = _handle_response(key_resp)
        if "error" in key_data:
            return f"❌ Could not get public key: {key_data['error']}"

        public_key = public.PublicKey(key_data["key"].encode("utf-8"), encoding.Base64Encoder())
        sealed_box = public.SealedBox(public_key)
        encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
        encrypted_value = base64.b64encode(encrypted).decode("utf-8")

        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/actions/secrets/{secret_name}"
        payload = {"encrypted_value": encrypted_value, "key_id": key_data["key_id"]}
        response = requests.put(url, headers=_get_headers(), json=payload)
        if response.status_code in (201, 204):
            return f"✅ Secret '{secret_name}' set successfully."
        result = _handle_response(response)
        return f"❌ {result.get('error', 'Failed to set secret')}"
    except Exception as e:
        return f"❌ Error: {str(e)}"


@tool("github_delete_repo_secret")
def github_delete_repo_secret(repo_full_name: str, secret_name: str) -> str:
    """একটি GitHub Actions secret ডিলিট করে।"""
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/actions/secrets/{secret_name}"
        response = requests.delete(url, headers=_get_headers())
        if response.status_code == 204:
            return f"✅ Secret '{secret_name}' deleted."
        return f"❌ Failed: {response.status_code}"
    except Exception as e:
        return f"❌ Error: {str(e)}"


# ─────────────────────────────────────────────
#  SELF-HOSTED RUNNERS
# ─────────────────────────────────────────────

@tool("github_list_self_hosted_runners")
def github_list_self_hosted_runners(repo_full_name: str) -> str:
    """Repository এর সাথে যুক্ত সব self-hosted runner লিস্ট করে, তাদের
    status (online/offline) ও busy কিনা সহ।"""
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/actions/runners"
        response = requests.get(url, headers=_get_headers())
        data = _handle_response(response)
        if "error" in data:
            return f"❌ {data['error']}"
        runners = data.get("runners", [])
        if not runners:
            return "📭 No self-hosted runners registered."
        result = f"🖥️ *Self-hosted Runners* for {repo_full_name}\n\n"
        for r in runners:
            status_emoji = "🟢" if r["status"] == "online" else "🔴"
            busy = " (busy)" if r.get("busy") else ""
            result += f"{status_emoji} **{r['name']}** (id: {r['id']}){busy}\n"
            result += f"   OS: {r.get('os', 'N/A')} | Labels: {', '.join(l['name'] for l in r.get('labels', []))}\n\n"
        return result
    except Exception as e:
        return f"❌ Error: {str(e)}"


@tool("github_get_runner_registration_token")
def github_get_runner_registration_token(repo_full_name: str) -> str:
    """
    নতুন self-hosted runner রেজিস্টার করার জন্য একটি temporary token
    জেনারেট করে। এই token দিয়ে target মেশিনে `./config.sh` চালিয়ে
    runner setup করতে হয় (এই step টা physical/virtual মেশিনে manually
    বা অন্য automation দিয়ে করতে হবে, এই tool শুধু token দেয়)।

    Args:
        repo_full_name: Full name "username/repo-name"
    """
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/actions/runners/registration-token"
        response = requests.post(url, headers=_get_headers())
        data = _handle_response(response)
        if "error" in data:
            return f"❌ {data['error']}"
        return (f"🔑 Registration token: {data['token']}\n"
                f"⏰ Expires at: {data['expires_at']}\n"
                f"ℹ️ Use this with `./config.sh --url https://github.com/{repo_full_name} --token <token>` "
                f"on the target machine to register a new runner.")
    except Exception as e:
        return f"❌ Error: {str(e)}"


@tool("github_remove_self_hosted_runner")
def github_remove_self_hosted_runner(repo_full_name: str, runner_id: int) -> str:
    """একটি self-hosted runner repository থেকে রেজিস্ট্রেশন বাতিল করে।"""
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/actions/runners/{runner_id}"
        response = requests.delete(url, headers=_get_headers())
        if response.status_code == 204:
            return f"✅ Runner #{runner_id} removed."
        return f"❌ Failed: {response.status_code}"
    except Exception as e:
        return f"❌ Error: {str(e)}"


# ─────────────────────────────────────────────
#  SEARCH
# ─────────────────────────────────────────────

@tool("github_search_code")
def github_search_code(query: str, repo_full_name: str = "") -> str:
    """
    GitHub এ কোড সার্চ করে — কোনো ফাংশন/variable/string কোথায় ব্যবহৃত
    হয়েছে খুঁজতে।

    Args:
        query: সার্চ কোয়েরি (GitHub code search syntax সাপোর্ট করে,
               যেমন "filename:.py import requests")
        repo_full_name: (optional) দিলে শুধু এই repo তে সার্চ হবে,
               না দিলে accessible সব repo তে

    Returns:
        ম্যাচ হওয়া ফাইলের তালিকা
    """
    if not GITHUB_TOKEN:
        return "❌ Error: GITHUB_ACCESS_TOKEN required"
    try:
        full_query = f"{query} repo:{repo_full_name}" if repo_full_name else query
        url = f"{GITHUB_API_BASE}/search/code"
        response = requests.get(url, headers=_get_headers(), params={"q": full_query, "per_page": 20})
        data = _handle_response(response)
        if "error" in data:
            return f"❌ {data['error']}"
        items = data.get("items", [])
        if not items:
            return "📭 No code matches found."
        result = f"🔍 *Code search results* ({data.get('total_count', 0)} total)\n\n"
        for item in items[:15]:
            result += f"📄 {item['repository']['full_name']} → {item['path']}\n"
            result += f"   🔗 {item['html_url']}\n\n"
        return result
    except Exception as e:
        return f"❌ Error: {str(e)}"


@tool("github_search_repositories")
def github_search_repositories(query: str) -> str:
    """
    GitHub এ repository সার্চ করে।

    Args:
        query: সার্চ কোয়েরি (যেমন "language:python stars:>1000 ai agent")

    Returns:
        ম্যাচ হওয়া repo তালিকা
    """
    try:
        url = f"{GITHUB_API_BASE}/search/repositories"
        response = requests.get(url, headers=_get_headers(), params={"q": query, "per_page": 15, "sort": "stars"})
        data = _handle_response(response)
        if "error" in data:
            return f"❌ {data['error']}"
        items = data.get("items", [])
        if not items:
            return "📭 No repositories found."
        result = f"🔍 *Repository search* ({data.get('total_count', 0)} total)\n\n"
        for repo in items:
            result += f"📁 **{repo['full_name']}** ⭐{repo['stargazers_count']}\n"
            result += f"   {repo.get('description', 'No description')[:100]}\n"
            result += f"   🔗 {repo['html_url']}\n\n"
        return result
    except Exception as e:
        return f"❌ Error: {str(e)}"
