#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path


HOST_RE = re.compile(r"^[a-z0-9.-]+$")


def secure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)


def secure_file(path: Path) -> None:
    path.chmod(0o600)


def normalize_candidate(raw: str) -> str | None:
    value = raw.strip().lower().rstrip(".")

    if value.startswith("*."):
        value = value[2:]

    if (
        not value
        or len(value) > 253
        or "/" in value
        or ":" in value
        or " " in value
        or "*" in value
        or not HOST_RE.fullmatch(value)
    ):
        return None

    labels = value.split(".")

    if len(labels) < 2:
        return None

    for label in labels:
        if (
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
        ):
            return None

    return value


def event_time() -> str:
    override = os.environ.get("BB_EVENT_DETECTED_AT", "").strip()

    if override:
        return override

    return datetime.now(timezone.utc).isoformat()


def event_id(
    *,
    dataset: str,
    raw_value: str,
    candidate_host: str,
    old_sha: str,
    new_sha: str,
) -> str:
    material = json.dumps(
        {
            "event_type": "source_onboarding_candidate",
            "trigger": "new_wildcard",
            "dataset": dataset,
            "raw_value": raw_value,
            "candidate_host": candidate_host,
            "old_sha256": old_sha,
            "new_sha256": new_sha,
        },
        sort_keys=True,
        separators=(",", ":"),
    )

    return hashlib.sha256(
        material.encode("utf-8")
    ).hexdigest()


def emit_events(
    *,
    input_file: Path,
    state_root: Path,
    dataset: str,
    old_sha: str,
    new_sha: str,
) -> dict[str, int]:
    if dataset != "wildcards.out":
        raise SystemExit(
            "BLOCKED: onboarding events are limited to wildcards.out"
        )

    if len(old_sha) != 64 or len(new_sha) != 64:
        raise SystemExit("BLOCKED: invalid baseline hash")

    pending = state_root / "events" / "pending"
    secure_directory(state_root)
    secure_directory(state_root / "events")
    secure_directory(pending)

    created = 0
    existing = 0
    rejected = 0

    values = sorted(
        {
            line.strip()
            for line in input_file.read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        }
    )

    for raw_value in values:
        candidate_host = normalize_candidate(raw_value)

        if candidate_host is None:
            rejected += 1
            continue

        identifier = event_id(
            dataset=dataset,
            raw_value=raw_value,
            candidate_host=candidate_host,
            old_sha=old_sha,
            new_sha=new_sha,
        )

        destination = pending / f"{identifier}.json"

        if destination.exists():
            existing += 1
            continue

        event = {
            "event_version": 1,
            "event_id": identifier,
            "event_type": "source_onboarding_candidate",
            "trigger": "new_wildcard",
            "status": "pending",
            "detected_at": event_time(),
            "dataset": dataset,
            "raw_value": raw_value,
            "candidate_host": candidate_host,
            "program_hint": candidate_host,
            "public_sources_only": True,
            "source_discovery_allowed": True,
            "old_sha256": old_sha,
            "new_sha256": new_sha,
            "producer": {
                "component": "bb-monitor",
                "source": "projectdiscovery/public-bugbounty-programs",
            },
        }

        temporary = pending / f".{identifier}.tmp"

        temporary.write_text(
            json.dumps(
                event,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ) + "\n",
            encoding="utf-8",
        )
        secure_file(temporary)
        os.replace(temporary, destination)
        secure_file(destination)
        created += 1

    return {
        "created": created,
        "existing": existing,
        "rejected": rejected,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Create deterministic Source Intelligence onboarding "
            "events from newly added wildcard entries. No network "
            "operations are performed."
        )
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--old-sha", required=True)
    parser.add_argument("--new-sha", required=True)
    args = parser.parse_args()

    result = emit_events(
        input_file=args.input.expanduser().resolve(),
        state_root=args.state.expanduser().resolve(),
        dataset=args.dataset,
        old_sha=args.old_sha.strip().lower(),
        new_sha=args.new_sha.strip().lower(),
    )

    print(f"source_onboarding_events_created={result['created']}")
    print(f"source_onboarding_events_existing={result['existing']}")
    print(f"source_onboarding_events_rejected={result['rejected']}")
    print("network_requests=ZERO")


if __name__ == "__main__":
    main()
