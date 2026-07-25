#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MONITOR="$ROOT/monitor.sh"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

helper_code="$(
  awk '
    /^send_inline_list\(\) \{/ {
      capture=1
      depth=0
    }

    capture {
      print
      opens=gsub(/\{/, "{")
      closes=gsub(/\}/, "}")
      depth += opens - closes

      if (depth == 0) {
        exit
      }
    }
  ' "$MONITOR"
)"

[[ -n "$helper_code" ]]
eval "$helper_code"

message_count=0
document_calls=0

send_message() {
  local chat="$1"
  local text="$2"

  [[ "$chat" == "fixture-chat" ]]

  message_count=$((message_count + 1))
  printf '%s\n' "$text" > "$tmp/message-${message_count}.txt"
}

send_document() {
  document_calls=$((document_calls + 1))
  return 99
}

list="$tmp/items.txt"

for index in $(seq -w 1 180); do
  printf 'asset-%s.example.test/path/to/resource-%s\n' \
    "$index" "$index" >> "$list"
done

summary=$'SCOPE DELTA — ADDED\n\nDataset: assets.out\nCount: 180\nScope status: PENDING VERIFICATION\nRecon: BLOCKED'

send_inline_list \
  "fixture-chat" \
  "$summary" \
  "$list"

[[ "$message_count" -gt 1 ]]
[[ "$document_calls" -eq 0 ]]

for message in "$tmp"/message-*.txt; do
  length="$(wc -m < "$message")"
  [[ "$length" -le 3801 ]]
done

while IFS= read -r entry; do
  matches="$(
    grep -hFx -- "$entry" "$tmp"/message-*.txt \
      | wc -l
  )"

  [[ "$matches" -eq 1 ]]
done < "$list"

grep -Fq 'Part: 2' "$tmp"/message-*.txt

echo "inline_messages_created=$message_count"
echo "telegram_message_limit=PASS"
echo "all_scope_entries_present=PASS"
echo "duplicate_scope_entries=ZERO"
echo "document_attachments=ZERO"
echo "INLINE-NOTIFICATION-TESTS-OK"
