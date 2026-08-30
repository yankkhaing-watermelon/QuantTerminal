#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "quant"))

from bmk_quant import write_artifacts  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Bursa MusangKing Quant v5")
    parser.add_argument("--output", default="artifacts/latest")
    parser.add_argument("--max-symbols", type=int, default=0, help="Development only; zero scans the full universe")
    args = parser.parse_args()
    target = write_artifacts(args.output, max_symbols=args.max_symbols)
    print(f"Quant artifacts written to {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
