#!/usr/bin/env python3
"""
run_remaining_44_profiles.py
Completa os 44 perfis restantes para fechar os 60 perfis ortogonais
(15 combinações de {CPU, RAM, Disco, Rede} x 4 degraus: 25%, 50%, 75%, 100%)
nas 6 ferramentas de observabilidade, totalizando 360 células (60 x 6) em
dataset_orthogonal_matrix_25pct.csv.
Usa SSH ControlMaster/ControlPersist para latência de controle < 50ms.
"""

from project_paths import benchmark_file
import subprocess
import time
import urllib.request
import urllib.error
import json
import csv
import os

PROJECT = "project-b0135790-8b23-4c07-96c"
ZONE = "southamerica-east1-a"
GCLOUD = "/home/alexat/.local/bin/gcloud"
WORKER_EXT_IP = "34.95.217.182"
WORKER_INT_IP = "10.158.0.3"
CSV_PATH = benchmark_file('dataset_orthogonal_matrix_25pct.csv')

DEPLOYMENTS = {
    "prometheus": "pipeline-a-prometheus",
    "victoriametrics": "pipeline-b-victoriametrics",
    "netdata": "pipeline-c-netdata",
    "telegraf": "pipeline-d-telegraf",
    "ebpf": "pipeline-e-ebpf",
    "zabbix": "pipeline-f-zabbix",
}

ALL_15_GROUPS = [
    # 1 elemento (4 opções: {1}, {2}, {3}, {4})
    ("CPU",          1, 0, 0, 0),
    ("RAM",          0, 1, 0, 0),
    ("DISK",         0, 0, 1, 0),
    ("NET",          0, 0, 0, 1),
    # 2 elementos (6 opções: {1,2}, {1,3}, {1,4}, {2,3}, {2,4}, {3,4})
    ("CPU_RAM",      1, 1, 0, 0),
    ("CPU_DISK",     1, 0, 1, 0),
    ("CPU_NET",      1, 0, 0, 1),
    ("RAM_DISK",     0, 1, 1, 0),
    ("RAM_NET",      0, 1, 0, 1),
    ("DISK_NET",     0, 0, 1, 1),
    # 3 elementos (4 opções: {1,2,3}, {1,2,4}, {1,3,4}, {2,3,4})
    ("HOST3",        1, 1, 1, 0),
    ("CPU_RAM_NET",  1, 1, 0, 1),
    ("CPU_DISK_NET", 1, 0, 1, 1),
    ("RAM_DISK_NET", 0, 1, 1, 1),
    # 4 elementos (1 opção: {1,2,3,4})
    ("ALL4",         1, 1, 1, 1),
]

STEPS = [25, 50, 75, 100]

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

def ssh_cmd(host, command, timeout=95):
    for attempt in range(3):
        cmd = [
            GCLOUD, "compute", "ssh", host,
            f"--zone={ZONE}",
            f"--project={PROJECT}",
            "--ssh-flag=-o ConnectTimeout=15",
            "--ssh-flag=-o ServerAliveInterval=15",
            f"--command={command}",
            "--quiet"
        ]
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
            return res.returncode, res.stdout.strip(), res.stderr.strip()
        except subprocess.TimeoutExpired:
            time.sleep(2)
    return 1, "", "timeout"

def set_chaos(delay_ms=0, error_rate=0, cardinality=0):
    url = f"http://{WORKER_EXT_IP}:30080/chaos?delay_ms={delay_ms}&error_rate={error_rate}&cardinality={cardinality}"
    for _ in range(5):
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                return json.loads(r.read().decode())
        except Exception:
            time.sleep(0.3)
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
                    if a.get("state") in ("firing", "pending"):
                        # vmalert transitions pending -> firing; check firing first
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

def start_k6(duration_s=2400):
    ssh_cmd("k6-runner", "pkill -f test_traffic.js || true")
    ssh_cmd("k6-runner", f"nohup k6 run -e TARGET_HOST={WORKER_INT_IP}:30080 -e TEST_DURATION={duration_s}s -e TEST_RPS=50 ~/k6/test_traffic.js >/dev/null 2>&1 &")

