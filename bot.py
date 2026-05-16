"""Claude Code Tips Bot — 毎朝Slackに最新のClaude Codeの裏技を投稿するボット"""

import hashlib
import json
import logging
import os
import re
import sys
import time
import urllib.robotparser
from datetime import date
from typing import Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

USER_AGENT = "claude-code-tips-bot/1.0 (+https://github.com/user/claude-code-tips-bot)"
REQUEST_TIMEOUT = 15
RETRY_COUNT = 3
RETRY_BACKOFF = 2.0
MAX_TIPS_PER_RUN = 10
SEEN_TIPS_PATH = "seen_tips.json"
SOURCES_PATH = "sources.json"

CATEGORIES = [
    "ショートカット",
    "スラッシュコマンド",
    "CLI",
    "CLAUDE.md",
    "MCP",
    "エージェント",
]

CLAUDE_CODE_KEYWORDS = [
    "claude code",
    "claude-code",
    "/project",
    "/memory",
    "/compact",
    "mcp",
    "claude.md",
    "slash command",
    "shortcut",
    "agent",
    "anthropic",
    "claude",
    "tip",
    "trick",
    "hack",
    "workflow",
    "productivity",
]

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "ショートカット": ["shortcut", "keybind", "keyboard", "hotkey", "ctrl", "cmd"],
    "スラッシュコマンド": ["slash", "/project", "/memory", "/compact", "/clear", "/help"],
    "CLI": ["cli", "command line", "terminal", "flag", "argument", "--"],
    "CLAUDE.md": ["claude.md", "context file", "system prompt", "project file"],
    "MCP": ["mcp", "model context protocol", "server", "tool use"],
    "エージェント": ["agent", "autonomous", "multi-agent", "orchestrat", "subagent"],
}


# ---------------------------------------------------------------------------
# robots.txt チェック
# ---------------------------------------------------------------------------

def _is_allowed_by_robots(url: str) -> bool:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = urllib.robotparser.RobotFileParser()
    try:
        rp.set_url(robots_url)
        rp.read()
        return rp.can_fetch(USER_AGENT, url)
    except Exception:
        return True  # 取得できない場合は許可扱い


def _get(url: str, as_json: bool = False, extra_headers: Optional[dict] = None) -> Optional[requests.Response]:
    headers = {"User-Agent": USER_AGENT}
    if extra_headers:
        headers.update(extra_headers)
    try:
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp
    except requests.RequestException as exc:
        logger.warning("GET failed url=%s error=%s", url, exc)
        return None


# ---------------------------------------------------------------------------
# カテゴリ推定・スコアリング
# ---------------------------------------------------------------------------

def _guess_category(text: str) -> str:
    lower = text.lower()
    for cat, kws in CATEGORY_KEYWORDS.items():
        if any(kw in lower for kw in kws):
            return cat
    return "CLI"


def _score(tip: dict) -> float:
    text = (tip.get("title", "") + " " + tip.get("summary", "")).lower()
    score = sum(1.0 for kw in CLAUDE_CODE_KEYWORDS if kw in text)
    if tip.get("category") != "CLI":
        score += 0.5
    return score


def _truncate(text: str, max_len: int = 150) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len] if len(text) <= max_len else text[: max_len - 1] + "…"


def _make_tip(title: str, url: str, summary: str, source: str) -> dict:
    category = _guess_category(title + " " + summary)
    tip = {
        "title": title.strip(),
        "url": url.strip(),
        "summary": _truncate(summary),
        "category": category,
        "source": source,
        "score": 0.0,
    }
    tip["score"] = _score(tip)
    return tip


# ---------------------------------------------------------------------------
# スクレイパー群
# ---------------------------------------------------------------------------

