#!/usr/bin/env bash

set -euo pipefail

export LC_ALL=C

BASE="${BB_MONITOR_BASE:-https://raw.githubusercontent.com/osamahamad/payout-targets-data/main}"
STATE="${BB_MONITOR_STATE:-state}"
DRY_RUN="${BB_MONITOR_DRY_RUN:-0}"
MAX_INLINE="${BB_MONITOR_MAX_INLINE:-20}"
MIN_LINES="${BB_MONITOR_MIN_LINES:-100}"
PLATFORM_FILTER="${PLATFORM_FILTER:-}"

mkdir -p "$STATE/sent"

if [[ "$DRY_RUN" == "1" ]]; then
  TG_ASSETS="${TG_ASSETS:-dry-run-assets}"
  TG_PROGRAMS="${TG_PROGRAMS:-dry-run-programs}"
else
  : "${TELEGRAM_TOKEN:?Missing TELEGRAM_TOKEN}"
  : "${TG_ASSETS:?Missing TG_ASSETS}"
  : "${TG_PROGRAMS:?Missing TG_PROGRAMS}"
fi


telegram_response_ok() {
  TELEGRAM_RESPONSE="$1" python3 - <<'PY'
import json
import os
import sys

try:
    response = json.loads(os.environ["TELEGRAM_RESPONSE"])
except json.JSONDecodeError:
    print("[!] Telegram returned invalid JSON", file=sys.stderr)
    raise SystemExit(1)

if response.get("ok") is not True:
    print(
        "[!] Telegram error: "
        + str(response.get("description", "unknown error")),
        file=sys.stderr,
    )
    raise SystemExit(1)
PY
}


send_message() {
  local chat="$1"
  local text="$2"

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "[DRY-RUN] sendMessage chat=$chat"
    printf '%s\n' "$text"
    return 0
  fi

  local response

  if ! response="$(
    curl \
      -sS \
      --fail-with-body \
      --retry 3 \
      --retry-delay 2 \
      --retry-all-errors \
      --connect-timeout 15 \
      --max-time 60 \
      -X POST \
      "https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage" \
      -d "chat_id=${chat}" \
      -d "disable_web_page_preview=true" \
      --data-urlencode "text=${text}"
  )"; then
    echo "[!] Telegram sendMessage failed" >&2
    return 1
  fi

  telegram_response_ok "$response"
}


send_document() {
  local chat="$1"
  local caption="$2"
  local file="$3"

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "[DRY-RUN] sendDocument chat=$chat file=$file"
    printf '%s\n' "$caption"
    return 0
  fi

  local response

  if ! response="$(
    curl \
      -sS \
      --fail-with-body \
      --retry 3 \
      --retry-delay 2 \
      --retry-all-errors \
      --connect-timeout 15 \
      --max-time 120 \
      -X POST \
      "https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendDocument" \
      -F "chat_id=${chat}" \
      -F "document=@${file};type=text/plain" \
      -F "caption=${caption}"
  )"; then
    echo "[!] Telegram sendDocument failed" >&2
    return 1
  fi

  telegram_response_ok "$response"
}


filter_file() {
  local file="$1"

  if [[ -z "$PLATFORM_FILTER" ]]; then
    return 0
  fi

  grep -Ei "$PLATFORM_FILTER" "$file" > "${file}.filtered" || true
  mv "${file}.filtered" "$file"
}


send_inline_list() {
  local chat="$1"
  local summary="$2"
  local file="$3"

  local part=1
  local chunk="${summary}"$'\n\n'
  local line candidate

  while IFS= read -r line || [[ -n "$line" ]]; do
    candidate="${chunk}${line}"$'\n'

    if [[ "${#candidate}" -gt 3800 ]]; then
      if [[ "$chunk" == "${summary}"$'\n\n' ]]; then
        echo "[!] scope entry exceeds Telegram inline limit" >&2
        return 1
      fi

      if ! send_message "$chat" "${chunk%$'\n'}"; then
        return 1
      fi

      part=$((part + 1))
      chunk="${summary}"$'\n'"Part: ${part}"$'\n\n'"${line}"$'\n'

      if [[ "${#chunk}" -gt 3800 ]]; then
        echo "[!] scope entry exceeds Telegram inline limit" >&2
        return 1
      fi
    else
      chunk="$candidate"
    fi
  done < "$file"

  if [[ "$chunk" != "${summary}"$'\n\n' ]]; then
    if ! send_message "$chat" "${chunk%$'\n'}"; then
      return 1
    fi
  fi
}

