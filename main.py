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

    check_parser = sub.add_parser("check", help="Sanity check a specific date")
    check_parser.add_argument("--date", required=True, help="Date to check (YYYY-MM-DD)")

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

    elif args.command == "check":
        from scripts.sanity_check import sanity_check
        sanity_check(args.date)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
