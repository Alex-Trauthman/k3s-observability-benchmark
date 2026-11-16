#!/usr/bin/env bash
set -e

ZONE="southamerica-east1-a"

echo "=========================================================="
echo " Provisionamento Automatizado do Laboratório GCP - TCC 2 "
echo " Zona: $ZONE"
echo "=========================================================="

# 1. Configura a zona padrão e habilita Compute Engine
gcloud config set compute/zone "$ZONE"
echo "✔ Habilitando Compute Engine API (se necessário)..."
gcloud services enable compute.googleapis.com

# 2. Criação das 3 VMs
echo "✔ Verificando e criando instâncias..."

if ! gcloud compute instances describe k3s-control-plane --zone="$ZONE" &>/dev/null; then
    echo "Criando k3s-control-plane (e2-small, 2 GB RAM)..."
    gcloud compute instances create k3s-control-plane \
        --zone="$ZONE" \
        --machine-type=e2-small \
        --image-family=debian-12 \
        --image-project=debian-cloud \
        --boot-disk-size=20GB \
        --tags=k3s-cluster
else
    echo "k3s-control-plane já existe."
fi

if ! gcloud compute instances describe k3s-worker-edge --zone="$ZONE" &>/dev/null; then
    echo "Criando k3s-worker-edge (e2-micro, 1 GB RAM estrito)..."
    gcloud compute instances create k3s-worker-edge \
        --zone="$ZONE" \
        --machine-type=e2-micro \
        --image-family=debian-12 \
        --image-project=debian-cloud \
        --boot-disk-size=15GB \
        --tags=k3s-cluster
else
    echo "k3s-worker-edge já existe."
fi

if ! gcloud compute instances describe k6-runner --zone="$ZONE" &>/dev/null; then
    echo "Criando k6-runner (e2-small, 2 GB RAM)..."
    gcloud compute instances create k6-runner \
        --zone="$ZONE" \
        --machine-type=e2-small \
        --image-family=debian-12 \
        --image-project=debian-cloud \
        --boot-disk-size=15GB \
        --tags=k3s-cluster
else
    echo "k6-runner já existe."
fi

# 3. Regras de Firewall
echo "✔ Configurando Regras de Firewall..."

if ! gcloud compute firewall-rules describe allow-internal-k3s &>/dev/null; then
    gcloud compute firewall-rules create allow-internal-k3s \
        --action=ALLOW \
        --rules=all \
        --source-tags=k3s-cluster \
        --target-tags=k3s-cluster
else
    echo "allow-internal-k3s já existe."
fi

if ! gcloud compute firewall-rules describe allow-k3s-nodeports &>/dev/null; then
    gcloud compute firewall-rules create allow-k3s-nodeports \
        --action=ALLOW \
        --rules=tcp:30000-32767,tcp:6443 \
        --source-ranges=0.0.0.0/0 \
        --target-tags=k3s-cluster
else
    echo "allow-k3s-nodeports já existe."
fi

echo "=========================================================="
echo "✔ Infraestrutura provisionada com sucesso!"
echo "Instâncias ativas:"
echo "=========================================================="
gcloud compute instances list --filter="tags.items=k3s-cluster"
