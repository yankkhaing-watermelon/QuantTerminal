#!/usr/bin/env python3
"""One 300-bar TradingView snapshot feeds Quant, Wizard and Astra."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "quant"))
from bmk_quant.astra import Config, build
from bmk_quant.astra_data import collect
from bmk_quant.pipeline import write_artifacts


def execute(output, config, report, publish=False, request_id=None):
    request_id = request_id or f"shared-{uuid.uuid4()}"
    report("start", message="Starting one shared TradingView scan (300 bars per stock)")
    output = Path(output)
    data = collect(300, lambda processed, total: report("progress", processed=processed, total=total,
        message="Downloading the shared Bursa snapshot for Quant, Wizard and Astra"))
    prices, metadata, benchmark, coverage = data
    source = output / "source"
    source.mkdir(parents=True, exist_ok=True)
    for symbol, frame in prices.items():
        if not symbol or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for c in symbol):
            raise ValueError("unsafe_provider_symbol")
        frame.tail(300).to_csv(source / f"{symbol}.csv")
    benchmark.tail(300).to_csv(source / "benchmark.csv")
    (source / "universe.json").write_text(json.dumps([asdict(s) for s in metadata.values()]))
    (source / "coverage.json").write_text(json.dumps(coverage))
    errors = []
    report("progress", processed=coverage["processed"], total=coverage["discovered"],
           message="Data collected once. Calculating Quant and Wizard.")
    try:
        quant = output / "latest"
        write_artifacts(quant, market_data=data)
        latest_path = quant / "latest.json"
        payload = json.loads(latest_path.read_text())
        payload["shared_run_id"] = request_id
        latest_path.write_text(json.dumps(payload, allow_nan=False))
        for validator in ("validate_rankings.py", "validate_portfolio_risk.py", "validate_backtest.py"):
            subprocess.run([sys.executable, str(ROOT / "scripts" / validator), "--artifacts", str(quant)], check=True)
        if publish:
            subprocess.run([sys.executable, str(ROOT / "scripts/publish.py"), "--artifacts", str(quant)], check=True)
    except Exception as exc:
        errors.append(f"Quant: {type(exc).__name__}")
        print(f"Quant calculation/publication failed: {exc}", flush=True)
    # Astra consumes exactly the same in-memory frames even if Quant validation
    # fails. A partial outcome is reported as failed, never 'both completed'.
    report("progress", processed=coverage["processed"], total=coverage["discovered"],
           message="Calculating Astra backtests from the same downloaded bars")
    try:
        astra = output / "astra"
        astra.mkdir(parents=True, exist_ok=True)
        payload = build(prices, metadata, benchmark, config)
        payload.update(coverage=coverage, shared_run_id=request_id,
                       generated_at=datetime.now(timezone.utc).isoformat())
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        payload["run_id"] = "astra-" + hashlib.sha256(encoded.encode()).hexdigest()[:24]
        (astra / "latest.json").write_text(json.dumps(payload, separators=(",", ":"), allow_nan=False))
        for key, result in payload["strategies"].items():
            pd.DataFrame([{k: json.dumps(v, separators=(",", ":")) if isinstance(v, (dict, list)) else v for k, v in trade.items()} for trade in result["trades"]]).to_csv(astra / f"{key}-trades.csv", index=False)
        if publish:
            report("publish", data=payload, defer_completion=True)
    except Exception as exc:
        errors.append(f"Astra: {type(exc).__name__}")
        print(f"Astra calculation/publication failed: {exc}", flush=True)
    if errors:
        raise RuntimeError("Shared scan incomplete: " + "; ".join(errors) + ". See workflow logs; successful output was retained.")
    report("complete", message="Quant, Wizard and Astra published from one shared 300-bar scan")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    if os.getenv("MAX_SYMBOLS", "0") not in ("", "0"):
        raise SystemExit("Shared production runs require the full universe (MAX_SYMBOLS=0)")
    config = Config(**json.loads(os.getenv("ASTRA_CONFIG") or "{}"))
    request_id = os.getenv("ASTRA_REQUEST_ID") or f"shared-{uuid.uuid4()}"
    base = os.getenv("QUANT_API_BASE", "").rstrip("/")
    token = os.getenv("PUBLISH_TOKEN", "")
    if args.publish and (not base or not token):
        raise SystemExit("Existing QUANT_API_BASE and PUBLISH_TOKEN secrets are required")
    session = requests.Session()
    session.headers.update({"authorization": f"Bearer {token}", "user-agent": "quant-shared/1"})
    def report(action, **payload):
        if not args.publish:
            print(action, payload.get("message", ""), flush=True)
            return
        response = session.post(f"{base}/api/admin/astra", json={"request_id": request_id, "action": action, **payload}, timeout=60)
        if not response.ok:
            raise RuntimeError(f"Shared scan {action}: HTTP {response.status_code}: {response.text[:200]}")
    try:
        execute(args.output, config, report, args.publish, request_id)
    except Exception as exc:
        try: report("failed", message=f"Shared run stopped ({type(exc).__name__}). Check GitHub logs; old results remain available.")
        except Exception: pass
        raise


if __name__ == "__main__":
    main()
