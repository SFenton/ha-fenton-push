# Fenton Home Push: app contract (v1)

How the Fenton Home iOS app (bundle `com.sfenton.home`) and this integration talk. Home Assistant's own
`mobile_app` integration sits in the middle and is used unchanged.

## 1. Ask the integration where to point pushes

`GET /api/fenton_push/config` (Home Assistant access token) →

```json
{"available": true, "push_url": "http://192.168.1.20:8123/api/fenton_push/apns", "bundle_id": "com.sfenton.home", "critical_alerts": false}
```

A 404 means the integration isn't installed: the app doesn't register at all, so no stray Mobile App device appears
in Home Assistant, and it asks again on the next launch. `available` is false (and `push_url` null) when it is installed
but not set up, or when `bundle_id` isn't the app's: the app then registers without `push_url`/`push_token` (websocket
push only) and adds them on a later `update_registration`. Until the app connects, a notification to such a device is logged and skipped; the person's other devices still get it.

## 2. Register as a mobile device

`POST /api/mobile_app/registrations` with the signed-in user's access token, as Home Assistant documents:

```json
{
  "app_id": "com.sfenton.home",
  "app_name": "Fenton Home",
  "app_version": "0.1.0 (1)",
  "device_name": "Stephen's iPhone · Fenton",
  "manufacturer": "Apple",
  "model": "iPhone19,2",
  "os_name": "iOS",
  "os_version": "27.0",
  "device_id": "<identifierForVendor>",
  "supports_encryption": false,
  "app_data": {
    "push_url": "<from step 1>",
    "push_token": "<APNs device token, lowercase hex>",
    "push_websocket_channel": true,
    "apns_environment": "sandbox"
  }
}
```

- `device_name` must end in ` · Fenton`. Home Assistant names notify services after the device name, so the
  suffix keeps ours (`notify.mobile_app_stephen_s_iphone_fenton`) from colliding with the official app's.
- `apns_environment` is `sandbox` for Xcode-installed builds (aps-environment `development`) and
  `production` for TestFlight/App Store builds.
- Keep the returned `webhook_id` in the Keychain, keyed by Home Assistant user id. Reuse it on later launches
  for the same user; register again only for a new user or after the webhook answers 410.
- On every launch and whenever APNs hands out a new token, send the `update_registration` webhook with the
  same `app_data` (fresh token) and `app_version`.
- **Sign-out:** before revoking the refresh token, send `update_registration` with
  `"app_data": {}`. That drops push for this device, so the person's targets stop reaching it at once.
  Signing back in as the same user restores `app_data`.

## 3. What arrives (APNs `userInfo`)

Alert pushes:

```json
{
  "aps": {
    "alert": {"title": "Front Door · Unlocked While Away", "subtitle": "…", "body": "The front door is unlocked…"},
    "sound": "default",
    "interruption-level": "time-sensitive",
    "thread-id": "front-door-unsecured",
    "category": "fenton.dynamic",
    "mutable-content": 1
  },
  "fenton": {
    "v": 1,
    "tag": "sfenton-front-door-unsecured-42",
    "group": "front-door-unsecured",
    "url": "/sfenton-react-dash/home?path=security",
    "actions": [
      {"action": "FRONT_DOOR_LOCK_42", "title": "Confirm: Lock Front Door", "authenticationRequired": true,
       "activationMode": "background", "icon": "sfsymbols:lock"},
      {"action": "URI", "title": "Open Security", "uri": "/sfenton-react-dash/home?path=security", "icon": "sfsymbols:shield"}
    ],
    "entity_id": "camera.front_door_camera",
    "ext": {"category": "security", "screen": "security/front-door"}
  }
}
```

- `aps.category` is `fenton.dynamic` whenever `fenton.actions` is non-empty. The notification service
  extension builds a category from the action list (id derived from the action ids), registers it with
  `setNotificationCategories` (keeping the existing ones) and sets `content.categoryIdentifier` to it.
  Action options: `authenticationRequired` → `.authenticationRequired`, `destructive` → `.destructive`,
  `activationMode: foreground` → `.foreground`, `behavior: textInput` → `UNTextInputNotificationAction`
  (`textInputButtonTitle`, `textInputPlaceholder`). `icon: sfsymbols:<name>` → `UNNotificationActionIcon(systemImageName:)`.
- `mutable-content` is 1 when there are actions or media. Media keys, all optional: `entity_id` (a camera:
  fetch `/api/camera_proxy/<entity_id>` with the app's token), `image`, `video`, `attachment.url` (paths are
  relative to the Home Assistant URL unless absolute).
- `url` (or `clickAction`) is where a tap on the body goes. It may be a dashboard path; the app maps it to a
  native screen and ignores what it can't map.
- `ext` is the automation's `data.fenton` block, passed through untouched (category, screen, layout …).
- `presentation_options` and anything else unknown in `data` lands under `fenton.extra`.
- `interruption-level` `critical` (with `sound: {"critical": 1, "name": …, "volume": …}`) only arrives when
  the integration's critical-alerts option is on; otherwise the integration downgrades it to
  `time-sensitive` with the default sound.
- `aps.sound` is omitted for silent notifications (`push.sound: none`).

Background commands (`apns-push-type: background`, `aps.content-available: 1`, no alert):

```json
{"aps": {"content-available": 1}, "fenton": {"v": 1, "command": "clear_notification", "tag": "sfenton-front-door-unsecured-42"}}
```

Commands: `clear_notification` (remove delivered notifications whose `fenton.tag` matches), and
`request_location_update`, `update_widgets`, `clear_badge`, passed through for the app to honour or ignore.

## 4. Button presses back to Home Assistant

For every action except `URI`, send the registration's webhook:

```json
{"type": "fire_event", "data": {"event_type": "mobile_app_notification_action",
  "event_data": {"action": "FRONT_DOOR_LOCK_42", "tag": "sfenton-front-door-unsecured-42", "reply_text": "…"}}}
```

`reply_text` only for text-input actions. Home Assistant stamps the event with the registration's user, which
is what your automations' identity checks compare against. `URI` actions open `uri` in the app instead.

## 5. Testing without Apple

Every payload above can be delivered to a simulator with
`xcrun simctl push <udid> com.sfenton.home payload.apns` (top-level `"Simulator Target Bundle"` key or the
bundle argument). `tests/fixtures/*.apns` holds the integration's real output for the front-door, doorbell,
Donetick and clear cases.
