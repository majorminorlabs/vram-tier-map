#!/usr/bin/env bash
set -euo pipefail
out=${1:?output CSV}
mkdir -p "$(dirname "$out")"
printf 'timestamp_utc,graphics_mhz,memory_mhz,temp_c,power_w,throttle_reasons,memory_used_mib,memory_total_mib,host_ram_used_gib\n' > "$out"
while :; do
  ts=$(date -u +%FT%TZ)
  values=$(nvidia-smi --query-gpu=clocks.current.graphics,clocks.current.memory,temperature.gpu,power.draw,clocks_throttle_reasons.active,memory.used,memory.total --format=csv,noheader,nounits | head -1)
  ram=$(awk '/MemTotal:/ {t=$2} /MemAvailable:/ {a=$2} END {printf "%.3f",(t-a)/1048576}' /proc/meminfo)
  printf '%s,%s,%s\n' "$ts" "$values" "$ram" >> "$out"
  sleep 5
done
