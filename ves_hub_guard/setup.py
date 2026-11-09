"""Settings VES Hub Guard keeps in place after every update (bench migrate)."""

import frappe

MAIL_PULL = "frappe.email.doctype.email_account.email_account.pull"
MAIL_PULL_CRON = "* * * * *"  # Hub Mail: fetch new mail every minute (was every 10 minutes)


def after_migrate():
    name = frappe.db.get_value("Scheduled Job Type", {"method": MAIL_PULL}, "name")
    if name:
        frappe.db.set_value("Scheduled Job Type", name, {"frequency": "Cron", "cron_format": MAIL_PULL_CRON})
