#!/usr/bin/env python3
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
CSV_PATH = benchmark_file('dataset_orthogonal_matrix_25pct.csv')

DEPLOYMENTS = {
    "prometheus": "pipeline-a-prometheus",
    "victoriametrics": "pipeline-b-victoriametrics",
    "netdata": "pipeline-c-netdata",
    "telegraf": "pipeline-d-telegraf",
    "ebpf": "pipeline-e-ebpf",
    "zabbix": "pipeline-f-zabbix",
}

TARGETS = {
    "telegraf": [
        "RAM_25",
        "CPU_DISK_25",
    ],
    "prometheus": [
        "CPU_RAM_NET_75",
        "CPU_DISK_NET_50",
        "CPU_DISK_NET_100",
        "RAM_DISK_NET_25",
        "RAM_DISK_NET_75",
    ],
}

def ssh_cmd(host, command, timeout=90):
    for _ in range(3):
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

def check_alert_and_delivery(pipeline):
    try:
        if pipeline == "prometheus":
            url = f"http://{WORKER_EXT_IP}:30090/api/v1/alerts"
            with urllib.request.urlopen(url, timeout=1.5) as r:
                data = json.loads(r.read().decode())
                for a in data.get("data", {}).get("alerts", []):
                    if a.get("state") in ("firing", "pending"):
                        if a.get("state") == "firing":
                            return True, True
                        return True, "pending"
                return True, False
        elif pipeline == "telegraf":
            url = f"http://{WORKER_EXT_IP}:30096/"
            with urllib.request.urlopen(url, timeout=1.5) as r:
                return True, (r.status == 503)
    except urllib.error.HTTPError as e:
        if pipeline == "telegraf" and e.code == 503:
            return True, True
    except Exception:
        return False, False
    return False, False

def ensure_k6_running():
    ssh_cmd("k6-runner", "killall -9 k6 2>/dev/null || true")
    ssh_cmd("k6-runner", f"nohup k6 run --address='' -e TARGET_HOST={WORKER_INT_IP}:30080 -e TEST_DURATION=1800s -e TEST_RPS=50 ~/k6/test_traffic.js >/dev/null 2>&1 </dev/null &")

def update_csv_cell(tool, prof_name, c_pct, r_pct, d_pct, n_pct, delivery_pct, ttd, polls_ok, polls_total):
    rows = []
    header = ["pipeline", "profile", "cpu_pct", "ram_pct", "disk_pct", "net_pct", "delivery_rate_pct", "ttd_seconds", "polls_ok", "polls_total"]
    with open(CSV_PATH, "r") as f:
        reader = csv.reader(f)
        next(reader, None)
        for r in reader:
            if not r:
                continue
            if r[0] == tool and r[1] == prof_name:
                rows.append([tool, prof_name, c_pct, r_pct, d_pct, n_pct, delivery_pct, ttd, polls_ok, polls_total])
            else:
                rows.append(r)
    with open(CSV_PATH, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)

def wait_until_clear(tool):
    set_chaos(0, 0, 0)
    for _ in range(25):
        ok, firing = check_alert_and_delivery(tool)
        if ok and firing is False:
            return
        time.sleep(1.0)
    ssh_cmd("k3s-control-plane", f"sudo kubectl rollout restart deployment {DEPLOYMENTS[tool]} && sudo kubectl rollout status deployment/{DEPLOYMENTS[tool]} --timeout=90s")
    for _ in range(20):
        ok, firing = check_alert_and_delivery(tool)
        if ok and firing is False:
            return
        time.sleep(1.0)

if __name__ == "__main__":
    prof_params = {}
    with open(CSV_PATH, "r") as f:
        for r in csv.DictReader(f):
            prof_params[r["profile"]] = (
                int(r["cpu_pct"]),
                int(r["ram_pct"]),
                int(r["disk_pct"]),
                int(r["net_pct"]),
            )

    ssh_cmd("k3s-worker-edge", "~/orthogonal_stress_ctl.sh stop")
    set_chaos(0, 0, 0)
    ensure_k6_running()

    for tool in ["telegraf", "prometheus"]:
        profs = TARGETS[tool]
        cmds = []
        for p, dep in DEPLOYMENTS.items():
            reps = 1 if p == tool else 0
            cmds.append(f"sudo kubectl scale deployment {dep} --replicas={reps}")
        ssh_cmd("k3s-control-plane", " && ".join(cmds))
        ssh_cmd("k3s-control-plane", f"sudo kubectl rollout status deployment/{DEPLOYMENTS[tool]} --timeout=90s")

        for prof_name in profs:
            c_pct, r_pct, d_pct, n_pct = prof_params[prof_name]
            wait_until_clear(tool)

            ssh_cmd("k3s-worker-edge", f"~/orthogonal_stress_ctl.sh {c_pct} {r_pct} {d_pct} {n_pct}")
            time.sleep(1.0)

            t0 = time.time()
            set_chaos(300, 20, 0)
            t1 = None
            polls_total = 0
            polls_ok = 0
            deadline = time.time() + 48
            while time.time() < deadline:
                polls_total += 1
                ok, firing = check_alert_and_delivery(tool)
                if ok:
                    polls_ok += 1
                if firing is True and t1 is None:
                    t1 = time.time()
                    break
                time.sleep(0.7)

            set_chaos(0, 0, 0)
            ssh_cmd("k3s-worker-edge", "~/orthogonal_stress_ctl.sh stop")

            delivery_pct = round((polls_ok / polls_total) * 100.0, 1) if polls_total > 0 else 0.0
            ttd = round(t1 - t0, 2) if t1 else -1.0
            print(f"  [{tool:12s} | {prof_name:16s}] -> Entrega={delivery_pct:5.1f}% | TTD={ttd:6.2f}s")
            update_csv_cell(tool, prof_name, c_pct, r_pct, d_pct, n_pct, delivery_pct, ttd, polls_ok, polls_total)

    ssh_cmd("k6-runner", "killall -9 k6 2>/dev/null || true")
    ssh_cmd("k3s-control-plane", "sudo kubectl scale deployment pipeline-a-prometheus pipeline-b-victoriametrics pipeline-c-netdata pipeline-d-telegraf pipeline-e-ebpf pipeline-f-zabbix --replicas=0")
    print("DONE 7 CELLS!")
