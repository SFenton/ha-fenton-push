# Fenton Home Push

A Home Assistant integration that delivers notifications to the **Fenton Home** iOS app through Apple Push, with targets that follow the person who is signed in.

The app registers as an ordinary Home Assistant mobile device. Its push address is this integration, which signs pushes with your own APNs key and sends them straight to Apple. Nothing runs outside Home Assistant, and existing automations keep their notification data unchanged: tags, groups, buttons, interruption levels, camera snapshots and `clear_notification`.

## Install

1. HACS → Integrations → ⋮ → Custom repositories → add `https://github.com/SFenton/ha-fenton-push` (category Integration), then download **Fenton Home Push** and restart Home Assistant.
2. In your Apple Developer account, open Certificates, Identifiers & Profiles → Keys, create a key with **Apple Push Notifications service** enabled and download the `.p8` file (Apple only lets you download it once). Note its Key ID and your Team ID.
3. Settings → Devices & services → Add integration → **Fenton Home Push**. Paste the whole `.p8` file, the Key ID and the Team ID. Apple checks the key before it is saved.
4. Open Fenton Home and allow notifications. The app registers itself; a new device appears under Mobile App named `<device> · Fenton`.

## Sending

Every Fenton Home device gets the usual `notify.mobile_app_<device>_fenton` service. On top of that:

| Service | Reaches |
|---|---|
| `notify.fenton_<first name>` (for example `notify.fenton_stephen`) | Every device where that person is signed in to Fenton Home |
| `notify.fenton` / `notify.fenton_everyone` | Everyone signed in to Fenton Home |

People are the `person` entities linked to a Home Assistant user. When two people share a first name, their target uses the person's entity id instead. A device stops receiving the moment its user signs out of the app.

`data` is the same as for the official app. Add an optional `fenton` block for app-only behaviour; the official app ignores it:

```yaml
action: notify.fenton_stephen
data:
  title: Front Door · Unlocked While Away
  message: The front door is unlocked and nobody is home.
  data:
    tag: front-door-unsecured-42
    push:
      interruption-level: time-sensitive
    actions:
      - action: FRONT_DOOR_LOCK_42
        title: "Confirm: Lock Front Door"
        authenticationRequired: true
    fenton:
      devices: [phone]        # phone, tablet, watch
      category: security
```

### Moving existing automations

Automations that already send through a notify group need no edits: add the Fenton targets to the group.

```yaml
notify:
  - platform: group
    name: stephen_s_phone
    services:
      - service: mobile_app_iphone_18_pro   # official app, while you still use it
      - service: fenton_stephen
```

Button handlers keep working: the app fires `mobile_app_notification_action` through its own registration, so the event carries the signed-in user just like the official app's.

## Options

- **Send critical alerts.** Leave off until Apple grants the app the Critical Alerts entitlement. While it is off, `critical` notifications arrive as time-sensitive with the default sound.
- **Push address base URL.** Where Home Assistant can reach itself (for example `http://127.0.0.1:8123`). Empty uses the internal URL.

## How it works

`docs/CONTRACT.md` is the wire contract with the app. In short:

- `GET /api/fenton_push/config` (access token) tells the app which `push_url` to register.
- `POST /api/fenton_push/apns` is that `push_url`. Home Assistant's `mobile_app` posts each notification there without credentials, so a request is honoured only when its webhook id and push token match a real Fenton Home registration. It maps the notification to an APNs payload (`payload.py`) and sends it over HTTP/2 with a token signed by your key (`apns.py`).
- A device gets at most 1,000 pushes per UTC day, as a guard against runaway automations.
- Apple tokens from Xcode builds use the sandbox service and TestFlight builds use production; the app says which, and a token from the other environment is retried there once.

## Development

`python -m pytest tests/unit` (no Home Assistant needed); CI also runs `tests/ha` with `pytest-homeassistant-custom-component`, plus HACS and hassfest validation. `tests/fixtures/*.apns` are real payloads for `xcrun simctl push`; regenerate them with `python tests/fixtures/build.py`.
