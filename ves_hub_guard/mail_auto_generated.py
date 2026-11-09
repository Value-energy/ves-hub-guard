"""VES 1.1.2: show auto-generated emails (support tickets, notifications) in Hub Mail.

ERPNext skips every incoming email marked "Auto-Submitted: auto-generated" (RFC 3834), to avoid auto-reply
loops, and keeps it as an "Unhandled Email" with that reason. Ordinary mail such as support-ticket updates
(e.g. GitHub Support) and system notifications carries that mark too, so it never reached Hub Mail.

Every few minutes this job files those emails as Received on their mailbox (no auto-reply is ever sent from
here). Each is filed once: matched by Message-ID, and its Unhandled Email record is marked as filed.
"""

import email
from email.utils import parseaddr

import frappe
from frappe.utils import add_days, now_datetime

from ves_hub_guard.mail_sent_sync import _addrs, _date, _h, _parent, _parts

LOOKBACK_DAYS = 30
MAX_PER_RUN = 100
REASON_PREFIX = "Auto-Submitted"
FILED_NOTE = "Filed in Hub Mail by VES Hub Guard. Original reason: "


def import_all():
    """Scheduler entry point (hooks.py, every 5 minutes)."""
    accounts = set(frappe.get_all("Email Account", filters={"enable_incoming": 1}, pluck="name"))
    rows = frappe.get_all(
        "Unhandled Email",
        filters={
            "reason": ["like", REASON_PREFIX + "%"],
            "creation": [">", add_days(now_datetime(), -LOOKBACK_DAYS)],
        },
        fields=["name", "email_account", "message_id", "reason"],
        order_by="creation asc",
        limit_page_length=MAX_PER_RUN,
    )
    for r in rows:
        if r.email_account not in accounts:
            continue
        frappe.db.savepoint("ves_auto_msg")
        try:
            raw = frappe.db.get_value("Unhandled Email", r.name, "raw")
            if raw:
                _file_received(frappe.get_doc("Email Account", r.email_account), raw)
            frappe.db.set_value("Unhandled Email", r.name, "reason", FILED_NOTE + (r.reason or ""), update_modified=False)
        except Exception:
            frappe.db.rollback(save_point="ves_auto_msg")
            frappe.log_error(title="VES auto-generated email import: {0}".format(r.name))
    frappe.db.commit()


def _file_received(acc, raw):
    msg = email.message_from_string(raw) if isinstance(raw, str) else email.message_from_bytes(raw)
    message_id = (msg.get("Message-ID") or "").strip().strip("<>")
    if message_id and frappe.db.exists("Communication", {"message_id": message_id}):
        return  # already in the ERP

    from_name, from_addr = parseaddr(_h(msg.get("From")))
    subject = _h(msg.get("Subject")) or "(no subject)"
    text, html, files = _parts(msg)
    when = _date(msg.get("Date"))

    comm = frappe.get_doc({
        "doctype": "Communication",
        "communication_type": "Communication",
        "communication_medium": "Email",
        "sent_or_received": "Received",
        "email_status": "Open",
        "subject": subject,
        "content": html or frappe.utils.escape_html(text or "").replace("\n", "<br>"),
        "text_content": text,
        "sender": from_addr or None,
        "sender_full_name": from_name or None,
        "recipients": _addrs(msg.get_all("To")),
        "cc": _addrs(msg.get_all("Cc")),
        "email_account": acc.name,
        "message_id": message_id or None,
        "communication_date": when,
        "has_attachment": 1 if files else 0,
        # Recent ones arrive unread; older ones (already read in Outlook long ago) arrive read.
        "seen": 0 if when >= add_days(now_datetime(), -3) else 1,
    })

    parent = _parent(msg)
    if parent:
        comm.in_reply_to = parent.name
        if parent.reference_doctype and parent.reference_name:
            comm.reference_doctype = parent.reference_doctype
            comm.reference_name = parent.reference_name

    comm.flags.ves_auto_generated = True
    comm.insert(ignore_permissions=True)

    for fname, content in files:
        frappe.get_doc({
            "doctype": "File",
            "file_name": fname,
            "content": content,
            "is_private": 1,
            "attached_to_doctype": "Communication",
            "attached_to_name": comm.name,
        }).insert(ignore_permissions=True)
