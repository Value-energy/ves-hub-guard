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
- A reply you send can also land in your own Inbox (e.g. Bcc to yourself); ERPNext's Inbox fetch then
  files it as Received. When the sync finds that email in the Sent folder it re-files it as Sent.
  Version 1.1.2 also re-filed the older ones once (patch `own_sent_copies_to_sent`).
- A message that cannot be filed is written to the Error Log ("VES Sent-folder sync: message …")
  and tried again on the next runs, up to 5 times.
- Problems with a whole mailbox are written to the Error Log as "VES Sent-folder sync failed".

## Auto-generated emails in Hub Mail

ERPNext skips every incoming email marked `Auto-Submitted: auto-generated` (support-ticket updates such as
GitHub Support, many system notifications) and keeps it as an *Unhandled Email*. Every 5 minutes
`mail_auto_generated.import_all` files those (last 30 days) as Received emails on their mailbox, once each
(matched by Message-ID; the Unhandled Email is marked as filed). No auto-reply is ever sent for them.
