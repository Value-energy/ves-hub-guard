app_name = "ves_hub_guard"
app_title = "VES Hub Guard"
app_publisher = "Value Energy Solutions Ltd"
app_description = "Hub-first access: staff work in the Value Energy Hub, Desk is for the directors"
app_email = ""
app_license = "mit"

required_apps = ["frappe"]

# Staff who open any Desk address are sent to the Hub before Desk loads (see guard.py).
before_request = ["ves_hub_guard.guard.before_request"]

# Keep Hub Mail fetching new mail every minute after each update (see setup.py).
after_migrate = ["ves_hub_guard.setup.after_migrate"]

# Emails sent from Outlook (or any mail app) are read from each mailbox's Sent folder
# and shown in Hub Mail > Sent (see mail_sent_sync.py).
# Hub Mail drafts (VE Mail Draft) are copied to each mailbox's Drafts folder on the mail server, so Outlook
# shows them, and removed there once sent or deleted (see mail_drafts.py).
scheduler_events = {
    "cron": {
        "* * * * *": [
            "ves_hub_guard.mail_drafts.push_all",
        ],
        "*/5 * * * *": [
            "ves_hub_guard.mail_sent_sync.sync_all",
            # Support tickets / notifications that ERPNext skips as auto-generated (see mail_auto_generated.py).
            "ves_hub_guard.mail_auto_generated.import_all",
        ],
    },
}
