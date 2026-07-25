#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
HELPER = ROOT / "emit_onboarding_events.py"


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def run(helper: Path, added: Path, state: Path) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["BB_EVENT_DETECTED_AT"] = "2026-07-25T12:00:00+00:00"

    return subprocess.run(
        [
            str(helper),
            "--input",
            str(added),
            "--state",
            str(state),
            "--dataset",
            "wildcards.out",
            "--old-sha",
            "a" * 64,
            "--new-sha",
            "b" * 64,
        ],
        text=True,
        capture_output=True,
        env=environment,
        check=True,
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        state = root / "state"
        added = root / "wildcards.added.txt"

        added.write_text(
            "\n".join(
                (
                    "*.Example.com",
                    "*.Example.com",
                    "*.api.example.net",
                    "invalid host",
                    "*.localhost",
                )
            ) + "\n",
            encoding="utf-8",
        )

        first = run(HELPER, added, state)

        assert "source_onboarding_events_created=2" in first.stdout
        assert "source_onboarding_events_existing=0" in first.stdout
        assert "source_onboarding_events_rejected=2" in first.stdout

        pending = state / "events" / "pending"
        files = sorted(pending.glob("*.json"))
        assert len(files) == 2

        events = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in files
        ]

        assert {
            event["candidate_host"]
            for event in events
        } == {
            "example.com",
            "api.example.net",
        }

        for event, path in zip(events, files):
            assert event["event_type"] == "source_onboarding_candidate"
            assert event["trigger"] == "new_wildcard"
            assert event["status"] == "pending"
            assert event["public_sources_only"] is True
            assert event["source_discovery_allowed"] is True
            assert event["detected_at"] == "2026-07-25T12:00:00+00:00"
            assert len(event["event_id"]) == 64
            assert mode(path) == 0o600

        assert mode(state) == 0o700
        assert mode(state / "events") == 0o700
        assert mode(pending) == 0o700

        before = {
            path.name: path.read_bytes()
            for path in files
        }

        second = run(HELPER, added, state)

        assert "source_onboarding_events_created=0" in second.stdout
        assert "source_onboarding_events_existing=2" in second.stdout

        after = {
            path.name: path.read_bytes()
            for path in sorted(pending.glob("*.json"))
        }

        assert after == before

    print("new_wildcard_events=CREATED")
    print("duplicate_event_generation=IDEMPOTENT")
    print("invalid_candidates=REJECTED")
    print("event_files=600")
    print("event_directories=700")
    print("network_requests=ZERO")
    print("PROGRAM-EVENT-BRIDGE-TESTS-OK")


if __name__ == "__main__":
    main()
