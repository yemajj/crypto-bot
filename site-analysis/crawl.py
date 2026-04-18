"""
Crawler for the site-analysis/redesign task.

Starts at TARGET, follows same-origin links up to MAX_DEPTH, writes each page's
raw HTML under site-analysis/original/pages/<slug>.html, downloads referenced
CSS/JS/images under site-analysis/original/assets/, and writes a sitemap plus
a fetch log.

Usage (from the repo root):

    python -m pip install requests beautifulsoup4
    python site-analysis/crawl.py

Notes
- Respects robots.txt via urllib.robotparser.
- 1s delay between fetches; single thread.
- Skips non-HTML URLs for link extraction, but still downloads referenced
  CSS/JS/images once.
- Failed fetches are logged to fetch.log and skipped; the crawl continues.
- If the site turns out to be a JS-rendered SPA (HTML body is a near-empty
  root div), the script prints a warning. Swap in Playwright at that point.
"""
from __future__ import annotations

import hashlib
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.robotparser import RobotFileParser

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError as exc:  # pragma: no cover
    sys.stderr.write(
        "Missing deps. Run: python -m pip install requests beautifulsoup4\n"
    )
    raise SystemExit(1) from exc


TARGET = "https://www.ruttyandmorris.com/"
MAX_DEPTH = 3
DELAY_SECONDS = 1.0
REQUEST_TIMEOUT = 20
USER_AGENT = (
    "SiteAnalysisBot/1.0 (+redesign-analysis; contact: owner) "
    "Mozilla/5.0 (compatible)"
)

ROOT = Path(__file__).resolve().parent
ORIGINAL = ROOT / "original"
PAGES_DIR = ORIGINAL / "pages"
ASSETS_DIR = ORIGINAL / "assets"
SITEMAP = ROOT / "sitemap.txt"
FETCH_LOG = ROOT / "fetch.log"


@dataclass
class CrawlState:
    visited_pages: set[str] = field(default_factory=set)
    downloaded_assets: set[str] = field(default_factory=set)
    page_titles: dict[str, str] = field(default_factory=dict)
    failures: list[tuple[str, str]] = field(default_factory=list)


def setup_dirs() -> None:
    PAGES_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("crawl")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.FileHandler(FETCH_LOG, mode="w")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


def same_origin(url: str, origin_host: str) -> bool:
    host = urlparse(url).netloc.lower()
    if not host:
        return False
    # Treat www.host and host as same origin.
    return host == origin_host or host == origin_host.removeprefix("www.") or \
        ("www." + host) == origin_host


def slug_for(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or "/"
    if path.endswith("/"):
        path += "index"
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", path.strip("/")) or "index"
    if parsed.query:
        slug += "__" + hashlib.md5(parsed.query.encode()).hexdigest()[:8]
    return slug


def asset_name_for(url: str) -> str:
    parsed = urlparse(url)
    base = Path(parsed.path).name or "asset"
    digest = hashlib.md5(url.encode()).hexdigest()[:8]
    return f"{digest}_{base}"


def fetch(session: requests.Session, url: str, logger: logging.Logger) -> requests.Response | None:
    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
    except requests.RequestException as exc:
        logger.warning("fetch failed url=%s err=%s", url, exc)
        return None
    if resp.status_code >= 400:
        logger.warning("fetch non-2xx url=%s status=%s", url, resp.status_code)
        return None
    return resp


def extract_links(html: str, base_url: str) -> Iterable[str]:
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href, _ = urldefrag(urljoin(base_url, a["href"]))
        if href.startswith(("http://", "https://")):
            yield href


def extract_assets(html: str, base_url: str) -> Iterable[str]:
    soup = BeautifulSoup(html, "html.parser")
    for tag, attr in (
        ("link", "href"),
        ("script", "src"),
        ("img", "src"),
        ("source", "src"),
    ):
        for el in soup.find_all(tag):
            val = el.get(attr)
            if not val:
                continue
            url = urljoin(base_url, val)
            if url.startswith(("http://", "https://")):
                yield url


def detect_spa(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    body = soup.body
    if not body:
        return True
    text = body.get_text(strip=True)
    # SPA shells typically render <250 chars of text server-side.
    return len(text) < 250 and bool(soup.find(id=re.compile(r"root|app|__next")))


def crawl() -> None:
    setup_dirs()
    logger = setup_logging()

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})

    origin_host = urlparse(TARGET).netloc.lower()

    # robots.txt
    rp = RobotFileParser()
    robots_url = urljoin(TARGET, "/robots.txt")
    try:
        rp.set_url(robots_url)
        rp.read()
        logger.info("robots.txt loaded from %s", robots_url)
    except Exception as exc:  # pragma: no cover
        logger.warning("robots.txt unreadable (%s); assuming allow-all", exc)

    state = CrawlState()
    queue: list[tuple[str, int]] = [(TARGET, 0)]
    spa_warned = False

    while queue:
        url, depth = queue.pop(0)
        url, _ = urldefrag(url)
        if url in state.visited_pages:
            continue
        if not same_origin(url, origin_host):
            continue
        if not rp.can_fetch(USER_AGENT, url):
            logger.info("robots disallow url=%s", url)
            continue

        resp = fetch(session, url, logger)
        time.sleep(DELAY_SECONDS)
        if resp is None:
            state.failures.append((url, "fetch_failed"))
            state.visited_pages.add(url)
            continue

        state.visited_pages.add(url)
        ctype = resp.headers.get("Content-Type", "")
        if "html" not in ctype.lower():
            logger.info("skip non-html url=%s ctype=%s", url, ctype)
            continue

        html = resp.text
        out = PAGES_DIR / f"{slug_for(url)}.html"
        out.write_text(html, encoding="utf-8")

        if not spa_warned and detect_spa(html):
            logger.warning(
                "SPA shell detected at %s — HTML has almost no body text. "
                "Switch to Playwright to get rendered content.",
                url,
            )
            spa_warned = True

        soup = BeautifulSoup(html, "html.parser")
        title = (soup.title.string.strip() if soup.title and soup.title.string else "(no title)")
        state.page_titles[url] = title
        logger.info("page depth=%d url=%s title=%r", depth, url, title)

        # Assets (download once, regardless of depth).
        for asset_url in extract_assets(html, url):
            if asset_url in state.downloaded_assets:
                continue
            state.downloaded_assets.add(asset_url)
            aresp = fetch(session, asset_url, logger)
            time.sleep(DELAY_SECONDS)
            if aresp is None:
                state.failures.append((asset_url, "asset_failed"))
                continue
            (ASSETS_DIR / asset_name_for(asset_url)).write_bytes(aresp.content)

        # Internal links, deeper.
        if depth < MAX_DEPTH:
            for link in extract_links(html, url):
                if same_origin(link, origin_host) and link not in state.visited_pages:
                    queue.append((link, depth + 1))

    SITEMAP.write_text(
        "\n".join(sorted(state.visited_pages)) + "\n", encoding="utf-8"
    )

    print("\n--- summary ---")
    print(f"pages fetched: {len(state.page_titles)}")
    print(f"assets downloaded: {len(state.downloaded_assets)}")
    print(f"failures: {len(state.failures)} (see fetch.log)")
    print("titles:")
    for url, title in sorted(state.page_titles.items()):
        print(f"  {url}  ::  {title}")


if __name__ == "__main__":
    crawl()
