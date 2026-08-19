# agents/runners.py
import json
import re
import logging

from pydantic import BaseModel, ValidationError

try:
    from json_repair import repair_json          # pip install json-repair
    _HAS_REPAIR = True
except ImportError:
    _HAS_REPAIR = False

from models.schemas import (
    DecisionResult, PTKResult, AssessmentResult, FeedbackResult, EscalationResult,
)

logger = logging.getLogger(__name__)

# Max questions allowed (enforced in CODE, not schema — Bedrock can't cap arrays)
MAX_ASSESSMENT_QUESTIONS = 5


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```json\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _validate_with_repair(model_cls: type[BaseModel], raw: str) -> BaseModel:
    """
    1) Direct validation.
    2) Extract first {...} block (surrounding prose).
    3) json-repair (truncated / malformed JSON).
    """
    try:
        return model_cls.model_validate_json(raw)
    except ValidationError:
        pass

    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return model_cls.model_validate_json(m.group(0))
        except ValidationError:
            pass

    if _HAS_REPAIR:
        try:
            repaired = repair_json(raw)
            return model_cls.model_validate_json(repaired)
        except Exception:
            logger.error("JSON repair failed for %s. Tail: ...%s",
                         model_cls.__name__, raw[-120:])

    # Give up -> re-raise the original direct-validation error
    return model_cls.model_validate_json(raw)


async def run_structured(agent, user_message: str, model_cls: type[BaseModel]) -> BaseModel:
    """
    Run a MAF agent and return a validated Pydantic instance.
      1) Prefer result.value (native structured output).
      2) Fallback: validate result.text (with json-repair for truncation).
    """
    result = await agent.run(user_message)

    # 1) Native structured output
    value = getattr(result, "value", None)
    if isinstance(value, model_cls):
        return value

    # 2) Fallback: parse/repair raw text
    raw = _strip_fences(getattr(result, "text", "") or "")
    if not raw:
        raise ValueError(f"{model_cls.__name__}: agent returned empty text.")

    return _validate_with_repair(model_cls, raw)


# ---------------- DECISION ----------------
async def run_decision(agent, audit_summary: dict) -> DecisionResult:
    msg = f"AUDIT SUMMARY:\n{json.dumps(audit_summary, indent=2, ensure_ascii=False)}"
    return await run_structured(agent, msg, DecisionResult)


# ---------------- PTK ----------------
async def run_ptk(agent, parameter: dict, sop_context: str) -> PTKResult:
    msg = (
        f"FAILED QA PARAMETER:\n{json.dumps(parameter, indent=2, ensure_ascii=False)}\n\n"
        f"RELEVANT SOP:\n{sop_context}"
    )
    return await run_structured(agent, msg, PTKResult)


# ---------------- ASSESSMENT ----------------
async def run_assessment(agent, ptk: PTKResult, parameter: str, subparameter: str,
                         sop_context: str) -> AssessmentResult:
    msg = (
        f"PARAMETER: {parameter}\nSUBPARAMETER: {subparameter}\n\n"
        f"RELEVANT SOP:\n{sop_context}\n\n"
        f"PTK (base the assessment on this):\n{ptk.model_dump_json(indent=2)}"
    )
    result = await run_structured(agent, msg, AssessmentResult)

    # Enforce question cap in CODE (schema can't do this on Bedrock)
    if len(result.questions) > MAX_ASSESSMENT_QUESTIONS:
        result.questions = result.questions[:MAX_ASSESSMENT_QUESTIONS]

    return result


# ---------------- FEEDBACK ----------------
async def run_feedback(agent, strength_json: list, audit_summary: dict) -> FeedbackResult:
    msg = (
        f"MET PARAMETERS (strengths):\n{json.dumps(strength_json, indent=2, ensure_ascii=False)}\n\n"
        f"AUDIT SUMMARY:\n{json.dumps(audit_summary, indent=2, ensure_ascii=False)}"
    )
    return await run_structured(agent, msg, FeedbackResult)



# ---------------- ESCALATION ----------------
async def run_escalation(
    agent,
    fatal_weaknesses: list,
    audit_summary: dict,
    ptk_context: list | None = None,
) -> EscalationResult:
    msg = (
        f"FATAL FAILED PARAMETERS:\n"
        f"{json.dumps(fatal_weaknesses, indent=2, ensure_ascii=False)}\n\n"
        f"AUDIT SUMMARY:\n"
        f"{json.dumps(audit_summary, indent=2, ensure_ascii=False)}\n\n"
        f"SOP-GROUNDED CORRECTIVE CONTEXT (from PTK — use ONLY to ground "
        f"risk_impact and recommended_actions; do NOT copy verbatim):\n"
        f"{json.dumps(ptk_context or [], indent=2, ensure_ascii=False)}"
    )
    return await run_structured(agent, msg, EscalationResult)