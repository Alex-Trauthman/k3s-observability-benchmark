#!/usr/bin/env python3
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

DEPLOYMENTS = {
    "prometheus": "pipeline-a-prometheus",
    "victoriametrics": "pipeline-b-victoriametrics",
    "netdata": "pipeline-c-netdata",
    "telegraf": "pipeline-d-telegraf",
    "ebpf": "pipeline-e-ebpf",
    "zabbix": "pipeline-f-zabbix",
}

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
    if active_pipeline in ("netdata", "telegraf", "zabbix"):
        cmds.append(f"sudo kubectl rollout restart deployment {DEPLOYMENTS[active_pipeline]}")
    ssh_cmd("k3s-control-plane", " && ".join(cmds))
    if active_pipeline:
        dep = DEPLOYMENTS[active_pipeline]
        ssh_cmd("k3s-control-plane", f"sudo kubectl rollout status deployment/{dep} --timeout=90s")
        time.sleep(12)
    else:
        time.sleep(8)
    reset_ebpf_state()

def parse_beyla_metrics(text):
    b025 = 0.0
    c200 = 0.0
    c500 = 0.0
    for line in text.splitlines():
        if line.startswith("#") or 'http_route="/work"' not in line or 'service_name="api-target"' not in line:
            continue
        if line.startswith("http_server_request_duration_seconds_bucket{") and 'http_response_status_code="200"' in line and 'le="0.25"' in line:
            try:
                b025 += float(line.split()[-1])
            except ValueError:
                pass
        elif line.startswith("http_server_request_duration_seconds_count{"):
            try:
                val = float(line.split()[-1])
                if 'http_response_status_code="200"' in line:
                    c200 += val
                elif 'http_response_status_code="500"' in line:
                    c500 += val
            except ValueError:
                pass
    return b025, c200, c500

def check_alert_firing(pipeline):
    try:
        if pipeline == "prometheus":
            url = f"http://{WORKER_EXT_IP}:30090/api/v1/alerts"
            with urllib.request.urlopen(url, timeout=2) as r:
                data = json.loads(r.read().decode())
                for a in data.get("data", {}).get("alerts", []):
                    if a.get("state") == "firing":
                        return True
        elif pipeline == "victoriametrics":
            url = f"http://{WORKER_EXT_IP}:30094/api/v1/alerts"
            with urllib.request.urlopen(url, timeout=2) as r:
                data = json.loads(r.read().decode())
                for a in data.get("data", {}).get("alerts", []):
                    if a.get("state") == "firing":
                        return True
        elif pipeline == "netdata":
            url = f"http://{WORKER_EXT_IP}:30095/api/v1/alarms?all=true"
            with urllib.request.urlopen(url, timeout=2) as r:
                data = json.loads(r.read().decode())
                for k, v in data.get("alarms", {}).items():
                    if ("HighLatencyP95" in k or "HighErrorRate" in k) and v.get("status") == "CRITICAL":
                        return True
        elif pipeline == "telegraf":
            url = f"http://{WORKER_EXT_IP}:30096/"
            with urllib.request.urlopen(url, timeout=2) as r:
                return r.status == 503
        elif pipeline == "ebpf":
            now = time.time()
            if now - ebpf_state["last_eval_ts"] < 4.5:
                return ebpf_state["firing"]
            ebpf_state["last_eval_ts"] = now
            url = f"http://{WORKER_EXT_IP}:30097/metrics"
            with urllib.request.urlopen(url, timeout=2) as resp:
                text = resp.read().decode()
            b025, c200, c500 = parse_beyla_metrics(text)
            if not ebpf_state["initialized"]:
                ebpf_state["prev_b025"] = b025
                ebpf_state["prev_c200"] = c200
                ebpf_state["prev_c500"] = c500
                ebpf_state["initialized"] = True
                return False
            d_b025 = b025 - ebpf_state["prev_b025"]
            d_c200 = c200 - ebpf_state["prev_c200"]
            d_c500 = c500 - ebpf_state["prev_c500"]
            d_all = d_c200 + d_c500
            ebpf_state["prev_b025"] = b025
            ebpf_state["prev_c200"] = c200
            ebpf_state["prev_c500"] = c500
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
            return ebpf_state["firing"]
        elif pipeline == "zabbix":
            url = f"http://{WORKER_EXT_IP}:30098/alerts"
            with urllib.request.urlopen(url, timeout=2) as r:
                data = json.loads(r.read().decode())
                return data.get("status") == "firing"
    except urllib.error.HTTPError as e:
        if pipeline == "telegraf" and e.code == 503:
            return True
    except Exception:
        pass
    return False

def stop_telemetry():
    ssh_cmd("k3s-worker-edge", "pkill -f collect_telemetry.sh || true")

def start_telemetry(run_id, pipeline, remote_csv="~/telemetry_worker.csv"):
    stop_telemetry()
    ssh_cmd("k3s-worker-edge", f"nohup bash ~/collect_telemetry.sh {run_id} {pipeline} {remote_csv} >/dev/null 2>&1 &")

