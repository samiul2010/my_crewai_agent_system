import os
import json
import urllib.request
import urllib.parse
import urllib.error
from crewai.tools import tool
from duckduckgo_search import DDGS


# ─────────────────────────────────────────────
#  SEARCH TOOLS
# ─────────────────────────────────────────────

@tool("internet_search")
def internet_search(query: str) -> str:
    """Search the internet for current information using DuckDuckGo. Input: a search query string."""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=6))
        if not results:
            return "No results found."
        output = []
        for r in results:
            output.append(
                f"Title: {r.get('title', 'N/A')}\n"
                f"Link: {r.get('href', 'N/A')}\n"
                f"Snippet: {r.get('body', 'N/A')}\n"
            )
        return "\n---\n".join(output)
    except Exception as e:
        return f"Search error: {str(e)}"


@tool("news_search")
def news_search(query: str) -> str:
    """Search for recent news articles using DuckDuckGo News. Input: a search query string."""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.news(query, max_results=6))
        if not results:
            return "No news found."
        output = []
        for r in results:
            output.append(
                f"Title: {r.get('title', 'N/A')}\n"
                f"Source: {r.get('source', 'N/A')}\n"
                f"Date: {r.get('date', 'N/A')}\n"
                f"Link: {r.get('url', 'N/A')}\n"
                f"Snippet: {r.get('body', 'N/A')}\n"
            )
        return "\n---\n".join(output)
    except Exception as e:
        return f"News search error: {str(e)}"


@tool("image_search")
def image_search(query: str) -> str:
    """Search for images using DuckDuckGo Images. Input: a search query string. Returns image URLs."""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.images(query, max_results=5))
        if not results:
            return "No images found."
        output = []
        for r in results:
            output.append(
                f"Title: {r.get('title', 'N/A')}\n"
                f"Image URL: {r.get('image', 'N/A')}\n"
                f"Source: {r.get('url', 'N/A')}\n"
            )
        return "\n---\n".join(output)
    except Exception as e:
        return f"Image search error: {str(e)}"


# ─────────────────────────────────────────────
#  BROWSING / WEB TOOLS
# ─────────────────────────────────────────────

