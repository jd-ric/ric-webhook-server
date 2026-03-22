"""
RIC Webhook Server - Real-time Slack Automation
Rajendra Industrial Corporation

Receives Slack Event Subscriptions and runs agent logic instantly,
without waiting for scheduled Cowork runs.

Agents handled by this server (no Chrome needed):
  - Agent 3.1  -> LinkedIn QC (formatting check, instant APPROVED/REVISE)
  - Agent 4.1  -> Email QC routing (instant APPROVED/REVISE)
  - Agent 5.1  -> Follow-up QC routing (instant APPROVED/REVISE)
  - Agent 8    -> Orchestrator (instant task dispatch on COMPLETED events)
"""

import os
import re
import hmac
import hashlib
import time
import logging

from flask import Flask, request, jsonify
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ric-webhook")

SLACK_BOT_TOKEN      = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET", "")
BOT_USER_ID          = os.environ.get("SLACK_BOT_USER_ID", "")

slack = WebClient(token=SLACK_BOT_TOKEN)
app   = Flask(__name__)

CH = {
    "linkedin_log"    : "C0AMT05PGES",
    "email_log"       : "C0AMRJWN11T",
    "followup_log"    : "C0AN7UMGFNV",
    "agent_state"     : "C0ANXA3KEKS",
    "alerts"          : "C0AMDJBDBKR",
    "crm_updates"     : "C0AMYK9B78U",
    "tasks_agent3"    : "C0AMV1LBDMG",
    "tasks_agent4"    : "C0AMV1PDP34",
    "tasks_agent5"    : "C0AMRJR3FGD",
    "tasks_agent6"    : "C0AMT045H3Q",
    "tasks_agent3_2"  : "C0AMKP6KWKH",
    "skill_updates"   : "C0AN81927UZ",
}
CH_NAME = {v: k for k, v in CH.items()}


def post(channel_id, text, thread_ts=None):
    try:
        kwargs = {"channel": channel_id, "text": text}
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        slack.chat_postMessage(**kwargs)
    except SlackApiError as e:
        log.error("Slack post error: %s", e.response["error"])


