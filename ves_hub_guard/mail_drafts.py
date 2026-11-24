"""VES 2026-11-24: copy Hub Mail drafts to the Drafts folder on the mail server, so Outlook shows them.

Hub Mail keeps an email that is being written as a *VE Mail Draft* (a custom DocType on the site, written by the
Hub Mail page through API ``ves_mail``). Every minute this job looks for drafts that changed (``imap_dirty``) and,
for each mailbox, opens its Drafts folder once and:

- puts the current version of the draft there as an unsent message (flag ``\\Draft``), with its attachments;
- takes away the copy it put there before (found by the header ``X-VES-Draft: <draft name>``);
- for a draft that was sent or deleted in the Hub (status "Removed"): takes the copy away and deletes the record.

One way only: Hub -> mail server. A draft changed or sent from Outlook is not read back into the Hub.
Only messages this job created itself (they carry the X-VES-Draft header) are ever removed from the folder.
"""

import imaplib
import json
import mimetypes
import time
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formataddr, formatdate, make_msgid

import frappe
from frappe.utils import add_to_date, cint, now_datetime

from ves_hub_guard.mail_sent_sync import _connect, _folder_name, _quote

DOCTYPE = "VE Mail Draft"
HEADER = "X-VES-Draft"
DRAFTS_FOLDER_GUESSES = ("Drafts", "INBOX.Drafts", "Draft", "INBOX.Draft", "Draft Items")
SETTLE_SECONDS = 20  # leave a draft alone while it is still being typed (Hub Mail saves every few seconds)
MAX_PER_RUN = 100
MAX_ATTEMPTS = 8  # after this many failed tries the draft stays in the Hub only (Error Log has the reason)
MAX_ATTACH_BYTES = 25 * 1024 * 1024  # attachments on the server copy, in total


def push_all():
    """Scheduler entry point (hooks.py, every minute)."""
    if not frappe.db.exists("DocType", DOCTYPE):
        return
    rows = frappe.get_all(
        DOCTYPE,
        filters={"imap_dirty": 1, "modified": ["<", add_to_date(now_datetime(), seconds=-SETTLE_SECONDS)]},
        fields=["name", "email_account"],
        order_by="modified asc",
        limit_page_length=MAX_PER_RUN,
    )
    by_account = {}
    for r in rows:
        by_account.setdefault(r.email_account, []).append(r.name)
    for account, names in by_account.items():
        try:
            push_account(account, names)
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()
            _failed(account, names, frappe.get_traceback())
            frappe.db.commit()


def push_account(account, names):
    acc = frappe.get_doc("Email Account", account)
    if not (acc.enable_incoming and acc.use_imap):
        for name in names:  # no IMAP on this mailbox: the draft lives in the Hub only
            _finish(name, synced=0, error="This mailbox is not read over IMAP, so the draft is kept in the Hub only.")
        return
    conn = _connect(acc)
    try:
        folder = _find_drafts_folder(conn)
        if not folder:
            raise frappe.ValidationError("No Drafts folder found on the mail server for {0}".format(account))
        typ, _data = conn.select(_quote(folder))
        if typ != "OK":
            raise frappe.ValidationError("Could not open folder {0} for {1}".format(folder, account))
        for name in names:
            _push_one(conn, folder, acc, name)
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _push_one(conn, folder, acc, name):
    d = frappe.db.get_value(DOCTYPE, name, "*", as_dict=True)
    if not d:
        return
    old = _find_copies(conn, name)
    if d.status == "Draft":
        raw = build_message(acc, d)
        typ, data = conn.append(_quote(folder), "(\\Draft \\Seen)", imaplib.Time2Internaldate(time.time()), raw)
        if typ != "OK":
            raise frappe.ValidationError("Mail server refused the draft {0}: {1}".format(name, data))
    _delete_uids(conn, old)

    if d.status != "Draft":
        frappe.delete_doc(DOCTYPE, name, ignore_permissions=True, force=True)
        return
    cur = frappe.db.get_value(DOCTYPE, name, ["status", "modified"], as_dict=True)
    if not cur:
        # deleted in the Hub while it was being copied: take the copy away again
        _delete_uids(conn, _find_copies(conn, name))
    elif cur.status == "Draft" and cur.modified == d.modified:
        _finish(name, synced=1)
    else:
        # changed (or sent / deleted) meanwhile: a copy is on the server now, the next run brings it up to date
        frappe.db.set_value(DOCTYPE, name, {"imap_synced": 1}, update_modified=False)


def _finish(name, synced, error=""):
    frappe.db.set_value(
        DOCTYPE, name,
        {"imap_dirty": 0, "imap_synced": synced, "imap_attempts": 0, "imap_error": error},
        update_modified=False,
    )


