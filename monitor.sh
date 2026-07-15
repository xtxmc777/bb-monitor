#!/usr/bin/env bash
set -uo pipefail

BASE="https://raw.githubusercontent.com/osamahamad/payout-targets-data/main"
STATE="state"
mkdir -p "$STATE"

# Filtr. Puste = wszystko.
# PLATFORM_FILTER="hackerone|intigriti|bugcrowd"
PLATFORM_FILTER=""

send() {
  local chat="$1" text="$2"
  curl -s -X POST \
    "https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage" \
    -d "chat_id=${chat}" \
    -d "disable_web_page_preview=true" \
    --data-urlencode "text=${text}" > /dev/null
}

send_chunked() {
  local chat="$1" header="$2" file="$3"
  local total msg count=0
  total=$(wc -l < "$file")
  msg="${header} (${total})"$'\n\n'

  while IFS= read -r line; do
    msg+="${line}"$'\n'
    count=$((count + 1))
    if [ "$count" -ge 25 ]; then
      send "$chat" "$msg"
      msg=""
      count=0
      sleep 1
    fi
  done < "$file"

  [ -n "$msg" ] && send "$chat" "$msg"
}

# Diff pelnego scope. Zrodlo prawdy = assets.out, nie cudza delta.
process() {
  local name="$1" url="$2" chat="$3" header="$4"
  local new="$STATE/${name}.new"
  local old="$STATE/${name}.txt"
  local diff="$STATE/${name}.diff"

  if ! curl -sf --max-time 120 "$url" -o "$new"; then
    echo "[!] fetch failed: $name"
    return 0
  fi

  # assets.out ma tysiace linii. Kilkanascie = upstream zepsuty.
  local lines
  lines=$(wc -l < "$new")
  if [ "$lines" -lt 100 ]; then
    echo "[!] suspicious size: $name ($lines lines) - skipping"
    rm -f "$new"
    return 0
  fi

  sort -u "$new" -o "$new"

  if [ ! -f "$old" ]; then
    mv "$new" "$old"
    echo "[+] baseline created: $name ($(wc -l < "$old") lines)"
    return 0
  fi

  comm -13 "$old" "$new" > "$diff"

  if [ -n "$PLATFORM_FILTER" ]; then
    grep -Ei "$PLATFORM_FILTER" "$diff" > "${diff}.f" || true
    mv "${diff}.f" "$diff"
  fi

  if [ -s "$diff" ]; then
    echo "[+] $name: $(wc -l < "$diff") new"
    send_chunked "$chat" "$header" "$diff"
  else
    echo "[-] $name: no changes"
  fi

  mv "$new" "$old"
  rm -f "$diff"
}

process "assets"    "${BASE}/assets.out"    "$TG_ASSETS"   "NOWE ASSETY / SCOPE UPDATE"
process "wildcards" "${BASE}/wildcards.out" "$TG_PROGRAMS" "NOWE WILDCARDY"
