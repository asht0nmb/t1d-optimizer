"""Train + evaluate a classifier on the M2 meal-rise corpus; emit a report.

*** RESEARCH / EXPLORATION TOOLING. Advisory only, same posture as
`scripts/score_meal_rise.py` — this never edits `config/user_config.yaml`
and nothing here is wired into the live meal-rise loop. See
`docs/ml-notes/supervised-models.md` for the pedagogical write-up and the
honest discussion of what these results do and don't show. ***

Prereq: `uv run python scripts/build_meal_rise_corpus.py` (writes
`data/processed/meal_rise_corpus.parquet`, gitignored — personal health
data).

Usage:
    uv run python scripts/train_meal_rise_classifier.py
        [--corpus data/processed/meal_rise_corpus.parquet]
        [--test-fraction 0.2] [--out-dir data/reports]
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pandas as pd

from detection.supervised import (
    SAFE_FEATURES,
    HourOfDayBaseline,
    MajorityClassBaseline,
    chronological_split,
    evaluate,
    train_random_forest,
)


def _accuracy(preds, truth) -> float:
    return float((pd.Series(preds).to_numpy() == truth.to_numpy()).mean())


def run(corpus_path: Path, test_fraction: float) -> dict:
    df = pd.read_parquet(corpus_path)
    train, test = chronological_split(df, test_fraction=test_fraction)

    maj = MajorityClassBaseline().fit(train["label"])
    maj_preds = maj.predict(len(test))
    maj_acc = _accuracy(maj_preds, test["label"])

    hod = HourOfDayBaseline().fit(train["hour_of_day"], train["label"])
    hod_preds = hod.predict(test["hour_of_day"])
    hod_acc = _accuracy(hod_preds, test["label"])

    model = train_random_forest(train)
    model_preds = model.predict(test[list(SAFE_FEATURES)])
    model_acc = _accuracy(model_preds, test["label"])
    model_eval = evaluate(test["label"], model_preds)

    importances = dict(
        sorted(zip(SAFE_FEATURES, model.feature_importances_.tolist()), key=lambda kv: -kv[1])
    )

    return {
        "n_total": len(df),
        "n_train": len(train),
        "n_test": len(test),
        "train_range": (str(train["rise_start_ts"].min()), str(train["rise_start_ts"].max())),
        "test_range": (str(test["rise_start_ts"].min()), str(test["rise_start_ts"].max())),
        "label_distribution": df["label"].value_counts(normalize=True).to_dict(),
        "majority_baseline_accuracy": maj_acc,
        "hour_of_day_baseline_accuracy": hod_acc,
        "random_forest_accuracy": model_acc,
        "random_forest_report": model_eval["report"],
        "random_forest_confusion_matrix": model_eval["confusion_matrix"],
        "confusion_matrix_labels": model_eval["labels"],
        "feature_importances": importances,
    }


def build_markdown_report(result: dict) -> str:
    beats_majority = result["random_forest_accuracy"] > result["majority_baseline_accuracy"]
    beats_hour = result["random_forest_accuracy"] > result["hour_of_day_baseline_accuracy"]
    lines = [
        "# Meal-rise outcome classifier report",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "",
        "> **Advisory / research only.** Never wired into the live meal-rise",
        "> loop or `config/user_config.yaml`. See `docs/ml-notes/supervised-models.md`",
        "> for the full write-up including the leakage-boundary and split-strategy design.",
        "",
        "## Data",
        "",
        f"- Total labeled instances: {result['n_total']}",
        f"- Train: {result['n_train']} ({result['train_range'][0]} .. {result['train_range'][1]})",
        f"- Test: {result['n_test']} ({result['test_range'][0]} .. {result['test_range'][1]})",
        f"- Label distribution (full corpus): {result['label_distribution']}",
        "",
        "## Accuracy vs. baselines",
        "",
        "| Model | Accuracy |",
        "|---|---|",
        f"| Majority-class baseline | {result['majority_baseline_accuracy']:.3f} |",
        f"| Hour-of-day baseline | {result['hour_of_day_baseline_accuracy']:.3f} |",
        f"| Random forest (safe features) | {result['random_forest_accuracy']:.3f} |",
        "",
        f"**Random forest {'beats' if beats_majority else 'does NOT beat'} the majority-class baseline; "
        f"{'beats' if beats_hour else 'does NOT beat'} the hour-of-day baseline.**",
        "",
        "Accuracy alone is not the full story with imbalanced classes — see the",
        "per-class precision/recall below, and `docs/ml-notes/supervised-models.md`",
        "for the honest interpretation of what a result like this does and doesn't show.",
        "",
        "## Random forest per-class report",
        "",
        "| Label | Precision | Recall | F1 | Support |",
        "|---|---|---|---|---|",
    ]
    for label in result["confusion_matrix_labels"]:
        m = result["random_forest_report"][label]
        lines.append(
            f"| {label} | {m['precision']:.2f} | {m['recall']:.2f} | {m['f1-score']:.2f} | {int(m['support'])} |"
        )
    lines += [
        "",
        "## Confusion matrix",
        "",
        f"Rows = true label, columns = predicted label, order = {result['confusion_matrix_labels']}",
        "",
    ]
    for label, row in zip(result["confusion_matrix_labels"], result["random_forest_confusion_matrix"]):
        lines.append(f"- **{label}**: {row}")
    lines += [
        "",
        "## Feature importances (random forest)",
        "",
        "| Feature | Importance |",
        "|---|---|",
    ]
    for feat, imp in result["feature_importances"].items():
        lines.append(f"| {feat} | {imp:.3f} |")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default="data/processed/meal_rise_corpus.parquet", type=Path)
    parser.add_argument("--test-fraction", default=0.2, type=float)
    parser.add_argument("--out-dir", default="data/reports", type=Path)
    args = parser.parse_args()

    if not args.corpus.exists():
        raise SystemExit(
            f"{args.corpus} not found. Run "
            f"'uv run python scripts/build_meal_rise_corpus.py' first."
        )

    result = run(args.corpus, args.test_fraction)
    md = build_markdown_report(result)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = args.out_dir / f"meal_rise_classifier_{stamp}.md"
    out_path.write_text(md)
    print(md)
    print(f"\nWrote report -> {out_path}")


if __name__ == "__main__":
    main()
