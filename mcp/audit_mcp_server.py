"""
audit_mcp_server.py
Standalone MCP server (Option A): wraps the existing Audit REST API.
"""

import json
import logging
import asyncio
import sys, os

# --- make phase3/ importable so config.py resolves ---
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from mcp.server.fastmcp import FastMCP
from audit_data import Audit_Data   # found locally in phase3/mcp/
from config import *                # found in phase3/ via sys.path fix

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("audit-mcp")

# ... rest unchanged ...
mcp = FastMCP(
    name="audit-server",
    host="0.0.0.0",
    port=8080,
)


@mcp.tool(
    description=(
        "Fetch internal audit details for a given ProcessId, EvaluationDate "
        "and AuditId. EvaluationDate accepts 'YYYY-MM-DD', "
        "'YYYY-MM-DD HH:MM:SS', or 'YYYY-MM-DD HH:MM:SS.ffffff'. "
        "Returns the raw audit JSON from the backend API."
    )
)
async def get_audit_details(
    process_id: str,
    evaluation_date: str,
    audit_id: str,
) -> str:
    """
    Wraps Audit_Data.get_audit_details() (which calls your existing URL).
    Runs the blocking `requests` call in a thread so the async
    event loop is not blocked. Returns raw JSON as a string.
    """
    def _run() -> dict:
        auditor = Audit_Data(
            logger=logger,
            process_id=process_id,
            evaluation_date=evaluation_date,
            audit_id=audit_id,
        )
        return auditor.get_audit_details()

    try:
        result = await asyncio.to_thread(_run)
    except Exception as e:
        logger.exception("Audit fetch failed")
        return json.dumps({"error": str(e)})

    return json.dumps(result, default=str)


if __name__ == "__main__":
    logger.info(f"Starting Audit MCP server on http://0.0.0.0:8080/mcp")
    mcp.run(transport="streamable-http")