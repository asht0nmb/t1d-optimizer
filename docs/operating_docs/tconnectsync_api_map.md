# tconnectsync API Map

Complete reference for every level of the tconnectsync package.

---

## Table of Contents
1. [Main Entry Point](#1-main-entry-point)
2. [API Layer](#2-api-layer)
3. [Event Parser Layer](#3-event-parser-layer)
4. [Domain Layer](#4-domain-layer)
5. [Sync Layer](#5-sync-layer)
6. [Parser Layer](#6-parser-layer)
7. [Nightscout API](#7-nightscout-api)
8. [Call Hierarchy](#8-call-hierarchy)
9. [Key Data Flow Example](#9-key-data-flow-example-cgm-reading)
10. [Configuration & Secrets](#10-configuration--secrets)

---

## 1. Main Entry Point

**File:** `tconnectsync/__init__.py`

```python
parse_args(*args, **kwargs) → argparse.Namespace
    # CLI flags: --pretend, --verbose, --start-date, --end-date,
    #            --days, --auto-update, --check-login, --features, --region

main(*args, **kwargs)
    # Orchestrates full sync workflow
```

**Flow:**
1. Parse CLI args
2. Create `TConnectApi` (login to Tandem)
3. Create `NightscoutApi` (upload target)
4. Run either `TandemSourceAutoupdate.process()` or `TandemSourceProcessTimeRange.process()`

**Key exports:** `TConnectApi`, `NightscoutApi`, `TandemSourceAutoupdate`, `TandemSourceProcessTimeRange`, `TandemSourceChooseDevice`

---

## 2. API Layer

### 2.1 `TConnectApi` — `api/__init__.py`

Top-level wrapper. Lazily instantiates child APIs.

```python
class TConnectApi:
    def __init__(self, email: str, password: str, region: str = 'US')

    # Lazy-load properties (cached after first access):
    @property tandemsource  → TandemSourceApi
    @property controliq     → ControlIQApi
    @property ws2           → WS2Api
    @property android       → AndroidApi
    @property webui         → WebUIScraper
```

---

### 2.2 `TandemSourceApi` — `api/tandemsource.py`

**The main API you'll use.** Authenticates with Tandem Source and fetches pump events.

```python
class TandemSourceApi:
    def __init__(self, email: str, password: str, region: str = 'US')

    # Auth
    def login(email, password)
        # Full OIDC + PKCE login flow. Caches credentials to disk.
    def extract_jwt()
        # Extracts pumperId, accountId from id_token JWT
    def try_load_cached_creds(email) → bool
        # Loads pickled credentials if not expired
    def cache_creds(email)
        # Pickles credentials to CACHE_CREDENTIALS_PATH
    def needs_relogin() → bool
        # True if access token expires within 5 min

    # Data
    def pumper_info() → dict
        # Returns user/pump account info

    def pump_event_metadata() → List[dict]
        # Returns one dict per pump ever registered on the account:
        # {
        #   'tconnectDeviceId': int,
        #   'serialNumber': str,
        #   'modelNumber': str,
        #   'minDateWithEvents': str (ISO),
        #   'maxDateWithEvents': str (ISO),
        #   'lastUpload': dict,
        #   'patientName': str,
        #   'patientDateOfBirth': str,
        #   'patientCareGiver': str or None,
        #   'softwareVersion': str,
        #   'partNumber': str
        # }

    def pump_events_raw(
        tconnect_device_id,
        min_date=None,
        max_date=None,
        event_ids_filter=DEFAULT_EVENT_IDS
    ) → str (base64)
        # Raw base64-encoded binary event blob from Tandem Source API

    def pump_events(
        tconnect_device_id,
        min_date=None,
        max_date=None,
        fetch_all_event_types=False
    ) → Generator[BaseEvent subclass]
        # Decodes pump_events_raw() and yields typed event objects
        # Each object is a dataclass — see Section 3 for all types
```

**Region URLs:**
| Region | Login | Source |
|--------|-------|--------|
| US | `tdcservices.tandemdiabetes.com` | `source.tandemdiabetes.com` |
| EU | `tdcservices.eu.tandemdiabetes.com` | `source.eu.tandemdiabetes.com` |

---

### 2.3 `ControlIQApi` — `api/controliq.py`

Legacy API. Uses web scraping to authenticate via t:connect website.

```python
class ControlIQApi:
    def __init__(self, email: str, password: str)
    def login(email, password)         # Scrapes ASP.NET login form
    def needs_relogin() → bool
    def api_headers() → dict           # Bearer token headers
    def get(endpoint, query, tries=0)  # HTTP GET with retry + auto-relogin on 401
```

---

### 2.4 `AndroidApi` — `api/android.py`

OAuth2 password grant flow using embedded Android client credentials.

```python
class AndroidApi:
    def __init__(self, email: str, password: str)
    def login(email, password)         # OAuth2 password grant
    def needs_relogin() → bool
    def get(endpoint, query={}, tries=0, **kwargs)
    def post(endpoint, query={}, **kwargs)
    def last_event_uploaded(pump_serial_number) → {'maxPumpEventIndex': int, 'processingStatus': int}
    def patient_info() → dict
```

---

### 2.5 `WS2Api` — `api/ws2.py`

Fetches therapy timeline from legacy t:connect web service.

```python
class WS2Api:
    def __init__(self, userGuid: str)
    def get(endpoint, **kwargs)
    def get_jsonp(endpoint, **kwargs)  # Strips cb(...) wrapper

    def therapy_timeline_csv(start=None, end=None, tries=0) → dict
        # Returns multi-section CSV parsed into:
        # {
        #   'readingData': List[dict],   # CGM EGV readings
        #   'iobData':     List[dict],   # IOB values
        #   'basalData':   List[dict],   # Basal rates
        #   'bolusData':   List[dict],   # Bolus events
        # }
        # Includes retry logic with exponential backoff on HTTP 500
```

---

### 2.6 `WebUIScraper` — `api/webui.py`

Scrapes device settings from t:connect web UI.

```python
class WebUIScraper:
    def __init__(self, controliq: ControlIQApi)

    def my_devices() → Dict[serialNumber: str, Device]
        # Returns dict of Device objects:
        # Device(name, model_number, status, guid)

    def device_settings_from_guid(pump_guid: str) → (List[Profile], DeviceSettings)
        # Scrapes device settings page
        # Profile fields: title, active, segments, calculated_total_daily_basal,
        #                 insulin_duration_min, carbs_enabled
        # ProfileSegment fields: display_time, time, basal_rate,
        #                        correction_factor, carb_ratio, target_bg_mgdl
        # DeviceSettings fields: low_bg_threshold, high_bg_threshold, raw_settings
```

---

### 2.7 `common.py` — Shared Utilities

```python
parse_date(date) → str              # → "MM-DD-YYYY"
parse_ymd_date(date) → str          # → "YYYY-MM-DD"
parsed_date_to_arrow(date) → Arrow
base_headers() → dict               # Random User-Agent headers
base_session() → requests.Session   # Session with optional proxy
days_between(start, end) → int
split_days_range(start, end, days=5) → List[Tuple[start, end]]

class ApiException(status_code, text)
class ApiLoginException(status_code, text)
```

---

## 3. Event Parser Layer

### 3.1 `eventparser/generic.py` — Entry Points

```python
EVENT_LEN = 26  # bytes per event

def Event(x: bytearray) → RawEvent | BaseEvent subclass
    # Parses 26 bytes into the appropriate typed event dataclass

def Events(x: bytes) → Generator
    # Batches decoded bytes into 26-byte chunks, yields typed event objects

def decode_raw_events(raw: str) → bytes
    # Base64-decodes the blob from pump_events_raw()
```

---

### 3.2 `eventparser/raw_event.py` — Base Types

```python
TANDEM_EPOCH = 1199145600   # Tandem's custom epoch offset

@dataclass
class RawEvent:
    source: int          # bits 12-15
    id: int              # bits 0-11 (event type ID)
    timestampRaw: int    # uint32 at offset 2
    seqNum: int          # uint32 at offset 6
    raw: bytearray       # full 26-byte buffer

    @staticmethod
    def build(raw: bytearray) → RawEvent

    @property timestamp() → Arrow     # timestampRaw + TANDEM_EPOCH → user timezone
    def todict() → dict

class BaseEvent:
    @property eventTimestamp() → Arrow
    @property eventId() → int
    @property seqNum() → int
    def todict() → dict
```

---

### 3.3 `eventparser/events.py` — All Event Types

All ~70 classes inherit from `BaseEvent`. Each has `eventTimestamp`, `seqNum`, `eventId`, and `todict()`.

**CGM Readings:**
```python
LidCgmDataGxb    # Dexcom G6
LidCgmDataG7     # Dexcom G7
LidCgmDataFsl2   # FreeStyle Libre 2
    .currentglucosedisplayvalue: int    # mg/dL
    .egvTimestamp: Arrow
    .seqNum: int
```

**Bolus Events:**
```python
LidBolusRequestedMsg1
    .carbamount: int          # grams, ready to use (do NOT ÷1000 — verified against CSV)
    .BG: int                  # mg/dL
    .iob: float

LidBolusRequestedMsg2
    .useroverride: int
    .declinedcorrection: int
    .optionsRaw: int
    .OptionsMap: dict

LidBolusRequestedMsg3
    .totalRequestedInsulin: float

LidBolusCompleted
    .insulindelivered: float
    .bolusid: int

LidBolexCompleted   # Extended bolus
    .insulindelivered: float
```

**Basal Events:**
```python
LidBasalDelivery             # Every 5 minutes
    .commandedRate: float    # milliunits/hr — divide by 1000 for u/hr
    .commandedRateSource: CommandedRateSourceBitmask

LidBasalRateChange
    .commandedbasalrate: float
    .basebasalrate: float
    .maxbasalrate: float
    .IDP: int
    .changetype: ChangetypeBitmask
    # Changetype values: TimedSegment, NewProfile, TempRateStart, TempRateEnd,
    #                    PumpSuspended, PumpResumed, PumpShutDown, BasalLimit
```

**System Events:**
```python
LidPumpingSuspended
LidPumpingResumed
LidCartridgeFilled
LidCannulaFilled
LidTubingFilled
LidAlarmActivated
    .alarmId: int
    .AlarmMap: dict    # alarm ID → description
LidMalfunctionActivated
LidAaUserModeChange    # Control-IQ mode (exercise, sleep, etc.)
LidDailyBasal          # Daily summary
LidNewDay
LidTimeChanged
LidDateChanged
LidVersionInfo
LidBgReadingTaken      # Manual BG fingerstick
LidShelfMode
LidDataLogCorruption
```

**CGM Session Events:**
```python
LidCgmAlertActivated
LidCgmAlertCleared
LidCgmStartSession*    # Multiple variants per sensor type
LidCgmJoinSession*
LidCgmStopSession*
```

---

## 4. Domain Layer

### 4.1 `domain/bolus.py`

```python
@dataclass
class Bolus:
    description: str
    complete: str              # "1" or "0"
    completion: str
    request_time: str          # ISO timestamp
    completion_time: str
    insulin: str               # units delivered
    requested_insulin: str
    carbs: str
    bg: str                    # may be ""
    user_override: str
    extended_bolus: str        # "1" or "0"
    bolex_completion_time: str
    bolex_start_time: str

    def to_dict() → dict
    @property is_extended_bolus() → bool
```

---

### 4.2 `domain/device_settings.py`

```python
@dataclass
class Device:
    name: str
    model_number: str
    status: str
    guid: Optional[str]

@dataclass
class ProfileSegment:
    display_time: str     # "Midnight", "Noon", etc.
    time: str             # "HH:MM"
    basal_rate: float     # u/hr
    correction_factor: int  # 1u corrects X mg/dL
    carb_ratio: float     # 1u covers X grams
    target_bg_mgdl: int

@dataclass
class Profile:
    title: str
    active: bool
    segments: List[ProfileSegment]
    calculated_total_daily_basal: float
    insulin_duration_min: int
    carbs_enabled: bool

    def activeProfile() → Profile
    def copy() → Profile

@dataclass
class DeviceSettings:
    low_bg_threshold: int
    high_bg_threshold: int
    raw_settings: dict
```

---

### 4.3 `domain/tandemsource/event_class.py`

Groups event types into logical categories for sync processors.

```python
class EventClass(set, Enum):
    BASAL             = {LidBasalDelivery, ...}
    BASAL_SUSPENSION  = {LidPumpingSuspended}
    BASAL_RESUME      = {LidPumpingResumed}
    ALARM             = {LidAlarmActivated, LidMalfunctionActivated}
    BOLUS             = {LidBolusRequestedMsg1/2/3, LidBolusCompleted, LidBolexCompleted}
    CARTRIDGE         = {LidCartridgeFilled, LidCannulaFilled, LidTubingFilled}
    CGM_ALERT         = {LidCgmAlertActivated, ...}
    CGM_START_JOIN_STOP = {LidCgmStart*, LidCgmJoin*, LidCgmStop*}
    CGM_READING       = {LidCgmDataGxb, LidCgmDataG7, LidCgmDataFsl2}
    USER_MODE         = {LidAaUserModeChange}
    DEVICE_STATUS     = {LidDailyBasal}

    @staticmethod
    def for_event(evt) → Optional[EventClass]
```

---

### 4.4 `domain/tandemsource/pump_settings.py`

Dataclasses mirroring the `lastUpload.settings` structure from `pump_event_metadata()`.

```python
@dataclass
class PumpProfileSegment:
    startTime: int      # minutes from midnight
    basalRate: int      # milliunits/hr (divide by 1000 for u/hr)
    isf: int            # insulin sensitivity factor
    carbRatio: int      # carb ratio (milliunits)
    targetBg: int       # mg/dL
    @property skip() → bool   # True if all zeros

@dataclass
class PumpProfile:
    name: str
    idp: int
    tDependentSegs: List[PumpProfileSegment]
    insulinDuration: int   # minutes
    carbEntry: int         # 1/0
    maxBolus: int          # milliunits

@dataclass
class PumpProfiles:
    activeIdp: int
    profile: List[PumpProfile]

@dataclass
class PumpGlucoseAlertSettings:
    mgPerDl: int
    enabled: int       # 1/0
    duration: int      # minutes
    status: int

@dataclass
class PumpCgmSettings:
    highGlucoseAlert: PumpGlucoseAlertSettings
    lowGlucoseAlert: PumpGlucoseAlertSettings

@dataclass
class PumpSettings:
    profiles: PumpProfiles
    cgmSettings: PumpCgmSettings
```

---

## 5. Sync Layer

### 5.1 `ProcessTimeRange` — `sync/tandemsource/process.py`

Fetches events and uploads them to Nightscout for a given time range.

```python
class ProcessTimeRange:
    def __init__(
        self,
        tconnect: TConnectApi,
        nightscout: NightscoutApi,
        tconnectDevice: dict,     # from pump_event_metadata()
        pretend: bool,
        secret: module,
        features: List[str] = DEFAULT_FEATURES
    )

    def process(time_start, time_end) → (int count, int last_seqnum)
        # 1. Fetch pump_events() for time range
        # 2. Categorize by EventClass
        # 3. For each enabled processor: .process() → .write()
        # 4. For each updater: .update()
```

**Processor Map:**
| EventClass | Processor |
|---|---|
| `BASAL` | `ProcessBasal` |
| `BASAL_SUSPENSION` | `ProcessBasalSuspension` |
| `BASAL_RESUME` | `ProcessBasalResume` |
| `ALARM` | `ProcessAlarm` |
| `BOLUS` | `ProcessBolus` |
| `CARTRIDGE` | `ProcessCartridge` |
| `CGM_ALERT` | `ProcessCGMAlert` |
| `CGM_START_JOIN_STOP` | `ProcessCGMStartJoinStop` |
| `CGM_READING` | `ProcessCGMReading` |
| `USER_MODE` | `ProcessUserMode` |
| `DEVICE_STATUS` | `ProcessDeviceStatus` |

---

### 5.2 `TandemSourceAutoupdate` — `sync/tandemsource/autoupdate.py`

Runs continuously, polling for new pump data.

```python
class TandemSourceAutoupdate:
    def __init__(self, secret)

    def process(tconnect, nightscout, pretend, features) → int (exit code)
        # Infinite loop:
        # 1. Poll pump_event_metadata() for new maxDateWithEvents
        # 2. If new data: call ProcessTimeRange.process()
        # 3. Sleep (uses rolling average of update frequency)
        # 4. Exit on AUTOUPDATE_FAILURE_MINUTES / NO_DATA failure timeouts
```

---

### 5.3 `ChooseDevice` — `sync/tandemsource/choose_device.py`

```python
class ChooseDevice:
    def __init__(self, secret, tconnect: TConnectApi)

    def choose() → dict   # returns one pump metadata dict
        # If PUMP_SERIAL_NUMBER set → validate and return that pump
        # Otherwise → return pump with most recent maxDateWithEvents
        # Warns if pump data is >3 days old
```

---

### 5.4 Event Processor Interface (all processors)

All processors follow this pattern:

```python
class ProcessXXX:
    def enabled() → bool
        # Checks if this feature is in the features list

    def process(events, time_start, time_end) → List[dict]
        # 1. Query Nightscout for last uploaded entry of this type
        # 2. Filter events newer than last upload
        # 3. Convert to Nightscout-format dicts
        # 4. Return list

    def write(ns_entries) → int
        # Upload each entry to Nightscout, return count
```

---

## 6. Parser Layer

### 6.1 `NightscoutEntry` — `parser/nightscout.py`

Static methods that convert pump data to Nightscout-format dicts.

```python
NightscoutEntry.entry(sgv: int, created_at: str, pump_event_id: str = "") → dict
    # {"type": "sgv", "sgv": 145, "date": epoch_ms, "dateString": "...", ...}

NightscoutEntry.basal(value: float, duration_mins: float, created_at: str, ...) → dict

NightscoutEntry.bolus(bolus: float, carbs: int, created_at: str, ...) → dict

NightscoutEntry.iob(iob: float, created_at: str) → dict

NightscoutEntry.sitechange(created_at, reason="", pump_event_id="") → dict

NightscoutEntry.basalsuspension(created_at, reason="", pump_event_id="") → dict

NightscoutEntry.basalresume(created_at, pump_event_id="") → dict

NightscoutEntry.alarm(created_at, reason="", pump_event_id="") → dict

NightscoutEntry.cgm_alert(created_at, reason="", pump_event_id="") → dict
```

**Event Type Constants:**
```python
BASAL_EVENTTYPE          = "Temp Basal"
BOLUS_EVENTTYPE          = "Combo Bolus"
SITECHANGE_EVENTTYPE     = "Site Change"
BASALSUSPENSION_EVENTTYPE = "Basal Suspension"
BASALRESUME_EVENTTYPE    = "Basal Resume"
ALARM_EVENTTYPE          = "Alarm"
CGM_ALERT_EVENTTYPE      = "CGM Alert"
CGM_START_EVENTTYPE      = "Sensor Start"
CGM_STOP_EVENTTYPE       = "Sensor Stop"
ENTERED_BY               = "Pump (tconnectsync)"
```

---

## 7. Nightscout API

### `NightscoutApi` — `nightscout.py`

```python
class NightscoutApi:
    def __init__(self, url: str, secret: str, skip_verify=False, ignore_conn_errors=False)

    def upload_entry(ns_format: dict, entity: str = 'treatments') → None
        # POST /api/v1/{entity}

    def delete_entry(entity: str) → None
    def put_entry(ns_format: dict, entity: str) → None

    def last_uploaded_entry(
        eventType: str,
        time_start: Arrow = None,
        time_end: Arrow = None
    ) → dict or None
        # GET /api/v1/treatments with type filter

    def last_uploaded_bg_entry(
        time_start: Arrow = None,
        time_end: Arrow = None
    ) → dict or None
        # GET /api/v1/entries.json

    def last_uploaded_activity(
        activityType: str,
        time_start: Arrow = None,
        time_end: Arrow = None
    ) → dict or None
        # GET /api/v1/activity
```

---

## 8. Call Hierarchy

```
main()
├── parse_args()
├── TConnectApi(email, password, region)
│   └── .tandemsource [lazy]
│       └── TandemSourceApi(email, password)
│           └── login()
│               ├── try_load_cached_creds()   [fast path]
│               └── OIDC/PKCE flow            [full login]
│                   ├── POST login_api_url
│                   ├── GET authorization_endpoint (code)
│                   ├── POST token_endpoint (access_token, id_token)
│                   └── extract_jwt() → pumperId, accountId
│
├── NightscoutApi(url, secret)
│
└── EITHER:
    ┌─ TandemSourceAutoupdate.process()          [--auto-update]
    │  └── loop:
    │      ├── ChooseDevice.choose()
    │      │   └── pump_event_metadata()
    │      └── ProcessTimeRange.process()
    │          └── [same as below]
    │
    └─ TandemSourceProcessTimeRange.process()    [--start/end-date]
        ├── TandemSourceApi.pump_events(device_id, start, end)
        │   ├── pump_events_raw() → base64 string
        │   └── decode_raw_events() + Events() → Generator[typed event objects]
        ├── EventClass.for_event(evt) → categorize
        └── For each EventClass:
            ├── ProcessXXX.process(events) → List[Nightscout dicts]
            └── ProcessXXX.write(dicts) → upload to Nightscout
```

---

## 9. Key Data Flow Example: CGM Reading

```
TandemSourceApi.pump_events(device_id, start, end)
  │
  ├── pump_events_raw() → base64 string
  ├── decode_raw_events() → bytes
  └── Events() generator
        └── yields LidCgmDataGxb / LidCgmDataG7 / LidCgmDataFsl2
              .currentglucosedisplayvalue  → sgv (mg/dL)
              .egvTimestamp                → created_at
              .seqNum                      → pump_event_id

  ↓ (if using sync layer)

ProcessCGMReading.process(events)
  ├── nightscout.last_uploaded_bg_entry() → filter to only new events
  └── NightscoutEntry.entry(sgv, created_at, pump_event_id)
        → {"type": "sgv", "sgv": 145, "date": 1234567890000, "dateString": "..."}

ProcessCGMReading.write(entries)
  └── nightscout.upload_entry(entry, entity='entries') × N
```

---

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

**Feature Flags** (from `features.py`):

`DEFAULT_FEATURES` and `ALL_FEATURES` control which event types sync. Pass via `--features` CLI arg or `features=` param.