def _failed(account, names, trace):
    """The mailbox could not be reached (or a draft could not be written): try again on the next runs."""
    gave_up = []
    for name in names:
        row = frappe.db.get_value(DOCTYPE, name, ["status", "imap_attempts"], as_dict=True)
        if not row:
            continue
        attempts = cint(row.imap_attempts) + 1
        vals = {"imap_attempts": attempts, "imap_error": (trace or "")[-500:]}
        if attempts >= MAX_ATTEMPTS:
            vals["imap_dirty"] = 0
            gave_up.append(name)
        frappe.db.set_value(DOCTYPE, name, vals, update_modified=False)
        if attempts >= MAX_ATTEMPTS and row.status != "Draft":
            # sent or deleted in the Hub and its server copy cannot be removed: drop the record anyway
            frappe.delete_doc(DOCTYPE, name, ignore_permissions=True, force=True)
    if gave_up:
        frappe.log_error(
            title="VES drafts copy: gave up on {0} ({1})".format(account, ", ".join(gave_up)), message=trace
        )


# ---------- the message ----------

def build_message(acc, d):
    """The draft as an unsent email (bytes), the way a mail app would store it in Drafts."""
    msg = EmailMessage(policy=SMTP)
    full_name = frappe.db.get_value("User", d.owner, "full_name") or ""
    msg["From"] = formataddr((full_name, acc.email_id))
    for header, value in (("To", d.to_addr), ("Cc", d.cc), ("Bcc", d.bcc)):
        value = _addresses(value)
        if value:
            try:
                msg[header] = value
            except Exception:
                pass  # half-typed address: leave the header out, the text is still in the Hub draft
    msg["Subject"] = (d.subject or "").replace("\r", " ").replace("\n", " ")
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=(acc.email_id or "value-energy.co.ke").split("@")[-1])
    msg[HEADER] = d.name

    html = (d.content or "") + (d.quote or "")
    text = " ".join(frappe.utils.strip_html(html).split())
    msg.set_content(text or " ")
    msg.add_alternative("<html><body>{0}</body></html>".format(html), subtype="html")

    total = 0
    for f in _files(d.files):
        try:
            content = frappe.get_doc("File", f.get("name")).get_content()
        except Exception:
            continue  # file removed meanwhile
        if isinstance(content, str):
            content = content.encode()
        total += len(content)
        if total > MAX_ATTACH_BYTES:
            break
        fname = f.get("file_name") or "attachment"
        ctype = mimetypes.guess_type(fname)[0] or "application/octet-stream"
        maintype, _sep, subtype = ctype.partition("/")
        msg.add_attachment(content, maintype=maintype, subtype=subtype or "octet-stream", filename=fname)
    return msg.as_bytes()


def _addresses(value):
    return ", ".join(p.strip() for p in (value or "").replace(";", ",").replace("\n", ",").split(",") if p.strip())


def _files(raw):
    try:
        out = json.loads(raw or "[]")
    except Exception:
        return []
    return [f for f in out if isinstance(f, dict) and f.get("name")]


# ---------- IMAP helpers ----------

def _find_drafts_folder(conn):
    typ, data = conn.list()
    names = []
    for line in data or []:
        line = line.decode(errors="replace") if isinstance(line, bytes) else str(line)
        if "\\Drafts" in line:
            return _folder_name(line)  # server marks its Drafts folder (RFC 6154)
        names.append(_folder_name(line))
    for guess in DRAFTS_FOLDER_GUESSES:
        for n in names:
            if n.lower() == guess.lower():
                return n
    return None


def _find_copies(conn, name):
    """UIDs of the copies of this draft that this job put in the (selected) Drafts folder."""
    typ, data = conn.uid("search", None, "HEADER", HEADER, '"{0}"'.format(name))
    found = ((data or [b""])[0] or b"") if typ == "OK" else b""
    if isinstance(found, bytes):
        found = found.decode(errors="replace")
    return [u for u in found.split() if u.isdigit()]


def _delete_uids(conn, uids):
    if not uids:
        return
    uidset = ",".join(uids)
    conn.uid("store", uidset, "+FLAGS.SILENT", "(\\Deleted)")
    # Remove only these messages. Without UIDPLUS a plain EXPUNGE would also remove whatever else the person
    # marked as deleted in the folder, so there the copies are left marked as deleted (mail apps hide those).
    if any("UIDPLUS" in str(c).upper() for c in (getattr(conn, "capabilities", None) or ())):
        conn.uid("expunge", uidset)
