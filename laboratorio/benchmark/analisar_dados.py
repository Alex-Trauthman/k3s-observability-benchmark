#!/usr/bin/env python3
"""
analisar_dados.py
Realiza a análise estatística descritiva e inferencial completa (ANOVA One-Way,
Kruskal-Wallis, Tamanho do Efeito Eta-Quadrado e Testes Post-Hoc de Welch)
para as 6 ferramentas de observabilidade e os 5 Workloads do TCC 2.
"""

from project_paths import benchmark_file
import pandas as pd
import numpy as np
from scipy import stats

TELEMETRY_CSV = benchmark_file('telemetry_worker.csv')
WORKLOADS_CSV = benchmark_file('telemetry_workloads_phase2.csv')
TOOLS = ["prometheus", "victoriametrics", "netdata", "telegraf", "ebpf", "zabbix"]

def main():
    df = pd.read_csv(TELEMETRY_CSV)
    df_tools = df[df["pipeline"].isin(TOOLS)]

    print("=" * 95)
    print(f"1. DECOMPOSIÇÃO DA ANOVA ONE-WAY (6 FERRAMENTAS @ 50 RPS, N={len(df_tools)})")
    print("=" * 95)
    for col in ["mem_rss_mb", "cpu_mcpu", "net_rx_kbs", "net_tx_kbs"]:
        groups = [df_tools[df_tools["pipeline"] == t][col].values for t in TOOLS]
        all_vals = np.concatenate(groups)
        grand_mean = np.mean(all_vals)
        k = len(groups)
        N = len(all_vals)
        df_b = k - 1
        df_w = N - k
        ss_b = sum(len(g) * (np.mean(g) - grand_mean) ** 2 for g in groups)
        ss_w = sum(sum((x - np.mean(g)) ** 2 for x in g) for g in groups)
        ss_t = ss_b + ss_w
        ms_b = ss_b / df_b
        ms_w = ss_w / df_w
        f_stat = ms_b / ms_w
        p_val = stats.f_oneway(*groups)[1]
        kw_h, _ = stats.kruskal(*groups)
        eta2 = ss_b / ss_t
        print(f"{col:12s} | SQ_Entre={ss_b:11.2f} (gl={df_b}) | SQ_Dentro={ss_w:11.2f} (gl={df_w}) | F={f_stat:8.2f} | p={p_val:.4e} | eta^2={eta2:.4f} | KW_H={kw_h:7.2f}")

    print("\n" + "=" * 95)
    print("2. TESTES POST-HOC PAREADOS (TESTE t DE WELCH CONTRA O PROMETHEUS)")
    print("=" * 95)
    prom_mem = df_tools[df_tools["pipeline"] == "prometheus"]["mem_rss_mb"].values
    prom_cpu = df_tools[df_tools["pipeline"] == "prometheus"]["cpu_mcpu"].values
    for t in ["ebpf", "victoriametrics", "telegraf", "netdata", "zabbix"]:
        g_mem = df_tools[df_tools["pipeline"] == t]["mem_rss_mb"].values
        g_cpu = df_tools[df_tools["pipeline"] == t]["cpu_mcpu"].values
        t_m, p_m = stats.ttest_ind(g_mem, prom_mem, equal_var=False)
        t_c, p_c = stats.ttest_ind(g_cpu, prom_cpu, equal_var=False)
        diff_m = np.mean(g_mem) - np.mean(prom_mem)
        diff_c = np.mean(g_cpu) - np.mean(prom_cpu)
        print(f"vs {t:15s} | Delta RAM={diff_m:+7.2f} MB (t={t_m:+7.2f}, p={p_m:.4e}) | Delta CPU={diff_c:+6.2f} mCPU (t={t_c:+7.2f}, p={p_c:.4e})")

    print("\n" + "=" * 95)
    print("3. ANOVA ONE-WAY POR WORKLOAD (FASE 2)")
    print("=" * 95)
    df_wl = pd.read_csv(WORKLOADS_CSV)
    for prefix, label in [
        ("wl_idle_", "W1: Idle (0 RPS)"),
        ("wl_stress200_", "W3: Stress (200 RPS)"),
        ("wl_card1000_", "W4: High Cardinality (1000)"),
        ("wl_netem_", "W5: Rede Instavel (netem)")
    ]:
        sub = df_wl[df_wl["run_id"].str.startswith(prefix) & df_wl["pipeline"].isin(TOOLS)]
        g_m = [sub[sub["pipeline"] == t]["mem_rss_mb"].values for t in TOOLS]
        g_c = [sub[sub["pipeline"] == t]["cpu_mcpu"].values for t in TOOLS]
        f_m, p_m = stats.f_oneway(*g_m)
        f_c, p_c = stats.f_oneway(*g_c)
        print(f"{label:28s} (N={len(sub):3d}) | ANOVA RAM: F={f_m:9.2f}, p={p_m:.4e} | ANOVA CPU: F={f_c:8.2f}, p={p_c:.4e}")

if __name__ == "__main__":
    main()
