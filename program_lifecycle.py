#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


CATALOG_MIN_PROGRAMS = 100
CATALOG_MAX_PROGRAMS = 5000
MAX_DOMAINS_PER_PROGRAM = 2000
MAX_FIELD_LENGTH = 4096
HOST_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$"
)


def secure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)


def atomic_json(path: Path, value: dict) -> None:
    secure_directory(path.parent)

    rendered = json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(rendered)
        handle.flush()
        os.fsync(handle.fileno())

    temporary.chmod(0o600)
    os.replace(temporary, path)
    path.chmod(0o600)


def read_json(path: Path, label: str):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"{label} not found: {path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"Invalid JSON in {path} at line {exc.lineno}"
        )


def sha256_json(value) -> str:
    material = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    return hashlib.sha256(material).hexdigest()


def clean_text(value, label: str) -> str:
    if not isinstance(value, str):
        raise SystemExit(f"BLOCKED: {label} must be a string")

    cleaned = " ".join(value.split()).strip()

    if not cleaned:
        raise SystemExit(f"BLOCKED: {label} is empty")

    if len(cleaned) > MAX_FIELD_LENGTH:
        raise SystemExit(f"BLOCKED: {label} is too long")

    return cleaned


def canonical_url(value: str) -> str:
    raw = clean_text(value, "program URL")
    parsed = urlsplit(raw)

    if parsed.scheme.lower() not in {"http", "https"}:
        raise SystemExit(
            "BLOCKED: program URL must use HTTP or HTTPS"
        )

    if not parsed.hostname:
        raise SystemExit(
            "BLOCKED: program URL hostname is missing"
        )

    if parsed.username is not None or parsed.password is not None:
        raise SystemExit(
            "BLOCKED: program URL userinfo is not allowed"
        )

    host = parsed.hostname.lower().rstrip(".")
    port = parsed.port

    if (
        (parsed.scheme.lower() == "http" and port == 80)
        or (parsed.scheme.lower() == "https" and port == 443)
    ):
        port = None

    netloc = host if port is None else f"{host}:{port}"
    path = parsed.path or "/"

    if path != "/":
        path = path.rstrip("/") or "/"

    return urlunsplit(
        (
            parsed.scheme.lower(),
            netloc,
            path,
            parsed.query,
            "",
        )
    )


def normalize_domain(value: str) -> str:
    return clean_text(value, "program domain").lower()


def normalize_program(record: dict) -> dict:
    if not isinstance(record, dict):
        raise SystemExit(
            "BLOCKED: catalog program entry must be an object"
        )

    name = clean_text(record.get("name"), "program name")
    url = canonical_url(record.get("url"))

    bounty = record.get("bounty")

    if not isinstance(bounty, bool):
        raise SystemExit(
            "BLOCKED: program bounty must be boolean"
        )

    domains = record.get("domains")

    if not isinstance(domains, list):
        raise SystemExit(
            "BLOCKED: program domains must be an array"
        )

    if len(domains) > MAX_DOMAINS_PER_PROGRAM:
        raise SystemExit(
            "BLOCKED: too many domains in one program"
        )

    normalized_domains = sorted(
        {
            normalize_domain(domain)
            for domain in domains
        }
    )

    identifier = hashlib.sha256(
        url.encode("utf-8")
    ).hexdigest()

    return {
        "id": identifier,
        "name": name,
        "url": url,
        "bounty": bounty,
        "domains": normalized_domains,
    }


def normalize_catalog(catalog: dict) -> list[dict]:
    if not isinstance(catalog, dict):
        raise SystemExit(
            "BLOCKED: program catalog root must be an object"
        )

    programs = catalog.get("programs")

    if not isinstance(programs, list):
        raise SystemExit(
            "BLOCKED: catalog programs must be an array"
        )

    if not (
        CATALOG_MIN_PROGRAMS
        <= len(programs)
        <= CATALOG_MAX_PROGRAMS
    ):
        raise SystemExit(
            "BLOCKED: suspicious program catalog size"
        )

    normalized = [
        normalize_program(record)
        for record in programs
    ]

    identifiers = [
        record["id"]
        for record in normalized
    ]

    if len(identifiers) != len(set(identifiers)):
        raise SystemExit(
            "BLOCKED: duplicate canonical program URLs"
        )

    return sorted(
        normalized,
        key=lambda item: item["id"],
    )


