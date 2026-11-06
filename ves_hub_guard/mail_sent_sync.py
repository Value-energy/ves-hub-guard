"""VES 2026-11-06: bring emails sent from Outlook (or any mail app) into Hub Mail.

ERPNext only reads each mailbox's INBOX, and only messages not yet marked read, so anything sent from
Outlook (saved in the mailbox's Sent folder) never reached the Hub. Every few minutes this job reads the
Sent folder of every mailbox the ERP receives for and files each new message as a *Sent* Communication
on that mailbox, threaded to the conversation it replies to.

- Messages sent from the Hub are copied to the Sent folder by ERPNext ("append to Sent folder") with the
  same Message-ID, so they are recognised and skipped - nothing appears twice.
- The first run only looks back LOOKBACK_DAYS; after that the last UID read is remembered per mailbox.
- Nothing is changed on the mail server (folder opened read-only, no flags set).
"""

import email
import re
from datetime import timezone
from email.header import decode_header, make_header
from email.utils import getaddresses, parseaddr, parsedate_to_datetime

import frappe
from frappe.utils import add_days, cint, convert_utc_to_system_timezone, get_datetime, now_datetime, nowdate

SENT_FOLDER_GUESSES = ("Sent", "INBOX.Sent", "Sent Items", "INBOX.Sent Items", "Sent Messages")
LOOKBACK_DAYS = 3
MAX_PER_RUN = 200
MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024
MAX_ATTEMPTS = 5
# v2 (1.1.1): new keys so every mailbox re-reads the last LOOKBACK_DAYS once - messages that 1.1.0
# could not file (time-zone bug) are picked up; ones already in the ERP are skipped by Message-ID.
STATE_KEY = "ves_sent_sync_v2::{0}"  # "<uidvalidity>:<last uid read>"
RETRY_KEY = "ves_sent_sync_v2_retry::{0}"  # "<uidvalidity>|<uid>:<attempts>,<uid>:<attempts>"


def sync_all():
    """Scheduler entry point (hooks.py, every 5 minutes)."""
    for name in frappe.get_all(
        "Email Account", filters={"enable_incoming": 1, "use_imap": 1}, pluck="name"
    ):
        try:
            sync_account(name)
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()
            frappe.log_error(title="VES Sent-folder sync failed: {0}".format(name))


def sync_account(name):
    acc = frappe.get_doc("Email Account", name)
    conn = _connect(acc)
    try:
        folder = _find_sent_folder(conn, acc.get("sent_folder_name"))
        if not folder:
            return
        typ, data = conn.select(_quote(folder), readonly=True)
        if typ != "OK":
            return
        validity = _select_value(conn, "UIDVALIDITY") or "0"
        uidnext = cint(_select_value(conn, "UIDNEXT"))
        state = (frappe.db.get_default(STATE_KEY.format(name)) or "").split(":")
        first_run = not (len(state) == 2 and state[0] == validity)
        last_uid = 0 if first_run else cint(state[1])

        if first_run:
            since = get_datetime(add_days(nowdate(), -LOOKBACK_DAYS)).strftime("%d-%b-%Y")
            typ, data = conn.uid("search", None, "SINCE {0}".format(since))
        else:
            typ, data = conn.uid("search", None, "UID {0}:*".format(last_uid + 1))
        found = ((data or [b""])[0] or b"") if typ == "OK" else b""
        if isinstance(found, bytes):
            found = found.decode(errors="replace")
        uids = sorted(int(u) for u in found.split() if u.isdigit() and int(u) > last_uid)[:MAX_PER_RUN]
        if first_run and not uids and uidnext:
            last_uid = uidnext - 1  # nothing recent: start from the next message sent

        # Messages that failed on an earlier run are tried again (up to MAX_ATTEMPTS times).
        retry = _load_retry(name, validity)
        for uid in sorted(retry):
            if _process_uid(conn, acc, uid):
                retry.pop(uid, None)
            else:
                retry[uid] = retry[uid] + 1
                if retry[uid] >= MAX_ATTEMPTS:
                    frappe.log_error(title="VES Sent-folder sync: gave up on message {0} in {1}".format(uid, name))
                    retry.pop(uid, None)

        for uid in uids:
            if not _process_uid(conn, acc, uid):
                retry[uid] = 1
            last_uid = uid

        _save_retry(name, validity, retry)
        if uids or first_run:
            frappe.db.set_default(STATE_KEY.format(name), "{0}:{1}".format(validity, last_uid))
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _process_uid(conn, acc, uid):
    """Fetch and file one message. True when it is filed (or there is nothing to file)."""
    typ, msg_data = conn.uid("fetch", str(uid), "(BODY.PEEK[])")
    raw = _raw_from_fetch(msg_data) if typ == "OK" else None
    if not raw:
        return True  # deleted from the Sent folder meanwhile: nothing to file
    frappe.db.savepoint("ves_sent_msg")
    try:
        _file_message(acc, raw)
        return True
    except Exception:
        frappe.db.rollback(save_point="ves_sent_msg")
        frappe.log_error(title="VES Sent-folder sync: message {0} in {1}".format(uid, acc.name))
        return False


