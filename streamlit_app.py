# streamlit_ui.py
import csv
import io
from pathlib import Path

import truststore
truststore.inject_into_ssl()

# Configure OTel + Langfuse ONCE, before any agent/LLM calls
from observe.observability import SPANS  # noqa: F401  (side effect: sets up exporters)

import requests
import streamlit as st

API_BASE = "http://localhost:8000/api"
OUTPUT_DIR = Path("outputs")

REPORT_FILES = {
    "feedback":   "feedback_report.docx",
    "ptk":        "ptk_report.docx",
    "assessment": "assessment_report.docx",
    "escalation": "escalation_report.docx",
}

st.set_page_config(
    page_title="Mentora — QA & Agent Coaching Engine",
    page_icon="🎯",
    layout="centered",
)


# ================= LOGIN =================
def do_login(username, password):
    try:
        resp = requests.post(
            f"{API_BASE}/login",
            json={"username": username, "password": password},
            timeout=30, verify=False,
        )
    except requests.exceptions.ConnectionError:
        st.error("⚠️ Cannot reach the API. Is app.py running on port 8000?")
        return None
    return resp.json() if resp.status_code == 200 else None


if "auth" not in st.session_state:
    st.session_state["auth"] = None

if not st.session_state["auth"]:
    st.title("Mentora")
    st.caption("AI-powered QA evaluation and agent coaching")
    st.subheader("Login")
    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        login_clicked = st.form_submit_button("Login", type="primary")
    if login_clicked:
        auth = do_login(username.strip(), password)
        if auth:
            st.session_state["auth"] = auth
            st.rerun()
        else:
            st.error("Invalid username or password.")
    st.stop()

auth = st.session_state["auth"]
token = auth["token"]

st.sidebar.success(f"{auth['role']}: {auth['display_name']}")
if st.sidebar.button("Logout"):
    st.session_state.clear()
    st.rerun()


# ================= HEADER =================
st.title("Mentora — QA & Agent Coaching Engine")
st.caption("AI-powered QA evaluation and agent coaching")
st.write("Enter the agent audit details to generate the report.")


# ================= EVALUATION FORM =================
with st.form("evaluation_form"):
    col1, col2 = st.columns(2)
    with col1:
        agent_emp_id = st.text_input("Agent Emp ID", placeholder="e.g. 2000160501")
        audit_id = st.text_input("Audit ID", placeholder="e.g. 1745690")
    with col2:
        process_id = st.text_input("Process ID", placeholder="e.g. 1746066")
        evaluation_date = st.text_input("Evaluation Date", placeholder="YYYY-MM-DD")
    submitted = st.form_submit_button("Run Evaluation", type="primary")


def call_evaluate(token, agent_emp_id, audit_id, process_id, evaluation_date):
    return requests.post(
        f"{API_BASE}/evaluate",
        headers={"Authorization": f"Bearer {token}"},
        data={
            "agent_emp_id": agent_emp_id,
            "audit_id": audit_id,
            "process_id": process_id,
            "evaluation_date": evaluation_date,
        },
        timeout=600, verify=False,
    )


def _resolve_report_type(result) -> str | None:
    """
    Reliably determine the report type:
      1. Trust the API's report_type (most reliable)
      2. Match the docx_file the workflow returned
      3. LAST resort: newest file on disk (avoids stale files)
    """
    report_type = None

    # 1. Trust API report_type
    if isinstance(result, dict):
        report_type = result.get("report_type")

    # 2. Match docx_file returned by the workflow
    if not report_type and isinstance(result, dict):
        docx = result.get("docx_file", "")
        for rtype, fname in REPORT_FILES.items():
            if fname == docx:
                report_type = rtype
                break

    # 3. Newest file on disk (NOT first-in-dict — avoids stale escalation/feedback mixups)
    if not report_type:
        existing = [
            (rtype, OUTPUT_DIR / fname)
            for rtype, fname in REPORT_FILES.items()
            if (OUTPUT_DIR / fname).exists()
        ]
        if existing:
            report_type = max(existing, key=lambda x: x[1].stat().st_mtime)[0]

    return report_type


if submitted:
    if not agent_emp_id.strip() and not audit_id.strip():
        st.error("Please enter an Agent Emp ID or Audit ID.")
    else:
        # clear previous run state so stale reports don't linger in the UI
        st.session_state.pop("report_type", None)
        st.session_state.pop("observability", None)

        with st.spinner("Running QA workflow (RBAC-checked)... please wait."):
            try:
                resp = call_evaluate(
                    token, agent_emp_id.strip(), audit_id.strip(),
                    process_id.strip(), evaluation_date.strip(),
                )
            except Exception as e:
                st.error(f"Could not reach API: {e}")
                resp = None

        if resp is None:
            pass
        elif resp.status_code == 200:
            data = resp.json()
            response = data.get("response")
            result = response[0] if isinstance(response, list) and response else response

            report_type = _resolve_report_type(result)

            if report_type:
                st.success(f"Done. Report type: **{report_type}**")
                st.session_state["report_type"] = report_type
                st.session_state["audit_ref"] = audit_id.strip() or agent_emp_id.strip()
            else:
                st.warning("Workflow finished but no report file was found.")

            if data.get("observability"):
                st.session_state["observability"] = data["observability"]

        elif resp.status_code == 403:
            st.error(f"🚫 Access Denied — {resp.json().get('detail', 'Access denied.')}")
        elif resp.status_code == 401:
            st.error("Session expired. Please log in again.")
            st.session_state["auth"] = None
            st.rerun()
        elif resp.status_code == 404:
            st.warning("No audit details found for the given inputs.")
            if resp.json().get("observability"):
                st.session_state["observability"] = resp.json()["observability"]
        else:
            st.error(f"Server error ({resp.status_code}): {resp.text}")


