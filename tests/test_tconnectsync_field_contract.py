"""Contract test: ingestion/builders.py's assumed field names vs. the real
installed tconnectsync classes.

Why this exists: the 2026-08 tconnectsync v2->v3 migration silently broke
every attribute access in builders.py (v3 renamed nearly every field to
true camelCase), but the 742-test suite stayed green throughout the entire
47-day production outage. Root cause: tests/test_builders.py et al. used
`MagicMock(spec=RealClass)` fixtures with the *old* field names hardcoded
in -- plain `spec=` restricts what a mock will let you *read* to the real
class's shape, but does not restrict *assignment*, so setting a
now-nonexistent attribute on the mock silently succeeds and the test suite
never touches the real dependency's actual shape at all.

This test closes that gap cheaply: no network, no fixtures, pure
introspection against the actually-installed tconnectsync package. If a
future `tconnectsync` bump renames a field builders.py depends on, this
fails immediately in CI on every push -- instead of only being caught by
a live production sync failure that may go unnoticed for weeks (as
happened here; see docs/updates/2026-08-16-tandem-nightly-sync-restoration.md).

Deliberately NOT a mirror of every field in ingestion/builders.py forever --
just the attribute names actually read there today. Update this alongside
builders.py when either changes.
"""

from __future__ import annotations

import dataclasses

from tconnectsync.eventparser.events import (
    LidAaDailyStatus,
    LidAaPcmChange,
    LidAaUserModeChange,
    LidAlarmActivated,
    LidAlarmCleared,
    LidAlertActivated,
    LidAlertCleared,
    LidBasalDelivery,
    LidBolusCompleted,
    LidBolusRequestedMsg1,
    LidBolusRequestedMsg2,
    LidBolusRequestedMsg3,
    LidCannulaFilled,
    LidCartridgeFilled,
    LidCgmAlertActivatedDex,
    LidCgmAlertClearedDex,
    LidCgmDataFsl2,
    LidCgmDataG7,
    LidCgmDataGxb,
    LidNewDay,
    LidPumpingResumed,
    LidPumpingSuspended,
    LidTubingFilled,
)

# class -> attribute names ingestion/builders.py reads from it. Covers both
# declared dataclass fields (e.g. currentGlucoseDisplayValue) and derived
# @property accessors (e.g. eventTimestamp, seqNum, alarmId).
EXPECTED_ATTRS: dict[type, tuple[str, ...]] = {
    LidCgmDataG7: ("currentGlucoseDisplayValue", "seqNum", "cgmDataTypeRaw", "egvTimeStamp", "eventTimestamp"),
    LidCgmDataGxb: ("currentGlucoseDisplayValue", "cgmDataTypeRaw", "egvTimeStamp"),
    LidCgmDataFsl2: ("currentGlucoseDisplayValue", "cgmDataTypeRaw", "egvTimeStamp"),
    LidBolusCompleted: ("insulinDelivered", "bolusId", "eventTimestamp"),
    LidBolusRequestedMsg1: ("bolusId", "carbAmount", "bg", "iob", "eventTimestamp"),
    LidBolusRequestedMsg2: ("bolusId", "optionsRaw", "userOverrideRaw"),
    LidBolusRequestedMsg3: ("bolusId", "foodBolusSize", "correctionBolusSize", "totalBolusSize"),
    LidBasalDelivery: ("commandedRate", "commandedRateSourceRaw", "eventTimestamp"),
    LidPumpingSuspended: ("suspendReasonRaw", "insulinAmount", "eventTimestamp"),
    LidPumpingResumed: ("eventTimestamp",),
    LidCartridgeFilled: ("insulinVolume", "eventTimestamp", "seqNum"),
    LidCannulaFilled: ("primeSize", "eventTimestamp", "seqNum"),
    LidTubingFilled: ("primeSize", "eventTimestamp", "seqNum"),
    LidAaUserModeChange: (
        "currentUserModeRaw", "previousUserModeRaw", "requestedActionRaw",
        "exerciseTime", "eventTimestamp", "seqNum",
    ),
    LidAaPcmChange: ("currentPcmRaw", "previousPcmRaw", "eventTimestamp", "seqNum"),
    LidNewDay: ("commandedBasalRate", "eventTimestamp", "seqNum"),
    LidAaDailyStatus: ("pumpControlStateRaw", "usermodeRaw", "eventTimestamp", "seqNum"),
    LidAlarmActivated: ("alarmIdRaw", "alarmId", "param1", "param2", "eventTimestamp", "seqNum"),
    LidAlarmCleared: ("alarmIdRaw", "alarmId", "eventTimestamp", "seqNum"),
    LidAlertActivated: ("alertIdRaw", "alertId", "param1", "param2", "eventTimestamp", "seqNum"),
    LidAlertCleared: ("alertIdRaw", "alertId", "eventTimestamp", "seqNum"),
    LidCgmAlertActivatedDex: ("dalertIdRaw", "dalertId", "param1", "param2", "eventTimestamp", "seqNum"),
    LidCgmAlertClearedDex: ("dalertIdRaw", "dalertId", "eventTimestamp", "seqNum"),
}


def _has_attr(cls: type, name: str) -> bool:
    """True if *name* is a declared dataclass field or a class-level
    descriptor (property) on *cls*. Plain `hasattr(cls, name)` alone misses
    dataclass fields with no default (they only exist on instances, not the
    class), so check `__dataclass_fields__` first."""
    fields = getattr(cls, "__dataclass_fields__", {})
    if name in fields:
        return True
    return hasattr(cls, name)


def test_every_expected_attr_exists_on_the_real_class():
    missing: list[str] = []
    for cls, attrs in EXPECTED_ATTRS.items():
        for attr in attrs:
            if not _has_attr(cls, attr):
                missing.append(f"{cls.__name__}.{attr}")

    assert not missing, (
        "tconnectsync no longer exposes these attributes that "
        "ingestion/builders.py depends on (dependency version drift?): "
        f"{missing}"
    )


def test_expected_attrs_covers_every_class_builders_actually_imports():
    """Guards against this contract silently going stale: every class listed
    here must actually be one builders.py imports from tconnectsync."""
    import ingestion.builders as builders

    for cls in EXPECTED_ATTRS:
        assert cls.__name__ in dir(builders), (
            f"{cls.__name__} is checked by this contract test but "
            "ingestion/builders.py no longer imports it -- remove it here "
            "or re-check why it dropped out of use"
        )
