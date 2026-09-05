#!/usr/bin/env python3
"""Run Astra independently; retain source bars and reproducible result artifacts."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "quant"))
from bmk_quant.astra import Config, build
from bmk_quant.astra_data import collect
from bmk_quant.universe import Security


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/astra")
    parser.add_argument("--bars", type=int, default=1500)
    parser.add_argument("--input", help="Replay a source directory from a previous Astra artifact")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    config = Config(**json.loads(os.getenv("ASTRA_CONFIG") or "{}"))
    request_id = os.getenv("ASTRA_REQUEST_ID") or f"gh-{uuid.uuid4()}"
    base, token = os.getenv("QUANT_API_BASE", "").rstrip("/"), os.getenv("PUBLISH_TOKEN", "")
    if args.publish and (not base or not token):
        raise SystemExit("QUANT_API_BASE and PUBLISH_TOKEN must be configured in GitHub secrets")
    session = requests.Session()
    session.headers.update({"authorization": f"Bearer {token}", "user-agent": "quant-astra/1.0"})

    def report(action, **payload):
        if not args.publish:
            return None
        response = session.post(f"{base}/api/admin/astra", json={"request_id": request_id, "action": action, **payload}, timeout=60)
        if not response.ok:
            raise RuntimeError(f"Astra {action} failed: HTTP {response.status_code}: {response.text[:250]}")
        return response.json()

    started = False
    try:
        report("start")
        started = True
        root = Path(args.output)
        source = root / "source"
        source.mkdir(parents=True, exist_ok=True)
        if args.input:
            src = Path(args.input)
            metadata = {row["symbol"]: Security(**row) for row in json.loads((src / "universe.json").read_text())}
            if any(not symbol or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for c in symbol) for symbol in metadata):
                raise ValueError("unsafe_provider_symbol")
            prices = {symbol: pd.read_csv(src / f"{symbol}.csv", index_col=0, parse_dates=True)
                      for symbol in metadata if (src / f"{symbol}.csv").exists()}
            benchmark = pd.read_csv(src / "benchmark.csv", index_col=0, parse_dates=True).iloc[:, 0]
            coverage = json.loads((src / "coverage.json").read_text())
            # Recompute identity from replay files, so edits cannot retain the original hash.
            digest = hashlib.sha256()
            digest.update(json.dumps([asdict(s) for s in metadata.values()], sort_keys=True).encode())
            digest.update(benchmark.to_csv().encode())
            for symbol, frame in sorted(prices.items()):
                digest.update(symbol.encode()); digest.update(frame.to_csv().encode())
            coverage["data_hash"] = digest.hexdigest()
        else:
            if not 300 <= args.bars <= 5000:
                raise ValueError("bars must be between 300 and 5000")
            prices, metadata, benchmark, coverage = collect(args.bars,
                lambda processed, total: report("progress", processed=processed, total=total))
        if not prices:
            raise RuntimeError("No usable stock histories; publication refused")
        for symbol, frame in prices.items():
            # Provider identifiers must never become arbitrary filesystem paths.
            if not symbol or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for c in symbol):
                raise ValueError("unsafe_provider_symbol")
            frame.to_csv(source / f"{symbol}.csv")
        benchmark.to_csv(source / "benchmark.csv")
        (source / "universe.json").write_text(json.dumps([asdict(s) for s in metadata.values()]))
        (source / "coverage.json").write_text(json.dumps(coverage))
        report("progress", processed=coverage["processed"], total=coverage["discovered"], message="Calculating both portfolio backtests and cost stress tests")
        payload = build(prices, metadata, benchmark, config)
        payload["coverage"] = coverage
        payload["generated_at"] = datetime.now(timezone.utc).isoformat()
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        payload["run_id"] = "astra-" + hashlib.sha256(encoded.encode()).hexdigest()[:24]
        (root / "latest.json").write_text(json.dumps(payload, separators=(",", ":"), allow_nan=False))
        for key, result in payload["strategies"].items():
            pd.DataFrame(result["trades"]).to_csv(root / f"{key}-trades.csv", index=False)
        publication = report("publish", data=payload)
        if publication:
            response = session.get(f"{base}/api/astra", timeout=60)
            response.raise_for_status()
            if response.json().get("data", {}).get("run_id") != payload["run_id"]:
                raise RuntimeError("Published Astra run was not returned by the live API")
        print(json.dumps({"run_id": payload["run_id"], "coverage": {k: v for k, v in coverage.items() if k != "issues"},
                          "history": payload["history"]}, indent=2))
    except Exception as exc:
        if started:
            try:
                report("failed", message=f"Astra stopped: {type(exc).__name__}. See GitHub workflow logs.")
            except Exception:
                pass
        raise


if __name__ == "__main__":
    main()