def snapshot(programs: list[dict]) -> dict:
    return {
        "program_catalog_snapshot_version": 1,
        "program_count": len(programs),
        "catalog_sha256": sha256_json(programs),
        "programs": programs,
    }


def load_current(path: Path) -> dict | None:
    if not path.exists():
        return None

    value = read_json(path, "Current program snapshot")

    if (
        not isinstance(value, dict)
        or value.get(
            "program_catalog_snapshot_version"
        )
        != 1
        or not isinstance(value.get("programs"), list)
    ):
        raise SystemExit(
            "BLOCKED: invalid current program snapshot"
        )

    return value


def load_history(path: Path) -> dict:
    if not path.exists():
        return {
            "program_history_version": 1,
            "programs": {},
        }

    value = read_json(path, "Program lifecycle history")

    if (
        not isinstance(value, dict)
        or value.get("program_history_version") != 1
        or not isinstance(value.get("programs"), dict)
    ):
        raise SystemExit(
            "BLOCKED: invalid program lifecycle history"
        )

    return value


def record_map(programs: list[dict]) -> dict[str, dict]:
    return {
        record["id"]: record
        for record in programs
    }


def changed_details(old: dict, new: dict) -> dict:
    fields = []

    if old["name"] != new["name"]:
        fields.append("name")

    if old["bounty"] != new["bounty"]:
        fields.append("bounty")

    old_domains = set(old["domains"])
    new_domains = set(new["domains"])

    added_domains = sorted(new_domains - old_domains)
    removed_domains = sorted(old_domains - new_domains)

    if added_domains or removed_domains:
        fields.append("domains")

    return {
        "id": new["id"],
        "name": new["name"],
        "url": new["url"],
        "fields": fields,
        "old_bounty": old["bounty"],
        "new_bounty": new["bounty"],
        "domains_added": added_domains,
        "domains_removed": removed_domains,
    }


def delta(
    old_programs: list[dict],
    new_programs: list[dict],
    history: dict,
) -> dict:
    old = record_map(old_programs)
    new = record_map(new_programs)
    history_programs = history["programs"]

    added = []
    reopened = []
    removed = []
    changed = []

    for identifier in sorted(new.keys() - old.keys()):
        record = new[identifier]
        historical = history_programs.get(identifier)

        if (
            isinstance(historical, dict)
            and historical.get("present") is False
        ):
            reopened.append(record)
        else:
            added.append(record)

    for identifier in sorted(old.keys() - new.keys()):
        removed.append(old[identifier])

    for identifier in sorted(old.keys() & new.keys()):
        if old[identifier] != new[identifier]:
            changed.append(
                changed_details(
                    old[identifier],
                    new[identifier],
                )
            )

    return {
        "added": added,
        "possible_reopened": reopened,
        "removed": removed,
        "changed": changed,
    }


def update_history(
    history: dict,
    programs: list[dict],
    changes: dict,
) -> dict:
    next_history = json.loads(
        json.dumps(history)
    )
    entries = next_history["programs"]
    current = record_map(programs)

    for identifier, record in current.items():
        prior = entries.get(identifier)

        if not isinstance(prior, dict):
            prior = {
                "times_removed": 0,
            }

        prior["present"] = True
        prior["last_record"] = record
        prior.setdefault("times_removed", 0)
        entries[identifier] = prior

    for record in changes["removed"]:
        identifier = record["id"]
        prior = entries.get(identifier)

        if not isinstance(prior, dict):
            prior = {
                "times_removed": 0,
            }

        prior["present"] = False
        prior["last_record"] = record
        prior["times_removed"] = (
            int(prior.get("times_removed") or 0) + 1
        )
        entries[identifier] = prior

    return next_history


def one_line(value: str) -> str:
    return " ".join(value.split())