@tool("browse_webpage")
def browse_webpage(url: str) -> str:
    """Fetch and read the text content of any public webpage. Input: a full URL (e.g. https://example.com)."""
    try:
        # Basic URL validation
        if not url.startswith(("http://", "https://")):
            return "Error: URL must start with http:// or https://"

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            charset = "utf-8"
            content_type = resp.headers.get("Content-Type", "")
            if "charset=" in content_type:
                charset = content_type.split("charset=")[-1].strip()
            html = raw.decode(charset, errors="replace")

        # Minimal HTML-to-text stripping (no external deps)
        import re
        # Remove scripts, styles, head
        html = re.sub(r"<(script|style|head)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
        # Remove all tags
        text = re.sub(r"<[^>]+>", " ", html)
        # Collapse whitespace
        text = re.sub(r"\s{2,}", "\n", text).strip()
        # Limit to 4000 chars to avoid overflow
        if len(text) > 4000:
            text = text[:4000] + "\n\n[...content truncated...]"
        return text
    except urllib.error.HTTPError as e:
        return f"HTTP Error {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return f"URL Error: {str(e.reason)}"
    except Exception as e:
        return f"Browse error: {str(e)}"


@tool("get_page_links")
def get_page_links(url: str) -> str:
    """Extract all hyperlinks from a webpage. Input: a full URL. Returns a list of links found."""
    try:
        import re
        if not url.startswith(("http://", "https://")):
            return "Error: URL must start with http:// or https://"

        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; AgentBot/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        links = re.findall(r'href=["\']([^"\'#>]+)["\']', html, re.IGNORECASE)
        # Make relative URLs absolute
        base = "/".join(url.split("/")[:3])
        abs_links = []
        seen = set()
        for link in links:
            if link.startswith("http"):
                abs_links.append(link)
            elif link.startswith("/"):
                abs_links.append(base + link)
            if abs_links and abs_links[-1] not in seen:
                seen.add(abs_links[-1])
        abs_links = list(seen)[:30]
        return "\n".join(abs_links) if abs_links else "No links found."
    except Exception as e:
        return f"Error extracting links: {str(e)}"


@tool("http_get")
def http_get(url: str) -> str:
    """Make an HTTP GET request to any URL and return the raw response text (useful for APIs). Input: URL."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "AgentBot/1.0", "Accept": "application/json, text/plain, */*"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read().decode("utf-8", errors="replace")
        if len(data) > 3000:
            data = data[:3000] + "\n[...truncated...]"
        return data
    except Exception as e:
        return f"HTTP GET error: {str(e)}"


# ─────────────────────────────────────────────
#  FILE SYSTEM TOOLS
# ─────────────────────────────────────────────

# Safe workspace — all file operations are sandboxed here
WORKSPACE = os.path.join(os.path.expanduser("~"), "agent_workspace")
os.makedirs(WORKSPACE, exist_ok=True)


def _safe_path(file_path: str) -> str:
    """Resolve path inside workspace, raise if escaping."""
    abs_path = os.path.realpath(os.path.join(WORKSPACE, file_path))
    if not abs_path.startswith(os.path.realpath(WORKSPACE)):
        raise ValueError(f"Access denied: path outside workspace — {file_path}")
    return abs_path


@tool("read_file")
def read_file(file_path: str) -> str:
    """Read the content of a file from the agent workspace. Input: relative file path (e.g. notes.txt)."""
    try:
        path = _safe_path(file_path)
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except ValueError as e:
        return str(e)
    except FileNotFoundError:
        return f"File not found: {file_path}"
    except Exception as e:
        return f"Error reading file: {str(e)}"


@tool("write_file")
def write_file(file_path: str, content: str) -> str:
    """Write content to a file in the agent workspace. Input: relative file path and content to write."""
    try:
        path = _safe_path(file_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully wrote to {file_path}"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Error writing file: {str(e)}"


@tool("append_file")
def append_file(file_path: str, content: str) -> str:
    """Append content to an existing file in the agent workspace. Input: relative file path and content."""
    try:
        path = _safe_path(file_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully appended to {file_path}"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Error appending file: {str(e)}"


@tool("delete_file")
def delete_file(file_path: str) -> str:
    """Delete a file from the agent workspace. Input: relative file path."""
    try:
        path = _safe_path(file_path)
        if os.path.exists(path):
            os.remove(path)
            return f"Deleted: {file_path}"
        return f"File not found: {file_path}"
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Error deleting file: {str(e)}"


@tool("list_files")
def list_files(directory: str = ".") -> str:
    """List files and folders in the agent workspace directory. Input: relative subdirectory path (default: root)."""
    try:
        path = _safe_path(directory)
        if not os.path.isdir(path):
            return f"Not a directory: {directory}"
        entries = os.listdir(path)
        if not entries:
            return "Empty directory."
        lines = []
        for entry in sorted(entries):
            full = os.path.join(path, entry)
            kind = "DIR" if os.path.isdir(full) else "FILE"
            size = os.path.getsize(full) if os.path.isfile(full) else "-"
            lines.append(f"[{kind}] {entry}  ({size} bytes)" if kind == "FILE" else f"[{kind}] {entry}/")
        return "\n".join(lines)
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Error listing directory: {str(e)}"


# ─────────────────────────────────────────────
#  UTILITY TOOLS
# ─────────────────────────────────────────────

@tool("calculator")
def calculator(expression: str) -> str:
    """Evaluate a safe mathematical expression. Input: math expression as string (e.g. '2 + 2 * 10')."""
    try:
        # Only allow safe characters
        import re
        if re.search(r"[^0-9+\-*/().,\s%]", expression):
            return "Error: Only basic math operators (+, -, *, /, %, parentheses, numbers) are allowed."
        result = eval(expression, {"__builtins__": {}})  # noqa: S307
        return str(result)
    except ZeroDivisionError:
        return "Error: Division by zero."
    except Exception as e:
        return f"Calculation error: {str(e)}"


@tool("get_current_datetime")
def get_current_datetime(timezone: str = "UTC") -> str:
    """Get the current date and time. Input: timezone name (e.g. 'UTC', 'Asia/Dhaka'). Defaults to UTC."""
    try:
        from datetime import datetime, timezone as tz
        now = datetime.now(tz.utc)
        try:
            import zoneinfo
            zone = zoneinfo.ZoneInfo(timezone)
            now = now.astimezone(zone)
        except Exception:
            pass
        return now.strftime(f"%Y-%m-%d %H:%M:%S %Z ({timezone})")
    except Exception as e:
        return f"Datetime error: {str(e)}"


@tool("summarize_text")
def summarize_text(text: str) -> str:
    """Extract and summarize key points from a long block of text. Input: the text to summarize."""
    if len(text) < 200:
        return text
    sentences = text.replace("\n", " ").split(". ")
    # Simple extractive summary — first, last, and every Nth sentence
    step = max(1, len(sentences) // 8)
    selected = sentences[:2] + sentences[2:-2:step] + sentences[-2:]
    seen = set()
    deduped = []
    for s in selected:
        if s.strip() and s not in seen:
            seen.add(s)
            deduped.append(s.strip())
    return ". ".join(deduped[:12]) + ("..." if len(sentences) > 12 else "")


@tool("save_lesson")
def save_lesson(topic: str, lesson: str) -> str:
    """Permanently remember something for future tasks: a fact, a fix to an error,
    a shortcut, a user preference, or a lesson learned from a mistake. Use this whenever
    you discover something useful that should be remembered next time (e.g. 'topic: deepseek_quota',
    'lesson: DeepSeek account ran out of balance on 2026-06-12, switched to Gemini').
    Input: topic (short key, e.g. 'telegram_network_fix') and lesson (the detail to remember)."""
    try:
        import memory
        memory.add_lesson(topic, lesson)
        return f"Remembered under topic '{topic}'."
    except Exception as e:
        return f"Could not save lesson: {str(e)}"
