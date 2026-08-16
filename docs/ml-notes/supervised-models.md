# Supervised modeling on the M2 meal-rise corpus — a learning write-up

**Status:** exploratory, branch `research/ml-clustering-and-models`, not merged. Code lives in `detection/supervised.py`; the corpus builder is `scripts/build_meal_rise_corpus.py`; the trainer/report is `scripts/train_meal_rise_classifier.py`. Advisory only, same posture as the existing `scripts/score_meal_rise.py` — nothing here edits `config/user_config.yaml` or the live meal-rise loop.

## The question, and why it's different from clustering

`detection/clustering.py` (see `docs/ml-notes/clustering.md`) is *unsupervised* — no labels, look for structure. This is *supervised*: `detection/calibration/meal_rise_scoring.py` (existing, unmodified, part of the shipped M2 calibration work) already produces a real ground-truth label for every historical meal-rise detection, by comparing the detection's timing against the pump's bolus log after the fact:

- `pre_bolused` — a food bolus preceded the rise
- `late_bolused` — a food bolus followed the rise
- `uncovered` — no food bolus in the lookahead window (further sub-classified by how, if at all, it later got a user or auto-correction — that's a `resolution` field, not part of the 3-way label used here)

That's a real target variable, so the natural next question: can a model, using only information available *at the moment the rise is first detected* — before any bolus has happened or not happened — predict which of those three outcomes is coming?

## Step 1 — build the corpus (and time it before trusting it scales)

No script existed to turn the M2 scorer's output into a training-ready dataset — `scripts/score_meal_rise.py` produces a Markdown/JSON *report*, not a DataFrame meant for `sklearn`. `scripts/build_meal_rise_corpus.py` does that: pull `cgm` + `requests` from Supabase over a date range, run the *existing, unmodified* `find_meal_rise_instances` + `score_instances` from `detection/calibration/meal_rise_scoring.py`, convert to a model-ready frame via the new `detection.supervised.scored_instances_to_frame`.

`find_meal_rise_instances` slides the detector across **every** CGM reading (a Python loop, not a vectorized scan) — worth timing before assuming it scales, since the same function's sweep mode is already known to be slow for the existing calibration report. Measured on this repo's real data before writing the rest of the pipeline: **~0.27ms per CGM row**. Even the full ~5-year, ~327K-row history finishes in under 2 minutes — so, empirically here, it's not the bottleneck it could have been. (Worth re-timing if the CGM table grows an order of magnitude, or if this runs somewhere without a fast DB connection.)

Same 18-month default window as the clustering dataset, for the same reason: therapy settings drift over years, and training across multiple regimens teaches a mixture of behaviors rather than the person's current one.

```
uv run python scripts/build_meal_rise_corpus.py
# 1619 labeled instances, 2025-02-14 .. 2026-08-16
```

## Step 2 — the most important design decision: what's a legitimate feature

`ScoredInstance` (the M2 scorer's output type) has 18 fields. Several of them are **definitionally derived from the label** — `score_instances` computes them only *after* deciding pre/late/uncovered, by searching forward from the detection through the bolus log:

```
matched_bolus_ts, matched_bolus_category, matched_bolus_carbs,
bolus_delay_min, resolution, resolution_ts, resolution_delay_min
```

Training a model on `bolus_delay_min` to predict a label that's literally `sign(bolus_delay_min)` would report near-100% accuracy while learning nothing — and would be useless in the one place this could ever matter live, because at the moment a real-time system detects a rise, none of these fields exist yet: the very bolus (or its absence) they describe hasn't happened.

The only fields computable **before** any bolus context is consulted — purely from the CGM window that produced the detection:

```
start_level, end_level, delta, slope_mgdl_per_min, hour_of_day
(+ day_of_week, derived here from the detection timestamp — timestamp
  arithmetic, not bolus data, so equally safe)
```

`detection/supervised.py` encodes this split as data, not just a comment — `SAFE_FEATURES` and `LEAKY_FEATURES` are both public constants, `scored_instances_to_frame()` asserts the two are disjoint from what it emits, and the test suite (`tests/test_detection_supervised.py::test_scored_instances_to_frame_excludes_leaky_fields`) fails loudly if a future edit ever lets a leaky field back in. This is the single most important thing to get right in this whole exercise — a subtler version of this exact mistake (training on a field that's downstream of the label) is one of the most common ways a "the model works great!" result turns out to be fake.

## Step 3 — train/test split: chronological, not random, and why

The standard `sklearn.model_selection.train_test_split` shuffles rows uniformly at random. That's correct for i.i.d. data. Health time-series data isn't i.i.d. in two ways that make a random split *optimistic* — it overstates how well a model will do on genuinely new, future days:

1. **Autocorrelation.** Instances from the same day, or a run of days with the same context (illness, travel, a pump-site problem), share signal a model can partially memorize even from features that look person-agnostic. A random split scatters same-day/same-week instances across train and test, letting the model implicitly "see" test-adjacent context during training.
2. **Regime drift.** A model is only useful if it generalizes *forward in time*. A random split can put a 2025 instance in training and a 2026 instance in test — a direction of "generalization" no live system will ever need, and one that can flatter a model that's really just interpolating within its own training era.

`chronological_split()` sorts by `rise_start_ts` and takes the trailing `test_fraction` as the held-out set. On this corpus:

```
train: 1296 instances (2025-02-14 .. 2026-05-07)
test:   323 instances (2026-05-07 .. 2026-08-12)
```

## Step 4 — baselines, because accuracy alone is not an honest result

With three heavily imbalanced classes — `uncovered` 70.8%, `late_bolused` 23.0%, `pre_bolused` 6.2% (full-corpus distribution; the chronological test split skews slightly further toward `uncovered`, 78.9%, than the training split, 68.8% — itself a small sign of drift worth noting) — a model can post a deceptively high accuracy just by leaning toward the majority class. Two baselines make any headline number honest:

- **`MajorityClassBaseline`** — always predicts the training set's mode. The floor: beat this or the model learned nothing.
- **`HourOfDayBaseline`** — predicts the majority label observed *for that hour* in training. Meal-rise labels plausibly correlate with time of day (a 7am breakfast bolus behaves differently than a 2am correction) — this is a materially higher, more honest bar than the majority-class floor.

## Step 5 — the model, and the honest result

`RandomForestClassifier` (`class_weight="balanced"` to counteract the imbalance, otherwise default hyperparameters) on the six `SAFE_FEATURES`. Run via `scripts/train_meal_rise_classifier.py`, which is the permanent, rerunnable version of this — it emits a saved Markdown report (`data/reports/meal_rise_classifier_<timestamp>.md`, gitignored — personal health data) the same way `scripts/score_meal_rise.py` does.

```
Majority-class baseline accuracy:  0.789
Hour-of-day baseline accuracy:     0.786
Random forest accuracy:            0.762
```

**The random forest does not beat either baseline.** This is a genuine, non-cherry-picked result on the owner's real data — not a bug I'm papering over. Per-class detail makes it clearer why:

| label | precision | recall | f1 | support |
|---|---:|---:|---:|---:|
| late_bolused | 0.30 | 0.15 | 0.20 | 53 |
| pre_bolused | 0.00 | 0.00 | 0.00 | 15 |
| uncovered | 0.80 | 0.93 | 0.86 | 255 |

The model **never once predicts `pre_bolused`** (0/15 recall) and is weak on `late_bolused` (recall 0.15 — it mostly folds these into `uncovered`, which is already the majority class). It's *better than the majority baseline* at correctly identifying `late_bolused` cases at all (majority-class trivially gets 0 recall there too, since it always predicts `uncovered`) — but that gain is more than offset by getting slightly worse at the dominant `uncovered` class, netting out to lower overall accuracy.

Feature importances (from the fitted forest) are fairly evenly spread, with no single dominant driver:

```
start_level          0.200
slope_mgdl_per_min   0.198
end_level             0.189
delta                0.170
hour_of_day           0.143
day_of_week           0.101
```

Nothing here jumps out as a strong isolated predictor — importance is spread almost uniformly across the six features, which is itself informative: it's what you'd expect to see when the model has found a little bit of weak signal everywhere but nothing decisive anywhere.

## Interpreting the negative result honestly

The straightforward reading: **the shape of the glucose rise itself (how fast, how far, what time of day) carries very little information about whether — and when — a bolus will follow.** That's not obviously wrong, on reflection: whether someone notices a rise and boluses for it is a *human behavioral choice* — were they looking at their phone, were they driving, did they already know they'd eaten and pre-bolused mentally without the pump reflecting it yet, is their insulin-on-board display telling them something different than what the detector sees — none of which is mechanically determined by the CGM curve's shape. The features available at detection time simply may not contain the signal that determines the outcome.

This is exactly the kind of result the M2 calibration report (`scripts/score_meal_rise.py`) was never positioned to surface — it reports *rates* (uncovered rate, per-hour breakdown) but was never asked "can this be predicted in advance from CGM shape alone," and now there's a documented, honest answer: not well, with this feature set.

**What a next pass should try, if pursued:**

- **Add legitimate, non-leaky context features that this pass didn't include**: prior-day aggregates from the daily-features dataset (yesterday's TIR, total insulin, meal count — genuinely available before today's detection, and easy to join in since `build_daily_features_dataset.py` already computes exactly this table), IOB at the moment of detection (if recoverable from the pump event log without peeking at the matched bolus itself), or a rolling "days since last uncovered rise" feature (behavioral streaks/fatigue).
- **Reframe the target.** A 3-class label with a 71/23/6 split is a hard framing. A binary "uncovered vs. covered" framing was tried in early exploration (majority baseline 0.789 also unbeaten by the random forest there, 0.737) and doesn't fix the underlying signal problem — but a *regression* on `bolus_delay_min` restricted to covered instances (excluding `uncovered`, which has no delay to predict) might expose gradient the classification framing throws away.
- **More data helps less than it sounds like it would** — 1619 instances isn't tiny, and the per-class support numbers (`pre_bolused` n=100 total, only ~15 in the test fold) suggest the minority class is simply too rare to learn well regardless of model choice; a longer window (the full ~5-year history, which the runtime check above says is feasible) would meaningfully grow `pre_bolused`'s sample size, at the cost of the regime-drift tradeoff discussed above — worth testing explicitly rather than assumed.

## What this module deliberately does not do

Same posture as `scripts/score_meal_rise.py`: advisory only. Nothing here touches `config/user_config.yaml`, nothing here is wired into `apps/personal/cron/` or the live Telegram alert loop. A trained model becoming a live "this rise looks headed for uncovered" alert is a real, separate feature decision for later — this pass only answers "is there signal to build that on," honestly, and for now the answer is "not with these features."
