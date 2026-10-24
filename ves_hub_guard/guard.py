"""Hub-first access for Value Energy Solutions Ltd.

Staff work in the Value Energy Hub (/me). Desk is for the directors.

Every request for a Desk address (/desk, /app or /apps) from a logged-in user who has
neither the System Manager role nor the "VE Desk Access" role is sent to the Hub before
Desk loads. The Hub opens the same record, list or report there (/me?desk=<Desk address>).

Everything else (the Hub, web forms, the API, Raven, the ESS mobile app, files, printing)
is not touched. Guests are left to Frappe, which sends them to the login page.
"""

from urllib.parse import quote

import frappe
from werkzeug.exceptions import HTTPException
from werkzeug.utils import redirect

DESK_ROLES = ("System Manager", "VE Desk Access")
DESK_PREFIXES = ("/desk", "/app", "/apps")
HUB = "/me"


class SendToHub(HTTPException):
    """A plain 302 to the Hub. Not 301/308, so browsers never remember it
    (a staff member who is later given Desk access is not stuck)."""

    code = 302

    def __init__(self, location):
        super().__init__(response=redirect(location, code=302))


def is_desk_path(path):
    path = path or ""
    for prefix in DESK_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


def may_use_desk(user):
    if not user or user == "Guest":
        return True  # Frappe handles guests (login page)
    if user == "Administrator":
        return True
    roles = frappe.get_roles(user)
    return any(role in roles for role in DESK_ROLES)


def hub_address(path, query_string=b""):
    if isinstance(query_string, bytes):
        query_string = query_string.decode("utf-8", "ignore")
    target = path + ("?" + query_string if query_string else "")
    if path.rstrip("/") in DESK_PREFIXES:
        return HUB
    return HUB + "?desk=" + quote(target, safe="")


def before_request():
    try:
        request = getattr(frappe.local, "request", None)
        if request is None or request.method not in ("GET", "HEAD"):
            return
        path = request.path or ""
        if not is_desk_path(path):
            return
        user = getattr(getattr(frappe, "session", None), "user", None)
        if may_use_desk(user):
            return
        location = hub_address(path, getattr(request, "query_string", b""))
    except Exception:
        # Never let this check break a request; if anything is unexpected, do nothing.
        return
    raise SendToHub(location)
