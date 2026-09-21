#!/usr/bin/env python3
"""Generate the reusable C3/C4 rate-limiting protection summary."""

import argparse
from pathlib import Path

from c3_protection import write_summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--k6-summary", required=True, type=Path)
    parser.add_argument("--database-summary", required=True, type=Path)
    parser.add_argument("--queue", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    write_summary(
        args.k6_summary,
        args.database_summary,
        args.queue,
        args.output,
    )


if __name__ == "__main__":
    main()
