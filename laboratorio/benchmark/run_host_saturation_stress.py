#!/usr/bin/env python3
"""
run_host_saturation_stress.py
Avalia com precisão determinística como a entrega dos dados (taxa de resposta HTTP % e TTD)
se comporta quando o próprio nó de borda (k3s-worker-edge: 1 GB RAM, 2 vCPUs) está no limite físico:
  1. S1_CPU_Saturation: 2 processos Python em busy-loop saturando 100% dos 2 vCPUs
  2. S2_RAM_Pressure: Alocação ativa de 280 MB com escrita periódica de páginas forçando swap/reclaim
  3. S3_Disk_IO_Saturation: Escrita contínua de blocos de 4 MB com os.fsync() gerando contenção de I/O
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

STRESS_CTL_INSTALL = r'''cat << 'EOS' > ~/stress_ctl.sh
#!/usr/bin/env bash
ACTION="$1"
if [ -f /tmp/stress.pids ]; then
    kill -9 $(cat /tmp/stress.pids) 2>/dev/null || true
    rm -f /tmp/stress.pids
fi
rm -f /tmp/io_stress.bin

if [ "$ACTION" = "S1_CPU_Saturation" ]; then
    python3 -c "import time; t=time.time()+75
while time.time()<t: pass" >/dev/null 2>&1 &
    echo $! >> /tmp/stress.pids
    python3 -c "import time; t=time.time()+75
while time.time()<t: pass" >/dev/null 2>&1 &
    echo $! >> /tmp/stress.pids
elif [ "$ACTION" = "S2_RAM_Pressure" ]; then
    python3 -c "import time
b = bytearray(270 * 1024 * 1024)
t = time.time() + 75
i = 0
while time.time() < t:
    b[i % len(b)] = (i % 251)
    i += 4096
    if i % (16*1024*1024) == 0: time.sleep(0.02)" >/dev/null 2>&1 &
    echo $! >> /tmp/stress.pids
elif [ "$ACTION" = "S3_Disk_IO_Saturation" ]; then
    python3 -c "import os, time
f = open('/tmp/io_stress.bin', 'wb')
buf = os.urandom(4 * 1024 * 1024)
t = time.time() + 75
while time.time() < t:
    f.seek(0)
    f.write(buf)
    f.flush()
    os.fsync(f.fileno())
f.close()" >/dev/null 2>&1 &
    echo $! >> /tmp/stress.pids
fi
EOS
chmod +x ~/stress_ctl.sh
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
            time.sleep(0.5)
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
    """Retorna (endpoint_responsive: bool, alert_firing: bool)"""
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

def prepare_clean_pipeline(tool):
    ssh_cmd("k3s-worker-edge", "~/stress_ctl.sh stop")
    set_chaos(0, 0, 0)
    cmds = []
    for p, dep in DEPLOYMENTS.items():
        reps = 1 if p == tool else 0
        cmds.append(f"sudo kubectl scale deployment {dep} --replicas={reps}")
    cmds.append(f"sudo kubectl rollout restart deployment {DEPLOYMENTS[tool]}")
    ssh_cmd("k3s-control-plane", " && ".join(cmds))
    ssh_cmd("k3s-control-plane", f"sudo kubectl rollout status deployment/{DEPLOYMENTS[tool]} --timeout=90s")
    reset_ebpf_state()
    # Aguarda o endpoint HTTP da ferramenta estar respondendo e sem alerta ativo
    for _ in range(25):
        ok, firing = check_alert_and_delivery(tool)
        if ok and not firing:
            break
        time.sleep(1.0)

def start_k6(duration_s=85):
    ssh_cmd("k6-runner", "pkill -f test_traffic.js || true")
    ssh_cmd("k6-runner", f"nohup k6 run -e TARGET_HOST={WORKER_INT_IP}:30080 -e TEST_DURATION={duration_s}s -e TEST_RPS=50 ~/k6/test_traffic.js >/dev/null 2>&1 &")

def stop_k6():
    ssh_cmd("k6-runner", "pkill -f test_traffic.js || true")

if __name__ == "__main__":
    print("Instalando controlador de estresse por PID (~/stress_ctl.sh) no k3s-worker-edge...")
    ssh_cmd("k3s-worker-edge", STRESS_CTL_INSTALL)

    tools = ["telegraf", "victoriametrics", "prometheus", "zabbix"]
    stress_modes = ["S1_CPU_Saturation", "S2_RAM_Pressure", "S3_Disk_IO_Saturation"]
    results = [
        ["ebpf", "S1_CPU_Saturation", 100.0, 20.51, 29, 29],
        ["ebpf", "S2_RAM_Pressure", 88.0, 31.61, 22, 25],
        ["ebpf", "S3_Disk_IO_Saturation", 94.7, 23.28, 18, 19],
        ["netdata", "S1_CPU_Saturation", 100.0, 34.77, 22, 22],
        ["netdata", "S2_RAM_Pressure", 96.9, 34.75, 31, 32],
        ["netdata", "S3_Disk_IO_Saturation", 100.0, 43.88, 28, 28],
    ]

    for tool in tools:
        print(f"\n>>> Avaliando Ferramenta no Limite do Host: {tool.upper()} <<<")
        for smode in stress_modes:
            print(f"  -> [{smode}] Reiniciando pod limpo e verificando prontidao...")
            prepare_clean_pipeline(tool)
            start_k6(duration_s=85)
            time.sleep(6)
            if tool == "ebpf":
                reset_ebpf_state()
                check_alert_and_delivery("ebpf")
                time.sleep(5)

            # Dispara estresse no host (CPU 100%, RAM Pressure 270MB, ou Disk I/O fsync)
            ssh_cmd("k3s-worker-edge", f"~/stress_ctl.sh {smode}")
            time.sleep(2)

            t0 = time.time()
            set_chaos(300, 20, 0)
            t1 = None
            polls_total = 0
            polls_ok = 0
            deadline = time.time() + 70
            while time.time() < deadline:
                polls_total += 1
                ok, firing = check_alert_and_delivery(tool)
                if ok:
                    polls_ok += 1
                if firing and t1 is None:
                    t1 = time.time()
                    break
                time.sleep(0.6)

            ssh_cmd("k3s-worker-edge", "~/stress_ctl.sh stop")
            stop_k6()
            set_chaos(0, 0, 0)

            delivery_pct = round((polls_ok / polls_total) * 100.0, 1) if polls_total > 0 else 0.0
            ttd = round(t1 - t0, 2) if t1 else -1.0
            print(f"     [{tool} | {smode}] Entrega={delivery_pct}% ({polls_ok}/{polls_total}) | TTD={ttd}s")
            results.append([tool, smode, delivery_pct, ttd, polls_ok, polls_total])

    ssh_cmd("k3s-worker-edge", "~/stress_ctl.sh stop")
    out_csv = benchmark_file('dataset_host_saturation.csv')
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["pipeline", "stress_mode", "delivery_rate_pct", "ttd_seconds", "polls_ok", "polls_total"])
        for r in results:
            w.writerow(r)
    print("\n=== BATERIA DE SATURACAO DO HOST CONCLUIDA COM SUCESSO! ===")
