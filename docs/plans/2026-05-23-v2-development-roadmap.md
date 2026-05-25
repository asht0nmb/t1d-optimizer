# V2 Detection — Development Roadmap

**Date:** 2026-05-23
**Status:** Working plan. This is the order we build and the principles that govern that order.
**Companion:** The suite map is the menu of what is possible. This roadmap is the sequence and the discipline. Where they disagree on build order, this document wins.

---

## 1. Guiding principle

Concrete-first, ship-then-extract. Build the smallest thing that is genuinely useful on its own, ship it, use it, and let shared structure condense out of repeated working code rather than designing it ahead of need.

The failure this avoids is real and we already walked into it once: justifying a general windowing engine on a meal detector that barely needs it, then designing the abstraction against imagined future consumers before a single piece runs. An abstraction earns its place by being pulled out of two or three things that visibly repeat, not by being built first because it feels foundational.

This does not forbid foundations early. The real distinction is between a foundation and a speculative abstraction, which carry different risks:

- A **foundation** is something nearly everything sits on, whose shape you can specify confidently right now. Building it early is sound engineering, not premature abstraction, and deferring it just strands the early code on a shape you retrofit later.
- A **speculative abstraction** is something whose right shape only becomes clear once real consumers exist. Building it early means guessing, so it should condense out of repetition.

Sort every candidate with two questions: can I specify its shape confidently right now without hand-waving, and will most things sit on it. Two yeses make it a foundation worth building early and thin. Any no makes it speculative, to be deferred until use reveals its shape.

The earlier mistake was treating the whole windowing-and-representation idea as one thing. Its geometry, an anchor with a pre and post offset over the series, is a foundation and passes both questions. Its representation catalog, which features and how parameterized, is speculative and fails the first. They get sorted differently: thin geometry early, catalog deferred.

So two rules govern order:

- **Lay down the thin, certain foundations as you go, and defer the thick, uncertain layers on top of them.** A milestone may build a foundation when doing so makes its own code cleaner rather than slowing it.
- **Every milestone stands on its own**, usable or demonstrable the day it ships, with no dependency on the milestone after it.

The ambition is unchanged. The complex glucose analysis, the physiology recovery work, the frontier ideas, all of it is still the destination. This reframe changes when each piece arrives, not whether it does.

---

## 2. Two phases

| Phase | Goal | Milestones |
|---|---|---|
| A — Usable product | A real, demonstrable, daily-useful system. The thing you would post about. | M1 live alerts, M2 calibration, M3 dashboard and metrics |
| B — Deep analysis | The differentiated frontier capability, built on top of a working product, with abstractions extracted from real use. | M4 physiology pillar, M5 window-based suite and engine extraction, M6 LLM capstone |

The line between phases is the important one. Reach a shippable product through Phase A before opening the deep analysis in Phase B. That ordering is the whole point of this reframe.

---

## 3. Milestones

### M0 — Foundation (done)

Ingestion, enrichment, storage, RLS, the `detection_results` sink, `daily_features` as a starting point, `AppConfig`, v1 quarantined. Nothing to do.

### M1 — Live missed-meal alerting

The first usable and first demonstrable thing. End to end and real time.

- A standalone detector, a function and not a framework. Take the last handful of CGM points (roughly 5 to 7, about 25 to 35 minutes), fit a robust time-aware slope, and fire when it clears a threshold that bends by time of day, lower around your usual meal windows and higher off-hours.
- It will over-fire, and that is by design rather than a defect. Five CGM-only points cannot reliably separate a meal from any other rise, so the detector is sensitive with a sensible prior, the time-of-day weighting being that prior. It catches the real ones and tolerates the noise.
- Dedup against what was already sent, with a refractory window so a single rise does not produce a stream of alerts.
- Writes to `detection_results` in the agreed shape. Sends a templated Telegram message with injected values, no LLM in the path.
- Wired to the live Vercel cron on the five-minute pydexcom poll.

Sits on: the thin windowing primitive (anchor plus pre and post, returning the slice and coverage), built here as a foundation per section 4. Does not require: the representation catalog, the cohort engine, or any speculative abstraction.

Why it is postable: a system that texts you in real time when you appear to have eaten without bolusing is a clean, legible demo on its own.

### M2 — Calibration and stage two

Makes the detector trustworthy and produces the labeled data everything later reuses.

- A nightly batch pass, running after the Tandem sync, scores each live detection against the bolus and carb data: was there a food-carrying bolus near it, how was it timed, what was the outcome.
- Produces precision and recall and lets us tune the thresholds and the time-of-day weighting against reality rather than guesswork.
- The scored detections are the labeled dataset that a later model pass, far down the line, would train on.