def stop_k6():
    ssh_cmd("k6-runner", "pkill -f test_traffic.js || true")

def run_k6_load(rps=50, duration_s=35, bg=False):
    stop_k6()
    k6_cmd = f"k6 run -e TARGET_HOST={WORKER_INT_IP}:30080 -e TEST_DURATION={duration_s}s -e TEST_RPS={rps} ~/k6/test_traffic.js --summary-export=/tmp/k6_sum.json"
    if bg:
        ssh_cmd("k6-runner", f"nohup {k6_cmd} >/tmp/k6_bg.log 2>&1 &")
        return 0.0, 0.0, 0.0, 0.0
    else:
        ssh_cmd("k6-runner", f"{k6_cmd} >/dev/null 2>&1", timeout=duration_s + 35)
        _, out, _ = ssh_cmd("k6-runner", "cat /tmp/k6_sum.json")
        try:
            data = json.loads(out)
            dur = data.get("metrics", {}).get("http_req_duration", {})
            fail = data.get("metrics", {}).get("http_req_failed", {})
            avg_ms = round(float(dur.get("avg", 0.0)), 2)
            p90_ms = round(float(dur.get("p(90)", 0.0)), 2)
            p95_ms = round(float(dur.get("p(95)", 0.0)), 2)
            err_pct = round(float(fail.get("value", 0.0)) * 100.0, 2)
            return avg_ms, p90_ms, p95_ms, err_pct
        except Exception:
            return 0.0, 0.0, 0.0, 0.0

def run_zabbix_standard_scenarios():
    print("=== PARTE 1: Executando Bateria Padrao (C0, C1, C2, C3) para ZABBIX ===")
    scale_only("zabbix")
    scenarios = [
        ("EXP2_ZABBIX_C0", "C0_saudavel", 0, 0, False),
        ("EXP2_ZABBIX_C1", "C1_atraso_100ms", 100, 0, False),
        ("EXP2_ZABBIX_C2", "C2_atraso_300ms", 300, 0, True),
        ("EXP2_ZABBIX_C3", "C3_erro_20pct", 0, 20, True),
    ]
    rows = []
    for run_id, sc_name, delay, err, expect_alert in scenarios:
        print(f"  -> Zabbix {sc_name} (delay={delay}ms, err={err}%)")
        set_chaos(0, 0, 0)
        ssh_cmd("k3s-control-plane", "sudo kubectl rollout restart deployment pipeline-f-zabbix && sudo kubectl rollout status deployment pipeline-f-zabbix --timeout=60s")
        time.sleep(8)
        start_telemetry(run_id, "zabbix", "~/telemetry_worker.csv")
        run_k6_load(rps=50, duration_s=55, bg=True)
        time.sleep(10)
        t0 = time.time()
        set_chaos(delay, err, 0)
        t1 = None
        fp = 0
        deadline = time.time() + 40
        while time.time() < deadline:
            if check_alert_firing("zabbix"):
                t1 = time.time()
                if not expect_alert:
                    fp = 1
                break
            time.sleep(0.5)
        stop_telemetry()
        stop_k6()
        set_chaos(0, 0, 0)
        ttd = round(t1 - t0, 2) if (t1 and expect_alert) else ("N/A" if not expect_alert else "TIMEOUT")
        det = 1 if (t1 and expect_alert) else 0
        print(f"     Resultado: TTD={ttd}s | Detectado={det} | FP={fp}")
        rows.append([run_id, "zabbix", sc_name, delay, err, round(t0, 2) if expect_alert else "", round(t1, 2) if t1 else "", ttd, det, fp])
    return rows