def _load_retry(name, validity):
    raw = frappe.db.get_default(RETRY_KEY.format(name)) or ""
    head, _, body = raw.partition("|")
    if head != validity:
        return {}
    out = {}
    for part in body.split(","):
        uid, _, n = part.partition(":")
        if uid.isdigit():
            out[int(uid)] = cint(n) or 1
    return out


def _save_retry(name, validity, retry):
    body = ",".join("{0}:{1}".format(u, n) for u, n in sorted(retry.items()))
    frappe.db.set_default(RETRY_KEY.format(name), "{0}|{1}".format(validity, body) if body else "")


# ---------- one message ----------

def _file_message(acc, raw):
    msg = email.message_from_bytes(raw)
    message_id = (msg.get("Message-ID") or "").strip().strip("<>")
    if message_id:
        existing = frappe.db.get_value(
            "Communication", {"message_id": message_id},
            ["name", "sent_or_received", "sender", "email_account"], as_dict=True,
        )
        if existing:
            # Already in the ERP (sent from the Hub, synced before, or brought in by the Inbox fetch).
            _fix_own_copy(acc, existing)
            return

    from_name, from_addr = parseaddr(_h(msg.get("From")))
    subject = _h(msg.get("Subject")) or "(no subject)"
    text, html, files = _parts(msg)

    comm = frappe.get_doc({
        "doctype": "Communication",
        "communication_type": "Communication",
        "communication_medium": "Email",
        "sent_or_received": "Sent",
        "email_status": "Open",
        "subject": subject,
        "content": html or frappe.utils.escape_html(text or "").replace("\n", "<br>"),
        "text_content": text,
        "sender": from_addr or acc.email_id,
        "sender_full_name": from_name or None,
        "recipients": _addrs(msg.get_all("To")),
        "cc": _addrs(msg.get_all("Cc")),
        "bcc": _addrs(msg.get_all("Bcc")),
        "email_account": acc.name,
        "message_id": message_id or None,
        "communication_date": _date(msg.get("Date")),
        "has_attachment": 1 if files else 0,
        "seen": 1,
    })

    parent = _parent(msg)
    if parent:
        comm.in_reply_to = parent.name
        if parent.reference_doctype and parent.reference_name:
            comm.reference_doctype = parent.reference_doctype
            comm.reference_name = parent.reference_name

    comm.flags.ves_from_sent_folder = True
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


def _fix_own_copy(acc, comm):
    """A reply you send can also land in your own Inbox (e.g. Bcc to yourself). ERPNext's Inbox fetch then
    files it as Received, from you. Found here in the Sent folder, it is a sent email: file it as Sent so it
    shows in Hub Mail > Sent and leaves the Inbox."""
    if (
        comm.sent_or_received == "Received"
        and comm.email_account == acc.name
        and (comm.sender or "").strip().lower() == (acc.email_id or "").strip().lower()
    ):
        frappe.db.set_value(
            "Communication", comm.name, {"sent_or_received": "Sent", "seen": 1}, update_modified=False
        )


