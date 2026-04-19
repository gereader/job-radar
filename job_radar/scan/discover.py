"""`jr portals discover` — detect ATS slugs on career pages via Playwright.

Many companies run Greenhouse/Ashby/Lever under the hood but link out from
their own domain. We visit each `source: manual` entry, look for:
  - anchor hrefs containing boards.greenhouse.io / ashbyhq.com / lever.co
  - iframe src pointing at same
  - inline scripts with `data-board="..."` or similar

Upgrades the portals.yml in place. Runs in batches so a stall on one URL
doesn't block the rest.
"""

from __future__ import annotations

import asyncio
import re

import yaml
from rich.console import Console
from rich.progress import Progress

from ..config import Config

console = Console()


_ATS_PATTERNS = [
    (re.compile(r"boards(?:-api)?\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)"), "greenhouse"),
    (re.compile(r"job-boards\.greenhouse\.io/([A-Za-z0-9_-]+)"), "greenhouse"),
    (re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_-]+)"), "ashby"),
    (re.compile(r"api\.ashbyhq\.com/posting-api/job-board/([A-Za-z0-9_-]+)"), "ashby"),
    (re.compile(r"jobs\.lever\.co/([A-Za-z0-9_-]+)"), "lever"),
    (re.compile(r"apply\.workable\.com/([A-Za-z0-9_-]+)"), "workable"),
]


async def _discover_one(browser, entry: dict, timeout_ms: int = 15000) -> tuple[str, str] | None:
    """Return (source, slug) if we found one, else None."""
    url = entry.get("careers_url") or ""
    if not url:
        return None
    context = await browser.new_context(user_agent=(
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ))
    page = await context.new_page()
    found: tuple[str, str] | None = None
    try:
        # Capture every network request URL so we catch API hits the page
        # fires even if the embed is lazy-loaded.
        seen_urls: list[str] = []

        def on_request(req):
            seen_urls.append(req.url)
        page.on("request", on_request)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            await page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:
            pass  # even partial loads often include the ATS URL

        html = await page.content()
        haystack = html + "\n" + "\n".join(seen_urls)
        for pat, src in _ATS_PATTERNS:
            m = pat.search(haystack)
            if m:
                found = (src, m.group(1))
                break
    finally:
        await context.close()
    return found


async def _run_discovery(entries: list[dict], concurrency: int) -> list[dict]:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise RuntimeError(
            "playwright not installed. `pip install -e '.[playwright]' && "
            "playwright install chromium`"
        )
    updated = list(entries)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        sem = asyncio.Semaphore(concurrency)

        async def work(i, entry):
            async with sem:
                try:
                    hit = await _discover_one(browser, entry)
                except Exception:
                    hit = None
                if hit:
                    src, slug = hit
                    updated[i]["source"] = src
                    updated[i]["slug"] = slug
                    updated[i]["enabled"] = entry.get("enabled", True)
                return hit

        with Progress(console=console, transient=True) as progress:
            task = progress.add_task("discovering", total=len(entries))
            coros = [work(i, e) for i, e in enumerate(entries)]
            results = []
            for coro in asyncio.as_completed(coros):
                results.append(await coro)
                progress.advance(task)
        await browser.close()

    resolved = sum(1 for r in results if r)
    console.print(
        f"[green]discovery[/green] resolved {resolved} / {len(entries)} entries"
    )
    return updated


def run_discover(batch: int = 50, concurrency: int = 8, only_manual: bool = True) -> None:
    cfg = Config.load()
    if not cfg.portals_path.exists():
        console.print(f"[red]{cfg.portals_path} missing[/red]")
        return
    data = yaml.safe_load(cfg.portals_path.read_text()) or {}
    companies = data.get("companies", [])

    def is_candidate(c):
        return (only_manual and c.get("source") == "manual") or not only_manual

    candidates_idx = [i for i, c in enumerate(companies) if is_candidate(c)]
    if not candidates_idx:
        console.print("no candidates to discover.")
        return

    total = len(candidates_idx)
    processed = 0
    changed = 0
    while processed < total:
        slice_ = candidates_idx[processed : processed + batch]
        batch_entries = [companies[i] for i in slice_]
        console.print(f"batch {processed // batch + 1}: {len(batch_entries)} companies")
        updated_slice = asyncio.run(_run_discovery(batch_entries, concurrency))
        for local_i, global_i in enumerate(slice_):
            if updated_slice[local_i].get("source") != companies[global_i].get("source"):
                companies[global_i] = updated_slice[local_i]
                changed += 1
        processed += len(slice_)

        # Persist after each batch so interruptions aren't total losses.
        data["companies"] = companies
        cfg.portals_path.write_text(yaml.safe_dump(data, sort_keys=False, width=120))

    console.print(f"[green]done[/green] upgraded {changed} entries across {total} candidates")
