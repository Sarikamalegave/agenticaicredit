# observe/observability.py
"""
Central observability: OTel + Langfuse exporters + span pricing.
Imported ONCE at startup (side effect configures providers).
"""
import base64
import os
from typing import Any, cast

from dotenv import load_dotenv
load_dotenv()

from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from agent_framework.observability import configure_otel_providers

# ---------------------------------------------------------------
# In-memory exporter for per-request token/cost accounting
# ---------------------------------------------------------------
SPANS = InMemorySpanExporter()

_exporters = [SPANS]

# ---------------------------------------------------------------
# Optional Langfuse exporter (only if keys are present)
# ---------------------------------------------------------------
LF_PUBLIC = os.environ.get("LANGFUSE_PUBLIC_KEY")
LF_SECRET = os.environ.get("LANGFUSE_SECRET_KEY")
LF_HOST = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")

if LF_PUBLIC and LF_SECRET:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    _auth = base64.b64encode(f"{LF_PUBLIC}:{LF_SECRET}".encode()).decode()
    _exporters.append(
        OTLPSpanExporter(
            endpoint=f"{LF_HOST}/api/public/otel/v1/traces",
            headers={"Authorization": f"Basic {_auth}"},
        )
    )

# Register ALL exporters on ONE tracer provider
configure_otel_providers(exporters=_exporters)


# ===============================================================
# PRICING TABLE  — USD per 1,000 tokens  (edit to your Bedrock model)
# ===============================================================
PRICES = {
    # model substring : (input_per_1k, output_per_1k)
    "claude-3-5-sonnet": (0.003, 0.015),
    "claude-3-haiku":    (0.00025, 0.00125),
    "claude-3-sonnet":   (0.003, 0.015),
    "nova-pro":          (0.0008, 0.0032),
    "nova-lite":         (0.00006, 0.00024),
    "default":           (0.003, 0.015),
}


def _rate_for(model: str) -> tuple[float, float]:
    m = (model or "").lower()
    for key, rate in PRICES.items():
        if key in m:
            return rate
    return PRICES["default"]


# ===============================================================
# Attribute-key helpers (handles different span conventions)
# ===============================================================
_IN_KEYS = [
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.prompt_tokens",
    "llm.token_count.prompt",
    "gen_ai.request.input_tokens",
]
_OUT_KEYS = [
    "gen_ai.usage.output_tokens",
    "gen_ai.usage.completion_tokens",
    "llm.token_count.completion",
    "gen_ai.response.output_tokens",
]
_MODEL_KEYS = ["gen_ai.request.model", "gen_ai.response.model", "llm.model_name"]
_AGENT_KEYS = ["gen_ai.agent.name", "agent.name"]
_TOOL_KEYS = ["gen_ai.tool.name", "tool.name"]


def _first(attrs: dict, keys: list[str], default=None):
    for k in keys:
        if k in attrs and attrs[k] not in (None, ""):
            return attrs[k]
    return default


def _int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


# ===============================================================
# Public API used by routes
# ===============================================================
def snapshot_mark() -> int:
    """Number of finished spans BEFORE a request runs."""
    return len(SPANS.get_finished_spans())


def force_flush() -> None:
    """Push spans to in-memory store + Langfuse before reading."""
    cast(Any, trace.get_tracer_provider()).force_flush()


def usage_since(mark: int, debug: bool = False) -> dict:
    force_flush()
    new_spans = list(SPANS.get_finished_spans())[mark:]

    total_in = total_out = calls = 0
    per_agent: dict[str, dict] = {}
    model_name = None

    for span in new_spans:
        attrs = dict(span.attributes or {})
        if debug:
            print("SPAN:", span.name, attrs)

        name_l = (span.name or "").lower()

        # capture model name from chat spans, then SKIP them (avoid double count)
        if name_l.startswith("chat ") or "anthropic" in name_l or "claude" in name_l:
            m = _first(attrs, _MODEL_KEYS)
            if m and not model_name:
                model_name = m
            continue

        # count ONLY named agent spans
        if not name_l.endswith("_agent"):
            continue

        in_tok = _int(_first(attrs, _IN_KEYS, 0))
        out_tok = _int(_first(attrs, _OUT_KEYS, 0))
        if in_tok == 0 and out_tok == 0:
            continue

        r_in, r_out = _rate_for(model_name or "default")
        usd = (in_tok / 1000) * r_in + (out_tok / 1000) * r_out

        calls += 1
        total_in += in_tok
        total_out += out_tok

        a = per_agent.setdefault(
            span.name, {"tool": "-", "calls": 0, "in": 0, "out": 0, "usd": 0.0}
        )
        a["calls"] += 1
        a["in"] += in_tok
        a["out"] += out_tok
        a["usd"] += usd

    total_usd = sum(a["usd"] for a in per_agent.values())
    return {
        "total": {"calls": calls, "in": total_in, "out": total_out, "usd": total_usd},
        "per_agent": per_agent,
        "model": model_name or "-",
        "agents": sorted(per_agent.keys()),
        "tools": [],
    }