"""
Crawler for the site-analysis/redesign task.

Starts at TARGET, follows same-origin links up to MAX_DEPTH, writes each page's
raw HTML under site-analysis/original/pages/<slug>.html, downloads referenced
CSS/JS/images under site-analysis/original/assets/, and writes a sitemap plus
a fetch log.

Fetchers
- `requests` (default on --fetcher=requests): fast, no browser, blocked by WAFs.
- `playwright` (on --fetcher=playwright): real headless Chromium, bypasses most
  bot heuristics, handles SPAs. Requires `playwright install chromium`.
- `auto` (default): try `requests` for the homepage; if it returns 403 / a
  Cloudflare challenge / an SPA shell, switch to `playwright` for all pages.
  Assets are always downloaded via `requests` (static bytes).

Usage (from the repo root):

    python -m pip install requests beautifulsoup4 playwright
    python -m playwright install --with-deps chromium   # only if using playwright
    python site-analysis/crawl.py                       # defaults: target above, auto

CLI:

    --target URL          (default: https://www.ruttyandmorris.com/)
    --fetcher MODE        requests | playwright | auto  (default: auto)
    --max-depth N         (default: 3)
    --delay SECONDS       (default: 1.0)
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Protocol
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


DEFAULT_TARGET = "https://www.ruttyandmorris.com/"
DEFAULT_MAX_DEPTH = 3
DEFAULT_DELAY = 1.0
REQUEST_TIMEOUT = 25
# Real Chrome UA — many WAFs reject anything that looks like a bot.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
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


class PageFetcher(Protocol):
    def fetch_html(self, url: str) -> tuple[int, str] | None: ...
    def close(self) -> None: ...


class RequestsFetcher:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": BROWSER_UA,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        })

    def fetch_html(self, url: str) -> tuple[int, str] | None:
        try:
            resp = self.session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        except requests.RequestException:
            return None
        return resp.status_code, resp.text

    def close(self) -> None:
        self.session.close()


class PlaywrightFetcher:
    """Renders pages in real headless Chromium. Lazy-imports Playwright."""

    def __init__(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise SystemExit(
                "Playwright not installed. Run: "
                "python -m pip install playwright && "
                "python -m playwright install --with-deps chromium"
            ) from exc
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._context = self._browser.new_context(user_agent=BROWSER_UA)

    def fetch_html(self, url: str) -> tuple[int, str] | None:
        page = self._context.new_page()
        try:
            response = page.goto(url, wait_until="networkidle", timeout=30000)
            status = response.status if response else 0
            html = page.content()
            return status, html
        except Exception:  # noqa: BLE001 — playwright raises many types
            return None
        finally:
            page.close()

    def close(self) -> None:
        self._context.close()
        self._browser.close()
        self._pw.stop()


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


def fetch_asset(session: requests.Session, url: str) -> bytes | None:
    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
    except requests.RequestException:
        return None
    if resp.status_code >= 400:
        return None
    return resp.content


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
    return len(text) < 250 and bool(soup.find(id=re.compile(r"root|app|__next")))


def looks_blocked(status: int, html: str) -> bool:
    """True if the response looks like a WAF challenge / bot block."""
    if status in (401, 403, 429, 503):
        return True
    lowered = html.lower()
    needles = (
        "just a moment",            # Cloudflare challenge title
        "attention required",        # Cloudflare / Sucuri
        "cf-chl-bypass",             # Cloudflare challenge form
        "checking your browser",     # generic challenge
        "captcha-delivery.com",      # DataDome
        "access denied",             # generic WAF
    )
    return any(n in lowered for n in needles)


def build_fetcher(mode: str, logger: logging.Logger) -> PageFetcher:
    if mode == "playwright":
        logger.info("fetcher=playwright")
        return PlaywrightFetcher()
    logger.info("fetcher=requests")
    return RequestsFetcher()


def crawl(
    target: str,
    max_depth: int,
    delay: float,
    fetcher_mode: str,
) -> int:
    setup_dirs()
    logger = setup_logging()

    origin_host = urlparse(target).netloc.lower()

    rp = RobotFileParser()
    robots_url = urljoin(target, "/robots.txt")
    try:
        rp.set_url(robots_url)
        rp.read()
        logger.info("robots.txt loaded from %s", robots_url)
    except Exception as exc:  # pragma: no cover
        logger.warning("robots.txt unreadable (%s); assuming allow-all", exc)

    initial_mode = "requests" if fetcher_mode == "auto" else fetcher_mode
    fetcher: PageFetcher = build_fetcher(initial_mode, logger)
    asset_session = requests.Session()
    asset_session.headers.update({"User-Agent": BROWSER_UA})

    state = CrawlState()
    queue: list[tuple[str, int]] = [(target, 0)]
    upgraded_to_playwright = (initial_mode == "playwright")
    spa_warned = False

    try:
        while queue:
            url, depth = queue.pop(0)
            url, _ = urldefrag(url)
            if url in state.visited_pages:
                continue
            if not same_origin(url, origin_host):
                continue
            if not rp.can_fetch(BROWSER_UA, url):
                logger.info("robots disallow url=%s", url)
                continue

            result = fetcher.fetch_html(url)
            time.sleep(delay)
            if result is None:
                logger.warning("fetch_failed url=%s", url)
                state.failures.append((url, "fetch_failed"))
                state.visited_pages.add(url)
                continue

            status, html = result

            # Auto-upgrade on blocked homepage.
            if (
                fetcher_mode == "auto"
                and not upgraded_to_playwright
                and looks_blocked(status, html)
            ):
                logger.warning(
                    "request blocked (status=%d, challenge=%s) — "
                    "switching to playwright",
                    status,
                    "yes" if status < 400 else "no",
                )
                fetcher.close()
                fetcher = build_fetcher("playwright", logger)
                upgraded_to_playwright = True
                result = fetcher.fetch_html(url)
                time.sleep(delay)
                if result is None:
                    logger.warning("playwright fetch_failed url=%s", url)
                    state.failures.append((url, "fetch_failed_playwright"))
                    state.visited_pages.add(url)
                    continue
                status, html = result

            if status >= 400:
                logger.warning("non-2xx url=%s status=%d", url, status)
                state.failures.append((url, f"status_{status}"))
                state.visited_pages.add(url)
                continue

            state.visited_pages.add(url)

            out = PAGES_DIR / f"{slug_for(url)}.html"
            out.write_text(html, encoding="utf-8")

            if not spa_warned and detect_spa(html) and not upgraded_to_playwright:
                logger.warning(
                    "SPA shell detected at %s — re-run with --fetcher=playwright",
                    url,
                )
                spa_warned = True

            soup = BeautifulSoup(html, "html.parser")
            title = (
                soup.title.string.strip()
                if soup.title and soup.title.string else "(no title)"
            )
            state.page_titles[url] = title
            logger.info("page depth=%d url=%s title=%r", depth, url, title)

            for asset_url in extract_assets(html, url):
                if asset_url in state.downloaded_assets:
                    continue
                state.downloaded_assets.add(asset_url)
                content = fetch_asset(asset_session, asset_url)
                time.sleep(delay)
                if content is None:
                    state.failures.append((asset_url, "asset_failed"))
                    continue
                (ASSETS_DIR / asset_name_for(asset_url)).write_bytes(content)

            if depth < max_depth:
                for link in extract_links(html, url):
                    if same_origin(link, origin_host) and link not in state.visited_pages:
                        queue.append((link, depth + 1))
    finally:
        fetcher.close()
        asset_session.close()

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

    return 0 if state.page_titles else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Site crawler for analysis/redesign")
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument(
        "--fetcher", choices=("auto", "requests", "playwright"), default="auto"
    )
    parser.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    args = parser.parse_args()

    return crawl(
        target=args.target,
        max_depth=args.max_depth,
        delay=args.delay,
        fetcher_mode=args.fetcher,
    )


if __name__ == "__main__":
    raise SystemExit(main())
