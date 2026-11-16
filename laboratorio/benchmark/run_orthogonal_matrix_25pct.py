#!/usr/bin/env python3
"""
run_orthogonal_matrix_25pct.py
Executa a Matriz Ortogonal de Estresse Combinado em Níveis de 25% (25%, 50%, 75%, 100%)
e Combinações Duplas, Triplas e Quádrupla Total para as 6 ferramentas de observabilidade:
  - CPU isolada via Duty-Cycle de registrador (0 MB RAM, 0 KB/s Disco)
  - RAM isolada via alocação estática travada + sleep() (0.0% CPU, 0 KB/s Disco)
  - Disco isolado via O_DIRECT + O_DSYNC (0 MB Page Cache RAM, <1% CPU)
  - Rede isolada via tc qdisc netem na bridge cni0 (0.0% CPU, 0 MB RAM)
"""

from project_paths import benchmark_file
import subprocess
import time
import urllib.request
import urllib.error
import json
import csv

PROJECT = "project-b0135790-8b23-4c07-96c"
ZONE = "southamerica-east1-a"
GCLOUD = "/home/alexat/.local/bin/gcloud"
WORKER_EXT_IP = "34.95.217.182"
WORKER_INT_IP = "10.158.0.3"

DEPLOYMENTS = {
    "prometheus": "pipeline-a-prometheus",
    "victoriametrics": "pipeline-b-victoriametrics",
    "netdata": "pipeline-c-netdata",
    "telegraf": "pipeline-d-telegraf",
    "ebpf": "pipeline-e-ebpf",
    "zabbix": "pipeline-f-zabbix",
}

ORTHOGONAL_CTL_SCRIPT = r'''cat << 'EOS' > ~/orthogonal_stress_ctl.sh
#!/usr/bin/env bash
# Uso: ~/orthogonal_stress_ctl.sh <CPU_PCT> <RAM_PCT> <DISK_PCT> <NET_PCT>
CPU_PCT="${1:-0}"
RAM_PCT="${2:-0}"
DISK_PCT="${3:-0}"
NET_PCT="${4:-0}"

# 1. Limpa estressores anteriores com precisao via PID file e tc
if [ -f /tmp/ortho_stress.pids ]; then
    kill -9 $(cat /tmp/ortho_stress.pids) 2>/dev/null || true
    rm -f /tmp/ortho_stress.pids
fi
rm -f /tmp/direct_io.bin
sudo /usr/sbin/tc qdisc del dev cni0 root 2>/dev/null || true

if [ "$CPU_PCT" = "stop" ]; then
    exit 0
fi

# 2. CPU ORTOGONAL (2 workers com duty-cycle preciso em janela de 100ms, 0 MB RAM, 0 I/O)
if [ "$CPU_PCT" -gt 0 ]; then
    for w in 1 2; do
        python3 -c "import time
pct = $CPU_PCT / 100.0
busy = 0.08 * pct
idle = 0.08 * (1.0 - pct)
end_t = time.time() + 70
while time.time() < end_t:
    t0 = time.monotonic()
    while (time.monotonic() - t0) < busy:
        pass
    if idle > 0.001:
        time.sleep(idle)
" >/dev/null 2>&1 &
        echo $! >> /tmp/ortho_stress.pids
    done
fi

# 3. RAM ORTOGONAL (Alocacao estatica travada em RAM + sleep profundo = 0.0% CPU, 0 KB/s Disco)
if [ "$RAM_PCT" -gt 0 ]; then
    # 100% da folga segura de teste = 272 MB (25%=68MB, 50%=136MB, 75%=204MB, 100%=272MB)
    MB=$(( (272 * RAM_PCT) / 100 ))
    python3 -c "import time, ctypes
size = $MB * 1024 * 1024
buf = bytearray(size)
# Toca 1 byte por pagina (4KB) uma unica vez na partida para materializar RSS fisico
for i in range(0, size, 4096):
    buf[i] = 1
try:
    libc = ctypes.CDLL('libc.so.6')
    libc.mlock(ctypes.c_char.from_buffer(buf), ctypes.c_size_t(size))
except Exception:
    pass
# Dorme passivamente com 0.0% de CPU e 0 I/O
time.sleep(70)
" >/dev/null 2>&1 &
    echo $! >> /tmp/ortho_stress.pids
fi

# 4. DISCO ORTOGONAL (dd com oflag=direct,dsync -> Bypass total do Page Cache da RAM e <1% CPU)
if [ "$DISK_PCT" -gt 0 ]; then
    case "$DISK_PCT" in
        25) SLEEP_IO="0.18" ;;
        50) SLEEP_IO="0.08" ;;
        75) SLEEP_IO="0.03" ;;
        *)  SLEEP_IO="0.00" ;;
    esac
    sh -c "
    END=\$(( \$(date +%s) + 70 ))
    while [ \$(date +%s) -lt \$END ]; do
        dd if=/dev/zero of=/tmp/direct_io.bin bs=1M count=4 oflag=direct,dsync 2>/dev/null
        if [ '$SLEEP_IO' != '0.00' ]; then sleep $SLEEP_IO; fi
    done
    " >/dev/null 2>&1 &
    echo $! >> /tmp/ortho_stress.pids
fi

# 5. REDE ORTOGONAL (tc qdisc netem na interface cni0 -> 0% CPU extra, 0 MB RAM)
if [ "$NET_PCT" -gt 0 ]; then
    case "$NET_PCT" in
        25) sudo /usr/sbin/tc qdisc replace dev cni0 root netem delay 25ms 10ms loss 5% rate 50mbit ;;
        50) sudo /usr/sbin/tc qdisc replace dev cni0 root netem delay 50ms 20ms loss 10% rate 10mbit ;;
        75) sudo /usr/sbin/tc qdisc replace dev cni0 root netem delay 100ms 35ms loss 18% rate 2mbit ;;
        *)  sudo /usr/sbin/tc qdisc replace dev cni0 root netem delay 150ms 50ms loss 25% rate 512kbit ;;
    esac
fi
EOS
chmod +x ~/orthogonal_stress_ctl.sh
'''

