# TCC — laboratório, código e evidências experimentais

**Alex Andrade Trauthman — Sistemas de Informação / UNITINS**

Esta pasta é a fonte dos códigos, manifestos, protocolo operacional, dados de análise e resultados do estudo de observabilidade em K3s. A redação, a bibliografia e as orientações editoriais ficam em [tcc_escrita](../tcc_escrita/LEIA_ME.md). O texto atual compara Prometheus, VictoriaMetrics, Netdata, Telegraf, eBPF/Grafana Beyla e Zabbix.

```text
tcc/
├── README.md
├── docs/METODOLOGIA_E_REPRODUCAO.md
├── scripts/exportar_para_escrita.py
└── laboratorio/
    ├── 00_GUIA_PROVISIONAMENTO_GCP.md
    ├── 00_provisionar_gcp.sh
    ├── 01_setup_cluster_k3s.sh
    ├── app/                  # Aplicação Go instrumentada
    ├── k8s/                  # Aplicação e seis pipelines
    ├── k6/                   # Geração de tráfego
    ├── scripts_edge/         # Estressores e coleta de I/O
    ├── benchmark/            # Orquestradores, análises e sete CSVs
    ├── logs/                 # Manifesto SHA256SUMS.txt dos arquivos publicados
    ├── resultados/graficos/  # Figuras derivadas; gerador fica em benchmark/
    ├── infraestrutura/legado/ # OVA e instaladores de ambiente anterior
    └── arquivos_historicos/   # Pacotes técnicos preservados
```

Leia [Metodologia e reprodução](docs/METODOLOGIA_E_REPRODUCAO.md) para relacionar cada etapa aos códigos e evidências. O protocolo redigido para a monografia fica no [Capítulo 3](../tcc_escrita/monografia/capitulo3_metodologia.tex).

## Reanalisar dados existentes

Comandos executados a partir desta pasta, sem acesso à nuvem:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r laboratorio/benchmark/requirements.txt
.venv/bin/python laboratorio/benchmark/analisar_dados.py
.venv/bin/python laboratorio/benchmark/gerar_graficos.py
python3 scripts/exportar_para_escrita.py
python3 scripts/exportar_para_escrita.py --verificar
(cd laboratorio/logs && sha256sum -c SHA256SUMS.txt)
```

Os caminhos dos dados são calculados pela localização dos scripts. O gerador grava somente em `laboratorio/resultados/graficos/`; a exportação copia as figuras para a monografia e registra hashes dos dados e scripts. Os CSVs continuam tendo uma única fonte em `laboratorio/benchmark/`.

## Coleta e histórico

Provisionamento e orquestradores atuam no ambiente GCP e alguns reescrevem CSVs. Para uma nova coleta, use uma cópia do laboratório, confira projeto, zona, IPs e executável `gcloud` nos scripts e preserve as evidências anteriores.

Os logs brutos e os CSVs originais integram o acervo local de evidências. Os CSVs versionados registram tempos relativos. A telemetria usa o início de cada execução ou condição de disco como origem. O benchmark usa a injeção da falha como origem de T0 e T1.
