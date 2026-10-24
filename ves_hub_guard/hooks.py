app_name = "ves_hub_guard"
app_title = "VES Hub Guard"
app_publisher = "Value Energy Solutions Ltd"
app_description = "Hub-first access: staff work in the Value Energy Hub, Desk is for the directors"
app_email = ""
app_license = "mit"

required_apps = ["frappe"]

# Staff who open any Desk address are sent to the Hub before Desk loads (see guard.py).
before_request = ["ves_hub_guard.guard.before_request"]