Does not require: any abstraction beyond reading the existing tables.

Why it matters on its own: you learn how good the detector actually is, and it gets measurably better.

### M3 — Dashboard and metric set

The flagship of Phase A and the product you would actually post about.

- Compute the full metric set in the nightly batch into `daily_features` v2: the hyperglycemia axis (TIR, TiTR, GMI, TAR), the hypoglycemia axis (TBR, LBGI, HBGI), variability (CV with its threshold flag, plus the detail-view indices), and GRI as the working default headline.
- Build the dashboard in Next.js: the metrics, and cohort and AGP-style percentile views.
- The cohort views are mostly SQL, using Postgres `percentile_cont` and grouped queries over indexed columns, computed on read. This is not an application-level abstraction and does not depend on the windowing engine.

Why it is the postable product: percentile bands over cohorts your pump vendor does not offer, your own metrics rendered well, and the live detector's findings annotated onto the day view. This is the "beyond the stock report" artifact.

**End of Phase A. At this point you have a usable, demonstrable product.**

### M4 — Physiology pillar

The first deep, differentiated analysis, and the one with the strongest frontier grounding.

- Effective insulin sensitivity tracking and an Autotune-style settings report, adapting the deviation-attribution method, surfaced as observation rather than auto-applied, filling the gap Control-IQ leaves open.
- Runs in the nightly batch, results persisted.

This is where Phase B opens, on top of a working product rather than instead of one.

### M5 — Window-based suite and engine extraction

The point where the windowing engine finally earns its place, by extraction.

- The window-slicing analyses cluster here: glucotype-style clustering, glucose complexity, volatility-regime detection, dawn-versus-rebound, and the meal-response library.
- Build the first one or two of these directly and concretely, the same way M1 was built. When two or three of them visibly slice windows the same way, extract the shared engine from that working code. It will be the right shape because it comes from real repetition.
- If the shared structure turns out thinner than expected, the engine stays small or never gets built, and nothing is lost.

See section 4 for the design notes preserved for that extraction.

### M6 — LLM capstone

- Pull commands, periodic digests, the dashboard conversation panel, assembled over the detection and analysis outputs that now exist.
- The n-of-1 experiment engine comes later still, LLM-driven, once the suite beneath it is rich enough to reason over.

---

## 4. The windowing layer, split into foundation and speculative parts

The windowing idea is not one thing. Sorted by the two-question test from section 1, it splits cleanly, and the two halves are built at different times.

**The foundation, built early in Phase A (thin).** A slicing primitive: take an anchor and a signed `pre` and `post` offset, return the CGM slice over the interval `[anchor - pre, anchor + post]` plus coverage metadata, the fraction of expected samples present and a flag for overlapping a known gap. This is arithmetic on timestamps that every CGM consumer needs identically, so its shape is certain and it is broadly load-bearing. It is an afternoon of work, not a framework. The meal detector sits on it rather than reimplementing slicing, and so do the calibration pass and the later analyses, which means none of them strand a private slicing implementation that has to be reconciled later.

Two seams belong to this foundation because they are certain and cost nothing now:

- Anchors as a generic `(timestamp, kind)` source. Sliding anchors are generated, event anchors come from the bolus, carb, and site-change data, and the future wearable adds wakeup, sleep, and exercise as new kinds into the same slot.
- The property that `post = 0` sees only the past and is the live-capable case, while `post > 0` is inherently retrospective. The stage-one versus stage-two distinction is just whether `post` is zero.

**The speculative layer, deferred to Phase B (thick).** Everything that grows on top of the primitive and whose shape is still a guess: the representation catalog (which features each window emits and how they are parameterized), the representer plugins, and the streaming sliding-window machinery for large historical passes. These fail the first question, their right shape is not knowable until real consumers exist, so they condense out of the M5 window-based tools rather than being designed now. The earlier full design pass is parked as the starting sketch for that point, validated then against actual consumers.

The net effect: the certain core is in place from M1 so the seam is real and nothing is stranded, while the uncertain bulk waits until use reveals its shape.

---

## 5. What carries over unchanged

- The suite map stays the catalog of what is possible. This roadmap reorders it, it does not replace it.
- The compute placement policy still governs: live cron stays minimal, heavy work is nightly batch, cheap aggregates are computed on read in Postgres.
- The settled foundation, the core and shell split, the storage Protocol, the observation-not-prescription constraint, is unchanged.
- The decisions recorded in the suite map hold, with the one revision that the shared engines are now extracted in Phase B rather than built up front.
