"""Constants for Fenton Home Push."""

from __future__ import annotations

DOMAIN = "fenton_push"

CONF_KEY = "key"
CONF_KEY_ID = "key_id"
CONF_TEAM_ID = "team_id"
CONF_BUNDLE_ID = "bundle_id"
#: Options.
CONF_CRITICAL_ALERTS = "critical_alerts"
CONF_PUSH_BASE_URL = "push_base_url"

DEFAULT_BUNDLE_ID = "com.sfenton.home"

#: ``app_data`` key the app sets to ``sandbox`` (Xcode installs) or ``production`` (TestFlight, App Store).
APP_DATA_ENVIRONMENT = "apns_environment"

#: Our devices' ``device_name`` suffix, which keeps their notify services apart from the official app's.
DEVICE_NAME_SUFFIX = " · Fenton"

#: Base notify service; person targets become ``notify.fenton_<name>``.
NOTIFY_SERVICE_NAME = "fenton"
TARGET_EVERYONE = "everyone"

#: Pushes per device per UTC day, a guard against runaway automations (Apple sets no fixed cap).
DAILY_LIMIT = 1000

API_CONFIG = "/api/fenton_push/config"
API_PUSH = "/api/fenton_push/apns"

MOBILE_APP = "mobile_app"
