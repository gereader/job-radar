"""Opt-in auto-trigger hooks for expensive LLM calls.

The user is always asked before we spend. This module is the policy layer
that decides *when to ask*, based on score thresholds in profile.yml.
"""

from __future__ import annotations

from rich.console import Console
from rich.prompt import Confirm

from ..config import Config

console = Console()


def maybe_research_after_triage(conn, cfg: Config, job_id: int, triage_json: dict) -> None:
    threshold = (cfg.profile.get("scoring") or {}).get("research_threshold", 4.0)
    score = triage_json.get("score_0_5")
    if score is None or float(score) < float(threshold):
        return
    if conn.execute(
        "SELECT 1 FROM llm_usage WHERE job_id = ? AND operation = 'research' LIMIT 1",
        (job_id,),
    ).fetchone():
        return
    if Confirm.ask(
        f"job {job_id} scored {score}/5 → run company research now?", default=False
    ):
        from .research import run_research

        run_research(job_id)


def maybe_interview_prep_on_status(conn, cfg: Config, app_id: int, new_status: str) -> None:
    if new_status not in ("Applied", "Responded", "Interview"):
        return
    if conn.execute(
        "SELECT 1 FROM llm_usage WHERE app_id = ? AND operation = 'interview' LIMIT 1",
        (app_id,),
    ).fetchone():
        return
    if Confirm.ask(
        f"application {app_id} status → {new_status}. generate interview prep?",
        default=new_status == "Interview",
    ):
        from .interview import run_interview_prep

        run_interview_prep(app_id)
