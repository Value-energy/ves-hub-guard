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
