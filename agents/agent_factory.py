# agents/agent_factory.py
from client import chat_client
from prompts.agent_instructions import (
    DECISION_AGENT_INSTRUCTIONS,
    PTK_AGENT_INSTRUCTIONS,
    ASSESSMENT_AGENT_INSTRUCTIONS,
    FEEDBACK_AGENT_INSTRUCTIONS,
    ESCALATION_AGENT_INSTRUCTIONS,
)
from models.schemas import (
    DecisionResult, PTKResult, AssessmentResult, FeedbackResult, EscalationResult,
)


def create_agents():
    return {
        "decision": chat_client.as_agent(
            name="decision_agent",
            instructions=DECISION_AGENT_INSTRUCTIONS,
            default_options={"response_format": DecisionResult},
        ),
        "ptk": chat_client.as_agent(
            name="ptk_agent",
            instructions=PTK_AGENT_INSTRUCTIONS,
            default_options={"response_format": PTKResult},
        ),
        "assessment": chat_client.as_agent(
            name="assessment_agent",
            instructions=ASSESSMENT_AGENT_INSTRUCTIONS,
            default_options={"response_format": AssessmentResult},
        ),
        "feedback": chat_client.as_agent(
            name="feedback_agent",
            instructions=FEEDBACK_AGENT_INSTRUCTIONS,
            default_options={"response_format": FeedbackResult},
        ),
        "escalation": chat_client.as_agent(
            name="escalation_agent",
            instructions=ESCALATION_AGENT_INSTRUCTIONS,
            default_options={"response_format": EscalationResult},
        ),
    }