ebpf_state = {
    "initialized": False,
    "last_eval_ts": 0.0,
    "prev_b025": 0.0,
    "prev_c200": 0.0,
    "prev_c500": 0.0,
    "violation_start": None,
    "firing": False,
}

def reset_ebpf_state():
    ebpf_state["initialized"] = False
    ebpf_state["last_eval_ts"] = 0.0
    ebpf_state["prev_b025"] = 0.0
    ebpf_state["prev_c200"] = 0.0
    ebpf_state["prev_c500"] = 0.0
    ebpf_state["violation_start"] = None
    ebpf_state["firing"] = False

def ssh_cmd(host, command, timeout=90):
    cmd = [GCLOUD, "compute", "ssh", host, f"--zone={ZONE}", f"--project={PROJECT}", f"--command={command}", "--quiet"]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    return res.returncode, res.stdout.strip(), res.stderr.strip()

def set_chaos(delay_ms=0, error_rate=0, cardinality=0):
    url = f"http://{WORKER_EXT_IP}:30080/chaos?delay_ms={delay_ms}&error_rate={error_rate}&cardinality={cardinality}"
    for _ in range(5):
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                return json.loads(r.read().decode())
        except Exception:
            time.sleep(0.4)
    return {}

def parse_beyla_metrics(text):
    b025, c200, c500 = 0.0, 0.0, 0.0
    for line in text.splitlines():
        if line.startswith("#") or 'http_route="/work"' not in line or 'service_name="api-target"' not in line:
            continue
        if line.startswith("http_server_request_duration_seconds_bucket{") and 'http_response_status_code="200"' in line and 'le="0.25"' in line:
            try: b025 += float(line.split()[-1])
            except ValueError: pass
        elif line.startswith("http_server_request_duration_seconds_count{"):
            try:
                val = float(line.split()[-1])
                if 'http_response_status_code="200"' in line: c200 += val
                elif 'http_response_status_code="500"' in line: c500 += val
            except ValueError: pass
    return b025, c200, c500

