# TransportMe → Home Assistant Integration

Track your subscribed TransportMe bus live on the Home Assistant map, get ETA and distance sensors, and trigger automations when the bus is nearby.

---

## What you get

| Entity | Type | Description |
|--------|------|-------------|
| `device_tracker.transportme_bus_<id>` | Device Tracker | Live bus position on the HA map |
| `sensor.transportme_<id>_bus_eta` | Sensor | Minutes until the bus reaches your stop |
| `sensor.transportme_<id>_bus_distance` | Sensor | Kilometres between bus and your stop |
| `sensor.transportme_<id>_bus_speed` | Sensor | Current bus speed (km/h) |
| `sensor.transportme_<id>_bus_status` | Sensor | Running status (`running` / `not_running`) |

---

## Requirements

- A **TransportMe Passenger** account (email/password login)
- Home Assistant 2023.1 or later
- Your **operator ID** and **route IDs** (visible in the TransportMe app under your subscribed routes)

---

## Installation

### Via HACS (recommended)

1. In Home Assistant go to **HACS → Integrations → ⋮ → Custom repositories**
2. Add `https://github.com/vishal-mohan/transportme-ha` as an **Integration**
3. Search for **"TransportMe Bus Tracker"** and install
4. Restart Home Assistant

### Manual install

1. Copy the `custom_components/transportme/` folder into your HA config directory:
   ```
   /config/custom_components/transportme/
   ```
   On a Synology NAS (Docker install) the config folder is typically at:
   ```
   /volume1/docker/homeassistant/config/
   ```
   or accessible via the Samba share **`homeassistant`** → `config/`
2. Restart Home Assistant

---

## Setup

1. Go to **Settings → Devices & Services → + Add Integration**
2. Search for **"TransportMe Bus Tracker"**
3. **Step 1 – Sign in**: enter your TransportMe email and password. The integration authenticates directly with the TransportMe backend — no manual token capture needed.
4. **Step 2 – Configure routes**:

   | Field | Description |
   |-------|-------------|
   | **Subscription ID** | `operator_id:route_id,route_id,...` — e.g. `123:1,2,3` |
   | **Stop Latitude** *(optional)* | Latitude of your bus stop — enables distance sensor |
   | **Stop Longitude** *(optional)* | Longitude of your bus stop |
   | **Poll interval (seconds)** | How often to refresh positions (10–300 s, default 30) |

5. Click **Submit**. HA will verify the credentials and create the entities.

> **How to find your operator and route IDs:** Open the TransportMe app, go to your tracked routes, and note the operator and route numbers shown. Your operator ID is the number associated with the bus company.

---

## Find your stop coordinates

Open Google Maps, long-press on your bus stop, and the coordinates appear at the top of the screen (e.g. `-33.8688, 151.2093`).

---

## Dashboard card

Paste this into a Lovelace YAML card (type: **Manual**), replacing `12345` with your subscription ID:

```yaml
type: vertical-stack
cards:
  - type: map
    title: My Bus
    entities:
      - entity: device_tracker.transportme_bus_12345
    hours_to_show: 0.25
    default_zoom: 14
  - type: glance
    title: Bus Info
    entities:
      - entity: sensor.transportme_12345_bus_eta
        name: ETA
      - entity: sensor.transportme_12345_bus_distance
        name: Distance
      - entity: sensor.transportme_12345_bus_speed
        name: Speed
      - entity: sensor.transportme_12345_bus_status
        name: Status
```

---

## Example automations

### Notification when bus is 10 minutes away

```yaml
alias: Bus arriving soon notification
trigger:
  - platform: numeric_state
    entity_id: sensor.transportme_12345_bus_eta
    below: 10
condition:
  - condition: time
    after: "06:00:00"
    before: "09:00:00"
action:
  - service: notify.mobile_app_your_phone
    data:
      title: "🚌 Bus arriving"
      message: "Your bus is {{ states('sensor.transportme_12345_bus_eta') }} minutes away!"
```

### Turn on the porch light when bus is 2 km away

```yaml
alias: Bus nearby – porch light on
trigger:
  - platform: numeric_state
    entity_id: sensor.transportme_12345_bus_distance
    below: 2
action:
  - service: light.turn_on
    target:
      entity_id: light.porch
```

### Announce on a speaker

```yaml
alias: Bus ETA announcement
trigger:
  - platform: numeric_state
    entity_id: sensor.transportme_12345_bus_eta
    below: 8
action:
  - service: tts.google_translate_say
    data:
      entity_id: media_player.kitchen_speaker
      message: "Heads up — the bus arrives in {{ states('sensor.transportme_12345_bus_eta') }} minutes."
```

---

## Re-configuring

Go to **Settings → Devices & Services → TransportMe → Configure**.

- All settings are pre-filled — change what you need and save.
- To update your login credentials, tick the **Re-authenticate** toggle and enter new credentials. Leave it unticked to save settings without re-authenticating.

---

## Troubleshooting

**Entities show "unavailable"**
- Check the Home Assistant logs (Settings → System → Logs) for details.
- If you see an auth error, go to Settings → Devices & Services → TransportMe → Configure and tick **Re-authenticate** to re-enter your credentials.

**"No trackable routes found"**
- Double-check your Subscription ID format: `operator_id:route_id,route_id` (e.g. `123:1,2`).

**ETA sensor shows "unknown" but location works**
- Make sure you entered valid Stop Latitude and Stop Longitude coordinates.

**Bus location not updating**
- The bus may not be running. The `status` sensor will show `not_running` when no vehicles are active for your routes.

---

## How it works

```
TransportMe app (your phone)
        │  Firebase auth + GraphQL polling
        ▼
TransportMe API  (GraphQL endpoint)
        │
        │  This integration polls the same API
        │  every 30 s (configurable) using your
        │  Firebase credentials (auto-refreshed)
        ▼
Home Assistant
  ├── device_tracker  →  Map card
  ├── sensor: ETA     →  Automations / notifications
  ├── sensor: distance
  ├── sensor: speed
  └── sensor: status
```

Authentication uses Firebase email/password sign-in and automatically refreshes the short-lived ID token in the background — no manual token management required.

---

## Contributing

Issues and pull requests welcome at https://github.com/vishal-mohan/transportme-ha

## Disclaimer

This integration is not affiliated with or endorsed by TransportMe. It uses the same API endpoints as the official app.