def verify_signature(req):
    if not SLACK_SIGNING_SECRET:
        return True
    sig = req.headers.get("X-Slack-Signature", "")
    ts  = req.headers.get("X-Slack-Request-Timestamp", "0")
    if abs(time.time() - int(ts)) > 300:
        return False
    base = f"v0:{ts}:{req.get_data(as_text=True)}"
    mine = "v0=" + hmac.new(
        SLACK_SIGNING_SECRET.encode(), base.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(mine, sig)


def is_bot_message(event):
    return bool(
        event.get("bot_id")
        or event.get("subtype") == "bot_message"
        or (BOT_USER_ID and event.get("user") == BOT_USER_ID)
    )


LINKEDIN_HARD_FAIL_PATTERNS = [
    (r"--",          'double-dash "--"'),
    (r"—",      "em-dash"),
    (r"•",      "bullet"),
    (r"
[-*] ",     "list prefix"),
    (r"
d+. ",    "numbered list"),
    (r"**[^*]+**", "bold markdown"),
    (r"*[^*]+*",   "italic markdown"),
]


def run_agent3_1(event):
    text    = event.get("text", "")
    ts      = event.get("ts")
    channel = event.get("channel")
    if "DRAFT" not in text.upper():
        return
    log.info("Agent 3.1: reviewing linkedin draft")
    hard_fails = [label for pat, label in LINKEDIN_HARD_FAIL_PATTERNS if re.search(pat, text)]
    if hard_fails:
        post(channel,
            "[AGENT 3.1 - LinkedIn QC - INSTANT]
REVISE
"
            f"CRITERION 7 FAIL: {', '.join(hard_fails)}
Fix and resubmit.",
            thread_ts=ts)
        post(CH["alerts"], f"[AGENT 3.1] REVISE - formatting fail: {', '.join(hard_fails)}")
        return
    issues = []
    lower = text.lower()
    wc = len(text.split())
    if wc < 30:
        issues.append(f"C1-LENGTH: too short ({wc} words)")
    elif wc > 150:
        issues.append(f"C1-LENGTH: too long ({wc} words)")
    for opener in ["i hope this", "i hope you", "hope you're", "i came across"]:
        if lower.startswith(opener):
            issues.append(f"C2-OPENER: generic opener")
    qm = text.count("?")
    if qm == 0:
        issues.append("C4-CTA: no closing question")
    elif qm > 2:
        issues.append(f"C4-CTA: too many questions ({qm})")
    if text.count("http") > 1:
        issues.append("C5-LINKS: multiple links")
    for f in ["dear sir", "dear mr", "dear ms"]:
        if f in lower:
            issues.append(f"C6-TONE: formal opener")
    if issues:
        post(channel,
            "[AGENT 3.1 - LinkedIn QC - INSTANT]
REVISE
" +
            "
".join(f"- {i}" for i in issues) + "
Fix and resubmit.",
            thread_ts=ts)
        post(CH["alerts"], f"[AGENT 3.1] REVISE: {'; '.join(issues)}")
    else:
        post(channel,
            "[AGENT 3.1 - LinkedIn QC - INSTANT]
APPROVED
All criteria passed. Agent may send.",
            thread_ts=ts)
        post(CH["alerts"], "[AGENT 3.1] APPROVED - LinkedIn draft cleared.")


def run_agent5_1(event):
    text    = event.get("text", "")
    ts      = event.get("ts")
    channel = event.get("channel")
    if "DRAFT" not in text.upper():
        return
    log.info("Agent 5.1: reviewing followup draft")
    lower = text.lower()
    touch = "1" if ("touch 1" in lower or " t1 " in lower) else "2" if ("touch 2" in lower or " t2 " in lower) else "3" if ("touch 3" in lower or " t3 " in lower) else "unknown"
    if touch == "3":
        post(CH["email_log"], f"[AGENT 5.1 - Routing]
TOUCH 3 detected. Routing to Agent 4.1.
Thread: {ts}")
        post(channel, "[AGENT 5.1] Touch 3 routed to Agent 4.1.", thread_ts=ts)
        return
    issues = []
    wc = len(text.split())
    if touch == "1":
        if wc < 40: issues.append(f"T1-2 LENGTH: too short ({wc} words, min 50)")
        elif wc > 120: issues.append(f"T1-2 LENGTH: too long ({wc} words, max 100)")
    elif touch == "2":
        if wc < 30: issues.append(f"T2-2 LENGTH: too short ({wc} words)")
        elif wc > 100: issues.append(f"T2-2 LENGTH: too long ({wc} words)")
    for phrase in ["just checking in", "following up on my", "wanted to follow up", "circling back", "touching base"]:
        if phrase in lower:
            issues.append(f'T1-3 GENERIC: banned phrase "{phrase}"')
    if not any(s in lower for s in ["new", "recent", "update", "project", "stock", "quote", "spec", "delivery"]):
        issues.append("T1-4 VALUE: no new value signal")
    qm = text.count("?")
    if qm == 0: issues.append("T1-5 CTA: no closing question")
    elif qm > 1: issues.append(f"T1-5 CTA: too many questions ({qm})")
    if issues:
        post(channel,
            f"[AGENT 5.1 - Follow-up QC - INSTANT]
REVISE
Touch {touch}:
" +
            "
".join(f"- {i}" for i in issues),
            thread_ts=ts)
    else:
        post(channel,
            f"[AGENT 5.1 - Follow-up QC - INSTANT]
APPROVED
Touch {touch} passed. Agent 5 may send.",
            thread_ts=ts)
        post(CH["alerts"], f"[AGENT 5.1] APPROVED - Touch {touch} cleared.")


def run_agent4_1(event):
    text    = event.get("text", "")
    ts      = event.get("ts")
    channel = event.get("channel")
    if "DRAFT" not in text.upper():
        return
    log.info("Agent 4.1: reviewing email draft")
    lower  = text.lower()
    issues = []
    if "subject:" not in lower:
        issues.append("No subject line - include 'Subject: ...'")
    if len(text.split()) > 300:
        issues.append(f"Too long ({len(text.split())} words)")
    for g in ["i hope this email", "to whom it may concern", "dear sir/madam"]:
        if g in lower: issues.append(f'Generic opener: "{g}"')
    qm = text.count("?")
    if qm == 0: issues.append("No CTA question")
    elif qm > 2: issues.append(f"Too many CTAs ({qm})")
    if "please find attached" in lower:
        issues.append("Use 'I have shared our company profile' not 'please find attached'")
    if issues:
        post(channel,
            "[AGENT 4.1 - Email QC - INSTANT]
REVISE
" +
            "
".join(f"- {i}" for i in issues),
            thread_ts=ts)
    else:
        post(channel,
            "[AGENT 4.1 - Email QC - INSTANT]
APPROVED - Agent 4 may send.",
            thread_ts=ts)
        post(CH["alerts"], "[AGENT 4.1] APPROVED - email cleared.")


DISPATCH_MAP = {
    "agent3"   : ("Agent 4", CH["tasks_agent4"], "Draft and send first outreach email for the new LinkedIn connection."),
    "agent3_2" : ("Agent 4", CH["tasks_agent4"], "Draft and send first outreach email for Vikas LinkedIn connection."),
    "agent4"   : ("Agent 5", CH["tasks_agent5"], "Schedule Touch 1 follow-up. Set +4 days from today."),
    "agent5"   : ("Agent 6", CH["tasks_agent6"], "Update CRM: mark follow-up as sent, log next touch date."),
    "agent6"   : ("Agent 8", CH["agent_state"],  "CRM update confirmed. Pipeline advancing."),
    "agent4_1" : ("Agent 4", CH["tasks_agent4"], "Email QC approved. Agent 4: send the approved draft now."),
    "agent5_1" : ("Agent 5", CH["tasks_agent5"], "Follow-up QC approved. Agent 5: send now."),
    "agent3_1" : ("Agent 3", CH["tasks_agent3"], "LinkedIn QC approved. Agent 3: send now."),
}


def run_agent8(event):
    text = event.get("text", "")
    if "COMPLETED" not in text.upper():
        return
    log.info("Agent 8: COMPLETED event detected")
    lower = text.lower()
    completed_by = next((k for k in DISPATCH_MAP if k.replace("_", " ") in lower or k in lower), None)
    if not completed_by:
        post(CH["alerts"], "[WEBHOOK ORCHESTRATOR] COMPLETED event received. Agents: check task channels.")
        return
    next_name, next_ch, next_instr = DISPATCH_MAP[completed_by]
    post(next_ch,
        f"[AGENT 8 - Orchestrator - INSTANT DISPATCH]
"
        f"Completed by: {completed_by.upper()}
"
        f"Next: {next_name}
{next_instr}
"
        f"Triggered: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    post(CH["alerts"], f"[AGENT 8] Dispatched {next_name} after {completed_by.upper()}.")
    log.info("Agent 8: dispatched %s -> %s", completed_by, next_name)


def run_task_channel_handler(event, channel_name):
    text = event.get("text", "")
    agent_map = {
        "tasks_agent3"   : "Agent 3 (LinkedIn)",
        "tasks_agent4"   : "Agent 4 (Email)",
        "tasks_agent5"   : "Agent 5 (Follow-up)",
        "tasks_agent6"   : "Agent 6 (CRM)",
        "tasks_agent3_2" : "Agent 3.2 (Vikas LinkedIn)",
    }
    agent_name = agent_map.get(channel_name, "Unknown Agent")
    post(CH["agent_state"],
        f"[WEBHOOK PRIORITY TRIGGER]
New task in #{channel_name.replace('_','-')}.
"
        f"{agent_name}: PRIORITY TASK WAITING.
"
        f"Preview: {text[:200]}{'...' if len(text) > 200 else ''}")
    log.info("Priority trigger posted for %s", agent_name)


def route_event(event):
    channel = event.get("channel", "")
    ch_name = CH_NAME.get(channel)
    if not ch_name:
        return
    log.info("Routing: %s (%s)", ch_name, channel)
    if ch_name == "linkedin_log":
        run_agent3_1(event)
    elif ch_name == "followup_log":
        run_agent5_1(event)
    elif ch_name == "email_log":
        run_agent4_1(event)
    elif ch_name == "agent_state":
        run_agent8(event)
    elif ch_name in ("tasks_agent3","tasks_agent4","tasks_agent5","tasks_agent6","tasks_agent3_2"):
        run_task_channel_handler(event, ch_name)


@app.route("/slack/events", methods=["POST"])
def slack_events():
    data = request.get_json(force=True) or {}
    if data.get("type") == "url_verification":
        return jsonify({"challenge": data["challenge"]})
    if not verify_signature(request):
        return jsonify({"error": "invalid signature"}), 401
    if data.get("type") == "event_callback":
        event = data.get("event", {})
        if not is_bot_message(event):
            try:
                route_event(event)
            except Exception as e:
                log.exception("Error routing event: %s", e)
    return jsonify({"ok": True})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "RIC Webhook Server",
        "version": "1.0.0",
        "agents": ["3.1", "4.1", "5.1", "8"],
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    })


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "service": "RIC Webhook Server - Rajendra Industrial Corporation",
        "status": "running"
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    log.info("Starting RIC webhook server on port %d", port)
    app.run(host="0.0.0.0", port=port, debug=False)
