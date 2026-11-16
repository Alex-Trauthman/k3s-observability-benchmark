#!/usr/bin/env python3
from project_paths import benchmark_file
import subprocess
import time
import json

PROJECT = "project-b0135790-8b23-4c07-96c"
ZONE = "southamerica-east1-a"
GCLOUD = "/home/alexat/.local/bin/gcloud"
WORKER_INT_IP = "10.158.0.3"

DEPLOYMENTS = {
    "prometheus": "pipeline-a-prometheus",
    "victoriametrics": "pipeline-b-victoriametrics",
    "netdata": "pipeline-c-netdata",
    "telegraf": "pipeline-d-telegraf",
    "ebpf": "pipeline-e-ebpf",
    "zabbix_full": "pipeline-f-zabbix",
}

def ssh_cmd(host, command, timeout=90):
    cmd = [GCLOUD, "compute", "ssh", host, f"--zone={ZONE}", f"--project={PROJECT}", f"--command={command}", "--quiet"]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    return res.returncode, res.stdout.strip(), res.stderr.strip()

def scale_only(active_key=None):
    cmds = []
    for k, dep in DEPLOYMENTS.items():
        reps = 1 if k == active_key else 0
        cmds.append(f"sudo kubectl scale deployment {dep} --replicas={reps}")
    ssh_cmd("k3s-control-plane", " && ".join(cmds))
    if active_key:
        dep = DEPLOYMENTS[active_key]
        ssh_cmd("k3s-control-plane", f"sudo kubectl rollout status deployment/{dep} --timeout=90s")
        time.sleep(8)
    else:
        time.sleep(5)

REMOTE_COLLECTOR = r'''cat << 'EOS' > ~/measure_disk_io.sh
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
EOS
chmod +x ~/measure_disk_io.sh
rm -f ~/disk_and_zabbix_telemetry.csv
'''

if __name__ == "__main__":
    print("Instalando coletor de Disk I/O no k3s-worker-edge...")
    ssh_cmd("k3s-worker-edge", REMOTE_COLLECTOR)
    ssh_cmd("k6-runner", "pkill -f test_traffic.js || true")
    ssh_cmd("k6-runner", f"nohup k6 run -e TARGET_HOST={WORKER_INT_IP}:30080 -e TEST_DURATION=300s -e TEST_RPS=50 ~/k6/test_traffic.js >/dev/null 2>&1 &")

    for target in ["baseline", "prometheus", "victoriametrics", "netdata", "telegraf", "ebpf", "zabbix_full"]:
        print(f"Coletando Disk I/O (15s @ 50 RPS) para: {target}...")
        scale_only(None if target == "baseline" else target)
        ssh_cmd("k3s-worker-edge", f"~/measure_disk_io.sh {target} 15 ~/disk_and_zabbix_telemetry.csv")

    print("Coletando Zabbix Agent 2 Puro (sem Zabbix Proxy SQLite)...")
    # Mede apenas zabbix_agent2 + avaliador leve (excluindo zabbix_proxy)
    ssh_cmd("k3s-worker-edge", "~/measure_disk_io.sh zabbix_agent2 15 ~/disk_and_zabbix_telemetry.csv")

    ssh_cmd("k6-runner", "pkill -f test_traffic.js || true")
    scale_only(None)
    subprocess.run([GCLOUD, "compute", "scp", "k3s-worker-edge:~/disk_and_zabbix_telemetry.csv", benchmark_file('disk_and_zabbix_telemetry.csv'), f"--zone={ZONE}", f"--project={PROJECT}", "--quiet"])
    print("Concluido!")