def _parent(msg):
    """The Communication this message replies to (In-Reply-To, then References)."""
    ids = []
    for header in ("In-Reply-To", "References"):
        for part in (msg.get(header) or "").split():
            mid = part.strip().strip("<>")
            if mid and mid not in ids:
                ids.append(mid)
    for mid in ids:
        name = frappe.db.get_value("Communication", {"message_id": mid}, "name")
        if name:
            return frappe.db.get_value(
                "Communication", name, ["name", "reference_doctype", "reference_name"], as_dict=True
            )
    return None


def _parts(msg):
    text, html, files = "", "", []
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        disp = (part.get("Content-Disposition") or "").lower()
        fname = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if fname or "attachment" in disp:
            if payload and len(payload) <= MAX_ATTACHMENT_BYTES:
                files.append((_h(fname) or "attachment", payload))
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            body = payload.decode(charset, errors="replace")
        except LookupError:
            body = payload.decode("utf-8", errors="replace")
        if ctype == "text/html" and not html:
            html = body
        elif ctype == "text/plain" and not text:
            text = body
    return text, html, files


# ---------- IMAP helpers ----------

def _connect(acc):
    """Logged-in IMAP connection, opened the same way Hub Mail opens it (password or OAuth)."""
    acc.flags.validate_imap_pop_connection = True  # a failed login here must not switch the mailbox off
    return acc.get_incoming_server(in_receive=True).imap


def _find_sent_folder(conn, preferred=None):
    typ, data = conn.list()
    names = []
    for line in data or []:
        line = line.decode(errors="replace") if isinstance(line, bytes) else str(line)
        if "\\Sent" in line:
            return _folder_name(line)  # server marks its Sent folder (RFC 6154)
        names.append(_folder_name(line))
    for guess in ((preferred,) if preferred else ()) + SENT_FOLDER_GUESSES:
        for n in names:
            if n.lower() == guess.lower():
                return n
    return None


LIST_RE = re.compile(r'^\((?P<flags>[^)]*)\)\s+(?P<delim>"[^"]*"|NIL)\s+(?P<name>.+)$')


def _folder_name(list_line):
    # '(\\HasNoChildren \\Sent) "." "INBOX.Sent"'  ->  INBOX.Sent
    m = LIST_RE.match(list_line.strip())
    name = m.group("name") if m else list_line.strip()
    return name.strip().strip('"')


def _quote(folder):
    return '"{0}"'.format(folder.replace('"', '\\"'))


def _select_value(conn, key):
    """UIDVALIDITY / UIDNEXT reported by the server when the folder was selected."""
    typ, data = conn.response(key)
    val = data[0] if data else None
    if isinstance(val, bytes):
        val = val.decode(errors="replace")
    return (val or "").strip()


def _raw_from_fetch(msg_data):
    for item in msg_data or []:
        if isinstance(item, tuple) and len(item) > 1:
            return item[1]
    return None


# ---------- header helpers ----------

def _h(value):
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def _addrs(values):
    pairs = getaddresses([_h(v) for v in (values or [])])
    return ", ".join(a for _, a in pairs if a) or None


def _date(value):
    """Email Date header -> naive datetime in the system time zone (Africa/Nairobi)."""
    try:
        d = parsedate_to_datetime(value)
        if d.tzinfo:
            d = convert_utc_to_system_timezone(d.astimezone(timezone.utc).replace(tzinfo=None))
        # Frappe returns an aware datetime ("...+03:00"); the database column takes a plain one.
        return d.replace(tzinfo=None) if getattr(d, "tzinfo", None) else d
    except Exception:
        return now_datetime()
