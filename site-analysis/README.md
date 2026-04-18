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
  continues, auto-falls back to Playwright on WAF 403 / Cloudflare
  challenge / SPA shells.
- `.github/workflows/site-crawl.yml` — `workflow_dispatch` trigger that
  runs `crawl.py` on a GitHub-hosted runner and commits the output back
  to this branch. Triggerable from the GitHub mobile app.
- This README.

## How to unblock (pick one)

### 1. Run it from your phone via GitHub Actions (preferred)

There's a dispatchable workflow at `.github/workflows/site-crawl.yml`.
From the GitHub mobile app: **Actions → "Site Crawl (analysis phase 1)"
→ Run workflow**, pick branch `claude/analyze-redesign-website-9udYM`,
leave the defaults, tap Run. The job:

1. Installs `requests` + `beautifulsoup4` + Playwright Chromium.
2. Runs `crawl.py` in `--fetcher=auto` (falls back to Playwright on
   Cloudflare / WAF / SPA shells automatically).
3. Commits `site-analysis/original/`, `sitemap.txt`, and `fetch.log`
   back to the dispatched branch.
4. Also uploads everything as a downloadable artifact, in case the
   commit step is ever skipped.

Once it lands, start a fresh Claude Code session on that branch and
ask for Phase 2.

### 2. Run the crawler locally

```bash
python -m pip install requests beautifulsoup4 playwright
python -m playwright install --with-deps chromium   # optional; only needed for WAFs / SPAs
python site-analysis/crawl.py                       # --fetcher=auto is default
git add site-analysis/original site-analysis/sitemap.txt site-analysis/fetch.log
git commit -m "Add crawled site-analysis original/ snapshot"
git push
```

### 3. Paste HTML manually

Drop a few pages into `site-analysis/original/pages/` by hand
(homepage + top conversion pages is enough to start Phase 2).

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

- GitHub Actions runner IPs are well-known; some Cloudflare configs block
  them outright even through Playwright. If the workflow run shows
  `fetch_failed` on every URL, fall back to option 2 (run locally from a
  residential IP) or option 3 (paste HTML in).
- If `crawl.py` hits WAFs / Cloudflare / SPA shells, `--fetcher=auto` now
  upgrades to headless Chromium automatically — no code change needed.
