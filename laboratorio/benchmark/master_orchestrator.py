#!/usr/bin/env python3
"""
master_orchestrator.py
Orquestrador Completo e Automatizado do Laboratório GCP - TCC 2
Executa os Experimentos 1, 2 e 3 comparando Baseline, Prometheus e VictoriaMetrics.
"""

from project_paths import benchmark_file
import csv
import json
import os
import subprocess
import time
import urllib.request

WORKER_EXT_IP = "34.95.217.182"
WORKER_INT_IP = "10.158.0.3"
CONTROL_PLANE = "k3s-control-plane"
WORKER_NODE = "k3s-worker-edge"
RUNNER_NODE = "k6-runner"
ZONE = "southamerica-east1-a"

OUTPUT_RESULTS = benchmark_file('dataset_benchmark_tcc2.csv')
OUTPUT_TELEMETRY = benchmark_file('telemetry_worker.csv')

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
    if pipeline == "baseline":
        cmd = "sudo kubectl scale deployment pipeline-a-prometheus --replicas=0 && sudo kubectl scale deployment pipeline-b-victoriametrics --replicas=0"
    elif pipeline == "prometheus":
        cmd = "sudo kubectl scale deployment pipeline-b-victoriametrics --replicas=0 && sudo kubectl scale deployment pipeline-a-prometheus --replicas=1"
    elif pipeline == "victoriametrics":
        cmd = "sudo kubectl scale deployment pipeline-a-prometheus --replicas=0 && sudo kubectl scale deployment pipeline-b-victoriametrics --replicas=1"
    else:
        raise ValueError(f"Pipeline desconhecida: {pipeline}")

    run_ssh(CONTROL_PLANE, cmd)
    print("[Cluster] Aguardando 15s para estabilização de cgroups e memória...")
    time.sleep(15)

def set_chaos(delay_ms=0, error_rate=0):
    url = f"http://{WORKER_EXT_IP}:30080/chaos?delay_ms={delay_ms}&error_rate={error_rate}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"[Chaos] Erro ao injetar caos ({url}): {e}")
        return None

def check_alert_firing(pipeline):
    if pipeline == "prometheus":
        url = f"http://{WORKER_EXT_IP}:30093/api/v2/alerts"
    elif pipeline == "victoriametrics":
        url = f"http://{WORKER_EXT_IP}:30094/api/v1/alerts"
    else:
        return False

    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            data = json.loads(resp.read().decode())
            if isinstance(data, list):
                for alert in data:
                    name = alert.get("labels", {}).get("alertname")
                    if name in ("HighLatencyP95", "HighErrorRate"):
                        return alert.get("status", {}).get("state") == "active"
            elif isinstance(data, dict):
                alerts = data.get("data", {}).get("alerts", [])
                for alert in alerts:
                    name = alert.get("name")
                    if name in ("HighLatencyP95", "HighErrorRate"):
                        return alert.get("state") == "firing"
    except Exception:
        pass
    return False

def execute_run(run_id, pipeline, scenario_name, delay_ms, error_rate, duration_seconds=120):
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
    # Roda k6 em background
    run_ssh(RUNNER_NODE, f"nohup {k6_remote_cmd} > ~/k6_{run_id}.log 2>&1 &")

    false_positives = 0
    t0_epoch = None
    t1_epoch = None
    ttd_seconds = None
    detected = False

    # 4. Fase estável (30s) para avaliar falsos positivos
    print("[Monitor] Aguardando 30s de regime estável inicial...")
    stable_start = time.time()
    while time.time() - stable_start < 30:
        if check_alert_firing(pipeline):
            false_positives += 1
        time.sleep(2)

    # 5. Injeção de Falha (Instante T0)
    if delay_ms > 0 or error_rate > 0:
        t0_epoch = time.time()
        print(f"💥 [Caos] Injetando anomalia no instante T0={t0_epoch:.2f} (delay={delay_ms}ms, error={error_rate}%)")
        set_chaos(delay_ms=delay_ms, error_rate=error_rate)

        # Polling para detecção T1
        poll_start = time.time()
        fault_window = min(duration_seconds - 30, 90)
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

    # 6. Aguarda término do k6
    time.sleep(10)
    run_ssh(RUNNER_NODE, "pkill -f k6 || true")

    # 7. Finaliza telemetria no nó de borda
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

    # Salva no CSV mestre
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
    print(" BATERIA EXPERIMENTAL COMPLETA: TCC 2 - OBSERVABILIDADE NA BORDA ")
    print("==================================================================")

    # 1. EXPERIMENTO 1: Linha de Base (B0)
    execute_run("EXP1_B0_01", "baseline", "C0_saudavel", delay_ms=0, error_rate=0, duration_seconds=90)
    execute_run("EXP1_B0_02", "baseline", "C0_saudavel", delay_ms=0, error_rate=0, duration_seconds=90)

    # 2. EXPERIMENTO 2 & 3: Comparativo Prometheus vs VictoriaMetrics
    # Cenário C0: Saudável (Controle de Falsos Positivos e Sobrecarga em repouso)
    execute_run("EXP2_PROM_C0", "prometheus", "C0_saudavel", delay_ms=0, error_rate=0, duration_seconds=90)
    execute_run("EXP2_VM_C0", "victoriametrics", "C0_saudavel", delay_ms=0, error_rate=0, duration_seconds=90)

    # Cenário C1: Atraso Leve (+100ms - p95 ultrapassa 200ms)
    execute_run("EXP2_PROM_C1", "prometheus", "C1_atraso_100ms", delay_ms=100, error_rate=0, duration_seconds=120)
    execute_run("EXP2_VM_C1", "victoriametrics", "C1_atraso_100ms", delay_ms=100, error_rate=0, duration_seconds=120)

    # Cenário C2: Atraso Severo (+300ms)
    execute_run("EXP2_PROM_C2", "prometheus", "C2_atraso_300ms", delay_ms=300, error_rate=0, duration_seconds=120)
    execute_run("EXP2_VM_C2", "victoriametrics", "C2_atraso_300ms", delay_ms=300, error_rate=0, duration_seconds=120)

    # Cenário C3: Degradação de Erro (20% HTTP 500)
    execute_run("EXP2_PROM_C3", "prometheus", "C3_erro_20pct", delay_ms=0, error_rate=20, duration_seconds=120)
    execute_run("EXP2_VM_C3", "victoriametrics", "C3_erro_20pct", delay_ms=0, error_rate=20, duration_seconds=120)

    # 3. Download final da telemetria
    download_telemetry()
    print("\n🎉 Bateria experimental finalizada com sucesso!")
