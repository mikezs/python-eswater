"""Constants for the Essex & Suffolk Water (NWG) portal API.

Derived from the reverse-engineered spec in ``docs/api.md`` (captured
22 Jul 2026). The portal is a same-origin ASP.NET Core app behind Cloudflare;
auth is a session cookie plus a short-lived LoginRadius JWT for smart-usage
endpoints.
"""

from __future__ import annotations

# Portal base URL; the JSON API lives under ``/api``.
BASE_URL = "https://www.eswater.co.uk"

# --- Endpoint paths -------------------------------------------------------
LOGIN_PATH = "/api/Auth/Login"
SAVE_USER_PROFILE_PATH = "/api/Auth/SaveUserProfile"
ACCOUNT_SUMMARY_PATH = "/api/Customer/GetAccountSummary"
ACCOUNT_DETAILS_PATH = "/api/Customer/GetAccountDetails"
SMART_AUTH_TOKEN_PATH = "/api/Customer/GetSmartAuthToken"

# Usage endpoints keyed by granularity value (see models.Granularity).
USAGE_PATHS = {
    "hourly": "/api/Customer/GetHourlyWaterUsage",
    "daily": "/api/Customer/GetDailyWaterUsage",
    "weekly": "/api/Customer/GetWeeklyWaterUsage",
    "monthly": "/api/Customer/GetMonthlyWaterUsage",
    "yearly": "/api/Customer/GetYearlyWaterUsage",
}

# The smart-usage JWT is issued server-side by GetSmartAuthToken (the portal
# talks to LoginRadius internally; no apikey or direct LoginRadius call needed).
# Its lifetime is ~10 minutes; refresh with this safety margin.
JWT_EXPIRY_MARGIN = 30  # seconds

DEFAULT_TIMEOUT = 30  # seconds

# A desktop-browser UA + this header are expected by the portal.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)
XHR_HEADER = {"X-Requested-With": "XMLHttpRequest"}
