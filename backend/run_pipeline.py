#!/usr/bin/env python3
"""Run the full automated reel pipeline once from the command line."""
import argparse
import asyncio
import json
import sys
import logging

from app.services.pipeline import run_podcast_pipeline
logging.basicConfig(level=logging.INFO)


def main():
    parser = argparse.ArgumentParser(description="Run the automated Instagram reel pipeline")
    parser.add_argument(
        "--category",
        help="Override content category (default: DEFAULT_CATEGORY env or random pick)",
    )
    args = parser.parse_args()

    try:
        result = asyncio.run(run_podcast_pipeline(category=args.category))
    except Exception as e:  # noqa: BLE001
        print(f"Pipeline failed: {e}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result.model_dump(), indent=2, default=str))


if __name__ == "__main__":
    main()
