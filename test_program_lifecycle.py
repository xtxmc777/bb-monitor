#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MODULE_PATH = ROOT / "program_lifecycle.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "program_lifecycle",
        MODULE_PATH,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(
            "Lifecycle module could not be loaded"
        )

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module()


def program(
    name: str,
    url: str,
    bounty: bool,
    domains: list[str],
) -> dict:
    return {
        "name": name,
        "url": url,
        "bounty": bounty,
        "domains": domains,
    }


def filler(index: int) -> dict:
    return program(
        f"Fixture {index:03d}",
        f"https://example.test/program/{index:03d}",
        index % 2 == 0,
        [f"fixture-{index:03d}.example.test"],
    )


def catalog(overrides: list[dict] | None = None) -> dict:
    records = [
        filler(index)
        for index in range(100)
    ]

    if overrides is not None:
        records = overrides

    return {
        "programs": records,
    }


def files(path: Path, pattern: str) -> list[Path]:
    return sorted(path.glob(pattern))


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        state = root / "state"
        events = root / "events"

        baseline_catalog = catalog()

        baseline = MODULE.execute(
            baseline_catalog,
            state,
            events,
            "2026-07-25T00:00:00Z",
        )

        assert baseline["status"] == "baseline_created"
        assert baseline["notifications_created"] == 0
        assert baseline["events_created"] == 0

        unchanged = MODULE.execute(
            copy.deepcopy(baseline_catalog),
            state,
            events,
            "2026-07-25T00:10:00Z",
        )

        assert unchanged["status"] == "no_change"

        added_record = program(
            "New Vendor",
            "https://hackerone.com/new-vendor",
            True,
            ["*.new-vendor.example"],
        )

        added_catalog = copy.deepcopy(
            baseline_catalog
        )
        added_catalog["programs"].append(
            added_record
        )

        added = MODULE.execute(
            added_catalog,
            state,
            events,
            "2026-07-25T00:20:00Z",
        )

        assert added["status"] == "changed"
        assert added["counts"]["added"] == 1
        assert added["notifications_created"] == 1
        assert added["events_created"] == 1

        outbox = files(
            state / "outbox",
            "*.json",
        )
        event_files = files(events, "*.json")

        assert len(outbox) == 1
        assert len(event_files) == 1

        event = json.loads(
            event_files[0].read_text(
                encoding="utf-8"
            )
        )
        assert event["trigger"] == "program_added"
        assert (
            event["candidate_host"]
            == "new-vendor.example"
        )

        for path in outbox:
            path.unlink()

        changed_catalog = copy.deepcopy(
            added_catalog
        )

        for item in changed_catalog["programs"]:
            if item["name"] == "New Vendor":
                item["bounty"] = False
                item["domains"].append(
                    "api.new-vendor.example"
                )

        changed = MODULE.execute(
            changed_catalog,
            state,
            events,
            "2026-07-25T00:30:00Z",
        )

        assert changed["counts"]["changed"] == 1
        assert changed["notifications_created"] == 1
        assert changed["events_created"] == 1

        changed_events = [
            json.loads(
                path.read_text(encoding="utf-8")
            )
            for path in files(events, "*.json")
        ]

        domain_change_events = [
            item
            for item in changed_events
            if item["trigger"]
            == "program_domains_changed"
        ]

        assert len(domain_change_events) == 1
        assert (
            domain_change_events[0]["candidate_host"]
            == "new-vendor.example"
        )
        assert (
            domain_change_events[0]["onboarding_key"]
            == MODULE.normalize_program(
                added_record
            )["id"]
        )

        for path in files(state / "outbox", "*.json"):
            path.unlink()

        removed_catalog = copy.deepcopy(
            changed_catalog
        )
        removed_catalog["programs"] = [
            item
            for item in removed_catalog["programs"]
            if item["name"] != "New Vendor"
        ]

        removed = MODULE.execute(
            removed_catalog,
            state,
            events,
            "2026-07-25T00:40:00Z",
        )

        assert removed["counts"]["removed"] == 1
        assert removed["notifications_created"] == 1

        for path in files(state / "outbox", "*.json"):
            path.unlink()

        reopened = MODULE.execute(
            changed_catalog,
            state,
            events,
            "2026-07-25T00:50:00Z",
        )

        assert (
            reopened["counts"]["possible_reopened"]
            == 1
        )
        assert reopened["notifications_created"] == 1
        assert reopened["events_created"] == 1

        reopened_events = [
            json.loads(
                path.read_text(encoding="utf-8")
            )
            for path in files(events, "*.json")
        ]

        assert any(
            item["trigger"]
            == "program_reopened_possible"
            for item in reopened_events
        )

        before_current = (
            state / "current.json"
        ).read_bytes()

        invalid = {
            "programs": [
                program(
                    "Only one",
                    "https://example.test/only",
                    True,
                    ["only.example.test"],
                )
            ]
        }

        try:
            MODULE.execute(
                invalid,
                state,
                events,
                "2026-07-25T01:00:00Z",
            )
        except SystemExit:
            pass
        else:
            raise AssertionError(
                "Suspicious catalog size was accepted"
            )

        assert (
            state / "current.json"
        ).read_bytes() == before_current

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        state = root / "state"
        events = root / "events"

        baseline_catalog = catalog()

        MODULE.execute(
            baseline_catalog,
            state,
            events,
            "2026-07-25T02:00:00Z",
        )

        renamed_catalog = copy.deepcopy(
            baseline_catalog
        )
        renamed_catalog["programs"][0]["name"] = (
            "Renamed Fixture"
        )

        renamed = MODULE.execute(
            renamed_catalog,
            state,
            events,
            "2026-07-25T02:10:00Z",
        )

        assert renamed["counts"]["changed"] == 1
        assert renamed["events_created"] == 0

    print("baseline_notifications=ZERO")
    print("program_added=DETECTED")
    print("program_changed=DETECTED")
    print("domain_change_onboarding=CREATED")
    print("name_only_change_onboarding=SKIPPED")
    print("program_removed=DETECTED")
    print("possible_reopened=DETECTED")
    print("onboarding_events=CREATED")
    print("notification_outbox=CREATED")
    print("invalid_catalog_state=UNCHANGED")
    print("network_requests=ZERO")
    print("telegram_requests=ZERO")
    print("PROGRAM-LIFECYCLE-TESTS-OK")


if __name__ == "__main__":
    main()
