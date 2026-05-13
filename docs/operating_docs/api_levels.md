# API Levels — What to Type to See Each Layer

Each level drills deeper. Start at Level 1 and work down.

> **Setup (required in every notebook session):**
> ```python
> from dotenv import load_dotenv
> import os
> load_dotenv()
> from tconnectsync.api.tandemsource import TandemSourceApi
> api = TandemSourceApi(os.getenv("TCONNECT_EMAIL"), os.getenv("TCONNECT_PASSWORD"))
> ```

---

## Level 1 — Your Account (What pumps exist?)

```python
metadata = api.pump_event_metadata()

for pump in metadata:
    print(pump['serialNumber'], pump['softwareVersion'], pump['minDateWithEvents'], '→', pump['maxDateWithEvents'])
```

**Returns:** One row per pump ever registered on your account. Shows serial number, firmware version, and date range of available data.

---

## Level 2 — One Device (Pick your current pump)

```python
# Your current pump is always the last one (most recent maxDateWithEvents)
device = metadata[-1]

print("Serial:", device['serialNumber'])
print("Software:", device['softwareVersion'])
print("Data from:", device['minDateWithEvents'], "to", device['maxDateWithEvents'])
print("Last uploaded:", device['lastUpload']['lastUploadedAt'])
```

---

## Level 2b — Device Settings (Basal profiles, ISF, carb ratios)

```python
settings = device['lastUpload']['settings']

# Active insulin profile
profiles = settings['profiles']
active_idp = profiles['activeIdp']
active_profile = next(p for p in profiles['profile'] if p['idp'] == active_idp)

print("Active profile:", active_profile['name'])
for seg in active_profile['tDependentSegs']:
    if seg['basalRate'] == 0:
        break
    print(f"  {seg['startTime']//60:02d}:{seg['startTime']%60:02d}  "
          f"basal={seg['basalRate']/1000:.3f}u/hr  "
          f"ISF={seg['isf']}  "
          f"carbRatio={seg['carbRatio']/1000:.1f}g/u  "
          f"target={seg['targetBg']}mg/dL")
```

---

## Level 3 — Raw Events (Everything for a date range)

```python
from collections import Counter

device_id = device['tconnectDeviceId']
events = list(api.pump_events(device_id, min_date="2026-03-01", max_date="2026-03-22"))

# See what event types you have and how many of each
Counter(type(e).__name__ for e in events)
```

**Returns:** All event types mixed together. Use this to understand what's in a time range before filtering.

---

## Level 4 — Filter by Event Type

Pick the event type you care about:

### CGM Readings (blood glucose every 5 min)
```python
from tconnectsync.eventparser.events import LidCgmDataGxb, LidCgmDataG7, LidCgmDataFsl2

cgm_events = [e for e in events if isinstance(e, (LidCgmDataGxb, LidCgmDataG7, LidCgmDataFsl2))]

# Peek at one
e = cgm_events[0]
print(e.eventTimestamp, e.currentglucosedisplayvalue, "mg/dL")
```

### Boluses
```python
from tconnectsync.eventparser.events import LidBolusCompleted

bolus_events = [e for e in events if isinstance(e, LidBolusCompleted)]

e = bolus_events[0]
print(e.eventTimestamp, e.insulindelivered, "units")
```

### Basal Rate Changes
```python
from tconnectsync.eventparser.events import LidBasalRateChange

basal_events = [e for e in events if isinstance(e, LidBasalRateChange)]

e = basal_events[0]
print(e.eventTimestamp, e.commandedbasalrate, "u/hr", "→ changetype:", e.changetype)
```

### Pump Suspensions
```python
from tconnectsync.eventparser.events import LidPumpingSuspended, LidPumpingResumed

suspensions = [e for e in events if isinstance(e, (LidPumpingSuspended, LidPumpingResumed))]
```

### Site Changes (cartridge/cannula/tubing)
```python
from tconnectsync.eventparser.events import LidCartridgeFilled, LidCannulaFilled, LidTubingFilled

site_changes = [e for e in events if isinstance(e, (LidCartridgeFilled, LidCannulaFilled, LidTubingFilled))]
```

### Bolus Requests (includes carbs + BG at time of bolus)
```python
from tconnectsync.eventparser.events import LidBolusRequestedMsg1

requests = [e for e in events if isinstance(e, LidBolusRequestedMsg1)]

e = requests[0]
print(e.eventTimestamp, "carbs:", e.carbamount, "g", "BG:", e.BG, "mg/dL")  # carbamount is raw grams; no /1000 needed
```

---

## Level 5 — Into a DataFrame (Ready for analysis)

### CGM → DataFrame
```python
import pandas as pd

cgm_df = pd.DataFrame([{
    'timestamp': e.eventTimestamp.datetime,
    'bg_mgdl': e.currentglucosedisplayvalue,
} for e in cgm_events])

cgm_df = cgm_df.sort_values('timestamp').reset_index(drop=True)
cgm_df.head()
```

