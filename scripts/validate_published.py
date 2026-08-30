#!/usr/bin/env python3
"""Fetch the live public payload only when Cloudflare verifies its stored hash."""
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
    if body.get("integrity") != "verified":
        raise SystemExit(f"published_payload_integrity_not_verified:{json.dumps(body)[:500]}")
    payload_hash = str(body.get("payload_hash") or "")
    if len(payload_hash) != 64 or any(char not in "0123456789abcdef" for char in payload_hash):
        raise SystemExit(f"published_payload_hash_invalid:{payload_hash!r}")

    root = Path(args.output)
    root.mkdir(parents=True, exist_ok=True)
    (root / "latest.json").write_text(json.dumps(body["data"], indent=2), encoding="utf-8")
    (root / "integrity.json").write_text(json.dumps({
        "run_id": body["data"].get("run_id"),
        "payload_hash": payload_hash,
        "integrity": body.get("integrity"),
    }, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Fetched verified published run: {body['data'].get('run_id')}")
    print(f"Payload hash: {payload_hash}")
    print(f"Universe: {body['data'].get('universe_size')} | Fresh: {body['data'].get('fresh_symbols')}")
    print(f"Regime: {body['data'].get('regime', {}).get('state')} | Score: {body['data'].get('regime', {}).get('score')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