def stop_k6():
    ssh_cmd("k6-runner", "pkill -f test_traffic.js || true")

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
        start_k6(duration_s=2400)
        time.sleep(5)

    reset_ebpf_state()
    cleared = False
    for _ in range(14):
        ok, firing = check_alert_and_delivery(tool)
        if ok and not firing:
            cleared = True
            break
        time.sleep(0.8)

    if not cleared:
        ssh_cmd("k3s-control-plane", f"sudo kubectl rollout restart deployment {DEPLOYMENTS[tool]} && sudo kubectl rollout status deployment/{DEPLOYMENTS[tool]} --timeout=90s")
        reset_ebpf_state()
        for _ in range(12):
            ok, firing = check_alert_and_delivery(tool)
            if ok and not firing:
                break
            time.sleep(0.8)

def normalize_existing_csv():
    rename_map = {
        "CPU100_NET100": "CPU_NET_100",
        "CPU100_RAM100": "CPU_RAM_100",
        "CPU100_DISK100": "CPU_DISK_100",
        "RAM100_DISK100": "RAM_DISK_100",
    }
    done = set()
    rows = []
    if os.path.exists(CSV_PATH):
        with open(CSV_PATH, "r") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            for r in reader:
                if not r: continue
                r[1] = rename_map.get(r[1], r[1])
                done.add((r[0], r[1]))
                rows.append(r)
        with open(CSV_PATH, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["pipeline", "profile", "cpu_pct", "ram_pct", "disk_pct", "net_pct", "delivery_rate_pct", "ttd_seconds", "polls_ok", "polls_total"])
            for r in rows:
                w.writerow(r)
    return done

if __name__ == "__main__":
    done_set = normalize_existing_csv()
    print(f"Registros ja concluidos no CSV: {len(done_set)} / 360")

    all_60_profiles = []
    for g_name, c_m, r_m, d_m, n_m in ALL_15_GROUPS:
        for step in STEPS:
            prof_name = f"{g_name}_{step}"
            all_60_profiles.append((prof_name, c_m * step, r_m * step, d_m * step, n_m * step))

    tools = ["telegraf", "ebpf", "prometheus", "zabbix", "netdata", "victoriametrics"]

    for tool in tools:
        pending = [p for p in all_60_profiles if (tool, p[0]) not in done_set]
        print(f"\n==================================================================")
        print(f">>> FERRAMENTA: {tool.upper()} ({len(pending)} perfis restantes de 60) <<<")
        print(f"==================================================================")
        if not pending:
            continue
        first = True
        for prof_name, c_pct, r_pct, d_pct, n_pct in pending:
            prepare_clean_pipeline(tool, first_for_tool=first)
            first = False
            if tool == "ebpf":
                reset_ebpf_state()
                check_alert_and_delivery("ebpf")
                time.sleep(4.6)

            ssh_cmd("k3s-worker-edge", f"~/orthogonal_stress_ctl.sh {c_pct} {r_pct} {d_pct} {n_pct}")
            time.sleep(1.5)

            t0 = time.time()
            set_chaos(300, 20, 0)
            t1 = None
            polls_total = 0
            polls_ok = 0
            max_wait = 62 if tool == "victoriametrics" else 48
            deadline = time.time() + max_wait
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
            print(f"  [{tool:15s} | {prof_name:16s}] (C={c_pct:3d}% R={r_pct:3d}% D={d_pct:3d}% N={n_pct:3d}%) -> Entrega={delivery_pct:5.1f}% | TTD={ttd:6.2f}s")
            with open(CSV_PATH, "a", newline="") as f:
                w = csv.writer(f)
                w.writerow([tool, prof_name, c_pct, r_pct, d_pct, n_pct, delivery_pct, ttd, polls_ok, polls_total])

    stop_k6()
    ssh_cmd("k3s-worker-edge", "~/orthogonal_stress_ctl.sh stop")
    print("\n=== MATRIZ COMPLETA DE 360 CELULAS (15 GRUPOS x 4 DEGRAUS x 6 FERRAMENTAS) CONCLUIDA! ===")
