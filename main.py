"""T1D Engine entry point."""

import argparse
import logging
import sys

from dotenv import load_dotenv

load_dotenv()


def main():
    parser = argparse.ArgumentParser(description="T1D Engine — diabetes data intelligence")
    sub = parser.add_subparsers(dest="command")

    fetch_parser = sub.add_parser("fetch", help="Full fetch from all pumps (additive)")
    fetch_parser.add_argument("--clean", action="store_true", help="Wipe data/processed/ before fetching")

    sub.add_parser("update", help="Incremental update (new data since last fetch)")

    day_parser = sub.add_parser("fetch-day", help="Fetch a single day from the active pump")
    day_parser.add_argument("--date", required=True, help="Date to fetch (YYYY-MM-DD)")

    check_parser = sub.add_parser("check", help="Sanity check a specific date")
    check_parser.add_argument("--date", required=True, help="Date to check (YYYY-MM-DD)")
    check_parser.add_argument(
        "--view",
        choices=["original", "enriched"],
        default="original",
        help=(
            "Data projection: 'original' (default) hides enrichment-only "
            "columns/sections for stable pre-enrichment output; 'enriched' "
            "adds bolus_category, override_delta, forced_by_alarm, site_issues, "
            "and cgm_gaps sections (backfilled in memory if missing on disk)."
        ),
    )

    viz_parser = sub.add_parser("viz", help="Visualize a day's data")
    viz_parser.add_argument("--date", required=True, help="Date to visualize (YYYY-MM-DD)")
    viz_parser.add_argument(
        "--view",
        choices=["original", "enriched"],
        default="original",
        help=(
            "Data projection: 'original' (default) draws the historical panels "
            "with alarm-derived CGM OOR shading; 'enriched' uses cgm_gaps spans "
            "(no double-draw), marks forced site changes, annotates bolus "
            "categories, and shades site_issues windows."
        ),
    )

    anomalies_parser = sub.add_parser(
        "analyze-anomalies",
        help="Run CGM anomaly detection for a specific date",
    )
    anomalies_parser.add_argument(
        "--date", required=True, help="Date to analyze (YYYY-MM-DD)"
    )

    meals_parser = sub.add_parser(
        "analyze-meals",
        help="Run missed-meal detection for a specific date",
    )
    meals_parser.add_argument(
        "--date", required=True, help="Date to analyze (YYYY-MM-DD)"
    )

    cluster_parser = sub.add_parser(
        "cluster-days",
        help="Build daily features across a date range and cluster them",
    )
    cluster_parser.add_argument(
        "--retrain",
        action="store_true",
        help="Refit the clustering pipeline instead of loading the saved model",
    )
    cluster_parser.add_argument(
        "--start",
        default=None,
        help="Start date (YYYY-MM-DD); defaults to earliest CGM date",
    )
    cluster_parser.add_argument(
        "--end",
        default=None,
        help="End date (YYYY-MM-DD); defaults to latest CGM date",
    )

    sub.add_parser(
        "doctor",
        help="Diagnose pipeline health (version, parquet presence, stacking)",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.command == "fetch":
        from ingestion import run_full_fetch, clean_all
        if args.clean:
            clean_all()
        run_full_fetch()

    elif args.command == "update":
        from ingestion import run_incremental_fetch
        run_incremental_fetch()

    elif args.command == "fetch-day":
        from ingestion import run_day_fetch
        run_day_fetch(args.date)

    elif args.command == "check":
        from scripts.sanity_check import sanity_check
        sanity_check(args.date, view=args.view)

    elif args.command == "viz":
        from scripts.daily_viz import daily_viz
        daily_viz(args.date, view=args.view)

    elif args.command == "analyze-anomalies":
        from scripts.run_detection import run_anomalies
        run_anomalies(args.date)

    elif args.command == "analyze-meals":
        from scripts.run_detection import run_meals
        run_meals(args.date)

    elif args.command == "cluster-days":
        from scripts.run_detection import run_clustering
        run_clustering(args.retrain, args.start, args.end)

    elif args.command == "doctor":
        from scripts.doctor import doctor
        doctor()

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
