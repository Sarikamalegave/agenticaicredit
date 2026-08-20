# agents/ptk_builder.py
"""
Builds PTK entries (LLM + RAG) and Assessment entries for each weakness.
Concurrent, error-isolated. RAG embedding nests under the parent trace
via OTel context propagation into worker threads.
"""

import asyncio
import contextvars
import functools
import logging

from rag.retrieve import retrieve_context
from agents.runners import run_ptk, run_assessment
from models.schemas import PTKResult, AssessmentResult

logger = logging.getLogger(__name__)

# Throttle concurrency to respect LLM API rate limits
MAX_CONCURRENT = 5


async def _run_in_thread_with_context(func, /, *args, **kwargs):
    """
    Run a blocking function in a thread WHILE preserving the current
    OTel/contextvars context, so spans created inside nest correctly.
    """
    ctx = contextvars.copy_context()
    call = functools.partial(ctx.run, func, *args, **kwargs)
    return await asyncio.to_thread(call)


# ---------------------------------------------------------
# Build one PTK entry for a single weakness
# ---------------------------------------------------------
async def _build_single_ptk(w: dict, ptk_agent, shape_sop_query_fn) -> dict | None:
    parameter = w.get("parameter", "")
    subparameter = w.get("subparameter", "")

    try:
        # 1. Shape the SOP query (async)
        refined_query = await shape_sop_query_fn(w)

        # 2. Retrieve SOP context (blocking) — context-propagated so the
        #    'rag_embedding' span nests under qa_workflow.
        sop_context = await _run_in_thread_with_context(
            retrieve_context, refined_query, parameter, subparameter
        )
        if not sop_context:
            logger.warning("No SOP context for %s / %s", parameter, subparameter)

        # 3. Run the PTK agent (async)
        ptk: PTKResult = await run_ptk(
            ptk_agent,
            {"parameter": parameter, "subparameter": subparameter},
            sop_context,
        )

        return {
            "ptk": ptk,
            "parameter": parameter,
            "subparameter": subparameter,
            "sop_context": sop_context,
        }

    except Exception as exc:
        logger.exception("PTK build failed for %s / %s: %s", parameter, subparameter, exc)
        return None


# ---------------------------------------------------------
# Build the full PTK list (concurrent, throttled)
# ---------------------------------------------------------
async def build_ptk_list(weakness_json, ptk_agent, shape_sop_query_fn) -> list[dict]:
    if not weakness_json:
        return []

    sem = asyncio.Semaphore(MAX_CONCURRENT)

    async def _guarded(w):
        async with sem:
            return await _build_single_ptk(w, ptk_agent, shape_sop_query_fn)

    results = await asyncio.gather(*[_guarded(w) for w in weakness_json])
    return [r for r in results if r is not None]


# ---------------------------------------------------------
# Build one Assessment for a single PTK entry
# ---------------------------------------------------------
async def _build_single_assessment(entry: dict, assessment_agent) -> AssessmentResult | None:
    parameter = entry.get("parameter", "")
    subparameter = entry.get("subparameter", "")
    try:
        return await run_assessment(
            assessment_agent,
            entry["ptk"],
            parameter,
            subparameter,
            entry["sop_context"],
        )
    except Exception as exc:
        logger.exception("Assessment failed for %s / %s: %s", parameter, subparameter, exc)
        return None


# ---------------------------------------------------------
# Build the full assessment list (concurrent, throttled)
# ---------------------------------------------------------
async def build_assessment_list(ptk_list, assessment_agent) -> list[AssessmentResult]:
    if not ptk_list:
        return []

    sem = asyncio.Semaphore(MAX_CONCURRENT)

    async def _guarded(entry):
        async with sem:
            return await _build_single_assessment(entry, assessment_agent)

    results = await asyncio.gather(*[_guarded(e) for e in ptk_list])
    return [r for r in results if r is not None]