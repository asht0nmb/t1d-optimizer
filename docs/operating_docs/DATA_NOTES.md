# Data Notes

Domain knowledge from the user that informs how the detection engine should interpret raw data. These are clinical/practical realities that can't be derived from the data alone.

---

## 1. Occlusion alarm frequency indicates site failure

A **single occlusion alarm** is typically a normal, isolated occlusion — air bubble, kink that resolves, etc. Not clinically significant on its own.

**Multiple occlusion alarms (2-3+) in a short window** typically indicates a site failure — the infusion set is bad and needs to be replaced. This is a much more significant event: insulin delivery is compromised until the site is changed.

**Implication for detection engine:** When counting occlusion alarms, cluster them by time proximity. A cluster of 2-3+ should be flagged as a probable site failure, not just repeated isolated occlusions.

---

## 2. Site changes after pump power loss are not real site changes

When the pump battery dies (BatteryShutdownAlarm), a "site change" (cartridge + tubing fill) is **required by the pump firmware** to resume insulin delivery. This does not mean the user actually changed their infusion site.

**Example from Mar 19:**
- 08:06 — BatteryShutdownAlarm, pump dies
- 11:53 — site_change (cartridge, insulin_volume=180) + site_change (tubing) logged
- These are just the steps required to get the pump running again after charging

~90% of the time, this is not a full site change — the user is refilling/repriming the same site.

**This only applies to site changes following a pump power loss (BatteryShutdownAlarm).** Any other site change is a real site change. The pump-death case is specific: the firmware forces a cartridge+tubing fill to resume delivery, regardless of whether the user actually swapped hardware.

**How to distinguish real vs forced in the pump-death case:** The presence of a cannula prime does NOT reliably indicate a real site change. The actual indicator is the **cartridge fill amount** (`insulin_volume` in the site_change details). If the cartridge fill amount exceeds a threshold (TBD — user will determine this value), it's a real full site change even post-battery-death. Below that threshold, the user is just topping off insulin to get the pump running again.

**Data observed:**
- Mar 18 10:18: cartridge insulin_volume=240 — real full site change (post battery death)
- Mar 19 11:53: cartridge insulin_volume=180 — forced restart only (battery death at 08:06)
- Mar 20 23:55: cartridge insulin_volume=240 — not post battery death, so real regardless

**Cartridge fill amount is already logged** in `events.parquet` under `details` JSON for `event_subtype=cartridge`. No pipeline change needed for capture — but the detection engine will need to parse this value and apply the threshold.

**Implication for detection engine:** Site changes that follow a BatteryShutdownAlarm (or similar power-loss suspension) within a reasonable window should be cross-referenced with the cartridge fill amount. Below the threshold → tag as `forced_by_alarm` and exclude from site rotation analysis or infusion set lifetime calculations. Site changes that are NOT preceded by a power-loss alarm are always real and need no threshold check.

---

## 3. Bolus categorization and override behavior

The `bolus_source` field on `requests_df` has three values: `auto`, `user`, `override`. These don't fully describe what's happening. The real categories are:

| Category | source | carbs | food_insulin | correction_insulin | Meaning |
|----------|--------|-------|--------------|--------------------|---------|
| Auto correction | auto | 0 | 0 | >0 | Control-IQ automatic correction bolus. Never contains food. |
| User meal + correction | user | >0 | >0 | >0 | User entered carbs, pump added correction for high BG |
| User meal only | user | >0 | >0 | 0 | User entered carbs, BG at/below target so no correction |
| User correction only | user | 0 | 0 | >0 | Manual correction without food |
| Override increase | override | 0 | 0 | ~0 | User overrode pump recommendation upward. `total_requested` is the forced amount; `correction_insulin` is what the pump recommended (usually near zero due to high IOB). Override delta = `total_requested - correction_insulin` |
| Override increase (corr) | override | 0 | 0 | >0 | Same as above but pump had some correction to recommend |

**Auto corrections never contain food.** They are always pure correction-only boluses issued by Control-IQ. However, auto corrections can fire within minutes of a user meal bolus — these are temporally adjacent but separate events (observed: 1.4 min and 8.4 min gaps in sample data).

**Overrides are always increases in this dataset.** Every observed override has `total_requested` significantly exceeding what the pump calculated. The pump doesn't store "override amount" directly, but it can be derived: `override_delta = total_requested - food_insulin - correction_insulin`. No food+override combinations appeared in the 5-day sample, but they likely exist.

**No override decreases observed yet** — where a user requests less than the pump recommends. Need more data to confirm whether these appear with a different `bolus_source` value or the same `override` tag.

**Possible solution:** Add a `bolus_category` column to `requests_df` during building, computed from the combination of `bolus_source`, `carbs_g`, `food_insulin`, and `correction_insulin`. Also add `override_delta` (float, NaN for non-overrides) to quantify the override amount and direction (+/-).
