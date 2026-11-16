#!/usr/bin/env bash
# 01_setup_cluster_k3s.sh
# Script de automação para instalação do K3s mínimo e nó de borda restrito

ROLE="${1:-server}"
SERVER_IP="${2:-}"
TOKEN="${3:-}"

echo "=============================================="
echo "Instalação e Otimização do K3s para Borda"
echo "Papel selecionado: $ROLE"
echo "=============================================="

# Atualiza pacotes básicos e instala NTP (chrony) e telemetria (sysstat)
sudo apt-get update -y
sudo apt-get install -y chrony sysstat curl iptables

# Sincronização estrita de relógio NTP (Mitigação do Risco R04 da Matriz GUT)
sudo systemctl enable --now chrony
sudo chronyc sources -v

if [ "$ROLE" = "server" ]; then
    echo "[K3s Server] Instalando Control Plane minimalista (sem Traefik e sem ServiceLB)..."
    curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="server --disable traefik --disable servicelb --node-taint node-role.kubernetes.io/master:NoSchedule" sh -
    
    # Aguarda inicialização
    sleep 10
    sudo chmod 644 /etc/rancher/k3s/k3s.yaml
    
    echo "✔ K3s Server instalado com sucesso!"
    echo "--------------------------------------------------------"
    echo "TOKEN PARA OS WORKERS:"
    sudo cat /var/lib/rancher/k3s/server/node-token
    echo "--------------------------------------------------------"
    echo "IP INTERNO DO SERVER: $(hostname -I | awk '{print $1}')"

elif [ "$ROLE" = "worker" ]; then
    if [ -z "$SERVER_IP" ] || [ -z "$TOKEN" ]; then
        echo "❌ Erro: Para nó worker, forneça: ./01_setup_cluster_k3s.sh worker <SERVER_IP> <TOKEN>"
        exit 1
    fi

    echo "[K3s Worker] Configurando margem de segurança de Swap ZRAM (Mitigação do R01 - OOM)..."
    # Cria swap de emergência de 1GB no nó medido
    sudo fallocate -l 1G /swapfile 2>/dev/null || sudo dd if=/dev/zero of=/swapfile bs=1M count=1024
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile

    echo "[K3s Worker] Conectando Worker ao cluster em $SERVER_IP..."
    curl -sfL https://get.k3s.io | K3S_URL="https://${SERVER_IP}:6443" K3S_TOKEN="${TOKEN}" sh -
    
    echo "✔ Nó de Borda conectado com sucesso ao K3s Server!"
fi