# ================= OBSERVABILITY PANEL =================
def _rows_from(bucket: dict, name_col: str, decimals: int) -> list[dict]:
    """Build display rows from a per_* bucket."""
    return [
        {
            name_col: name,
            "Tool": d.get("tool", "-") or "-",
            "Calls": d.get("calls", 0),
            "Input": d.get("in", 0),
            "Output": d.get("out", 0),
            "Total Tokens": d.get("in", 0) + d.get("out", 0),
            "USD": round(d.get("usd", 0), decimals),
        }
        for name, d in bucket.items()
    ]


obs = st.session_state.get("observability")
if obs:
    st.divider()
    st.subheader("Observability — Agents, RAG, Tokens & Cost")

    t = obs.get("total", {})
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("LLM Calls", t.get("calls", 0))
    c2.metric("Input Tokens", f"{t.get('in', 0):,}")
    c3.metric("Output Tokens", f"{t.get('out', 0):,}")
    c4.metric("Total Cost (USD)", f"${t.get('usd', 0):.6f}")

    model_name = obs.get("model")
    if model_name and model_name != "-":
        st.markdown(f"**Model:** `{model_name}`")

    per_agent = obs.get("per_agent", {})
    per_rag = obs.get("per_rag", {})
    per_tool = obs.get("per_tool", {})

    # ---- 1. Per-Agent Breakdown (LLM agents only) ----
    agent_rows = _rows_from(per_agent, "Agent", 6)
    if agent_rows:
        st.markdown("**Per-Agent Breakdown**")
        st.table(agent_rows)
    else:
        st.info("No billed agent spans captured. "
                "(Check the API terminal SPAN dump for token attribute keys.)")

    # ---- 2. RAG / Embedding Breakdown (Titan functions, NOT agents) ----
    rag_rows = _rows_from(per_rag, "RAG Model", 8)   # 8 decimals: tiny embedding cost
    if rag_rows:
        st.markdown("**RAG / Embedding Breakdown**")
        st.caption("Retrieval embedding calls (Amazon Titan). These are functions, not agents.")
        st.table(rag_rows)

    # ---- 3. Framework Tools (e.g. MCP) ----
    tool_rows = _rows_from(per_tool, "Tool", 6)
    if tool_rows:
        st.markdown("**Framework Tools Breakdown**")
        st.table(tool_rows)

    # ---- DOWNLOAD: combined observability report (CSV) ----
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf, fieldnames=["Section", "Name", "Tool", "Calls", "Input", "Output", "Total Tokens", "USD"]
    )
    writer.writeheader()

    def _write_section(section: str, bucket: dict, decimals: int):
        for name, d in bucket.items():
            writer.writerow({
                "Section": section,
                "Name": name,
                "Tool": d.get("tool", "-") or "-",
                "Calls": d.get("calls", 0),
                "Input": d.get("in", 0),
                "Output": d.get("out", 0),
                "Total Tokens": d.get("in", 0) + d.get("out", 0),
                "USD": round(d.get("usd", 0), decimals),
            })

    _write_section("AGENT", per_agent, 6)
    _write_section("RAG", per_rag, 8)
    _write_section("TOOL", per_tool, 6)

    writer.writerow({
        "Section": "TOTAL", "Name": "-", "Tool": "-",
        "Calls": t.get("calls", 0),
        "Input": t.get("in", 0),
        "Output": t.get("out", 0),
        "Total Tokens": t.get("in", 0) + t.get("out", 0),
        "USD": round(t.get("usd", 0), 8),
    })

    audit_ref = st.session_state.get("audit_ref", "report")
    st.download_button(
        label="⬇️ Download Observability Report (CSV)",
        data=buf.getvalue().encode("utf-8"),
        file_name=f"observability_{audit_ref}.csv",
        mime="text/csv",
        key="download_obs",
    )


# ================= DOWNLOAD REPORT =================
if st.session_state.get("report_type"):
    report_type = st.session_state["report_type"]
    audit_ref = st.session_state.get("audit_ref", "report")
    fname = REPORT_FILES.get(report_type)
    fpath = OUTPUT_DIR / fname if fname else None

    st.divider()
    st.subheader(f"{report_type.upper()} Report")
    if fpath and fpath.exists():
        with open(fpath, "rb") as f:
            st.download_button(
                label=f"⬇️ Download {report_type.upper()} Report",
                data=f.read(),
                file_name=f"{report_type}_report_{audit_ref}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                key="download_report",
            )
    else:
        st.error(f"Report file not found: {fpath}")