def _scrape_devto(source: dict) -> list[dict]:
    url = source["url"]
    if not _is_allowed_by_robots(url):
        logger.info("robots.txt disallows %s", url)
        return []
    resp = _get(url)
    if resp is None:
        return []
    soup = BeautifulSoup(resp.text, "lxml")
    tips: list[dict] = []
    for article in soup.select("article.crayons-story")[:20]:
        title_el = article.select_one("h2 a, h3 a")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        href = title_el.get("href", "")
        if href.startswith("/"):
            href = "https://dev.to" + href
        excerpt_el = article.select_one(".crayons-story__snippet")
        summary = excerpt_el.get_text(strip=True) if excerpt_el else title
        tips.append(_make_tip(title, href, summary, source["name"]))
    return tips


def _scrape_reddit(source: dict) -> list[dict]:
    url = source["url"]
    resp = _get(url, extra_headers={"Accept": "application/json"})
    if resp is None:
        return []
    try:
        data = resp.json()
    except ValueError as exc:
        logger.warning("Reddit JSON parse error: %s", exc)
        return []
    posts = data.get("data", {}).get("children", [])
    tips: list[dict] = []
    for post in posts[:30]:
        pd = post.get("data", {})
        title: str = pd.get("title", "")
        permalink: str = pd.get("permalink", "")
        full_url = "https://www.reddit.com" + permalink if permalink else pd.get("url", "")
        selftext: str = pd.get("selftext", "")
        summary = selftext if selftext else title
        tips.append(_make_tip(title, full_url, summary, source["name"]))
    return tips


def _scrape_github_markdown(source: dict) -> list[dict]:
    url = source["url"]
    resp = _get(url)
    if resp is None:
        return []
    lines = resp.text.splitlines()
    tips: list[dict] = []
    current_section = ""
    for line in lines:
        if line.startswith("## ") or line.startswith("### "):
            current_section = line.lstrip("#").strip()
        elif line.startswith("- ") or line.startswith("* "):
            content = line.lstrip("-* ").strip()
            if len(content) < 10:
                continue
            page_url = "https://github.com/Njengah/claude-code-cheat-sheet"
            summary = f"{current_section}: {content}" if current_section else content
            tips.append(_make_tip(content[:100], page_url, summary, source["name"]))
    return tips


def _scrape_html_generic(source: dict) -> list[dict]:
    url = source["url"]
    if not _is_allowed_by_robots(url):
        logger.info("robots.txt disallows %s", url)
        return []
    resp = _get(url)
    if resp is None:
        return []
    soup = BeautifulSoup(resp.text, "lxml")
    tips: list[dict] = []
    for el in soup.select("article, .post, .entry, h2 a, h3 a")[:20]:
        if el.name in ("h2", "h3"):
            anchor = el.find("a")
            if not anchor:
                continue
            title = anchor.get_text(strip=True)
            href = anchor.get("href", "")
        else:
            anchor = el.find("a")
            title_el = el.find(["h2", "h3", "h1"])
            title = title_el.get_text(strip=True) if title_el else el.get_text(strip=True)[:100]
            href = anchor.get("href", "") if anchor else url
        if not href:
            continue
        if href.startswith("/"):
            parsed = urlparse(url)
            href = f"{parsed.scheme}://{parsed.netloc}{href}"
        summary_el = el.find(["p", ".excerpt", ".summary"])
        summary = summary_el.get_text(strip=True) if summary_el else title
        if title:
            tips.append(_make_tip(title, href, summary, source["name"]))
    return tips


_SCRAPER_MAP = {
    "html": _scrape_html_generic,
    "json": _scrape_reddit,
    "markdown": _scrape_github_markdown,
}


# ---------------------------------------------------------------------------
# 公開API: collect / filter / select / format / post / update / main
# ---------------------------------------------------------------------------

def collect_tips() -> list[dict]:
    try:
        with open(SOURCES_PATH, encoding="utf-8") as f:
            sources = json.load(f)
    except (OSError, ValueError) as exc:
        logger.error("Failed to load sources.json: %s", exc)
        return []

    all_tips: list[dict] = []
    for source in sources:
        try:
            scraper = _SCRAPER_MAP.get(source.get("type", "html"), _scrape_html_generic)
            tips = scraper(source)
            logger.info("Collected %d tips from %s", len(tips), source["name"])
            all_tips.extend(tips)
        except Exception as exc:
            logger.warning("Source %s failed: %s", source.get("name"), exc)
    return all_tips


