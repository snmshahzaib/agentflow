"""Probe candidate models for real structured-output compatibility.

The catalogue's `structured_outputs` flag says a model *advertises* native
JSON-Schema support. Whether a given endpoint actually honours a strict schema
is a different question, and the only honest way to answer it is to ask.

Each candidate gets exactly one call with a real node schema. Runs with the
free-only guard on by default, so a billable request cannot be made by
mistake, and with retries disabled so a rate-limited free tier is not hammered.

    uv run python scripts/probe_models.py                 # free candidates
    uv run python scripts/probe_models.py --allow-paid m1 m2
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict, dataclass

from app.adapters.llm.capabilities import fetch_catalogue
from app.adapters.llm.openai_compat import OpenAICompatibleLLMClient
from app.domain.nodes import NodeName
from app.graph.schemas import PlanOutput

BASE_URL = os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1")
QUESTION = "Compare three open-source workflow orchestration frameworks for a small team."

#: Pause between candidates so a shared free-tier rate limit is not tripped.
PAUSE_SECONDS = 3.0


@dataclass
class ProbeResult:
    model: str
    is_free: bool | None = None
    context_length: int | None = None
    ok: bool = False
    output_mode: str | None = None
    subtasks: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    latency_ms: int | None = None
    cost_usd_estimate: float | None = None
    cost_usd_reported: float | None = None
    model_served: str | None = None
    error_code: str | None = None
    error: str | None = None


async def probe(model: str, api_key: str, catalogue: object, *, free_only: bool) -> ProbeResult:
    result = ProbeResult(model=model)
    caps = catalogue.get(model)  # type: ignore[attr-defined]
    if caps is None:
        result.error = "not in catalogue"
        return result
    result.is_free = caps.is_free
    result.context_length = caps.context_length

    client = OpenAICompatibleLLMClient(
        catalogue=catalogue,  # type: ignore[arg-type]
        models=dict.fromkeys(NodeName, model),
        base_url=BASE_URL,
        api_key=api_key,
        fallback_models=[],
        require_structured_output=True,  # never relaxed to make a model pass
        timeout_seconds=90.0,
        max_attempts=1,  # one shot: respect free-tier limits
        free_models_only=free_only,
    )
    started = time.perf_counter()
    try:
        outcome = await client.complete_structured(
            node=NodeName.PLAN,
            schema=PlanOutput,
            system="You are a research planner. Return only the structured object.",
            user=f"Research question:\n{QUESTION}\n\nProduce at most 3 subtasks.",
        )
    except Exception as exc:
        result.error_code = getattr(exc, "code", type(exc).__name__)
        result.error = str(exc)[:300]
        result.latency_ms = int((time.perf_counter() - started) * 1000)
    else:
        usage = outcome.usage
        result.ok = True
        result.output_mode = usage.output_mode.value
        result.subtasks = len(outcome.output.subtasks)
        result.tokens_in = usage.tokens_in
        result.tokens_out = usage.tokens_out
        result.latency_ms = usage.latency_ms
        result.cost_usd_estimate = usage.cost_usd_estimate
        result.cost_usd_reported = usage.cost_usd_reported
        result.model_served = usage.model_served
    finally:
        await client.aclose()
    return result


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("models", nargs="*", help="model ids; default: every free candidate")
    parser.add_argument(
        "--allow-paid",
        action="store_true",
        help="permit billable models (off by default so a paid call cannot happen by accident)",
    )
    args = parser.parse_args()

    api_key = os.environ.get("LLM_API_KEY", "").strip()
    if not api_key or api_key.startswith("__"):
        print("LLM_API_KEY is not set", file=sys.stderr)
        return 2

    catalogue = await fetch_catalogue(base_url=BASE_URL, api_key=api_key, timeout_seconds=60)

    candidates = args.models or sorted(
        model_id
        for model_id in getattr(catalogue, "_entries", {})
        if (caps := catalogue.get(model_id)) is not None
        and caps.is_free
        and caps.supports_structured_outputs
        and caps.context_length >= 200_000
    )

    print(f"probing {len(candidates)} model(s); free_only={not args.allow_paid}\n")
    results: list[ProbeResult] = []
    for index, model in enumerate(candidates):
        outcome = await probe(model, api_key, catalogue, free_only=not args.allow_paid)
        results.append(outcome)
        mark = "ok  " if outcome.ok else "FAIL"
        detail = (
            f"{outcome.output_mode} subtasks={outcome.subtasks} "
            f"in={outcome.tokens_in} out={outcome.tokens_out} {outcome.latency_ms}ms"
            if outcome.ok
            else f"{outcome.error_code}: {outcome.error}"
        )
        print(f"  [{mark}] {model:46} {detail}")
        if index < len(candidates) - 1:
            await asyncio.sleep(PAUSE_SECONDS)

    pathname = os.environ.get("PROBE_OUTPUT", "probe-results.json")
    with open(pathname, "w", encoding="utf-8") as handle:
        json.dump([asdict(r) for r in results], handle, indent=2)
    print(f"\nwrote {pathname}")

    spent = sum(r.cost_usd_estimate or 0.0 for r in results)
    print(f"estimated spend for this probe: ${spent:.6f}")
    return 0 if any(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
