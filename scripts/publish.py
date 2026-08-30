#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import requests


def token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_auth(session: requests.Session, base: str, token: str) -> None:
    local = token_fingerprint(token)
    response = session.get(f"{base}/api/auth-fingerprint", timeout=30)
    if not response.ok:
        raise RuntimeError(f"auth diagnostic failed with HTTP {response.status_code}: {response.text[:300]}")
    data = response.json()
    remote = str(data.get("fingerprint") or "")
    if not data.get("configured"):
        raise RuntimeError("Cloudflare PUBLISH_TOKEN is not configured")
    if remote != local:
        raise RuntimeError(f"PUBLISH_TOKEN_MISMATCH: github={local} cloudflare={remote}")
    print(f"PUBLISH_TOKEN fingerprint match: {local}")


def post(session: requests.Session, url: str, payload: dict) -> dict:
    response = session.post(url, json=payload, timeout=60)
    if not response.ok:
        raise RuntimeError(f"publish rejected with HTTP {response.status_code}: {response.text[:500]}")
    return response.json()


def get(session: requests.Session, url: str) -> dict:
    response = session.get(url, timeout=60)
    if not response.ok:
        raise RuntimeError(f"verification request rejected with HTTP {response.status_code}: {response.text[:500]}")
    return response.json()


def chunks(rows: list[dict], size: int = 75):
    for index in range(0, len(rows), size):
        yield rows[index:index + size]


def research_fingerprint(research: list[dict]) -> str:
    canonical = sorted(research, key=lambda row: str(row.get("symbol") or ""))
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:12]


def content_addressed_run_id(payload: dict, research: list[dict]) -> str:
    scan_date = str(payload.get("scan_date") or "").strip()
    if not scan_date:
        raise RuntimeError("missing scan_date for content-addressed run id")
    return f"qv5-{scan_date}-{research_fingerprint(research)}"


def validate_archive_status(status: dict, run_id: str, expected_symbols: int) -> None:
    if not status.get("ok"):
        raise RuntimeError(f"research archive status not ok: {status}")
    if str(status.get("run_id") or "") != run_id:
        raise RuntimeError(f"research archive run_id mismatch: {status.get('run_id')} != {run_id}")
    if status.get("status") != "archived" or status.get("integrity") != "verified":
        raise RuntimeError(f"research archive not verified: {status}")
    expected = int(status.get("expected_symbols") or 0)
    received = int(status.get("received_symbols") or 0)
    if expected != expected_symbols or received != expected_symbols:
        raise RuntimeError(f"research archive count mismatch: expected={expected} received={received} local={expected_symbols}")
    for key in ("payload_hash", "manifest_hash"):
        value = str(status.get(key) or "")
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise RuntimeError(f"research archive missing valid {key}: {value!r}")
    if not status.get("archived_at"):
        raise RuntimeError("research archive missing archived_at")


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish Quant artifacts to Cloudflare Pages Functions")
    parser.add_argument("--artifacts", default="artifacts/latest")
    parser.add_argument("--api-base", default=os.getenv("QUANT_API_BASE", ""))
    args = parser.parse_args()
    token = os.getenv("PUBLISH_TOKEN", "")
    if not args.api_base or not token:
        raise SystemExit("QUANT_API_BASE and PUBLISH_TOKEN are required")

    root = Path(args.artifacts)
    latest_path = root / "latest.json"
    research_path = root / "research.json"
    payload = json.loads(latest_path.read_text(encoding="utf-8"))
    research = json.loads(research_path.read_text(encoding="utf-8"))
    if not isinstance(research, list) or not research:
        raise RuntimeError("research.json must contain at least one row")

    run_id = content_addressed_run_id(payload, research)
    payload["run_id"] = run_id
    latest_path.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False), encoding="utf-8")
    local_integrity = {
        "schema_version": 1,
        "run_id": run_id,
        "scan_date": payload.get("scan_date"),
        "latest_file_sha256": file_sha256(latest_path),
        "research_file_sha256": file_sha256(research_path),
        "research_fingerprint": research_fingerprint(research),
    }

    session = requests.Session()
    session.headers.update({"authorization": f"Bearer {token}", "user-agent": "bmk-quant-publisher/5.0-step15"})
    base = args.api_base.rstrip("/")
    check_auth(session, base, token)

    start = post(session, f"{base}/api/admin/runs/{run_id}/research/start", {"expected_symbols": len(research)})
    print("Research archive start:", start)
    if start.get("status") != "already_archived":
        for batch in chunks(research):
            post(session, f"{base}/api/admin/runs/{run_id}/research/batch", {"rows": batch})
        print("Research archive commit:", post(session, f"{base}/api/admin/runs/{run_id}/research/commit", {}))

    archive = get(session, f"{base}/api/admin/runs/{run_id}/research/status")
    validate_archive_status(archive, run_id, len(research))
    print("Research archive verified; continuing normal Quant publication.")

    for batch in chunks(payload.get("portfolio", [])):
        post(session, f"{base}/api/admin/runs/{run_id}/portfolio", {"rows": batch})

    publication = post(session, f"{base}/api/admin/publish", {"data": payload})
    if publication.get("publication_integrity") != "verified":
        raise RuntimeError(f"Quant publication did not confirm payload integrity: {publication}")
    if publication.get("research_integrity") != "verified":
        raise RuntimeError(f"Quant publication did not confirm research integrity: {publication}")
    if publication.get("research_payload_hash") != archive.get("payload_hash"):
        raise RuntimeError("Quant publication research payload hash differs from verified archive")
    if publication.get("research_manifest_hash") != archive.get("manifest_hash"):
        raise RuntimeError("Quant publication research manifest hash differs from verified archive")

    live = get(session, f"{base}/api/latest")
    if not live.get("ok") or live.get("integrity") != "verified" or not isinstance(live.get("data"), dict):
        raise RuntimeError(f"live publication integrity check failed: {live}")
    if str(live["data"].get("run_id") or "") != run_id:
        raise RuntimeError(f"live run_id mismatch: {live['data'].get('run_id')} != {run_id}")
    if str(live.get("payload_hash") or "") != str(publication.get("payload_hash") or ""):
        raise RuntimeError("live payload hash differs from committed publication hash")

    integrity = {
        **local_integrity,
        "server_payload_hash": publication.get("payload_hash"),
        "research_payload_hash": archive.get("payload_hash"),
        "research_manifest_hash": archive.get("manifest_hash"),
        "publication_integrity": publication.get("publication_integrity"),
        "live_integrity": live.get("integrity"),
    }
    (root / "integrity.json").write_text(json.dumps(integrity, indent=2, sort_keys=True), encoding="utf-8")
    print("Quant publication:", publication)
    print("Live publication integrity verified:", run_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
