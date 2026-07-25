#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent
MONITOR = ROOT / "monitor.sh"


def main() -> None:
    lines = MONITOR.read_text(
        encoding="utf-8"
    ).splitlines()

    assets = [
        index
        for index, line in enumerate(lines)
        if line == "process_dataset \\"
        and index + 1 < len(lines)
        and lines[index + 1].strip() == '"assets" \\'
    ]

    wildcards = [
        index
        for index, line in enumerate(lines)
        if line == "process_dataset \\"
        and index + 1 < len(lines)
        and lines[index + 1].strip() == '"wildcards" \\'
    ]

    lifecycle = [
        index
        for index, line in enumerate(lines)
        if line.strip()
        == "process_program_catalog || status=1"
    ]

    exits = [
        index
        for index, line in enumerate(lines)
        if line.strip() == 'exit "$status"'
    ]

    assert len(assets) == 1
    assert len(wildcards) == 1
    assert len(lifecycle) == 1
    assert len(exits) == 1

    assert (
        assets[0]
        < wildcards[0]
        < lifecycle[0]
        < exits[0]
    )

    assert not any(
        line.strip().startswith(
            "process_program_catalog"
        )
        for line in lines[exits[0] + 1 :]
    )

    print("assets_call=BEFORE_WILDCARDS")
    print("wildcards_call=BEFORE_PROGRAM_LIFECYCLE")
    print("program_lifecycle_call=BEFORE_FINAL_EXIT")
    print("program_lifecycle_failure=PROPAGATES_TO_STATUS")
    print("unreachable_main_flow=ZERO")
    print("MONITOR-MAIN-FLOW-TESTS-OK")


if __name__ == "__main__":
    main()
