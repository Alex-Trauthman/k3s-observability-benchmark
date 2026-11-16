# Guia Passo a Passo: Provisionamento do Test-Bed na GCP

> **Instruções operacionais para criar a infraestrutura na nuvem via Google Cloud Shell.**  
> Você não precisa instalar ferramentas pesadas na sua máquina local. O **Google Cloud Shell** é um terminal Linux gratuito no navegador com `gcloud`, `kubectl` e `git` já configurados.

---

## 1. Acessando o Google Cloud Shell

1. Acesse o console da GCP: [console.cloud.google.com](https://console.cloud.google.com/).
2. No canto superior direito, clique no ícone **Ativar o Cloud Shell** (`>_`).
3. Uma linha de comando abrirá no rodapé do seu navegador.

---

## 2. Passo 1 — Criar as 3 VMs na Região de São Paulo (`southamerica-east1-a`)

Execute os comandos abaixo diretamente no Cloud Shell para criar as três máquinas virtuais com Debian 12:

```bash
# Define a zona padrão de São Paulo
gcloud config set compute/zone southamerica-east1-a

# 1. Nó 1: Control Plane (K3s Server - e2-small com 2 GB RAM)
gcloud compute instances create k3s-control-plane \
    --machine-type=e2-small \
    --image-family=debian-12 \
    --image-project=debian-cloud \
    --boot-disk-size=20GB \
    --tags=k3s-cluster

# 2. Nó 2: Worker Node Medido (Nó de Borda - e2-micro com 1 GB RAM estrito)
gcloud compute instances create k3s-worker-edge \
    --machine-type=e2-micro \
    --image-family=debian-12 \
    --image-project=debian-cloud \
    --boot-disk-size=15GB \
    --tags=k3s-cluster

# 3. Nó 3: Gerador K6 e Runner de Testes (Isolado do Nó Medido)
gcloud compute instances create k6-runner \
    --machine-type=e2-small \
    --image-family=debian-12 \
    --image-project=debian-cloud \
    --boot-disk-size=15GB \
    --tags=k3s-cluster
```

---

## 3. Passo 2 — Criar as Regras de Firewall (Comunicação do Cluster)

Permitir tráfego interno entre as VMs e liberar as portas dos serviços de teste (NodePort 30000–32767):

```bash
# Permite comunicação interna irrestrita entre as instâncias da mesma rede VPC
gcloud compute firewall-rules create allow-internal-k3s \
    --action=ALLOW \
    --rules=all \
    --source-tags=k3s-cluster \
    --target-tags=k3s-cluster

# Permite acesso às portas NodePort do K3s (API: 30080, Prometheus: 30090/30093, Victoria: 30091/30094)
gcloud compute firewall-rules create allow-k3s-nodeports \
    --action=ALLOW \
    --rules=tcp:30000-32767,tcp:6443 \
    --source-ranges=0.0.0.0/0 \
    --target-tags=k3s-cluster
```

---

## 4. Passo 3 — Instalação do Cluster K3s

### A. No Nó 1 (`k3s-control-plane`):
Conecte via SSH pelo Cloud Shell:
```bash
gcloud compute ssh k3s-control-plane
```
Dentro da VM, instale o K3s Server com isolamento do master:
```bash
# Instala K3s Server sem Traefik e com taint para não receber contêineres comuns
curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="server --disable traefik --disable servicelb --node-taint node-role.kubernetes.io/master:NoSchedule" sh -

# Guarda o token do cluster
sudo cat /var/lib/rancher/k3s/server/node-token

# Pega o IP interno do nó
hostname -I | awk '{print $1}'
```
*(Guarde o Token e o IP Interno exibidos).* Saia digitando `exit`.

---

### B. No Nó 2 (`k3s-worker-edge`):
Conecte via SSH:
```bash
gcloud compute ssh k3s-worker-edge
```
Dentro da VM, configure o swap de emergência (mitigação de OOM) e conecte o agente:
```bash
# Cria 1 GB de swap como margem contra OOM Killer
sudo fallocate -l 1G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile

# Instala ferramentas de telemetria
sudo apt-get update && sudo apt-get install -y chrony sysstat
sudo systemctl enable --now chrony

# Conecta ao K3s Server (substitua SERVER_IP e TOKEN)
curl -sfL https://get.k3s.io | K3S_URL="https://<IP_DO_CONTROL_PLANE>:6443" K3S_TOKEN="<TOKEN_DO_CONTROL_PLANE>" sh -
```
Saia digitando `exit`.

---

## 5. Passo 4 — No `k6-runner` (Execução do Benchmark)

Conecte no nó do K6:
```bash
gcloud compute ssh k6-runner
```
Instale o Grafana K6:
```bash
sudo gpg -k
sudo gpg --no-default-keyring --keyring /usr/share/keyrings/k6-archive-keyring.gpg --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys C5AD17C747E3415A3642D57D77C6C491D6AC1D69
echo "deb [signed-by=/usr/share/keyrings/k6-archive-keyring.gpg] https://dl.k6.io/deb stable main" | sudo tee /etc/apt/sources.list.d/k6.list
sudo apt-get update && sudo apt-get install -y k6 python3
```

Agora você tem o ambiente pronto para disparar os scripts de teste do Experimento 1, 2 e 3!
