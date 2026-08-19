# routes/evaluate.py
from fastapi import APIRouter, Header, HTTPException, Form
from fastapi.responses import JSONResponse

from routes.authin import User, get_user
from author import authorize_agent
from utils.logger import logger
from workflow.qa_workflow import run_workflow

from observe.observability import snapshot_mark, usage_since  # <-- IMPORT

router = APIRouter()


def _require_user(authorization: str | None) -> User:
    token = (authorization or "").removeprefix("Bearer ").strip()
    user = get_user(token) if token else None
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    return user


@router.post("/evaluate")
async def evaluate(
    agent_emp_id: str = Form(None),
    audit_id: str = Form(None),
    process_id: str = Form(None),
    evaluation_date: str = Form(None),
    authorization: str | None = Header(default=None),
):
    user = _require_user(authorization)

    allowed, reason = authorize_agent(user, agent_emp_id)
    if not allowed:
        logger.warning(f"BLOCKED (pre-fetch): {reason}")
        raise HTTPException(status_code=403, detail=reason)

    logger.info(f"AUTHORIZED: {user.role} '{user.display_name}' -> agent {agent_emp_id}")

    config_data = {
        "agent_emp_id": (agent_emp_id or "").strip(),
        "audit_id": (audit_id or "").strip(),
        "process_id": (process_id or "").strip(),
        "evaluation_date": (evaluation_date or "").strip(),
    }

    # ---- OBSERVABILITY: mark BEFORE the run ----
    mark = snapshot_mark()

    try:
        response = await run_workflow(logger, config_data)
    except Exception as e:
        logger.error(f"Workflow error: {e}")
        return JSONResponse(status_code=500, content={"response": "error", "message": str(e)})

    # ---- OBSERVABILITY: price spans for THIS request ----
    obs = usage_since(mark, debug=True)   # debug=True prints span attrs once; set False later
    logger.info(f"OBS: calls={obs['total']['calls']} usd={obs['total']['usd']:.6f} "
                f"agents={obs['agents']} tools={obs['tools']}")

    if not response:
        return JSONResponse(
            status_code=404,
            content={"response": "No audit details found", "observability": obs},
        )

    return {"response": response, "status": 200, "observability": obs}