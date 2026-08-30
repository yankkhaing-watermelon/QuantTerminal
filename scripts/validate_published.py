#!/usr/bin/env python3
"""Fetch the live public payload and run the same ranking/portfolio contract checks."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import requests


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base", default=os.getenv("QUANT_API_BASE", ""))
    parser.add_argument("--output", default="artifacts/published")
    args = parser.parse_args()
    if not args.api_base:
        raise SystemExit("QUANT_API_BASE is required")

    response = requests.get(f"{args.api_base.rstrip('/')}/api/latest", timeout=60)
    response.raise_for_status()
    body = response.json()
    if not body.get("ok") or not body.get("data"):
        raise SystemExit(f"published_payload_unavailable:{json.dumps(body)[:500]}")

    root = Path(args.output)
    root.mkdir(parents=True, exist_ok=True)
    (root / "latest.json").write_text(json.dumps(body["data"], indent=2), encoding="utf-8")
    print(f"Fetched published run: {body['data'].get('run_id')}")
    print(f"Universe: {body['data'].get('universe_size')} | Fresh: {body['data'].get('fresh_symbols')}")
    print(f"Regime: {body['data'].get('regime', {}).get('state')} | Score: {body['data'].get('regime', {}).get('score')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