def check_alert_and_delivery(pipeline):
    try:
        if pipeline == "prometheus":
            url = f"http://{WORKER_EXT_IP}:30090/api/v1/alerts"
            with urllib.request.urlopen(url, timeout=1.5) as r:
                data = json.loads(r.read().decode())
                for a in data.get("data", {}).get("alerts", []):
                    if a.get("state") == "firing":
                        return True, True
                return True, False
        elif pipeline == "victoriametrics":
            url = f"http://{WORKER_EXT_IP}:30094/api/v1/alerts"
            with urllib.request.urlopen(url, timeout=1.5) as r:
                data = json.loads(r.read().decode())
                for a in data.get("data", {}).get("alerts", []):
                    if a.get("state") == "firing":
                        return True, True
                return True, False
        elif pipeline == "netdata":
            url = f"http://{WORKER_EXT_IP}:30095/api/v1/alarms?all=true"
            with urllib.request.urlopen(url, timeout=1.5) as r:
                data = json.loads(r.read().decode())
                for k, v in data.get("alarms", {}).items():
                    if ("HighLatencyP95" in k or "HighErrorRate" in k) and v.get("status") == "CRITICAL":
                        return True, True
                return True, False
        elif pipeline == "telegraf":
            url = f"http://{WORKER_EXT_IP}:30096/"
            with urllib.request.urlopen(url, timeout=1.5) as r:
                return True, (r.status == 503)
        elif pipeline == "ebpf":
            now = time.time()
            url = f"http://{WORKER_EXT_IP}:30097/metrics"
            with urllib.request.urlopen(url, timeout=1.5) as resp:
                text = resp.read().decode()
            if now - ebpf_state["last_eval_ts"] < 4.5:
                return True, ebpf_state["firing"]
            ebpf_state["last_eval_ts"] = now
            b025, c200, c500 = parse_beyla_metrics(text)
            if not ebpf_state["initialized"]:
                ebpf_state["prev_b025"], ebpf_state["prev_c200"], ebpf_state["prev_c500"] = b025, c200, c500
                ebpf_state["initialized"] = True
                return True, False
            d_b025 = b025 - ebpf_state["prev_b025"]
            d_c200 = c200 - ebpf_state["prev_c200"]
            d_c500 = c500 - ebpf_state["prev_c500"]
            d_all = d_c200 + d_c500
            ebpf_state["prev_b025"], ebpf_state["prev_c200"], ebpf_state["prev_c500"] = b025, c200, c500
            high_lat = (d_c200 >= 5.0 and (d_b025 / d_c200) < 0.95)
            high_err = (d_all >= 5.0 and (d_c500 / d_all) > 0.10)
            if high_lat or high_err:
                if ebpf_state["violation_start"] is None:
                    ebpf_state["violation_start"] = now
                elif now - ebpf_state["violation_start"] >= 14.0:
                    ebpf_state["firing"] = True
            else:
                ebpf_state["violation_start"] = None
                ebpf_state["firing"] = False
            return True, ebpf_state["firing"]
        elif pipeline == "zabbix":
            url = f"http://{WORKER_EXT_IP}:30098/alerts"
            with urllib.request.urlopen(url, timeout=1.5) as r:
                data = json.loads(r.read().decode())
                return True, (data.get("status") == "firing")
    except urllib.error.HTTPError as e:
        if pipeline == "telegraf" and e.code == 503:
            return True, True
    except Exception:
        return False, False
    return False, False

def prepare_clean_pipeline(tool, first_for_tool=False):
    ssh_cmd("k3s-worker-edge", "~/orthogonal_stress_ctl.sh stop")
    set_chaos(0, 0, 0)
    if first_for_tool:
        cmds = []
        for p, dep in DEPLOYMENTS.items():
            reps = 1 if p == tool else 0
            cmds.append(f"sudo kubectl scale deployment {dep} --replicas={reps}")
        ssh_cmd("k3s-control-plane", " && ".join(cmds))
        ssh_cmd("k3s-control-plane", f"sudo kubectl rollout status deployment/{DEPLOYMENTS[tool]} --timeout=90s")
        start_k6(duration_s=1200)
        time.sleep(6)

    reset_ebpf_state()
    cleared = False
    for _ in range(16):
        ok, firing = check_alert_and_delivery(tool)
        if ok and not firing:
            cleared = True
            break
        time.sleep(1.0)

    if not cleared:
        ssh_cmd("k3s-control-plane", f"sudo kubectl rollout restart deployment {DEPLOYMENTS[tool]} && sudo kubectl rollout status deployment/{DEPLOYMENTS[tool]} --timeout=90s")
        reset_ebpf_state()
        for _ in range(15):
            ok, firing = check_alert_and_delivery(tool)
            if ok and not firing:
                break
            time.sleep(1.0)

def start_k6(duration_s=75):
    ssh_cmd("k6-runner", "pkill -f test_traffic.js || true")
    ssh_cmd("k6-runner", f"nohup k6 run -e TARGET_HOST={WORKER_INT_IP}:30080 -e TEST_DURATION={duration_s}s -e TEST_RPS=50 ~/k6/test_traffic.js >/dev/null 2>&1 &")