### Boluses → DataFrame
```python
bolus_df = pd.DataFrame([{
    'timestamp': e.eventTimestamp.datetime,
    'insulin_units': e.insulindelivered,
    'bolus_id': e.bolusid,
} for e in bolus_events])

bolus_df.head()
```

### Bolus Requests (with carbs + BG) → DataFrame
```python
request_df = pd.DataFrame([{
    'timestamp': e.eventTimestamp.datetime,
    'carbs_g': e.carbamount,  # carbamount is raw grams; no /1000 needed
    'bg_mgdl': e.BG,
    'iob': e.iob,
} for e in requests])

request_df.head()
```

---

## Summary: The Drill-Down

```
api.pump_event_metadata()           ← Level 1: all your pumps
    └── metadata[-1]                ← Level 2: your current pump
        └── device['lastUpload']    ← Level 2b: settings (profiles, ISF, etc.)

api.pump_events(device_id, start, end)   ← Level 3: all events mixed together
    └── filter by isinstance()           ← Level 4: one event type
        └── build DataFrame             ← Level 5: ready for analysis
```

The jump from Level 3 → 4 is the most important one. You always pull everything at once, then filter.

## Data Types 
  ┌────────────────────────────┬─────────────┬─────────────────────────────────────────┐                                         
  │           Field            │  Raw type   │                  Notes                  │
  ├────────────────────────────┼─────────────┼─────────────────────────────────────────┤                                         
  │ eventTimestamp             │ arrow.Arrow │ convert to .datetime for pandas         │                                       
  ├────────────────────────────┼─────────────┼─────────────────────────────────────────┤
  │ currentglucosedisplayvalue │ int         │ mg/dL, ready to use                     │                                         
  ├────────────────────────────┼─────────────┼─────────────────────────────────────────┤                                         
  │ insulindelivered           │ float       │ units, ready to use                     │                                         
  ├────────────────────────────┼─────────────┼─────────────────────────────────────────┤                                         
  │ carbamount                 │ int         │ grams, ready to use (do NOT ÷1000)      │
  ├────────────────────────────┼─────────────┼─────────────────────────────────────────┤
  │ basalRate (settings)       │ int         │ milliunits/hr → divide by 1000 for u/hr │
  ├────────────────────────────┼─────────────┼─────────────────────────────────────────┤
  │ commandedRate (delivery)   │ float       │ milliunits/hr → divide by 1000 for u/hr │
  ├────────────────────────────┼─────────────┼─────────────────────────────────────────┤
  │ seqNum                     │ int         │ event sequence number                   │
  └────────────────────────────┴─────────────┴─────────────────────────────────────────┘                                         

## 10. Configuration & Secrets

All read from `.env` in cwd (or `~/.config/tconnectsync/.env`).

| Variable | Default | Description |
|---|---|---|
| `TCONNECT_EMAIL` | `email@email.com` | Tandem account email |
| `TCONNECT_PASSWORD` | `password` | Tandem account password |
| `TCONNECT_REGION` | `US` | `US` or `EU` |
| `PUMP_SERIAL_NUMBER` | `11111111` | Filter to specific pump; `None` = most recent |
| `NS_URL` | `https://yournightscouturl/` | Nightscout URL |
| `NS_SECRET` | `apisecret` | Nightscout API secret |
| `NS_SKIP_TLS_VERIFY` | `false` | Skip TLS cert check |
| `NS_IGNORE_CONN_ERRORS` | `false` | Don't fail on Nightscout connection errors |
| `TIMEZONE_NAME` | `America/New_York` | Timezone for arrow timestamps |
| `CACHE_CREDENTIALS` | `true` | Cache login credentials to disk |
| `CACHE_CREDENTIALS_PATH` | `.creds_cache` | Path for credential cache |
| `FETCH_ALL_EVENT_TYPES` | `false` | Fetch all event types vs filtered set |
| `IGNORE_ZERO_UNIT_BASAL` | `false` | Skip zero-unit basal entries |
| `REQUESTS_PROXY` | `` | Optional HTTP proxy |
| `AUTOUPDATE_DEFAULT_SLEEP_SECONDS` | `300` | Sleep between auto-update polls |
| `AUTOUPDATE_MAX_SLEEP_SECONDS` | `1500` | Max sleep duration |
| `AUTOUPDATE_FAILURE_MINUTES` | `75` | Fail if no sync in this many minutes |
| `AUTOUPDATE_NO_DATA_FAILURE_MINUTES` | `180` | Fail if no new pump data |
| `AUTOUPDATE_MAX_LOOP_INVOCATIONS` | `-1` | Max loop iterations (-1 = infinite) | 
