"""One-time cleanup (1.1.2): replies you sent that ERPNext's Inbox fetch filed as Received.

A reply from a mailbox's own address can land in its Inbox too (e.g. Bcc to yourself); ERPNext lets it in
because it is a reply, but files it as Received, so Hub Mail shows it in the Inbox as if from yourself.
This re-files those as Sent. Only replies ("Re: ...") from the mailbox's own address that were not also
addressed to that mailbox (To / Cc) are changed, so emails you sent to yourself stay as they are.
"""

import frappe


def execute():
    for acc in frappe.get_all("Email Account", fields=["name", "email_id"]):
        own = (acc.email_id or "").strip().lower()
        if not own:
            continue
        like_own = "%" + own + "%"
        frappe.db.sql(
            """
            update `tabCommunication`
            set sent_or_received = 'Sent', seen = 1
            where email_account = %(acct)s
              and communication_medium = 'Email'
              and sent_or_received = 'Received'
              and lower(trim(sender)) = %(own)s
              and lower(subject) like 're:%%'
              and lower(ifnull(recipients, '')) not like %(like_own)s
              and lower(ifnull(cc, '')) not like %(like_own)s
            """,
            {"acct": acc.name, "own": own, "like_own": like_own},
        )