def stop_k6():
    ssh_cmd("k6-runner", "pkill -f test_traffic.js || true")

# Matriz Fatorial Fracionada + Rampa de 25% nas 4 dimensoes (CPU, RAM, DISCO, REDE)
PROFILES = [
    # 1. Rampa de Rede Isolada em degraus de 25%
    ("NET_25",           0,   0,   0,  25),
    ("NET_50",           0,   0,   0,  50),
    ("NET_75",           0,   0,   0,  75),
    ("NET_100",          0,   0,   0, 100),
    # 2. Rampa Combinada das 3 do Host (CPU+RAM+Disco) em degraus de 25%
    ("HOST3_25",        25,  25,  25,   0),
    ("HOST3_50",        50,  50,  50,   0),
    ("HOST3_75",        75,  75,  75,   0),
    ("HOST3_100",      100, 100, 100,   0),
    # 3. Rampa Combinada Quádrupla Total (CPU+RAM+Disco+Rede) em degraus de 25%
    ("ALL4_25",         25,  25,  25,  25),
    ("ALL4_50",         50,  50,  50,  50),
    ("ALL4_75",         75,  75,  75,  75),
    ("ALL4_100",       100, 100, 100, 100),
    # 4. Combinações Duplas Cruzadas Críticas em 100%
    ("CPU100_NET100",  100,   0,   0, 100),
    ("CPU100_RAM100",  100, 100,   0,   0),
    ("CPU100_DISK100", 100,   0, 100,   0),
    ("RAM100_DISK100",   0, 100, 100,   0),
]

if __name__ == "__main__":
    print("Instalando Controlador Ortogonal de Subsistemas (~/orthogonal_stress_ctl.sh) no k3s-worker-edge...")
    ssh_cmd("k3s-worker-edge", ORTHOGONAL_CTL_SCRIPT)

    tools = ["telegraf", "ebpf", "prometheus", "netdata", "victoriametrics", "zabbix"]
    out_csv = benchmark_file('dataset_orthogonal_matrix_25pct.csv')
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["pipeline", "profile", "cpu_pct", "ram_pct", "disk_pct", "net_pct", "delivery_rate_pct", "ttd_seconds", "polls_ok", "polls_total"])

    for tool in tools:
        print(f"\n==================================================================")
        print(f">>> FERRAMENTA: {tool.upper()} (16 Perfis Ortogonais) <<<")
        print(f"==================================================================")
        first = True
        for prof_name, c_pct, r_pct, d_pct, n_pct in PROFILES:
            prepare_clean_pipeline(tool, first_for_tool=first)
            first = False
            if tool == "ebpf":
                reset_ebpf_state()
                check_alert_and_delivery("ebpf")
                time.sleep(5)

            # Aplica carga ortogonal exata nos 4 subsistemas simultaneamente
            ssh_cmd("k3s-worker-edge", f"~/orthogonal_stress_ctl.sh {c_pct} {r_pct} {d_pct} {n_pct}")
            time.sleep(2)

            t0 = time.time()
            set_chaos(300, 20, 0)
            t1 = None
            polls_total = 0
            polls_ok = 0
            # Janela maxima de 58s para detecção
            deadline = time.time() + 58
            while time.time() < deadline:
                polls_total += 1
                ok, firing = check_alert_and_delivery(tool)
                if ok:
                    polls_ok += 1
                if firing and t1 is None:
                    t1 = time.time()
                    break
                time.sleep(0.7)

            ssh_cmd("k3s-worker-edge", "~/orthogonal_stress_ctl.sh stop")
            set_chaos(0, 0, 0)

            delivery_pct = round((polls_ok / polls_total) * 100.0, 1) if polls_total > 0 else 0.0
            ttd = round(t1 - t0, 2) if t1 else -1.0
            print(f"  [{tool:15s} | {prof_name:14s}] (CPU={c_pct:3d}% RAM={r_pct:3d}% DSK={d_pct:3d}% NET={n_pct:3d}%) -> Entrega={delivery_pct:5.1f}% | TTD={ttd:6.2f}s")
            with open(out_csv, "a", newline="") as f:
                w = csv.writer(f)
                w.writerow([tool, prof_name, c_pct, r_pct, d_pct, n_pct, delivery_pct, ttd, polls_ok, polls_total])

    ssh_cmd("k3s-worker-edge", "~/orthogonal_stress_ctl.sh stop")
    print("\n=== MATRIZ ORTOGONAL DE 25% CONCLUIDA COM SUCESSO! ===")
