# site-analysis

Analysis/redesign of https://www.ruttyandmorris.com requested in task
`claude/analyze-redesign-website-9udYM`.

## Status

**Blocked at Phase 1.** The Claude Code session that started this task cannot
reach the public internet — the sandbox egress proxy rejects every external
host with `HTTP 403 Host not in allowlist` (verified against the target,
`google.com`, and `example.com`). So no pages have been crawled yet and no
analysis has been written. Writing an analysis of a site that wasn't actually
fetched would be fabrication, so that step is deferred until real HTML exists
on disk.

What *is* committed:

- `crawl.py` — the Phase 1 crawler, ready to run. requests + BeautifulSoup,
  depth 3, same-origin only, 1s delay, `robots.txt`-aware, logs failures and
  continues, detects SPA shells and warns.
- This README.

## How to unblock

Pick one:

1. **Run the crawler locally** and commit the output:
   ```bash
   python -m pip install requests beautifulsoup4
   python site-analysis/crawl.py
   git add site-analysis/original site-analysis/sitemap.txt site-analysis/fetch.log
   git commit -m "Add crawled site-analysis original/ snapshot"
   git push
   ```
   Then ping Claude and it will pick up Phase 2 from the committed HTML.

2. **Paste a handful of pages' HTML** into `site-analysis/original/pages/`
   manually (homepage + top conversion pages is enough to start).

3. **Allow outbound access** to `www.ruttyandmorris.com` in the sandbox and
   re-run the task — Claude will crawl, analyse, redesign, and report in one
   pass.

## Layout after Phase 1 runs

```
site-analysis/
  crawl.py
  sitemap.txt              # all URLs discovered
  fetch.log                # per-request log incl. failures
  original/
    pages/<slug>.html      # one file per crawled page
    assets/<hash>_<name>   # css / js / images
```

## Subsequent phases (planned)

- Phase 2 — `analysis.md`: design/visual hierarchy, UX/IA, copy, a11y,
  static-perf hints, SEO, mobile responsiveness; impact-ranked per category.
- **Pause** for user to re-prioritise.
- Phase 3 — `redesigned/`: static HTML rebuild of the 3–5 top pages, each with
  a sibling `CHANGES.md`.
- Phase 4 — `REPORT.md`: exec summary, before/after, unimplemented
  recommendations, prioritised roadmap.

## Caveats flagged early

- If `crawl.py` gets 403s from Cloudflare/WAF on your machine as well, the
  site is bot-gated. Switch to Playwright (`playwright install chromium`,
  use `page.goto(url); page.content()`) — I can rewrite `crawl.py` against
  Playwright once we confirm that's needed.
- If the crawler warns `SPA shell detected`, same fix: Playwright renders
  JS, `requests` doesn't.
