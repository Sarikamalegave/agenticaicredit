# author.py
from routes.authin import User, get_agent_by_emp_id


def _norm(v) -> str:
    return " ".join(str(v or "").split()).strip().lower()


def can_access_agent(user: User, agent: dict) -> bool:
    """
    Can `user` view reports of the target `agent`?

    AGENT   : agent is himself         -> agent.emp_id       == user.emp_id
    TL      : agent works under this TL-> agent.reports_to_tl == user.display_name
    QA      : agent under QA's TL      -> agent.reports_to_tl == user.reports_to_tl
    MANAGER : agent's TL is his TL     -> agent.reports_to_tl in user.manages_tls
    """
    if not agent:
        return False

    a_emp = _norm(agent.get("emp_id"))
    a_tl = _norm(agent.get("reports_to_tl"))

    if user.is_agent:
        return a_emp == _norm(user.emp_id)

    if user.is_tl:
        return a_tl == _norm(user.display_name)

    if user.is_qa:
        return a_tl == _norm(user.reports_to_tl)

    if user.is_manager:
        return a_tl in [_norm(t) for t in user.manages_tls]

    return False


def authorize_agent(user: User, agent_emp_id: str) -> tuple[bool, str]:
    """
    PRE-FETCH authorization by AGENT emp_id.
    Verifies the user may view this agent BEFORE fetching any audit.
    """
    agent = get_agent_by_emp_id(agent_emp_id)
    if not agent:
        return False, f"You do not have access to see audit report for the agent {agent_emp_id}."

    if can_access_agent(user, agent):
        return True, "authorized"

    return False, (
        f"Access denied: {user.role} '{user.display_name}' cannot access "
        f"reports of agent {agent.get('display_name')} "
        f"Because Agent with  emp_id {agent_emp_id} reports to team lead {agent.get('reports_to_tl')} and his manager is {agent.get('reports_to_manager')})."
    )