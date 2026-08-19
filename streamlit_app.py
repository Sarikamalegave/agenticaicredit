# streamlit_ui.py
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


if submitted:
    if not agent_emp_id.strip() and not audit_id.strip():
        st.error("Please enter an Agent Emp ID or Audit ID.")
    else:
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

            report_type = None
            if isinstance(result, dict):
                report_type = result.get("report_type")
            if not report_type:
                for rtype, fname in REPORT_FILES.items():
                    if (OUTPUT_DIR / fname).exists():
                        report_type = rtype

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
def _is_model_row(name: str) -> bool:
    """Identify the raw LLM/model span so we can exclude it from agent rows."""
    n = (name or "").lower()
    return n.startswith("chat ") or "anthropic" in n or "claude" in n or "us." in n


obs = st.session_state.get("observability")
if obs:
    st.divider()
    st.subheader("Observability — Agents, Tokens & Cost")

    t = obs.get("total", {})
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("LLM Calls", t.get("calls", 0))
    c2.metric("Input Tokens", f"{t.get('in', 0):,}")
    c3.metric("Output Tokens", f"{t.get('out', 0):,}")
    c4.metric("Total Cost (USD)", f"${t.get('usd', 0):.6f}")

    per_agent = obs.get("per_agent", {})

    # ---- Split model row out of the agent list ----
    model_name = obs.get("model")
    agent_rows = {}
    for name, d in per_agent.items():
        if _is_model_row(name):
            if not model_name:
                model_name = name
            continue
        agent_rows[name] = d

    if model_name:
        st.markdown(f"**Model:** `{model_name}`")

    # ---- Per-Agent Breakdown (agents ONLY, no model/LLM row) ----
    if agent_rows:
        st.markdown("**Per-Agent Breakdown**")
        rows = [
            {
                "Agent": name,
                "Tool": d.get("tool", "-") or "-",
                "Calls": d.get("calls", 0),
                "Input": d.get("in", 0),
                "Output": d.get("out", 0),
                "Total Tokens": d.get("in", 0) + d.get("out", 0),
                "USD": round(d.get("usd", 0), 6),
            }
            for name, d in agent_rows.items()
        ]
        st.table(rows)

        # ---- DOWNLOAD: per-agent observability report (CSV) ----
        import csv
        buf = io.StringIO()
        writer = csv.DictWriter(
            buf, fieldnames=["Agent", "Tool", "Calls", "Input", "Output", "Total Tokens", "USD"]
        )
        writer.writeheader()
        writer.writerows(rows)
        # append totals
        writer.writerow({
            "Agent": "TOTAL", "Tool": "-",
            "Calls": t.get("calls", 0),
            "Input": t.get("in", 0),
            "Output": t.get("out", 0),
            "Total Tokens": t.get("in", 0) + t.get("out", 0),
            "USD": round(t.get("usd", 0), 6),
        })

        audit_ref = st.session_state.get("audit_ref", "report")
        st.download_button(
            label="⬇️ Download Observability Report (CSV)",
            data=buf.getvalue().encode("utf-8"),
            file_name=f"observability_{audit_ref}.csv",
            mime="text/csv",
            key="download_obs",
        )
    else:
        st.info("No billed agent spans captured. "
                "(Check the API terminal SPAN dump for token attribute keys.)")


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