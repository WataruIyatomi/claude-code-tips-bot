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
from deep_translator import GoogleTranslator

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
    "/clear",
    "/help",
    "/review",
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

TIP_INDICATORS = [
    "how to",
    "tip",
    "trick",
    "shortcut",
    "you can",
    "use ",
    "using ",
    "enable",
    "command",
    "workflow",
    "cheat",
    "guide",
    "tutorial",
    "best practice",
    "pro tip",
    "hidden",
    "feature",
    "prompt",
    "slash",
    "config",
    "setup",
]

NON_TIP_INDICATORS = [
    "rant",
    "complaint",
    "broken",
    "not working",
    "bug",
    "issue",
    "problem",
    "error",
    "fail",
    "anyone else",
    "why does",
    "why is",
    "is it just me",
    "frustrated",
    "disappointed",
    "concerning",
    "cracked",
    "sleep",
    "bedtime",
    "slower",
    "worse",
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
    score += sum(0.8 for kw in TIP_INDICATORS if kw in text)
    score -= sum(2.0 for kw in NON_TIP_INDICATORS if kw in text)
    if tip.get("category") != "CLI":
        score += 0.5
    return score


def _is_tip_content(title: str, summary: str) -> bool:
    text = (title + " " + summary).lower()
    has_tip = any(kw in text for kw in TIP_INDICATORS)
    has_noise = sum(1 for kw in NON_TIP_INDICATORS if kw in text) >= 2
    return has_tip and not has_noise


def _clean_text(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # [label](url) → label
    text = re.sub(r"https?://\S+", "", text)               # 生URL除去
    text = re.sub(r"[*_`#~]", "", text)                    # Markdown記号除去
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _truncate(text: str, max_len: int = 150) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len] if len(text) <= max_len else text[: max_len - 1] + "…"


def _make_concrete_summary(section: str, content: str) -> str:
    """GitHub Cheatsheet の箇条書きを「〜することができます」形式に整形する"""
    content = _clean_text(content)
    section = _clean_text(section)
    if not content:
        return section
    verb_phrases = ["use ", "run ", "type ", "press ", "add ", "set ", "enable ", "create "]
    lower = content.lower()
    if any(lower.startswith(v) for v in verb_phrases):
        return f"【{section}】{content}"
    return f"【{section}】{content}"


def _translate_to_ja(text: str) -> str:
    if not text or not text.strip():
        return text
    try:
        translator = GoogleTranslator(source="auto", target="ja")
        result = translator.translate(text[:4999])  # API上限5000文字
        return result if result else text
    except Exception as exc:
        logger.warning("Translation failed, using original: %s", exc)
        return text


def _make_tip(title: str, url: str, summary: str, source: str) -> dict:
    clean_title = _clean_text(title.strip())
    clean_summary = _clean_text(summary)
    category = _guess_category(clean_title + " " + clean_summary)
    ja_title = _translate_to_ja(clean_title)
    ja_summary = _truncate(_translate_to_ja(clean_summary))
    tip = {
        "title": ja_title,
        "url": url.strip(),
        "summary": ja_summary,
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
    for post in posts[:50]:
        pd = post.get("data", {})
        title: str = pd.get("title", "")
        permalink: str = pd.get("permalink", "")
        full_url = "https://www.reddit.com" + permalink if permalink else pd.get("url", "")
        selftext: str = pd.get("selftext", "")
        summary = selftext[:500] if selftext else title
        if not _is_tip_content(title, summary):
            continue
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
    page_url = "https://github.com/Njengah/claude-code-cheat-sheet"
    for line in lines:
        if line.startswith("## ") or line.startswith("### "):
            current_section = _clean_text(line.lstrip("#").strip())
        elif line.startswith("- ") or line.startswith("* "):
            content = _clean_text(line.lstrip("-* ").strip())
            if len(content) < 10:
                continue
            # リンクだけの行・リソース紹介行はスキップ
            lower_content = content.lower()
            if content.startswith("http") or lower_content.startswith("resource") or lower_content.startswith("further") or lower_content.startswith("more "):
                continue
            summary = _make_concrete_summary(current_section, content)
            tips.append(_make_tip(content[:100], page_url, summary, source["name"]))
    return tips


def _scrape_hackernews(source: dict) -> list[dict]:
    url = source["url"]
    resp = _get(url, extra_headers={"Accept": "application/json"})
    if resp is None:
        return []
    try:
        data = resp.json()
    except ValueError as exc:
        logger.warning("HN JSON parse error: %s", exc)
        return []
    tips: list[dict] = []
    for hit in data.get("hits", []):
        title: str = hit.get("title", "")
        story_url: str = hit.get("url", "") or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"
        raw_text: str = hit.get("story_text", "") or ""
        if raw_text:
            raw_text = BeautifulSoup(raw_text, "lxml").get_text(strip=True)[:500]
        summary = raw_text or title
        if not _is_tip_content(title, summary):
            continue
        tips.append(_make_tip(title, story_url, summary, source["name"]))
    return tips


def _scrape_zenn(source: dict) -> list[dict]:
    url = source["url"]
    resp = _get(url)
    if resp is None:
        return []
    soup = BeautifulSoup(resp.text, "lxml-xml")
    tips: list[dict] = []
    for item in soup.find_all("item")[:20]:
        title_el = item.find("title")
        link_el = item.find("link")
        desc_el = item.find("description")
        title = title_el.get_text(strip=True) if title_el else ""
        link = link_el.get_text(strip=True) if link_el else ""
        summary = _clean_text(desc_el.get_text(strip=True)) if desc_el else title
        if not title or not link:
            continue
        # Zennは日本語コンテンツなので翻訳をスキップ
        tips.append(_make_tip_ja(title, link, _truncate(summary), source["name"]))
    return tips


def _make_tip_ja(title: str, url: str, summary: str, source: str) -> dict:
    """日本語コンテンツ用: 翻訳をスキップして直接格納する"""
    clean_title = _clean_text(title.strip())
    clean_summary = _clean_text(summary)
    category = _guess_category(clean_title + " " + clean_summary)
    tip = {
        "title": clean_title,
        "url": url.strip(),
        "summary": _truncate(clean_summary),
        "category": category,
        "source": source,
        "score": 0.0,
    }
    tip["score"] = _score(tip)
    return tip


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


def _fetch_article_summary(url: str) -> str:
    """URLのページ本文を取得して要約テキストを返す（翻訳前の生テキスト）"""
    # HackerNewsのコメントページはスキップしてそのURLのまま
    if "news.ycombinator.com/item" in url:
        return ""
    if not _is_allowed_by_robots(url):
        return ""
    resp = _get(url)
    if resp is None:
        return ""
    soup = BeautifulSoup(resp.text, "lxml")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
        tag.decompose()

    # TL;DR / まとめ / ポイント セクションを優先して探す
    tl_dr_patterns = ["tl;dr", "tldr", "まとめ", "ポイント", "概要", "結論", "要約"]
    for heading in soup.find_all(["h2", "h3", "h4", "strong", "b"]):
        text = heading.get_text(strip=True).lower()
        if any(p in text for p in tl_dr_patterns):
            # 直後の要素を取得
            sibling = heading.find_next_sibling()
            if sibling:
                content = sibling.get_text(strip=True)
                if len(content) > 20:
                    return content[:600]

    # 箇条書きの最初のいくつかを収集（tips記事に多い）
    items = soup.find_all("li")
    tip_items = [li.get_text(strip=True) for li in items[:5] if len(li.get_text(strip=True)) > 20]
    if len(tip_items) >= 2:
        return " / ".join(tip_items[:3])[:600]

    # メインコンテンツの最初の段落を使う
    main_el = soup.find("main") or soup.find("article") or soup
    paragraphs = main_el.find_all("p")
    meaningful = [p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 40]
    if meaningful:
        return " ".join(meaningful[:2])[:600]

    return ""


def enrich_with_article_content(tips: list[dict]) -> list[dict]:
    """各tipのURLを実際に取得してsummaryを本文ベースに更新する"""
    enriched: list[dict] = []
    for tip in tips:
        logger.info("Fetching article: %s", tip["title"])
        raw = _fetch_article_summary(tip["url"])
        if raw and len(raw) > 30:
            # GitHub cheatsheetは既に内容が確定しているのでスキップ
            if "github.com/Njengah" not in tip["url"]:
                ja = _truncate(_translate_to_ja(_clean_text(raw)))
                enriched.append({**tip, "summary": ja})
                continue
        enriched.append(tip)
    return enriched


_SCRAPER_MAP = {
    "html": _scrape_html_generic,
    "json": _scrape_reddit,
    "markdown": _scrape_github_markdown,
    "hackernews": _scrape_hackernews,
    "rss": _scrape_zenn,
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
    seen_urls: set[str] = set()
    deduped: list[dict] = []
    for tip in scored:
        if tip["url"] not in seen_urls:
            seen_urls.add(tip["url"])
            deduped.append(tip)
        if len(deduped) >= n:
            break
    return deduped


def format_message(tips: list[dict], today: str) -> dict:
    total = len(tips)
    lines = [f"🧠 *Claude Code Tips — 今日の{total}本* ({today})"]
    for i, tip in enumerate(tips, 1):
        lines.append(
            f"\n{i}. 💡 *{tip['title']}*\n"
            f"　{tip['summary']}\n"
            f"　📂 {tip['category']}　🔗 {tip['url']}"
        )
    return {
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "\n".join(lines)},
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
        best = enrich_with_article_content(best)

        payload = format_message(best, today_str)
        success = post_to_slack(payload, webhook_url)
        if not success:
            logger.error("Failed to post tips to Slack")
            sys.exit(1)

        logger.info("Posted %d tips to Slack", len(best))
        update_seen(best)
        logger.info("Updated seen_tips.json with %d entries", len(best))

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
