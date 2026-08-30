#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import requests


def post(session: requests.Session, url: str, payload: dict) -> dict:
    response = session.post(url, json=payload, timeout=60)
    if not response.ok:
        raise RuntimeError(f"publish rejected with HTTP {response.status_code}: {response.text[:500]}")
    return response.json()


def chunks(rows: list[dict], size: int = 75):
    for index in range(0, len(rows), size):
        yield rows[index:index + size]


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish Quant artifacts to Cloudflare Pages Functions")
    parser.add_argument("--artifacts", default="artifacts/latest")
    parser.add_argument("--api-base", default=os.getenv("QUANT_API_BASE", ""))
    args = parser.parse_args()
    token = os.getenv("PUBLISH_TOKEN", "")
    if not args.api_base or not token:
        raise SystemExit("QUANT_API_BASE and PUBLISH_TOKEN are required")
    root = Path(args.artifacts)
    payload = json.loads((root / "latest.json").read_text(encoding="utf-8"))
    research = json.loads((root / "research.json").read_text(encoding="utf-8"))
    run_id = payload["run_id"]
    session = requests.Session()
    session.headers.update({"authorization": f"Bearer {token}", "user-agent": "bmk-quant-publisher/5.0"})
    base = args.api_base.rstrip("/")
    print("Research archive start:", post(session, f"{base}/api/admin/runs/{run_id}/research/start", {"expected_symbols": len(research)}))
    for batch in chunks(research):
        post(session, f"{base}/api/admin/runs/{run_id}/research/batch", {"rows": batch})
    print("Research archive commit:", post(session, f"{base}/api/admin/runs/{run_id}/research/commit", {}))
    for batch in chunks(payload.get("portfolio", [])):
        post(session, f"{base}/api/admin/runs/{run_id}/portfolio", {"rows": batch})
    print("Quant publication:", post(session, f"{base}/api/admin/publish", {"data": payload}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