def filter_seen(tips: list[dict]) -> list[dict]:
    try:
        with open(SEEN_TIPS_PATH, encoding="utf-8") as f:
            seen: dict = json.load(f)
    except (OSError, ValueError):
        seen = {}

    def url_hash(url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()

    return [t for t in tips if url_hash(t["url"]) not in seen]


def select_best(tips: list[dict], n: int = MAX_TIPS_PER_RUN) -> list[dict]:
    scored = sorted(tips, key=lambda t: t["score"], reverse=True)
    return scored[:n]


def format_message(tip: dict, index: int, total: int, today: str) -> dict:
    header = f"🧠 Claude Code Tips — 今日の{total}本 ({today})  [{index}/{total}]"
    text = (
        f"{header}\n"
        f"💡 {tip['title']}\n"
        f"{tip['summary']}\n"
        f"📂 カテゴリ: {tip['category']}\n"
        f"🔗 ソース: {tip['url']}"
    )
    return {
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": text},
            }
        ]
    }


def post_to_slack(payload: dict, webhook_url: str) -> bool:
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            resp = requests.post(
                webhook_url,
                json=payload,
                timeout=REQUEST_TIMEOUT,
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code == 200:
                return True
            logger.warning("Slack returned status=%d attempt=%d", resp.status_code, attempt)
        except requests.RequestException as exc:
            logger.warning("Slack POST error=%s attempt=%d", exc, attempt)
        if attempt < RETRY_COUNT:
            time.sleep(RETRY_BACKOFF ** attempt)
    return False


def update_seen(tips: list[dict]) -> dict:
    try:
        with open(SEEN_TIPS_PATH, encoding="utf-8") as f:
            existing: dict = json.load(f)
    except (OSError, ValueError):
        existing = {}

    today_str = date.today().isoformat()
    additions = {
        hashlib.sha256(t["url"].encode()).hexdigest(): today_str
        for t in tips
    }
    updated = {**existing, **additions}

    with open(SEEN_TIPS_PATH, "w", encoding="utf-8") as f:
        json.dump(updated, f, ensure_ascii=False, indent=2)
    return updated


def _post_error_notification(message: str, webhook_url: str) -> None:
    payload = {"text": message}
    post_to_slack(payload, webhook_url)


def main() -> None:
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        logger.error("SLACK_WEBHOOK_URL is not set")
        sys.exit(1)

    today_str = date.today().strftime("%Y/%m/%d")

    try:
        all_tips = collect_tips()
        new_tips = filter_seen(all_tips)
        logger.info("New tips after dedup: %d", len(new_tips))

        if not new_tips:
            payload = {"text": f"📭 本日（{today_str}）は新しいClaude Code tipsが見つかりませんでした。"}
            post_to_slack(payload, webhook_url)
            return

        best = select_best(new_tips)
        total = len(best)
        failed_count = 0

        for i, tip in enumerate(best, start=1):
            payload = format_message(tip, i, total, today_str)
            success = post_to_slack(payload, webhook_url)
            if not success:
                logger.error("Failed to post tip %d/%d: %s", i, total, tip["url"])
                failed_count += 1
            else:
                logger.info("Posted tip %d/%d: %s", i, total, tip["title"])

        update_seen(best)
        logger.info("Updated seen_tips.json with %d entries", total)

        if failed_count > 0:
            logger.error("%d/%d posts failed", failed_count, total)
            sys.exit(1)

    except Exception as exc:
        logger.exception("Unexpected error: %s", exc)
        try:
            _post_error_notification(
                f"⚠️ Claude Code Tips Bot: 本日のtips収集に失敗しました ({today_str})",
                webhook_url,
            )
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
