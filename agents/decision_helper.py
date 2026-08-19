# agents/decision_helper.py
import json
from pydantic import BaseModel
from client import chat_client


class SOPQuery(BaseModel):
    query: str


SHAPE_SOP_INSTRUCTIONS = """
You refine a failed QA parameter into a short, focused SOP search query.
Return ONLY valid JSON: { "query": "..." }
No explanation, no markdown.
"""


class DecisionHelper:
    def __init__(self):
        self._agent = chat_client.as_agent(
            name="sop_query_agent",
            instructions=SHAPE_SOP_INSTRUCTIONS,
            default_options={"response_format": SOPQuery},
        )

    async def shape_sop_query(self, parameter: dict) -> str:
        msg = f"Failed QA parameter:\n{json.dumps(parameter, ensure_ascii=False)}"
        result = await self._agent.run(msg)
        value = getattr(result, "value", None)
        if isinstance(value, SOPQuery) and value.query:
            return value.query.strip()
        return (
            f"QA Parameter: {parameter.get('subparameter', 'Unknown')}\n"
            f"Failure Reason: {parameter.get('reason', 'N/A')}\n"
            "Retrieve ONLY the SOP section explaining the correct process."
        )