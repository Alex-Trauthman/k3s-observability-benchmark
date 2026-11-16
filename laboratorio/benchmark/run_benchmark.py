#!/usr/bin/env python3
"""
run_benchmark.py
Orquestrador Automatizado do Benchmark do TCC 2.
Executa os 3 Experimentos Formais:
  - Experimento 1: Linha de Base (B0) e Validação Arquitetural
  - Experimento 2: Eficácia Temporal de Detecção (TTD = T1 - T0)
  - Experimento 3: Sobrecarga de Recursos (mCPU, RSS em MB, Rede KB/s)
Gera o dataset final consolidado: dataset_benchmark_tcc2.csv
"""

from project_paths import benchmark_file
import csv
import json
import os
import subprocess
import time
import urllib.request

TARGET_NODE_IP = os.getenv("TARGET_NODE_IP", "10.158.0.3") # IP interno do Nó 2 (Borda)
API_PORT = 30080
PROMETHEUS_ALERT_PORT = 30093
VICTORIA_ALERT_PORT = 30094
OUTPUT_DATASET = benchmark_file("dataset_benchmark_tcc2.csv")

def set_chaos(delay_ms=0, error_rate=0):
    """Injeta falha controlada via endpoint /chaos da API alvo."""
    url = f"http://{TARGET_NODE_IP}:{API_PORT}/chaos?delay_ms={delay_ms}&error_rate={error_rate}"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"[Chaos] Erro ao injetar caos: {e}")
        return None

def check_alert_firing(pipeline):
    """Consulta a API do Alertmanager ou vmalert para verificar se o alerta de latência está FIRING."""
    if pipeline == "prometheus":
        url = f"http://{TARGET_NODE_IP}:{PROMETHEUS_ALERT_PORT}/api/v2/alerts"
    else:
        url = f"http://{TARGET_NODE_IP}:{VICTORIA_ALERT_PORT}/api/v1/alerts"

    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            data = json.loads(resp.read().decode())
            # Verifica alertas no Prometheus Alertmanager v2
            if isinstance(data, list):
                for alert in data:
                    if alert.get("labels", {}).get("alertname") == "HighLatencyP95":
                        return alert.get("status", {}).get("state") == "active"
            # Formato do vmalert
            elif isinstance(data, dict):
                alerts = data.get("data", {}).get("alerts", [])
                for alert in alerts:
                    if alert.get("name") == "HighLatencyP95":
                        return alert.get("state") == "firing"
    except Exception:
        pass
    return False

def run_single_experiment(run_id, pipeline, scenario_name, delay_ms, error_rate, duration_seconds=300):
    print(f"\n=======================================================")
    print(f"▶ Iniciando: Run {run_id} | Pipeline: {pipeline} | Cenário: {scenario_name}")
    print(f"=======================================================")

    # 1. Reset da API (sem falha inicial)
    set_chaos(delay_ms=0, error_rate=0)
    time.sleep(10) # Estabilização

    # 2. Inicia o gerador K6 em background
    k6_cmd = [
        "k6", "run",
        "-e", f"TARGET_HOST={TARGET_NODE_IP}:{API_PORT}",
        "-e", f"TEST_DURATION={duration_seconds}s",
        "../k6/test_traffic.js"
    ]
    print("[K6] Disparando carga sintética constante de 50 RPS...")
    k6_proc = subprocess.Popen(k6_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    t0_epoch = None
    t1_epoch = None
    ttd_seconds = None
    detected = False
    false_positives = 0

    # 3. Regime estável inicial (60 segundos) para checar falsos positivos
    start_time = time.time()
    while time.time() - start_time < 60:
        if check_alert_firing(pipeline):
            false_positives += 1
        time.sleep(2)

    # 4. Injeção de Falha (instante T0)
    if delay_ms > 0 or error_rate > 0:
        t0_epoch = time.time()
        print(f"[Caos] Injetando falha no instante T0 ({t0_epoch}): delay={delay_ms}ms, error={error_rate}%")
        set_chaos(delay_ms=delay_ms, error_rate=error_rate)

        # Polling para detectar T1 (alerta firing)
        fault_timeout = 180 # até 3 minutos de observação da falha
        poll_start = time.time()
        while time.time() - poll_start < fault_timeout:
            if check_alert_firing(pipeline):
                t1_epoch = time.time()
                ttd_seconds = round(t1_epoch - t0_epoch, 2)
                detected = True
                print(f"🔔 [Alerta DISPARADO!] T1={t1_epoch} | TTD = {ttd_seconds} segundos")
                break
            time.sleep(1)

        # Remove a falha
        set_chaos(delay_ms=0, error_rate=0)

    # Aguarda término do K6
    k6_proc.wait()

    result = {
        "run_id": run_id,
        "pipeline": pipeline,
        "scenario": scenario_name,
        "injected_delay_ms": delay_ms,
        "injected_error_rate": error_rate,
        "t0_epoch": t0_epoch,
        "t1_epoch": t1_epoch,
        "ttd_seconds": ttd_seconds if detected else "N/A",
        "detected": 1 if detected else 0,
        "false_positives": false_positives
    }

    # Gravação no CSV consolidado
    file_exists = os.path.isfile(OUTPUT_DATASET)
    with open(OUTPUT_DATASET, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=result.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(result)

    print(f"✔ Finalizado: Run {run_id}. Resultados salvos em {OUTPUT_DATASET}")
    return result

if __name__ == "__main__":
    print("Iniciando bateria automatizada de testes...")
    # Exemplo: 1 rodada de cada cenário para validação do laboratório
    # Cenário 0: Saudável (Baseline)
    run_single_experiment("pilot_01", "baseline", "C0_saudavel", delay_ms=0, error_rate=0, duration_seconds=120)
    # Cenário 1: Atraso Leve (+100ms)
    run_single_experiment("pilot_02", "prometheus", "C1_atraso_100ms", delay_ms=100, error_rate=0, duration_seconds=180)
    run_single_experiment("pilot_03", "victoriametrics", "C1_atraso_100ms", delay_ms=100, error_rate=0, duration_seconds=180)
    # Cenário 2: Atraso Severo (+300ms)
    run_single_experiment("pilot_04", "prometheus", "C2_atraso_300ms", delay_ms=300, error_rate=0, duration_seconds=180)
    run_single_experiment("pilot_05", "victoriametrics", "C2_atraso_300ms", delay_ms=300, error_rate=0, duration_seconds=180)
