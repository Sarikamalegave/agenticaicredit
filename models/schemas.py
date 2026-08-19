# models/schemas.py
from typing import Literal
from pydantic import BaseModel, Field


# ---------------- DECISION ----------------
class DecisionResult(BaseModel):
    route: Literal["FEEDBACK", "PTK", "ASSESSMENT", "ESCALATION"]
    confidence: float = 0.0          # no ge/le -> Bedrock-safe
    reasoning: str = ""
    key_signals: list[str] = Field(default_factory=list)


# ---------------- PTK ----------------
class PTKResult(BaseModel):
    training_topic: str
    mistake: str = ""
    business_impact: str = ""
    correct_sop: str = ""
    best_practices: list[str] = Field(default_factory=list)
    sample_conversation: str = ""
    coaching_tips: str = ""


# ---------------- ASSESSMENT ----------------
class AssessmentQuestion(BaseModel):
    question: str
    type: str = "mcq"
    options: list[str] = Field(default_factory=list)      # <-- NO max_length (Bedrock rejects maxItems)
    correct_answer: str = ""
    explanation: str = ""                                 # <-- NO max_length


class AssessmentResult(BaseModel):
    parameter: str = ""
    subparameter: str = ""
    questions: list[AssessmentQuestion] = Field(default_factory=list)   # <-- NO max_length


# ---------------- FEEDBACK ----------------
class FeedbackResult(BaseModel):
    type: Literal["FEEDBACK"] = "FEEDBACK"
    agent_name: str = ""
    overall_performance: str = ""
    strengths: list[str] = Field(default_factory=list)
    keep_doing: list[str] = Field(default_factory=list)
    encouragement: str = ""


# ---------------- ESCALATION ----------------

class EscalationResult(BaseModel):
    type: Literal["ESCALATION"] = "ESCALATION"
    severity: Literal["HIGH", "CRITICAL"]
    agent_name: str
    escalation_reason: str
    fatal_breaches: list[str]          # "parameter - subparameter - reason"
    risk_impact: list[str]             # "compliance/legal/customer risk"
    assigned_to: str
    recommended_actions: list[str]     # containment / investigation steps