notify_change() {
  local name="$1"
  local change="$2"
  local file="$3"
  local chat="$4"
  local dataset="$5"
  local old_sha="$6"
  local new_sha="$7"

  local count
  count="$(wc -l < "$file")"

  [[ "$count" -gt 0 ]] || return 0

  local marker_material
  marker_material="${name}|${change}|${old_sha}|${new_sha}"

  local marker_hash
  marker_hash="$(
    printf '%s' "$marker_material" |
      sha256sum |
      awk '{print $1}'
  )"

  local marker="$STATE/sent/${name}-${marker_hash}.sent"

  if [[ -f "$marker" ]]; then
    echo "[-] already notified: $name $change"
    return 0
  fi

  local title

  case "$change" in
    added)
      title="SCOPE DELTA — ADDED"
      ;;
    removed)
      title="SCOPE DELTA — REMOVED"
      ;;
    *)
      echo "[!] unknown change type: $change" >&2
      return 1
      ;;
  esac

  local summary
  summary="${title}"$'\n\n'
  summary+="Dataset: ${dataset}"$'\n'
  summary+="Count: ${count}"$'\n'
  summary+="Scope status: PENDING VERIFICATION"$'\n'
  summary+="Recon: BLOCKED"

  if ! send_inline_list \
    "$chat" \
    "$summary" \
    "$file"
  then
    return 1
  fi

  touch "$marker"
  echo "[+] notified: $name $change ($count)"
}


process_dataset() {
  local name="$1"
  local url="$2"
  local chat="$3"
  local dataset="$4"

  local new="$STATE/${name}.new"
  local old="$STATE/${name}.txt"
  local added="$STATE/${name}.added.txt"
  local removed="$STATE/${name}.removed.txt"

  if ! curl \
    -sSf \
    --retry 3 \
    --retry-delay 2 \
    --retry-all-errors \
    --connect-timeout 15 \
    --max-time 120 \
    "$url" \
    -o "$new"
  then
    echo "[!] fetch failed: $name" >&2
    return 1
  fi

  sort -u "$new" -o "$new"

  local lines
  lines="$(wc -l < "$new")"

  if [[ "$lines" -lt "$MIN_LINES" ]]; then
    echo "[!] suspicious size: $name ($lines lines)" >&2
    rm -f "$new"
    return 1
  fi

  if [[ ! -f "$old" ]]; then
    mv "$new" "$old"
    echo "[+] baseline created: $name ($lines lines)"
    return 0
  fi

  sort -u "$old" -o "$old"

  comm -13 "$old" "$new" > "$added"
  comm -23 "$old" "$new" > "$removed"

  filter_file "$added"
  filter_file "$removed"

  local added_count removed_count
  added_count="$(wc -l < "$added")"
  removed_count="$(wc -l < "$removed")"

  echo "[+] $name: added=$added_count removed=$removed_count"

  local old_sha new_sha
  old_sha="$(sha256sum "$old" | awk '{print $1}')"
  new_sha="$(sha256sum "$new" | awk '{print $1}')"

  # SOURCE_INTELLIGENCE_ONBOARDING_EVENTS_V1
  if [[ "$name" == "wildcards" && "$added_count" -gt 0 ]]; then
    python3 "$(dirname "$0")/emit_onboarding_events.py" \
      --input "$added" \
      --state "$STATE" \
      --dataset "$dataset" \
      --old-sha "$old_sha" \
      --new-sha "$new_sha"
  fi

  local failed=0

  notify_change \
    "$name" \
    "added" \
    "$added" \
    "$chat" \
    "$dataset" \
    "$old_sha" \
    "$new_sha" || failed=1

  notify_change \
    "$name" \
    "removed" \
    "$removed" \
    "$chat" \
    "$dataset" \
    "$old_sha" \
    "$new_sha" || failed=1

  if [[ "$failed" -ne 0 ]]; then
    rm -f "$new" "$added" "$removed"
    echo "[!] notification incomplete: $name" >&2
    return 1
  fi

  mv "$new" "$old"
  rm -f "$added" "$removed"
  rm -f "$STATE/sent/${name}-"*.sent

  echo "[+] baseline advanced: $name"
}


