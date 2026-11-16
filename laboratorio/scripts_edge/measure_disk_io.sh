#!/usr/bin/env bash
PIPELINE="$1"
DURATION="${2:-20}"
OUT="$3"

if [ ! -f "$OUT" ]; then
    echo "timestamp,pipeline,cpu_mcpu,mem_rss_mb,disk_read_kbs,disk_write_kbs,proc_write_kbs" > "$OUT"
fi

get_disk_sectors() {
    # sda / nvme0n1 sectors read ($6) and written ($10) in /proc/diskstats (1 sector = 512 bytes = 0.5 KB)
    awk '$3 ~ /^(sda|vda|nvme0n1)$/ {r+=$6; w+=$10} END {printf "%.0f %.0f\n", r/2, w/2}' /proc/diskstats
}

get_proc_write_kb() {
    local pat="$1"
    local sum=0
    for pid in $(pgrep -f "$pat" 2>/dev/null); do
        if [ -r "/proc/$pid/io" ]; then
            w=$(sudo awk '/^write_bytes:/ {print $2}' "/proc/$pid/io" 2>/dev/null || echo 0)
            sum=$((sum + w))
        fi
    done
    echo $((sum / 1024))
}

case "$PIPELINE" in
    prometheus) PAT="prometheus|alertmanager" ;;
    victoriametrics) PAT="victoria-metrics|vmagent|vmalert" ;;
    netdata) PAT="netdata|go\.d\.plugin|apps\.plugin|cgroups\.plugin" ;;
    telegraf) PAT="telegraf" ;;
    ebpf) PAT="beyla|ebpf" ;;
    zabbix_full) PAT="zabbix_proxy|zabbix_agent2|zabbix_eval" ;;
    zabbix_agent2) PAT="zabbix_agent2|zabbix_eval" ;;
    *) PAT="api-target" ;;
esac

PREV_D=$(get_disk_sectors)
PREV_DR=$(echo "$PREV_D" | awk '{print $1}')
PREV_DW=$(echo "$PREV_D" | awk '{print $2}')
PREV_PW=$(get_proc_write_kb "$PAT")

for i in $(seq 1 "$DURATION"); do
    sleep 1
    NOW=$(date +%s)
    CURR_D=$(get_disk_sectors)
    CURR_DR=$(echo "$CURR_D" | awk '{print $1}')
    CURR_DW=$(echo "$CURR_D" | awk '{print $2}')
    CURR_PW=$(get_proc_write_kb "$PAT")

    DIFF_DR=$((CURR_DR - PREV_DR))
    DIFF_DW=$((CURR_DW - PREV_DW))
    DIFF_PW=$((CURR_PW - PREV_PW))
    [ "$DIFF_PW" -lt 0 ] && DIFF_PW=0

    PREV_DR=$CURR_DR
    PREV_DW=$CURR_DW
    PREV_PW=$CURR_PW

    if [ "$PIPELINE" = "zabbix_agent2" ]; then
        MEM_KB=$(ps aux | grep -E "zabbix_agent2|zabbix_eval" | grep -v grep | grep -v measure_disk_io | awk '{sum+=$6} END {print sum+0}')
        CPU_PCT=$(ps aux | grep -E "zabbix_agent2|zabbix_eval" | grep -v grep | grep -v measure_disk_io | awk '{sum+=$3} END {print sum+0}')
    else
        MEM_KB=$(ps aux | grep -E "$PAT" | grep -v grep | grep -v measure_disk_io | awk '{sum+=$6} END {print sum+0}')
        CPU_PCT=$(ps aux | grep -E "$PAT" | grep -v grep | grep -v measure_disk_io | awk '{sum+=$3} END {print sum+0}')
    fi
    MEM_MB=$(awk "BEGIN {printf \"%.2f\", $MEM_KB / 1024}")
    MCPU=$(awk "BEGIN {printf \"%.2f\", $CPU_PCT * 10}")
    echo "$NOW,$PIPELINE,$MCPU,$MEM_MB,$DIFF_DR,$DIFF_DW,$DIFF_PW" >> "$OUT"
done
