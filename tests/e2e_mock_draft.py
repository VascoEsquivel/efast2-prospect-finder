"""Browser end-to-end check: a full mock draft through the search box, with a refresh mid-draft.

Starts its own `streamlit run app.py` on a temporary data dir (your real draft is untouched),
then drives Chromium with Playwright:
  - every pick: type the first 4 letters of the best available player's last name + Enter
  - at pick 71: reload the page, click Resume, and check nothing was lost
  - finish all N x R picks and check the saved file

Usage (needs `pip install playwright` and a Chromium; not part of the pytest run):
    python tests/e2e_mock_draft.py [--teams 12] [--rounds 13] [--chromium /path/to/chrome] [--shots DIR]
"""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

from playwright.sync_api import sync_playwright

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))
import data_fetcher as dfm  # noqa: E402
import valuation as v  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--teams", type=int, default=12)
    ap.add_argument("--rounds", type=int, default=13)
    ap.add_argument("--refresh-at", type=int, default=71)
    ap.add_argument("--chromium", default=None, help="Chromium executable (default: Playwright's)")
    ap.add_argument("--shots", type=Path, default=None, help="folder for screenshots")
    args = ap.parse_args()
    total = args.teams * args.rounds

    runtime = Path(tempfile.mkdtemp(prefix="draft_e2e_"))
    (runtime / "draft_state.json").write_text(json.dumps({
        "schema_version": 1, "settings": {"n_teams": args.teams, "n_rounds": args.rounds, "my_slot": 1,
                                          "team_names": []}, "picks": [], "next_seq": 1}))
    # Pin category scoring: the "intended player" check below ranks by 9-cat value.
    (runtime / "settings.json").write_text(json.dumps({"settings_version": 3, "template": "9-cat"}))
    port = free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "app.py", "--server.headless", "true", "--server.port", str(port),
         "--browser.gatherUsageStats", "false"],
        cwd=APP_DIR, env={**__import__("os").environ, "FANTASY_DATA_DIR": str(runtime)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        run(args, runtime, port, total)
    finally:
        server.terminate()
        shutil.rmtree(runtime, ignore_errors=True)


def run(args, runtime: Path, port: int, total: int) -> None:
    players, _ = v.valuate(dfm.load_projections()[0], v.ValuationSettings(  # the app's default source
        n_teams=args.teams, roster_size=args.rounds, min_gp=10))
    ranked = players.sort_values("rank")["name"].tolist()
    state = lambda: json.loads((runtime / "draft_state.json").read_text())  # noqa: E731

    def shot(page, name):
        if args.shots:
            args.shots.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(args.shots / f"{name}.png"), full_page=name.endswith("board"))

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.chromium) if args.chromium else p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        for _ in range(60):
            try:
                page.goto(f"http://localhost:{port}", wait_until="networkidle")
                break
            except Exception:  # server still starting
                time.sleep(0.5)
        metric = page.locator('[data-testid="stMetricValue"]').first
        search = page.locator(".st-key-pick_search input")
        search.wait_for(timeout=60000)

        drafted: set[str] = set()
        timings, top_hits = [], 0
        for pick in range(1, total + 1):
            if pick == args.refresh_at:
                before = state()["picks"]
                shot(page, "before_refresh")
                page.reload(wait_until="networkidle")
                page.get_by_text("Saved draft found").wait_for(timeout=30000)
                shot(page, "resume_banner")
                page.get_by_role("button", name="▶ Resume draft").click()
                search.wait_for(timeout=30000)
                page.wait_for_function(f"document.querySelector('[data-testid=\"stMetricValue\"]').innerText === '#{pick}'",
                                       timeout=30000)
                after = state()["picks"]
                key = lambda ps: [(x["pick_no"], x["player_id"], x["team"]) for x in ps]  # noqa: E731
                assert key(after) == key(before), "picks changed across refresh"
                print(f"refreshed at pick {pick}: resumed with all {len(after)} picks intact")

            target = next(n for n in ranked if n not in drafted)
            query = target.split()[-1][:4] if " " in target else target[:4]
            old = metric.inner_text()
            if not page.evaluate("!!(document.activeElement && document.activeElement.closest('.st-key-pick_search'))"):
                search.click()
            t0 = time.time()
            page.keyboard.type(query, delay=15)
            page.keyboard.press("Enter")
            page.wait_for_function(
                f"document.querySelector('[data-testid=\"stMetricValue\"]').innerText !== {json.dumps(old)}",
                timeout=20000)
            timings.append(time.time() - t0)
            newest = max(state()["picks"], key=lambda x: x["seq"])
            drafted.add(newest["name"])
            top_hits += newest["name"] == target
            if pick in (1, 2, args.teams, args.teams + 1, total):
                print(f"pick {pick:3d}: typed {query!r:8} -> {newest['name']} (team {newest['team']})")

        shot(page, "final_room")
        page.get_by_role("tab", name="Draft Board").click()
        time.sleep(1.5)
        shot(page, "final_board")
        final_metric = metric.inner_text()
        browser.close()

    picks = state()["picks"]
    per_team = Counter(x["team"] for x in picks)
    print(f"final pick metric: {final_metric}")
    print(f"picks saved: {len(picks)} / {total}; unique players: {len({x['player_id'] for x in picks})}")
    print(f"picks per team: min {min(per_team.values())}, max {max(per_team.values())}")
    print(f"top search suggestion was the intended player: {top_hits}/{total}")
    print(f"seconds per pick (type + Enter + rerun): median {sorted(timings)[len(timings) // 2]:.2f}, max {max(timings):.2f}")
    assert len(picks) == total and len({x["player_id"] for x in picks}) == total
    assert set(per_team.values()) == {args.rounds} and final_metric == "Done"
    print("E2E OK")


if __name__ == "__main__":
    main()