def lifecycle_lines(kind: str, records: list[dict]) -> list[str]:
    lines = []

    if kind in {"added", "possible_reopened", "removed"}:
        for record in records:
            bounty = "bounty" if record["bounty"] else "VDP"
            lines.append(
                one_line(
                    f"• {record['name']} — "
                    f"{record['url']} — {bounty}"
                )
            )

        return lines

    for record in records:
        details = []

        if "name" in record["fields"]:
            details.append("name changed")

        if "bounty" in record["fields"]:
            details.append(
                "bounty: "
                f"{str(record['old_bounty']).lower()}"
                "→"
                f"{str(record['new_bounty']).lower()}"
            )

        if "domains" in record["fields"]:
            details.append(
                "domains: "
                f"+{len(record['domains_added'])}"
                f"/-{len(record['domains_removed'])}"
            )

        lines.append(
            one_line(
                f"• {record['name']} — "
                f"{record['url']} — "
                + "; ".join(details)
            )
        )

    return lines


def notification_title(kind: str) -> str:
    return {
        "added": "PROGRAM CATALOG — ADDED",
        "possible_reopened": (
            "PROGRAM CATALOG — POSSIBLE REOPENED — VERIFY"
        ),
        "removed": "PROGRAM CATALOG — REMOVED",
        "changed": "PROGRAM CATALOG — CHANGED",
    }[kind]


def notification(
    kind: str,
    records: list[dict],
    old_sha: str,
    new_sha: str,
) -> dict:
    semantic = {
        "kind": kind,
        "records": records,
        "old_sha256": old_sha,
        "new_sha256": new_sha,
    }

    return {
        "program_notification_version": 1,
        "kind": kind,
        "title": notification_title(kind),
        "count": len(records),
        "dedupe_key": sha256_json(semantic),
        "old_sha256": old_sha,
        "new_sha256": new_sha,
        "lines": lifecycle_lines(kind, records),
    }


def host_candidate(value: str) -> str | None:
    raw = value.strip().lower()

    if not raw:
        return None

    if "://" in raw:
        parsed = urlsplit(raw)
        raw = parsed.hostname or ""
    else:
        raw = raw.split("/", 1)[0]
        raw = raw.split(":", 1)[0]

    raw = raw.lstrip("*.").lstrip(".").rstrip(".")

    if not raw or "*" in raw or "_" in raw:
        return None

    try:
        ipaddress.ip_address(raw)
        return None
    except ValueError:
        pass

    if (
        not HOST_RE.fullmatch(raw)
        or len(raw.split(".")) < 2
    ):
        return None

    return raw


def choose_candidate_host(record: dict) -> str | None:
    candidates = {
        candidate
        for domain in record["domains"]
        if (candidate := host_candidate(domain))
        is not None
    }

    if not candidates:
        return None

    return sorted(
        candidates,
        key=lambda host: (
            len(host.split(".")),
            len(host),
            host,
        ),
    )[0]


def onboarding_event(
    kind: str,
    record: dict,
    old_sha: str,
    new_sha: str,
    detected_at: str,
) -> dict | None:
    candidate = choose_candidate_host(record)

    if candidate is None:
        return None

    trigger = (
        "program_reopened_possible"
        if kind == "possible_reopened"
        else "program_added"
    )

    event_id = hashlib.sha256(
        (
            "program-lifecycle|"
            f"{kind}|{record['id']}|{new_sha}"
        ).encode("utf-8")
    ).hexdigest()

    return {
        "event_version": 1,
        "event_id": event_id,
        "event_type": "source_onboarding_candidate",
        "trigger": trigger,
        "status": "pending",
        "detected_at": detected_at,
        "dataset": "dist/data.json",
        "raw_value": record["url"],
        "candidate_host": candidate,
        "program_hint": record["name"],
        "public_sources_only": True,
        "source_discovery_allowed": True,
        "old_sha256": old_sha,
        "new_sha256": new_sha,
        "producer": {
            "component": (
                "bb-monitor-program-lifecycle"
            ),
            "source": (
                "projectdiscovery/"
                "public-bugbounty-programs"
            ),
        },
    }


