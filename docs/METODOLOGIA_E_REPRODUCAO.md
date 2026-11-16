# Metodologia operacional e rastreabilidade

Este guia descreve onde encontrar a implementação. A redação acadêmica do método está em [capitulo3_metodologia.tex](../../tcc_escrita/monografia/capitulo3_metodologia.tex). Para nomes e ordem dos objetivos, consulte o [Capítulo 1](../../tcc_escrita/monografia/capitulo1_introducao.tex); documentos antigos de planejamento usam outra numeração.

| Etapa / objetivo no texto atual | Implementação | Dados / evidências |
| --- | --- | --- |
| Ambiente e isolamento | `laboratorio/00_provisionar_gcp.sh`, `01_setup_cluster_k3s.sh`, `app/`, `k8s/`, `k6/` | Registro de ambiente no acervo local |
| OE1: linha de base | `benchmark/master_orchestrator.py`, `run_additional_pipelines.py` | `telemetry_worker.csv`, `dataset_benchmark_tcc2.csv`|
| OE2: sobrecarga | `benchmark/collect_telemetry.sh`, `collect_disk_and_zabbix_lite.py`, `analisar_dados.py` | `telemetry_worker.csv`, `disk_and_zabbix_telemetry.csv`|
| OE3: TTD e workloads | `benchmark/run_additional_pipelines.py`, `run_phase2_complete.py`, `collect_phase2_telemetry_fast.py` | `dataset_benchmark_tcc2.csv`, `dataset_workloads_phase2.csv`, `telemetry_workloads_phase2.csv`|
| Saturação isolada do host | `benchmark/run_host_saturation_stress.py`, `scripts_edge/stress_ctl.sh` | `dataset_host_saturation.csv`|
| OE4: matriz combinada | `benchmark/run_orthogonal_matrix_25pct.py`, `run_remaining_44_profiles.py`, `fix_k6_expired_cells.py`, `scripts_edge/orthogonal_stress_ctl.sh` | `dataset_orthogonal_matrix_25pct.csv`|
| Análise e publicação | `benchmark/analisar_dados.py`, `gerar_graficos.py`, `scripts/exportar_para_escrita.py` | `laboratorio/resultados/graficos/` → `../tcc_escrita/monografia/imagens/` |

Nesta tabela, `benchmark/` e `scripts_edge/` são subpastas de `laboratorio/`. Os caminhos são um mapa de implementação, não uma certificação dos resultados. Os orquestradores iniciais (`run_benchmark.py`, `master_orchestrator.py`) cobrem duas ferramentas; as extensões completam as demais.

## Conferência das evidências

A partir da raiz `tcc`:

```bash
(cd laboratorio/logs && sha256sum -c SHA256SUMS.txt)
python3 scripts/exportar_para_escrita.py --verificar
```

O manifesto de hashes identifica os arquivos versionados. Logs e coletas originais integram o acervo local. O manifesto da escrita registra as figuras publicadas e hashes das fontes disponíveis na exportação; ele detecta alterações, mas não comprova causalidade nem execução do gerador.

## Ambiente de análise

Dependências em `laboratorio/benchmark/requirements.txt`. A geração usa backend Matplotlib `Agg`, sem interface gráfica. Para conferir sem substituir as figuras preservadas:

```bash
.venv/bin/python laboratorio/benchmark/gerar_graficos.py --output-dir /tmp/tcc-graficos-conferencia
```

Após revisar novas figuras, gere no destino padrão e execute a exportação. Evite editar manualmente gráficos em `tcc_escrita/monografia/imagens/`; mantenha o logotipo `unitins.png` como recurso editorial independente.

## Limites da reorganização

Não houve alteração de hipóteses, métricas, tabelas ou capítulos. As pendências de coerência dos guias e do texto estão em [PENDENCIAS_EDITORIAIS.md](../../tcc_escrita/PENDENCIAS_EDITORIAIS.md). Recursos legados e pacotes arquivados não integram automaticamente o ambiente GCP documentado.