status=0

# PROGRAM_LIFECYCLE_V1
notify_program_lifecycle_outbox() {
  local outbox_dir="$STATE/programs/outbox"

  [[ -d "$outbox_dir" ]] || return 0

  local notification
  local temporary
  local dedupe_key
  local title
  local count
  local marker
  local summary

  shopt -s nullglob
  local notifications=(
    "$outbox_dir"/*.json
  )
  shopt -u nullglob

  for notification in "${notifications[@]}"; do
    jq -e '
      .program_notification_version == 1
      and (.dedupe_key | type == "string" and length == 64)
      and (.title | type == "string" and length > 0)
      and (.count | type == "number" and . > 0)
      and (.lines | type == "array" and length > 0)
    ' "$notification" >/dev/null

    dedupe_key="$(
      jq -r '.dedupe_key' "$notification"
    )"
    title="$(
      jq -r '.title' "$notification"
    )"
    count="$(
      jq -r '.count' "$notification"
    )"

    marker="$STATE/sent/programs-${dedupe_key}.sent"

    if [[ -f "$marker" ]]; then
      rm -f "$notification"
      echo "[-] already notified: program lifecycle $dedupe_key"
      continue
    fi

    temporary="$(mktemp)"
    jq -r '.lines[]' "$notification" > "$temporary"

    summary="${title}"$'\n\n'
    summary+="Count: ${count}"$'\n'
    summary+="Catalog: public-bugbounty-programs"$'\n'
    summary+="Status: PENDING VERIFICATION"

    if ! send_inline_list \
      "$TG_PROGRAMS" \
      "$summary" \
      "$temporary"
    then
      rm -f "$temporary"
      return 1
    fi

    rm -f "$temporary"
    touch "$marker"
    rm -f "$notification"

    echo "[+] notified: program lifecycle $title ($count)"
  done
}


process_program_catalog() {
  local catalog_url
  catalog_url="https://raw.githubusercontent.com/projectdiscovery/public-bugbounty-programs/main/dist/data.json"

  local script_dir
  script_dir="$(
    cd "$(dirname "${BASH_SOURCE[0]}")"
    pwd
  )"

  local programs_state="$STATE/programs"
  local catalog_file="$programs_state/catalog.new.json"
  local result_file="$programs_state/last-execution.json"

  install -d -m 0700 "$programs_state"
  install -d -m 0700 "$STATE/events/pending"

  if ! curl \
    -sSf \
    --retry 3 \
    --retry-delay 2 \
    --retry-all-errors \
    --connect-timeout 15 \
    --max-time 120 \
    "$catalog_url" \
    -o "$catalog_file"
  then
    echo "[!] fetch failed: program catalog" >&2
    rm -f "$catalog_file"
    return 1
  fi

  if ! python3 \
    "$script_dir/program_lifecycle.py" \
    --catalog "$catalog_file" \
    --state-dir "$programs_state" \
    --events-dir "$STATE/events/pending" \
    --detected-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --result-output "$result_file"
  then
    rm -f "$catalog_file"
    return 1
  fi

  rm -f "$catalog_file"

  echo "[+] programs: added=$(
    jq -r '.counts.added' "$result_file"
  ) reopened=$(
    jq -r '.counts.possible_reopened' "$result_file"
  ) removed=$(
    jq -r '.counts.removed' "$result_file"
  ) changed=$(
    jq -r '.counts.changed' "$result_file"
  )"

  notify_program_lifecycle_outbox
}

process_dataset \
  "assets" \
  "${BASE}/assets.out" \
  "$TG_ASSETS" \
  "assets.out" || status=1

process_dataset \
  "wildcards" \
  "${BASE}/wildcards.out" \
  "$TG_PROGRAMS" \
  "wildcards.out" || status=1
process_program_catalog || status=1


exit "$status"