def execute(
    catalog: dict,
    state_dir: Path,
    events_dir: Path,
    detected_at: str,
) -> dict:
    secure_directory(state_dir)
    secure_directory(events_dir)
    secure_directory(state_dir / "outbox")

    lock_path = state_dir / ".lock"
    lock_path.touch(mode=0o600, exist_ok=True)
    lock_path.chmod(0o600)

    with lock_path.open("r+") as lock_handle:
        fcntl.flock(lock_handle, fcntl.LOCK_EX)

        programs = normalize_catalog(catalog)
        next_snapshot = snapshot(programs)

        current_path = state_dir / "current.json"
        history_path = state_dir / "history.json"
        result_path = state_dir / "last-result.json"

        current = load_current(current_path)
        history = load_history(history_path)

        if current is None:
            initial_history = {
                "program_history_version": 1,
                "programs": {
                    record["id"]: {
                        "present": True,
                        "times_removed": 0,
                        "last_record": record,
                    }
                    for record in programs
                },
            }

            result = {
                "program_lifecycle_result_version": 1,
                "status": "baseline_created",
                "changed": False,
                "program_count": len(programs),
                "catalog_sha256": (
                    next_snapshot["catalog_sha256"]
                ),
                "counts": {
                    "added": 0,
                    "possible_reopened": 0,
                    "removed": 0,
                    "changed": 0,
                },
                "notifications_created": 0,
                "events_created": 0,
            }

            atomic_json(current_path, next_snapshot)
            atomic_json(history_path, initial_history)
            atomic_json(result_path, result)
            return result

        old_programs = current["programs"]
        old_sha = str(current.get("catalog_sha256") or "")
        new_sha = next_snapshot["catalog_sha256"]
        changes = delta(old_programs, programs, history)

        counts = {
            key: len(value)
            for key, value in changes.items()
        }

        changed = any(counts.values())
        notifications_created = 0
        events_created = 0

        if changed:
            for kind in (
                "added",
                "possible_reopened",
                "removed",
                "changed",
            ):
                records = changes[kind]

                if not records:
                    continue

                payload = notification(
                    kind,
                    records,
                    old_sha,
                    new_sha,
                )
                outbox_path = (
                    state_dir
                    / "outbox"
                    / f"{payload['dedupe_key']}.json"
                )

                if not outbox_path.exists():
                    atomic_json(outbox_path, payload)
                    notifications_created += 1

            for kind in (
                "added",
                "possible_reopened",
            ):
                for record in changes[kind]:
                    event = onboarding_event(
                        kind,
                        record,
                        old_sha,
                        new_sha,
                        detected_at,
                    )

                    if event is None:
                        continue

                    event_path = (
                        events_dir
                        / f"{event['event_id']}.json"
                    )

                    if not event_path.exists():
                        atomic_json(event_path, event)
                        events_created += 1

        next_history = update_history(
            history,
            programs,
            changes,
        )

        result = {
            "program_lifecycle_result_version": 1,
            "status": "changed" if changed else "no_change",
            "changed": changed,
            "program_count": len(programs),
            "old_sha256": old_sha,
            "new_sha256": new_sha,
            "counts": counts,
            "notifications_created": notifications_created,
            "events_created": events_created,
        }

        atomic_json(current_path, next_snapshot)
        atomic_json(history_path, next_history)
        atomic_json(result_path, result)

        return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Track public bug bounty program lifecycle "
            "changes without performing network requests."
        )
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--events-dir",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--detected-at",
        required=True,
    )
    parser.add_argument(
        "--result-output",
        type=Path,
    )
    args = parser.parse_args()

    catalog_path = args.catalog.expanduser().resolve()
    state_dir = args.state_dir.expanduser().resolve()
    events_dir = args.events_dir.expanduser().resolve()

    catalog = read_json(
        catalog_path,
        "Program catalog",
    )

    result = execute(
        catalog,
        state_dir,
        events_dir,
        args.detected_at,
    )

    rendered = json.dumps(
        result,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"

    if args.result_output is not None:
        destination = (
            args.result_output
            .expanduser()
            .resolve()
        )
        atomic_json(destination, result)

    print(rendered, end="")


if __name__ == "__main__":
    main()
