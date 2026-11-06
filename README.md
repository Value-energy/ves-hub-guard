# VES Hub Guard

Hub-first access for Value Energy Solutions Ltd (erp.value-energy.co.ke).

- Staff work in the Value Energy Hub (`/me`).
- Any Desk address (`/desk`, `/app`, `/apps`) opened by a user without the
  **System Manager** or **VE Desk Access** role is redirected to the Hub before Desk
  loads; the Hub opens the same record, list or report (`/me?desk=<address>`).
- The directors (System Manager) keep Desk. Give anyone else Desk by adding the
  **VE Desk Access** role.
- Nothing else is changed: the Hub, web forms, the API, Raven, the ESS mobile app,
  files and printing work as before.

To switch it off, uninstall the app from the site on Frappe Cloud.

## Sent mail from Outlook in Hub Mail

ERPNext only reads each mailbox's Inbox, so emails sent from Outlook, webmail or a
phone never reached the Hub. Every 5 minutes `mail_sent_sync.sync_all` reads the Sent
folder of every Email Account with incoming mail switched on and files each new message
as a **Sent** email on that mailbox, so it shows in Hub Mail > Sent and in the client's
conversation it replies to.

- Emails sent from the Hub are already copied to the Sent folder with the same
  Message-ID, so they are recognised and not added twice.
- The first run looks back 3 days; after that only new messages are read.
- The mail server is only read (folder opened read-only); nothing is moved or marked.
- Problems are written to the Error Log as "VES Sent-folder sync failed".
