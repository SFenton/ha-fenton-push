"""Notification bodies as Home Assistant's mobile_app posts them, modelled on the household's real automations.

Shared by the unit tests and ``tests/fixtures`` (the APNs payloads the app tests against with ``simctl push``).
"""

REGISTRATION_INFO = {"app_id": "com.sfenton.home", "app_version": "0.1.0 (1)", "os_version": "27.0",
                     "webhook_id": "hook-stephen-iphone"}

FRONT_DOOR_UNSECURED = {
    "message": "The front door is unlocked and nobody is home. Lock it from here or open Security.",
    "title": "Front Door · Unlocked While Away",
    "data": {
        "tag": "sfenton-front-door-unsecured-42",
        "group": "front-door-unsecured",
        "url": "/sfenton-react-dash/home?path=security",
        "clickAction": "/sfenton-react-dash/home?path=security",
        "alert_once": False,
        "push": {"interruption-level": "time-sensitive", "sound": "default"},
        "actions": [
            {"action": "FRONT_DOOR_LOCK_42", "title": "Confirm: Lock Front Door", "authenticationRequired": True,
             "activationMode": "background", "icon": "sfsymbols:lock"},
            {"action": "URI", "title": "Open Security", "uri": "/sfenton-react-dash/home?path=security",
             "icon": "sfsymbols:shield"},
        ],
    },
    "push_token": "a" * 64,
    "registration_info": REGISTRATION_INFO,
}

DOORBELL = {
    "message": "Someone is at the front door.",
    "title": "Doorbell",
    "data": {
        "tag": "doorbell",
        "url": "/sfenton-react-dash/home?path=security#camera-front-door",
        "entity_id": "camera.front_door_camera",
        "push": {"interruption-level": "critical", "sound": {"name": "default", "critical": 1, "volume": 0.6}},
        "actions": [{"action": "URI", "title": "Open Camera",
                     "uri": "/sfenton-react-dash/home?path=security#camera-front-door"}],
    },
    "push_token": "a" * 64,
    "registration_info": REGISTRATION_INFO,
}

DONETICK = {
    "message": "Empty the dishwasher is past due.",
    "title": "Chore past due",
    "data": {
        "tag": "donetick_task_17",
        "url": "/sfenton-react-dash/home?path=chores",
        "clickAction": "/sfenton-react-dash/home?path=chores",
        "push": {"sound": "default", "interruption-level": "active"},
        "actions": [
            {"action": "DONETICK_COMPLETE_17", "title": "Complete"},
            {"action": "DONETICK_SNOOZE_1H_17", "title": "Snooze 1 hour"},
            {"action": "DONETICK_SNOOZE_1D_17", "title": "Snooze 1 day"},
        ],
    },
    "push_token": "a" * 64,
    "registration_info": REGISTRATION_INFO,
}

CLEAR = {
    "message": "clear_notification",
    "data": {"tag": "sfenton-front-door-unsecured-42"},
    "push_token": "a" * 64,
    "registration_info": REGISTRATION_INFO,
}

FIXTURES = {
    "front-door-unsecured": FRONT_DOOR_UNSECURED,
    "doorbell": DOORBELL,
    "donetick": DONETICK,
    "clear-notification": CLEAR,
}