def run_multi_workload_battery():
    print("\n=== PARTE 2: Executando Bateria Multi-Workload (Idle, HighLoad 200 RPS, HighCardinality 1000, UnstableNet) ===")
    ssh_cmd("k3s-worker-edge", "rm -f ~/telemetry_workloads_phase2.csv")
    all_targets = ["baseline", "prometheus", "victoriametrics", "netdata", "telegraf", "ebpf", "zabbix"]
    summary_rows = []

    for target in all_targets:
        print(f"\n>>> Preparando Pipeline: {target.upper()} <<<")
        set_chaos(0, 0, 0)
        ssh_cmd("k3s-worker-edge", "sudo /usr/sbin/tc qdisc del dev cni0 root 2>/dev/null || true")
        scale_only(None if target == "baseline" else target)

        # 1. WORKLOAD: IDLE (0 RPS)
        run_id_idle = f"wl_idle_{target}"
        print(f"  [W1 - IDLE 0 RPS] Coletando 25s em repouso absoluto...")
        stop_k6()
        time.sleep(3)
        start_telemetry(run_id_idle, target, "~/telemetry_workloads_phase2.csv")
        time.sleep(25)
        stop_telemetry()
        summary_rows.append([run_id_idle, target, "W1_Idle_0RPS", 0, 0, 0.0, 0.0, 0.0, 0.0, -1.0])

        # 2. WORKLOAD: HIGH LOAD / STRESS (200 RPS)
        run_id_stress = f"wl_stress200_{target}"
        print(f"  [W3 - STRESS 200 RPS] Executando K6 a 200 RPS por 25s...")
        start_telemetry(run_id_stress, target, "~/telemetry_workloads_phase2.csv")
        avg_ms, p90_ms, p95_ms, err_pct = run_k6_load(rps=200, duration_s=25, bg=False)
        stop_telemetry()
        print(f"     K6 @ 200 RPS -> avg={avg_ms:.2f}ms, p90={p90_ms:.2f}ms, p95={p95_ms:.2f}ms, err={err_pct:.2f}%")
        summary_rows.append([run_id_stress, target, "W3_Stress_200RPS", 200, 0, avg_ms, p90_ms, p95_ms, err_pct, -1.0])

        # 3. WORKLOAD: HIGH CARDINALITY (1,000 series @ 50 RPS)
        run_id_card = f"wl_card1000_{target}"
        print(f"  [W4 - HIGH CARDINALITY 1000 SERIES] Ativando 1000 series IoT + 50 RPS por 25s...")
        set_chaos(0, 0, 1000)
        start_telemetry(run_id_card, target, "~/telemetry_workloads_phase2.csv")
        avg_c, p90_c, p95_c, err_c = run_k6_load(rps=50, duration_s=25, bg=False)
        stop_telemetry()
        set_chaos(0, 0, 0)
        print(f"     K6 @ 1000 Series -> avg={avg_c:.2f}ms, p90={p90_c:.2f}ms, p95={p95_c:.2f}ms")
        summary_rows.append([run_id_card, target, "W4_HighCardinality_1000", 50, 1000, avg_c, p90_c, p95_c, err_c, -1.0])

        # 4. WORKLOAD: UNSTABLE NETWORK (tc netem on cni0: 80ms delay +/- 30ms jitter, 12% packet loss)
        if target != "baseline":
            run_id_net = f"wl_netem_{target}"
            print(f"  [W5 - UNSTABLE NET (12% loss, 80ms+-30ms jitter)] Testando TTD e resiliencia sob rede degradada...")
            if target in ("netdata", "telegraf", "zabbix"):
                dep = DEPLOYMENTS[target]
                ssh_cmd("k3s-control-plane", f"sudo kubectl rollout restart deployment {dep} && sudo kubectl rollout status deployment {dep} --timeout=60s")
                time.sleep(8)
            reset_ebpf_state()
            ssh_cmd("k3s-worker-edge", "sudo /usr/sbin/tc qdisc replace dev cni0 root netem delay 80ms 30ms loss 12%")
            start_telemetry(run_id_net, target, "~/telemetry_workloads_phase2.csv")
            run_k6_load(rps=50, duration_s=85, bg=True)
            time.sleep(10)
            if target == "ebpf":
                check_alert_firing("ebpf")
                time.sleep(5)
            t0 = time.time()
            set_chaos(300, 20, 0)
            t1 = None
            deadline = time.time() + 70
            while time.time() < deadline:
                if check_alert_firing(target):
                    t1 = time.time()
                    break
                time.sleep(0.5)
            stop_telemetry()
            stop_k6()
            ssh_cmd("k3s-worker-edge", "sudo /usr/sbin/tc qdisc del dev cni0 root 2>/dev/null || true")
            set_chaos(0, 0, 0)
            ttd_net = round(t1 - t0, 2) if t1 else -1.0
            print(f"     Resultado Rede Instavel ({target}): TTD={ttd_net}s")
            summary_rows.append([run_id_net, target, "W5_UnstableNet_12pctLoss", 50, 0, 0.0, 0.0, 0.0, 0.0, ttd_net])

    scale_only(None)
    return summary_rows

if __name__ == "__main__":
    zbx_rows = run_zabbix_standard_scenarios()
    csv_path = benchmark_file('dataset_benchmark_tcc2.csv')
    existing = []
    if os.path.exists(csv_path):
        with open(csv_path, "r") as f:
            existing = [r for r in csv.reader(f) if r and r[1] != "zabbix"]
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        for r in existing:
            w.writerow(r)
        for r in zbx_rows:
            w.writerow(r)

    wl_rows = run_multi_workload_battery()
    wl_csv_path = benchmark_file('dataset_workloads_phase2.csv')
    with open(wl_csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["run_id", "pipeline", "workload", "rps", "cardinality", "k6_avg_ms", "k6_p90_ms", "k6_p95_ms", "k6_err_pct", "ttd_unstable_s"])
        for r in wl_rows:
            w.writerow(r)

    subprocess.run([GCLOUD, "compute", "scp", f"k3s-worker-edge:~/telemetry_worker.csv", benchmark_file('telemetry_worker.csv'), f"--zone={ZONE}", f"--project={PROJECT}", "--quiet"])
    subprocess.run([GCLOUD, "compute", "scp", f"k3s-worker-edge:~/telemetry_workloads_phase2.csv", benchmark_file('telemetry_workloads_phase2.csv'), f"--zone={ZONE}", f"--project={PROJECT}", "--quiet"])
    print("\n=== BENCHMARK FASE 2 CONCLUIDO COM SUCESSO! ===")
