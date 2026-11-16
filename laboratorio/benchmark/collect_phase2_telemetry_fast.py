#!/usr/bin/env python3
from project_paths import benchmark_file
import subprocess
import time
import urllib.request
import json

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

def ssh_cmd(host, command, timeout=90):
    cmd = [
        GCLOUD, "compute", "ssh", host,
        f"--zone={ZONE}",
        f"--project={PROJECT}",
        f"--command={command}",
        "--quiet"
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    return res.returncode, res.stdout.strip(), res.stderr.strip()

def set_chaos(delay_ms=0, error_rate=0, cardinality=0):
    url = f"http://{WORKER_EXT_IP}:30080/chaos?delay_ms={delay_ms}&error_rate={error_rate}&cardinality={cardinality}"
    for _ in range(5):
        try:
            with urllib.request.urlopen(url, timeout=4) as r:
                return json.loads(r.read().decode())
        except Exception:
            time.sleep(1)
    return {}

def scale_only(active_pipeline=None):
    cmds = []
    for p, dep in DEPLOYMENTS.items():
        reps = 1 if p == active_pipeline else 0
        cmds.append(f"sudo kubectl scale deployment {dep} --replicas={reps}")
    ssh_cmd("k3s-control-plane", " && ".join(cmds))
    if active_pipeline:
        dep = DEPLOYMENTS[active_pipeline]
        ssh_cmd("k3s-control-plane", f"sudo kubectl rollout status deployment/{dep} --timeout=90s")
        time.sleep(8)
    else:
        time.sleep(5)

def collect_window(run_id, pipeline, remote_csv, duration_s=20):
    ssh_cmd("k3s-worker-edge", f"timeout {duration_s} bash ~/collect_telemetry.sh {run_id} {pipeline} {remote_csv} || true", timeout=duration_s + 20)

def start_k6_bg(rps=50, duration_s=25):
    ssh_cmd("k6-runner", "pkill -f test_traffic.js || true")
    k6_cmd = f"k6 run -e TARGET_HOST={WORKER_INT_IP}:30080 -e TEST_DURATION={duration_s}s -e TEST_RPS={rps} ~/k6/test_traffic.js"
    ssh_cmd("k6-runner", f"nohup {k6_cmd} >/dev/null 2>&1 &")

def stop_k6():
    ssh_cmd("k6-runner", "pkill -f test_traffic.js || true")

if __name__ == "__main__":
    print("=== Coletando Telemetria 50 RPS Padrao para ZABBIX (telemetry_worker.csv) ===")
    scale_only("zabbix")
    set_chaos(0, 0, 0)
    start_k6_bg(rps=50, duration_s=70)
    collect_window("EXP2_ZABBIX_STD", "zabbix", "~/telemetry_worker.csv", duration_s=60)
    stop_k6()

    print("=== Coletando Telemetria Multi-Workload (telemetry_workloads_phase2.csv) ===")
    ssh_cmd("k3s-worker-edge", "rm -f ~/telemetry_workloads_phase2.csv")
    all_targets = ["baseline", "prometheus", "victoriametrics", "netdata", "telegraf", "ebpf", "zabbix"]

    for target in all_targets:
        print(f"-> Pipeline: {target.upper()}")
        set_chaos(0, 0, 0)
        ssh_cmd("k3s-worker-edge", "sudo /usr/sbin/tc qdisc del dev cni0 root 2>/dev/null || true")
        scale_only(None if target == "baseline" else target)

        # W1: Idle 0 RPS
        stop_k6()
        time.sleep(2)
        print(f"   [W1_Idle_0RPS] 20s...")
        collect_window(f"wl_idle_{target}", target, "~/telemetry_workloads_phase2.csv", duration_s=20)

        # W3: Stress 200 RPS
        print(f"   [W3_Stress_200RPS] 20s...")
        start_k6_bg(rps=200, duration_s=25)
        collect_window(f"wl_stress200_{target}", target, "~/telemetry_workloads_phase2.csv", duration_s=20)
        stop_k6()

        # W4: High Cardinality 1000 Series @ 50 RPS
        print(f"   [W4_HighCardinality_1000] 20s...")
        set_chaos(0, 0, 1000)
        start_k6_bg(rps=50, duration_s=25)
        collect_window(f"wl_card1000_{target}", target, "~/telemetry_workloads_phase2.csv", duration_s=20)
        stop_k6()
        set_chaos(0, 0, 0)

        # W5: Unstable Net @ 50 RPS
        if target != "baseline":
            print(f"   [W5_UnstableNet] 20s...")
            ssh_cmd("k3s-worker-edge", "sudo /usr/sbin/tc qdisc replace dev cni0 root netem delay 80ms 30ms loss 12%")
            start_k6_bg(rps=50, duration_s=25)
            collect_window(f"wl_netem_{target}", target, "~/telemetry_workloads_phase2.csv", duration_s=20)
            stop_k6()
            ssh_cmd("k3s-worker-edge", "sudo /usr/sbin/tc qdisc del dev cni0 root 2>/dev/null || true")

    scale_only(None)
    subprocess.run([GCLOUD, "compute", "scp", f"k3s-worker-edge:~/telemetry_worker.csv", benchmark_file('telemetry_worker.csv'), f"--zone={ZONE}", f"--project={PROJECT}", "--quiet"])
    subprocess.run([GCLOUD, "compute", "scp", f"k3s-worker-edge:~/telemetry_workloads_phase2.csv", benchmark_file('telemetry_workloads_phase2.csv'), f"--zone={ZONE}", f"--project={PROJECT}", "--quiet"])
    print("=== TELEMETRIA FASE 2 BAIXADA COM SUCESSO! ===")
