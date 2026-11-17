"""Export live-run evidence to a committed file.

Live metrics — real tokens, latency, served models, costs — are the product of
a metered quota. A database is the wrong single home for them: one routine
reset already destroyed the record of a successful run. This writes them
somewhere version-controlled.

Reads only. Makes no provider calls, so it is safe to run at any time.

    uv run python scripts/export_live_evidence.py
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.models import LLMCall, Report, ReportClaim, ResearchTask
from app.domain.enums import LLMProvider

LIVE_DATABASE_URL = os.environ.get(
    "LIVE_DATABASE_URL",
    "postgresql+asyncpg://agentflow:agentflow@localhost:5432/agentflow_live",
)
OUTPUT = Path(os.environ.get("LIVE_EVIDENCE_OUT", "../docs/milestones/m3-live-evidence.json"))


async def collect() -> dict[str, Any]:
    engine = create_async_engine(LIVE_DATABASE_URL, poolclass=NullPool)
    try:
        async with AsyncSession(engine) as session:
            tasks = list(
                (
                    await session.scalars(
                        select(ResearchTask)
                        .where(ResearchTask.llm_provider == LLMProvider.OPENROUTER)
                        .order_by(ResearchTask.created_at)
                    )
                ).all()
            )
            calls = list(
                (
                    await session.scalars(
                        select(LLMCall)
                        .where(LLMCall.provider == LLMProvider.OPENROUTER)
                        .order_by(LLMCall.created_at)
                    )
                ).all()
            )
            reports = list(
                (
                    await session.scalars(
                        select(Report).where(Report.task_id.in_([t.id for t in tasks] or [None]))
                    )
                ).all()
            )
            claims = list(
                (
                    await session.scalars(
                        select(ReportClaim).where(
                            ReportClaim.report_id.in_([r.id for r in reports] or [None])
                        )
                    )
                ).all()
            )
    finally:
        await engine.dispose()

    return {
        "exported_at": datetime.now(UTC).isoformat(),
        "note": (
            "Live OpenRouter evidence. Models were free-tier; search was the committed "
            "fixture corpus, so analysis is real model output over synthetic sources."
        ),
        "totals": {
            "tasks": len(tasks),
            "calls": len(calls),
            "tokens_in": sum(c.tokens_in for c in calls),
            "tokens_out": sum(c.tokens_out for c in calls),
            "cost_usd_estimate": round(sum(float(c.cost_usd_estimate or 0) for c in calls), 6),
            "cost_usd_reported": round(sum(float(c.cost_usd_reported or 0) for c in calls), 6),
            "reports": len(reports),
            "claims": len(claims),
            "claims_unsupported": sum(1 for c in claims if c.support_status.value == "unsupported"),
        },
        "tasks": [
            {
                "id": str(t.id),
                "created_at": t.created_at.isoformat(),
                "status": t.status.value,
                "search_mode": t.search_mode.value,
                "llm_provider": t.llm_provider.value,
                "calls_used": t.calls_used,
                "tokens_used": t.tokens_used,
                "cost_usd_estimate": float(t.cost_usd_estimate),
                "error_code": t.error_code,
            }
            for t in tasks
        ],
        "calls": [
            {
                "task_id": str(c.task_id),
                "created_at": c.created_at.isoformat(),
                "node": c.node.value,
                "attempt": c.attempt,
                "model_requested": c.model_requested,
                "model_served": c.model_served,
                "output_mode": c.output_mode.value if c.output_mode else None,
                "tokens_in": c.tokens_in,
                "tokens_out": c.tokens_out,
                "latency_ms": c.latency_ms,
                "cost_usd_estimate": float(c.cost_usd_estimate or 0),
                "cost_usd_reported": (
                    float(c.cost_usd_reported) if c.cost_usd_reported is not None else None
                ),
                "attempt_outcome": c.attempt_outcome.value,
            }
            for c in calls
        ],
    }


async def main() -> int:
    evidence = await collect()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")

    totals = evidence["totals"]
    print(f"wrote {OUTPUT}")
    print(
        f"  tasks={totals['tasks']} calls={totals['calls']} "
        f"tokens={totals['tokens_in']}/{totals['tokens_out']} "
        f"est=${totals['cost_usd_estimate']:.6f}"
    )
    if totals["calls"] == 0:
        print("  (no live calls recorded yet)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
