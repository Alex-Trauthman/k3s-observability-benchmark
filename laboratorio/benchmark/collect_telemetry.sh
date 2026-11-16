#!/usr/bin/env bash
# collect_telemetry.sh
# Coleta continua de telemetria de CPU (mCPU), Memoria (RSS em MB) e Rede (KB/s) no nó medido
# Uso: ./collect_telemetry.sh <RUN_ID> <PIPELINE_NAME> <OUTPUT_CSV>

RUN_ID="${1:-run_default}"
PIPELINE="${2:-baseline}"
OUTPUT_FILE="${3:-telemetry_output.csv}"

# Header do CSV se o arquivo não existir
if [ ! -f "$OUTPUT_FILE" ]; then
    echo "timestamp_epoch,run_id,pipeline,cpu_total_percent,cpu_mcpu,mem_rss_mb,net_rx_kbs,net_tx_kbs" > "$OUTPUT_FILE"
fi

get_net_bytes() {
    # Soma bytes recebidos e transmitidos em todas as interfaces fisicas e veth (formato inteiro sem notação científica)
    awk '/:/ {rx+=$2; tx+=$10} END {printf "%.0f %.0f\n", rx, tx}' /proc/net/dev
}

echo "[Telemetry] Iniciando coleta contínua para $PIPELINE (Run: $RUN_ID)..."

PREV_NET=$(get_net_bytes)
PREV_RX=$(echo "$PREV_NET" | awk '{print $1}')
PREV_TX=$(echo "$PREV_NET" | awk '{print $2}')

while true; do
    SLEEP_START=$(date +%s%N)
    sleep 1
    NOW=$(date +%s)
    
    # Leitura de Rede
    CURR_NET=$(get_net_bytes)
    CURR_RX=$(echo "$CURR_NET" | awk '{print $1}')
    CURR_TX=$(echo "$CURR_NET" | awk '{print $2}')
    
    DIFF_RX=$(( (CURR_RX - PREV_RX) / 1024 ))
    DIFF_TX=$(( (CURR_TX - PREV_TX) / 1024 ))
    PREV_RX=$CURR_RX
    PREV_TX=$CURR_TX

    # Leitura de Memoria RSS Agregada da Pipeline em MB (via ps aux)
    if [ "$PIPELINE" = "prometheus" ]; then
        MEM_KB=$(ps aux | grep -E "prometheus|alertmanager" | grep -v grep | grep -v collect_telemetry | awk '{sum+=$6} END {print sum+0}')
        CPU_PCT=$(ps aux | grep -E "prometheus|alertmanager" | grep -v grep | grep -v collect_telemetry | awk '{sum+=$3} END {print sum+0}')
    elif [ "$PIPELINE" = "victoriametrics" ]; then
        MEM_KB=$(ps aux | grep -E "victoria-metrics|vmagent|vmalert" | grep -v grep | grep -v collect_telemetry | awk '{sum+=$6} END {print sum+0}')
        CPU_PCT=$(ps aux | grep -E "victoria-metrics|vmagent|vmalert" | grep -v grep | grep -v collect_telemetry | awk '{sum+=$3} END {print sum+0}')
    elif [ "$PIPELINE" = "netdata" ]; then
        MEM_KB=$(ps aux | grep -E "netdata|go\.d\.plugin|apps\.plugin|ebpf\.plugin|spawn-plugins|nfacct|cgroups\.plugin" | grep -v grep | grep -v collect_telemetry | awk '{sum+=$6} END {print sum+0}')
        CPU_PCT=$(ps aux | grep -E "netdata|go\.d\.plugin|apps\.plugin|ebpf\.plugin|spawn-plugins|nfacct|cgroups\.plugin" | grep -v grep | grep -v collect_telemetry | awk '{sum+=$3} END {print sum+0}')
    elif [ "$PIPELINE" = "telegraf" ]; then
        MEM_KB=$(ps aux | grep -E "telegraf" | grep -v grep | grep -v collect_telemetry | awk '{sum+=$6} END {print sum+0}')
        CPU_PCT=$(ps aux | grep -E "telegraf" | grep -v grep | grep -v collect_telemetry | awk '{sum+=$3} END {print sum+0}')
    elif [ "$PIPELINE" = "ebpf" ]; then
        MEM_KB=$(ps aux | grep -E "beyla|ebpf" | grep -v grep | grep -v collect_telemetry | awk '{sum+=$6} END {print sum+0}')
        CPU_PCT=$(ps aux | grep -E "beyla|ebpf" | grep -v grep | grep -v collect_telemetry | awk '{sum+=$3} END {print sum+0}')
    elif [ "$PIPELINE" = "zabbix" ]; then
        MEM_KB=$(ps aux | grep -E "zabbix_proxy|zabbix_agent2|zabbix_eval" | grep -v grep | grep -v collect_telemetry | awk '{sum+=$6} END {print sum+0}')
        CPU_PCT=$(ps aux | grep -E "zabbix_proxy|zabbix_agent2|zabbix_eval" | grep -v grep | grep -v collect_telemetry | awk '{sum+=$3} END {print sum+0}')
    else
        # Baseline (somente API alvo)
        MEM_KB=$(ps aux | grep -E "api-target" | grep -v grep | grep -v collect_telemetry | awk '{sum+=$6} END {print sum+0}')
        CPU_PCT=$(ps aux | grep -E "api-target" | grep -v grep | grep -v collect_telemetry | awk '{sum+=$3} END {print sum+0}')
    fi

    MEM_MB=$(awk "BEGIN {printf \"%.2f\", $MEM_KB / 1024}")
    # Normalização em millicores (100% de 1 core = 1000 mCPU)
    MCPU=$(awk "BEGIN {printf \"%.2f\", $CPU_PCT * 10}")

    echo "$NOW,$RUN_ID,$PIPELINE,$CPU_PCT,$MCPU,$MEM_MB,$DIFF_RX,$DIFF_TX" >> "$OUTPUT_FILE"
done
