# workflow.py
"""
QA Multi-Agent Workflow (Microsoft Agent Framework 1.13.0)

Flow:
  fetch -> aggregate -> decision (LLM) -> [feedback | ptk | assessment | escalation] -> final

RAG runs ONLY inside build_ptk_list (PTK generation).
Fatal detection comes from the audit response 'errorType' (no RAG).
All agents return validated Pydantic objects via response_format.
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Never
from agent_framework import Executor, WorkflowBuilder, WorkflowContext, handler, MCPStreamableHTTPTool
from agents.agent_factory import create_agents
from agents.decision_helper import DecisionHelper
from agents.runners import run_decision, run_feedback, run_escalation
from agents.ptk_builder import build_ptk_list, build_assessment_list
from utils.savedocs import _write_ptk_docx, _write_assessment_docx, _write_report_docx
OUTPUT_DIR = Path("outputs")
OUTPUT_DIR.mkdir(exist_ok=True)
import json
import logging
from typing import Any

AUDIT_MCP_URL = "http://localhost:8080/mcp"


class FetchAuditDetails(Executor):
    def __init__(self, id: str, logger: logging.Logger,
                 mcp_url: str = AUDIT_MCP_URL):
        super().__init__(id=id)
        self.logger = logger
        self.mcp_url = mcp_url

    @handler
    async def go(self, message: dict[str, Any],
                 ctx: WorkflowContext[dict[str, Any]]) -> None:
        payload = dict(message)
        try:
            async with MCPStreamableHTTPTool(
                name="audit",
                url=self.mcp_url,
            ) as audit:
                # The connected tool holds an MCP ClientSession.
                # Invoke the server tool directly
                result = await audit.session.call_tool(
                    "get_audit_details",
                    arguments={
                        "process_id": str(payload.get("process_id")),
                        "evaluation_date": str(payload.get("evaluation_date")),
                        "audit_id": str(payload.get("audit_id")),
                    },
                )
                raw = result.content[0].text if result.content else "{}"
                payload["audit_details"] = json.loads(raw)

        except Exception as e:
            self.logger.exception(f"FetchAuditDetails failed: {e}")
            payload["audit_details"] = {}
            payload["error"] = str(e)

        await ctx.send_message(payload)
# ======================================================================
# NODE 2: Aggregate summary (PURE PYTHON — reads errorType, NO RAG)
# ======================================================================
class AggregateAuditSummary(Executor):
    def __init__(self, id: str, logger: logging.Logger):
        super().__init__(id=id)
        self.logger = logger

    @handler
    async def go(self, message: dict[str, Any], ctx: WorkflowContext[dict[str, Any]]) -> None:
        payload = dict(message)
        audit = message.get("audit_details", {}) or {}

        strength_json, weakness_json = [], []
        for pblock in audit.get("parameters", []):
            parameter = pblock.get("parameter")
            for sub in pblock.get("subparameters", []):
                obs = str(sub.get("observation", "")).strip().upper()
                etype = str(sub.get("errorType", "")).strip().upper()
                record = {
                    "parameter": parameter,
                    "subparameter": sub.get("subCategory"),
                    "error_type": etype,             # FATAL / NON FATAL
                    "observation": obs,              # Y = met, N = not met
                    "reason": sub.get("reason"),
                    "secondary_reason": sub.get("secondary_reason"),
                    "tertiary_reason": sub.get("tertiary_reason"),
                    "industry": audit.get("industry"),
                    "call_scenario": audit.get("call_scenario"),
                    "target": audit.get("target"),
                    "quality_scored": audit.get("quality_scored"),
                    "tenure": audit.get("tenure"),
                    "audit_id": audit.get("audit_id"),
                }
                if obs == "N":
                    weakness_json.append(record)
                elif obs == "Y":
                    strength_json.append(record)

        total_sub = len(strength_json) + len(weakness_json)
        failed_count = len(weakness_json)
        pass_rate = round(len(strength_json) / total_sub * 100, 1) if total_sub else 100.0
        quality_scored = self._parse_percent(audit.get("quality_scored"))
        tp = self._to_float(audit.get("total_points"))
        to = self._to_float(audit.get("total_opportunity"))
        points_ratio = round(tp / to * 100, 1) if to else quality_scored

        fatal_failures = [w for w in weakness_json if w["error_type"] == "FATAL"]

        summary = {
            "audit_id": audit.get("audit_id"),
            "agent_name": audit.get("agent_name"),
            "process_name": audit.get("process_name"),
            "tenure": audit.get("tenure"),
            "target": audit.get("target"),
            "total_subparameters": total_sub,
            "failed_count": failed_count,
            "pass_rate": pass_rate,
            "quality_scored": quality_scored,
            "points_ratio": points_ratio,
            "effective_score": points_ratio,
            "has_fatal_failure": len(fatal_failures) > 0,
            "fatal_failure_count": len(fatal_failures),
            "error_types": sorted({w["error_type"] for w in weakness_json}),
            "failed_parameters": [
                {"parameter": w["parameter"], "subparameter": w["subparameter"], "error_type": w["error_type"]}
                for w in weakness_json
            ],
        }

        payload["strength_json"] = strength_json
        payload["weakness_json"] = weakness_json
        payload["audit_summary"] = summary
        # self.logger.info(f"strength summary: {strength_json}")
        self.logger.info(f"weakness summary: {weakness_json}")
        self.logger.info(f"Audit summary: {summary}")
        await ctx.send_message(payload)

    @staticmethod
    def _parse_percent(v):
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return float(v)
        try:
            return float(str(v).replace("%", "").strip())
        except (ValueError, AttributeError):
            return None

    @staticmethod
    def _to_float(v):
        try:
            return float(v)
        except (ValueError, TypeError):
            return None


# ======================================================================
# NODE 3: Decision Agent (LLM, Pydantic). Guardrail on fatal.
# ======================================================================
class DecisionAgentNode(Executor):
    def __init__(self, id: str, logger: logging.Logger, agent):
        super().__init__(id=id)
        self.logger = logger
        self.agent = agent

    @handler
    async def go(self, message: dict[str, Any], ctx: WorkflowContext[dict[str, Any]]) -> None:
        payload = dict(message)
        summary = payload.get("audit_summary", {})
        
        decision = await run_decision(self.agent, summary)   # LLM call inside span
        route = decision.route

        # SAFETY GUARDRAIL: fatal breach must always escalate
        if summary.get("has_fatal_failure") and route != "ESCALATION":
            self.logger.warning(f"LLM chose {route} but FATAL present -> forcing ESCALATION")
            route = "ESCALATION"

        payload["route"] = route
        payload["decision"] = decision.model_dump()          # dict for persistence
        self.logger.info(f"DECISION -> {route} | {decision.reasoning}")
        await ctx.send_message(payload)


# ======================================================================
# NODE 4a: Feedback Agent (LLM) — strengths only. NO RAG, NO PTK.
# ======================================================================
class FeedbackAgentNode(Executor):
    def __init__(self, id: str, logger: logging.Logger, agent):
        super().__init__(id=id)
        self.logger = logger
        self.agent = agent

    @handler
    async def go(self, message: dict[str, Any], ctx: WorkflowContext[dict[str, Any]]) -> None:
        payload = dict(message)
        
        feedback = await run_feedback(
                self.agent, payload.get("strength_json", []), payload.get("audit_summary", {})
            )
        fb_dict = feedback.model_dump()

        payload["agent_output"] = fb_dict
        payload["report_type"] = "feedback"
        _write_report_docx(fb_dict, OUTPUT_DIR / "feedback_report.docx", "Feedback Report")
        await ctx.send_message(payload)


# ======================================================================
# NODE 4b: PTK Agent (LLM + RAG)
# ======================================================================
class PTKAgentNode(Executor):
    def __init__(self, id: str, logger: logging.Logger, ptk_agent, helper: DecisionHelper):
        super().__init__(id=id)
        self.logger = logger
        self.ptk_agent = ptk_agent
        self.helper = helper

    @handler
    async def go(self, message: dict[str, Any], ctx: WorkflowContext[dict[str, Any]]) -> None:
        payload = dict(message)
        
            # ptk_list entries: {"ptk": PTKResult, "parameter", "subparameter", "sop_context"}
        ptk_list = await build_ptk_list(
                payload.get("weakness_json", []), self.ptk_agent, self.helper.shape_sop_query
            )

        # serialize PTKResult objects to dicts for payload + docx
        ptk_list_serialized = [
            {**p, "ptk": p["ptk"].model_dump()} for p in ptk_list
        ]

        payload["ptk_list"] = ptk_list_serialized
        payload["agent_output"] = ptk_list_serialized
        payload["report_type"] = "ptk"
     
        _write_ptk_docx(ptk_list_serialized, OUTPUT_DIR / "ptk_report.docx")
        await ctx.send_message(payload)


# ======================================================================
# NODE 4c: Assessment Agent (LLM) — uses PTK output as reference
# ======================================================================
class AssessmentAgentNode(Executor):
    def __init__(self, id: str, logger: logging.Logger, ptk_agent, assessment_agent, helper: DecisionHelper):
        super().__init__(id=id)
        self.logger = logger
        self.ptk_agent = ptk_agent
        self.assessment_agent = assessment_agent
        self.helper = helper

    @handler
    async def go(self, message: dict[str, Any], ctx: WorkflowContext[dict[str, Any]]) -> None:
        payload = dict(message)

        # 1. Build PTK (RAG here). ptk_list holds PTKResult objects.
    
        ptk_list = await build_ptk_list(
                payload.get("weakness_json", []), self.ptk_agent, self.helper.shape_sop_query
            )
        print("ptklist",ptk_list)
        # 2. Assessment references the PTK objects
        
        assessment_list = await build_assessment_list(ptk_list, self.assessment_agent)
        print("asslist",assessment_list)
        # serialize for payload + docx
        ptk_list_serialized = [{**p, "ptk": p["ptk"].model_dump()} for p in ptk_list]
        assessment_serialized = [a.model_dump() for a in assessment_list]

        payload["ptk_list"] = ptk_list_serialized
        payload["assessment_list"] = assessment_serialized
        payload["agent_output"] = assessment_serialized
        payload["report_type"] = "assessment"

        _write_ptk_docx(ptk_list_serialized, OUTPUT_DIR / "ptk_report.docx")
        _write_assessment_docx(assessment_serialized, OUTPUT_DIR / "assessment_report.docx")
        await ctx.send_message(payload)


# ======================================================================
# NODE 4d: Escalation Agent (LLM) — risk/breach section only
# PTK is used ONLY as SOP-grounded context, NOT attached to output.
# ======================================================================
class EscalationAgentNode(Executor):
    def __init__(self, id: str, logger: logging.Logger, escalation_agent, ptk_agent, helper: DecisionHelper):
        super().__init__(id=id)
        self.logger = logger
        self.escalation_agent = escalation_agent
        self.ptk_agent = ptk_agent
        self.helper = helper

    @handler
    async def go(self, message: dict[str, Any], ctx: WorkflowContext[dict[str, Any]]) -> None:
        payload = dict(message)
        weakness_json = payload.get("weakness_json", [])

        # Fatal detection from audit response (NO RAG)
        fatal_weaknesses = [w for w in weakness_json if w["error_type"] == "FATAL"]
        
            # Build PTK (RAG) — used ONLY as SOP-grounded context for recommendations
        ptk_list = await build_ptk_list(
                weakness_json, self.ptk_agent, self.helper.shape_sop_query
            )

        ptk_context = [
            {
                "parameter": p["parameter"],
                "subparameter": p["subparameter"],
                "correct_sop": p["ptk"].correct_sop,
                "best_practices": p["ptk"].best_practices,
                "coaching_tips": p["ptk"].coaching_tips,
                "business_impact":p["ptk"].business_impact,
            }
            for p in ptk_list
        ]

        # LLM writes the escalation, grounded by PTK context
        
        escalation = await run_escalation(
                self.escalation_agent,
                fatal_weaknesses,
                payload.get("audit_summary", {}),
                ptk_context,
            )
        esc_dict = escalation.model_dump()

        # No coaching_reference attached — escalation output stands alone
        payload["agent_output"] = esc_dict
        payload["report_type"] = "escalation"
        _write_report_docx(esc_dict, OUTPUT_DIR / "escalation_report.docx", "Escalation Report")
        await ctx.send_message(payload)


# ======================================================================
# NODE 5: Final Report (PURE PYTHON — fan-in)
# ======================================================================
class FinalReportNode(Executor):
    def __init__(self, id: str, logger: logging.Logger):
        super().__init__(id=id)
        self.logger = logger

    @handler
    async def go(self, message: dict[str, Any], ctx: WorkflowContext[Never, dict[str, Any]]) -> None:
        payload = dict(message)
        report = {
            "audit_id": payload.get("audit_id"),
            "route_taken": payload.get("route"),
            "report_type": payload.get("report_type"),
            "decision": payload.get("decision"),
            "audit_summary": payload.get("audit_summary"),
            "agent_output": payload.get("agent_output"),
        }
        with open(OUTPUT_DIR / "report.json", "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        self.logger.info(
            f"Report saved. Route={payload.get('route')}, "
            f"file={payload.get('report_type')}_report.docx"
        )
        await ctx.yield_output({
            "status": "done",
            "route": payload.get("route"),
            "report_type": payload.get("report_type"),
            "docx_file": f"{payload.get('report_type')}_report.docx",
        })


# ======================================================================
# WORKFLOW BUILDER
# ======================================================================
def build_qa_workflow(logger: logging.Logger, mcp_url: str = "http://localhost:8080/mcp"):
    agents = create_agents()
    helper = DecisionHelper()

    # pass mcp_url through so it's configurable per environment
    fetch = FetchAuditDetails(id="fetch_audit_details", logger=logger, mcp_url=mcp_url)
    aggregate  = AggregateAuditSummary(id="aggregate_audit_summary", logger=logger)
    decision   = DecisionAgentNode(id="decision_agent", logger=logger, agent=agents["decision"])

    feedback   = FeedbackAgentNode(id="feedback_agent", logger=logger, agent=agents["feedback"])
    ptk        = PTKAgentNode(id="ptk_agent", logger=logger, ptk_agent=agents["ptk"], helper=helper)
    assessment = AssessmentAgentNode(
        id="assessment_agent", logger=logger,
        ptk_agent=agents["ptk"], assessment_agent=agents["assessment"], helper=helper,
    )
    escalation = EscalationAgentNode(
        id="escalation_agent", logger=logger,
        escalation_agent=agents["escalation"], ptk_agent=agents["ptk"], helper=helper,
    )
    final      = FinalReportNode(id="final_report", logger=logger)

    # Routing predicates (read 'route' set by DecisionAgentNode)
    def is_feedback(m):   return m.get("route") == "FEEDBACK"
    def is_ptk(m):        return m.get("route") == "PTK"
    def is_assessment(m): return m.get("route") == "ASSESSMENT"
    def is_escalation(m): return m.get("route") == "ESCALATION"

    workflow = (
        WorkflowBuilder(start_executor=fetch, output_from=[final])
        .add_edge(fetch, aggregate)
        .add_edge(aggregate, decision)

        # conditional fan-out
        .add_edge(decision, feedback,   condition=is_feedback)
        .add_edge(decision, ptk,        condition=is_ptk)
        .add_edge(decision, assessment, condition=is_assessment)
        .add_edge(decision, escalation, condition=is_escalation)

        # fan-in
        .add_edge(feedback,   final)
        .add_edge(ptk,        final)
        .add_edge(assessment, final)
        .add_edge(escalation, final)
        .build()
    )
    return workflow

# ... your existing code ...

async def run_workflow(logger: logging.Logger, config_data: dict[str, Any]):
    workflow = build_qa_workflow(logger)
    result = await workflow.run(config_data)
    return result.get_outputs()


# ========================================================================
# ADD STEP 1 HERE — new streaming runner (below run_workflow)
# ========================================================================
async def run_workflow_stream(logger: logging.Logger, config_data: dict[str, Any]):
    """
    Runs the workflow and yields events as each node executes,
    then yields the final outputs at the end.
    """

    workflow = build_qa_workflow(logger)
    async for event in workflow.run(config_data,stream=True):
        etype = type(event).__name__
        exec_id = getattr(event, "executor_id", None) or getattr(event, "source_id", None)
        yield ("event", etype, exec_id, event)

    # after streaming completes, emit final outputs
    outputs = workflow.get_outputs()   # confirm this method exists via Step 2 probe
    yield ("final", outputs)