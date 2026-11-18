"""Run the grounding evaluation and write a report.

    uv run python scripts/run_evals.py                  # offline, writes the baseline
    uv run python scripts/run_evals.py --no-write       # print only

Offline by default: the deterministic stub model and the committed fixture
corpus, so it needs no credentials and costs nothing. It needs a database --
the same one the integration tests use.

What it measures is the grounding pipeline, not model quality. See
``evals/__init__.py`` for why that distinction matters.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.adapters.llm.stub import StubLLMClient
from app.adapters.search.fixture import FixtureSearchProvider
from app.core.config import Settings
from app.graph.deps import NodeDeps
from evals.metrics import Metric
from evals.runner import CaseResult, run_all

DATABASE_URL = os.environ.get(
    "EVAL_DATABASE_URL",
    "postgresql+asyncpg://agentflow:agentflow@localhost:5432/agentflow_eval",
)
OUTPUT = Path(os.environ.get("EVAL_OUTPUT", "../docs/evaluation-baseline.json"))


async def _ensure_database() -> None:
    """Create the evaluation database if it does not exist.

    The evaluation keeps its own database so a run cannot disturb the
    integration-test database or the preserved live-evidence one. Creating it
    here means a clean checkout needs one command rather than a documented
    psql incantation.
    """
    import asyncpg

    name = DATABASE_URL.rsplit("/", 1)[-1]
    admin_dsn = DATABASE_URL.rsplit("/", 1)[0].replace("postgresql+asyncpg://", "postgresql://")
    connection = await asyncpg.connect(f"{admin_dsn}/postgres")
    try:
        exists = await connection.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name)
        if not exists:
            # Identifier, so it cannot be parameterised; the name comes from
            # our own configuration, not from user input.
            await connection.execute(f'CREATE DATABASE "{name}"')
            print(f"  created database {name}")
    finally:
        await connection.close()


def _migrate() -> None:
    cfg = Config("alembic.ini")
    cfg.set_main_option(
        "sqlalchemy.url", DATABASE_URL.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    )
    command.upgrade(cfg, "head")


async def _offline_deps() -> tuple[NodeDeps, Any]:
    settings = Settings(
        _env_file=None,
        app_env="test",
        llm_provider="stub",
        search_provider="fixture",
        database_url=DATABASE_URL,
    )
    engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
    deps = NodeDeps(
        settings=settings,
        session_factory=async_sessionmaker(engine, expire_on_commit=False),
        llm=StubLLMClient(),
        search=FixtureSearchProvider(),
    )
    return deps, engine


def _render(results: list[CaseResult]) -> str:
    lines = [
        "",
        f"{'case':<22} {'src':>4} {'clm':>4} {'cite':>6} {'grnd':>6} "
        f"{'uniq':>6} {'unsup':>6} {'util':>6}  status",
        "-" * 86,
    ]

    def pct(by_name: dict[str, Metric], name: str) -> str:
        value = by_name[name].value
        return f"{float(value) * 100:5.0f}%" if not isinstance(value, bool) else "   ok"

    for result in results:
        by_name = {metric.name: metric for metric in result.metrics}
        status = "PASS" if result.ok else "FAIL"
        lines.append(
            f"{result.case.key:<22} {len(result.facts.sources):>4} "
            f"{len(result.facts.claims):>4} {pct(by_name, 'citation_resolution'):>6} "
            f"{pct(by_name, 'grounded_support'):>6} {pct(by_name, 'source_uniqueness'):>6} "
            f"{pct(by_name, 'unsupported_rate'):>6} {pct(by_name, 'evidence_utilisation'):>6}"
            f"  {status}"
        )

    failures = [r for r in results if not r.ok]
    lines.append("")
    lines.append(
        f"  {len(results) - len(failures)}/{len(results)} cases pass every invariant"
        if not failures
        else f"  {len(failures)} case(s) FAILED an invariant"
    )
    for result in failures:
        for metric in result.metrics:
            if not metric.ok:
                lines.append(f"    {result.case.key}: {metric.name} -- {metric.detail}")
        if not result.retrieval_expectations_met:
            lines.append(
                f"    {result.case.key}: retrieval expectations unmet "
                f"(missing {result.missing_expected or 'enough sources'})"
            )
    lines.append("")
    lines.append("  cite = citations resolving to a retrieved source      (invariant: 100%)")
    lines.append("  grnd = positive verdicts citing a retrieved source    (invariant: 100%)")
    lines.append("  uniq = distinct URL digests / sources stored          (invariant: 100%)")
    lines.append("  unsup = claims the system declined to support         (observation)")
    lines.append("  util = retrieved sources a claim actually cited       (observation)")
    return "\n".join(lines)


def _as_json(results: list[CaseResult], profile: dict[str, str]) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "profile": profile,
        "what_this_measures": (
            "The grounding pipeline, not model quality. On the offline profile the "
            "model is the deterministic stub, whose claims are fixed; what varies per "
            "case is the evidence retrieved for the question, and therefore whether "
            "those claims can be grounded in it."
        ),
        "invariants_all_pass": all(result.ok for result in results),
        "cases": [
            {
                "key": result.case.key,
                "question": result.case.question,
                "rationale": result.case.rationale,
                "tags": list(result.case.tags),
                "adversarial": result.case.adversarial,
                "sources": len(result.facts.sources),
                "claims": len(result.facts.claims),
                "model_calls": result.facts.model_calls,
                "report_version": result.facts.report_version,
                "retrieved_urls": [source.url for source in result.facts.sources],
                "metrics": [
                    {
                        "name": metric.name,
                        "value": metric.value,
                        "invariant": metric.invariant,
                        "ok": metric.ok,
                        "detail": metric.detail,
                    }
                    for metric in result.metrics
                ],
                "retrieval_expectations_met": result.retrieval_expectations_met,
                "passed": result.ok,
            }
            for result in results
        ],
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-write", action="store_true", help="print without writing the report")
    args = parser.parse_args()

    await _ensure_database()
    _migrate()
    deps, engine = await _offline_deps()
    try:
        results = await run_all(deps)
    finally:
        await engine.dispose()

    print(_render(results))

    if not args.no_write:
        payload = _as_json(
            results,
            {
                "llm_provider": deps.settings.llm_provider.value,
                "search_provider": deps.settings.search_provider.value,
                "note": "deterministic: no network, no credentials, no spend",
            },
        )
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"  wrote {OUTPUT}")

    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
