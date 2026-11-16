#!/usr/bin/env python3
"""
run_additional_pipelines.py
Executa a bateria experimental padronizada (Experimentos 2 e 3) para as 3 ferramentas
adicionais do escopo original do TCC:
  - Pipeline C: Netdata (Agente em C de tempo real + Health Daemon)
  - Pipeline D: Telegraf (Agente push-based com Starlark + Health Output)
  - Pipeline E: eBPF (Grafana Beyla instrumentando no Kernel Linux 6.1 via uprobes/BTF)
Anexa os resultados diretamente em dataset_benchmark_tcc2.csv e baixa telemetry_worker.csv.
"""

from project_paths import benchmark_file
import csv
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request

WORKER_EXT_IP = "34.95.217.182"
WORKER_INT_IP = "10.158.0.3"
CONTROL_PLANE = "k3s-control-plane"
WORKER_NODE = "k3s-worker-edge"
RUNNER_NODE = "k6-runner"
ZONE = "southamerica-east1-a"

OUTPUT_RESULTS = benchmark_file('dataset_benchmark_tcc2.csv')
OUTPUT_TELEMETRY = benchmark_file('telemetry_worker.csv')

ALL_DEPLOYMENTS = {
    "prometheus": "pipeline-a-prometheus",
    "victoriametrics": "pipeline-b-victoriametrics",
    "netdata": "pipeline-c-netdata",
    "telegraf": "pipeline-d-telegraf",
    "ebpf": "pipeline-e-ebpf",
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

def run_ssh(vm_name, command):
    cmd = [
        "gcloud", "compute", "ssh", vm_name,
        f"--zone={ZONE}",
        f"--command={command}",
        "--quiet"
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return res.returncode, res.stdout, res.stderr

def set_pipeline(pipeline):
    """Garante isolamento estrito escalando apenas a pipeline sob teste."""
    print(f"\n[Cluster] Ajustando pipeline ativa para: {pipeline.upper()}...")
    cmds = []
    for key, dep in ALL_DEPLOYMENTS.items():
        replicas = 1 if key == pipeline else 0
        cmds.append(f"sudo kubectl scale deployment {dep} --replicas={replicas}")
    if pipeline == "netdata":
        cmds.append("sudo kubectl rollout restart deployment pipeline-c-netdata")
    full_cmd = " && ".join(cmds)
    run_ssh(CONTROL_PLANE, full_cmd)
    print("[Cluster] Aguardando 20s para estabilização do pod, cgroups e memória...")
    time.sleep(20)
    reset_ebpf_state()

def set_chaos(delay_ms=0, error_rate=0):
    url = f"http://{WORKER_EXT_IP}:30080/chaos?delay_ms={delay_ms}&error_rate={error_rate}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"[Chaos] Erro ao injetar caos ({url}): {e}")
        return None

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
    if pipeline == "netdata":
        url = f"http://{WORKER_EXT_IP}:30095/api/v1/alarms?all=true"
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                data = json.loads(resp.read().decode())
                alarms = data.get("alarms", {})
                for k, v in alarms.items():
                    if ("HighLatencyP95" in k or "HighErrorRate" in k) and v.get("status") == "CRITICAL":
                        return True
        except Exception:
            pass
        return False

    elif pipeline == "telegraf":
        url = f"http://{WORKER_EXT_IP}:30096/"
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                return resp.status == 503
        except urllib.error.HTTPError as e:
            if e.code == 503:
                return True
        except Exception:
            pass
        return False

    elif pipeline == "ebpf":
        now = time.time()
        if now - ebpf_state["last_eval_ts"] < 5.0:
            return ebpf_state["firing"]
        ebpf_state["last_eval_ts"] = now
        url = f"http://{WORKER_EXT_IP}:30097/metrics"
        try:
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
                elif now - ebpf_state["violation_start"] >= 15.0:
                    ebpf_state["firing"] = True
            else:
                ebpf_state["violation_start"] = None
                ebpf_state["firing"] = False
            return ebpf_state["firing"]
        except Exception:
            return False

    return False

def execute_run(run_id, pipeline, scenario_name, delay_ms, error_rate, duration_seconds=90):
    print("\n" + "="*70)
    print(f"▶ EXECUÇÃO: {run_id} | Pipeline: {pipeline} | Cenário: {scenario_name}")
    print(f"  Parâmetros: delay={delay_ms}ms, error_rate={error_rate}%, duration={duration_seconds}s")
    print("="*70)

    # 1. Configura pipeline e reseta caos
    set_pipeline(pipeline)
    set_chaos(delay_ms=0, error_rate=0)

    # 2. Inicia telemetria no nó de borda
    print("[Telemetry] Iniciando telemetria em segundo plano no nó de borda...")
    telem_cmd = f"nohup bash ~/collect_telemetry.sh {run_id} {pipeline} ~/telemetry_worker.csv >/dev/null 2>&1 &"
    run_ssh(WORKER_NODE, telem_cmd)

    # 3. Dispara carga K6 do runner externo
    print("[K6] Disparando 50 RPS constantes a partir do k6-runner...")
    k6_remote_cmd = (
        f"k6 run -e TARGET_HOST={WORKER_INT_IP}:30080 "
        f"-e TEST_DURATION={duration_seconds}s "
        f"~/k6/test_traffic.js"
    )
    run_ssh(RUNNER_NODE, f"nohup {k6_remote_cmd} > ~/k6_{run_id}.log 2>&1 &")

    false_positives = 0
    t0_epoch = None
    t1_epoch = None
    ttd_seconds = None
    detected = False

    # 4. Fase estável (25s) para avaliar falsos positivos
    print("[Monitor] Aguardando 25s de regime estável inicial...")
    stable_start = time.time()
    while time.time() - stable_start < 25:
        if check_alert_firing(pipeline):
            false_positives += 1
        time.sleep(2)

    # 5. Injeção de Falha (Instante T0)
    if delay_ms > 0 or error_rate > 0:
        t0_epoch = time.time()
        print(f"💥 [Caos] Injetando anomalia no instante T0={t0_epoch:.2f} (delay={delay_ms}ms, error={error_rate}%)")
        set_chaos(delay_ms=delay_ms, error_rate=error_rate)

        poll_start = time.time()
        fault_window = min(duration_seconds - 25, 65)
        while time.time() - poll_start < fault_window:
            if check_alert_firing(pipeline):
                t1_epoch = time.time()
                ttd_seconds = round(t1_epoch - t0_epoch, 2)
                detected = True
                print(f"🔔 [ALERTA DISPARADO!] T1={t1_epoch:.2f} | TTD = {ttd_seconds}s")
                break
            time.sleep(1)

        # Remove a falha
        set_chaos(delay_ms=0, error_rate=0)
        print("[Caos] Falha removida. Aguardando estabilização final...")

    # 6. Aguarda término do k6 e encerra processos
    time.sleep(8)
    run_ssh(RUNNER_NODE, "pkill -f k6 || true")
    run_ssh(WORKER_NODE, "pkill -f collect_telemetry.sh || true")

    result = {
        "run_id": run_id,
        "pipeline": pipeline,
        "scenario": scenario_name,
        "injected_delay_ms": delay_ms,
        "injected_error_rate": error_rate,
        "t0_epoch": t0_epoch,
        "t1_epoch": t1_epoch,
        "ttd_seconds": ttd_seconds if detected else ("N/A" if (delay_ms == 0 and error_rate == 0) else "TIMEOUT"),
        "detected": 1 if detected else 0,
        "false_positives": false_positives
    }

    file_exists = os.path.isfile(OUTPUT_RESULTS)
    with open(OUTPUT_RESULTS, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=result.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(result)

    print(f"✔ Rodada concluída: {run_id}. Dados salvos em {OUTPUT_RESULTS}")
    return result

def download_telemetry():
    print("\n[Download] Baixando telemetria detalhada do nó medido...")
    cmd = [
        "gcloud", "compute", "scp",
        f"{WORKER_NODE}:~/telemetry_worker.csv",
        OUTPUT_TELEMETRY,
        f"--zone={ZONE}",
        "--quiet"
    ]
    subprocess.run(cmd)
    if os.path.exists(OUTPUT_TELEMETRY):
        lines = open(OUTPUT_TELEMETRY).readlines()
        print(f"✔ Telemetria baixada com sucesso! Total de registros: {len(lines)}")

if __name__ == "__main__":
    print("==================================================================")
    print(" BATERIA EXPERIMENTAL ADICIONAL: NETDATA, TELEGRAF E eBPF (BEYLA) ")
    print("==================================================================")

    for pipe, prefix in [("netdata", "NETDATA")]:
        execute_run(f"EXP2_{prefix}_C0", pipe, "C0_saudavel", delay_ms=0, error_rate=0, duration_seconds=60)
        execute_run(f"EXP2_{prefix}_C1", pipe, "C1_atraso_100ms", delay_ms=100, error_rate=0, duration_seconds=75)
        execute_run(f"EXP2_{prefix}_C2", pipe, "C2_atraso_300ms", delay_ms=300, error_rate=0, duration_seconds=90)
        execute_run(f"EXP2_{prefix}_C3", pipe, "C3_erro_20pct", delay_ms=0, error_rate=20, duration_seconds=90)

    download_telemetry()
    print("\n🎉 Bateria experimental das 3 novas pipelines finalizada com sucesso!")
