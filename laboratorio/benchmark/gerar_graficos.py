#!/usr/bin/env python3
"""
gerar_graficos.py
Gera gráficos acadêmicos em PDF/PNG de alta resolução para os Capítulos 4 e 5 da Monografia,
abrangendo Linha de Base (B0) e todas as 6 ferramentas:
Prometheus, VictoriaMetrics, Netdata, Telegraf, eBPF (Grafana Beyla) e Zabbix,
além do painel comparativo de múltiplos Workloads (Idle, Stress 200 RPS, Alta Cardinalidade e Rede Instável).
"""

from project_paths import benchmark_file
import os
import argparse
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from project_paths import GRAPH_DIR
import pandas as pd
import numpy as np

OUTPUT_DIR = str(GRAPH_DIR)
TELEMETRY_CSV = benchmark_file('telemetry_worker.csv')
BENCHMARK_CSV = benchmark_file('dataset_benchmark_tcc2.csv')
WORKLOADS_TELEM_CSV = benchmark_file('telemetry_workloads_phase2.csv')
WORKLOADS_SUM_CSV = benchmark_file('dataset_workloads_phase2.csv')
SATURATION_CSV = benchmark_file('dataset_host_saturation.csv')
ORTHO_CSV = benchmark_file('dataset_orthogonal_matrix_25pct.csv')


plt.rcParams.update({
    "font.family": "serif",
    "font.size": 10.0,
    "axes.labelsize": 11.0,
    "axes.titlesize": 12.0,
    "xtick.labelsize": 9.5,
    "ytick.labelsize": 9.5,
    "figure.titlesize": 12.5,
    "grid.alpha": 0.4,
    "grid.linestyle": "--"
})

ORDER = ["baseline", "ebpf", "victoriametrics", "prometheus", "netdata", "telegraf", "zabbix"]
LABELS = [
    "Baseline\n(B0)",
    "eBPF\n(Beyla)",
    "Victoria\nMetrics",
    "Prometheus\n(Padrão)",
    "Netdata\n(Agente C)",
    "Telegraf\n(Stream)",
    "Zabbix\n(Proxy+Ag2)"
]
COLORS = ["#7f7f7f", "#ff7f0e", "#2ca02c", "#1f77b4", "#d62728", "#9467bd", "#8c564b"]

def plot_memory_comparison(df):
    plt.figure(figsize=(9.2, 4.8))
    data = [df[df["pipeline"] == p]["mem_rss_mb"] for p in ORDER]

    box = plt.boxplot(data, tick_labels=LABELS, patch_artist=True, widths=0.5,
                      medianprops=dict(color="black", linewidth=1.5))

    for patch, color in zip(box['boxes'], COLORS):
        patch.set_facecolor(color)
        patch.set_alpha(0.78)

    plt.ylabel("Memória Residente - RSS (MB)")
    plt.title("Comparação da Pegada de Memória das 6 Ferramentas no Nó de Borda (1 GB RAM)")
    plt.grid(True, axis="y")
    plt.tight_layout()

    plt.savefig(os.path.join(OUTPUT_DIR, "grafico_memoria_rss.pdf"))
    plt.savefig(os.path.join(OUTPUT_DIR, "grafico_memoria_rss.png"), dpi=300)
    plt.close()
    print("✔ Gráfico de Memória (6 ferramentas) gerado.")

def plot_cpu_comparison(df):
    plt.figure(figsize=(9.2, 4.8))
    data = [df[df["pipeline"] == p]["cpu_mcpu"] for p in ORDER]

    box = plt.boxplot(data, tick_labels=LABELS, patch_artist=True, widths=0.5,
                      medianprops=dict(color="black", linewidth=1.5))

    for patch, color in zip(box['boxes'], COLORS):
        patch.set_facecolor(color)
        patch.set_alpha(0.78)

    plt.ylabel("Consumo de Processador (mCPU)")
    plt.title("Sobrecarga de CPU das 6 Ferramentas no Nó de Borda Medido (50 RPS)")
    plt.grid(True, axis="y")
    plt.tight_layout()

    plt.savefig(os.path.join(OUTPUT_DIR, "grafico_cpu_mcpu.pdf"))
    plt.savefig(os.path.join(OUTPUT_DIR, "grafico_cpu_mcpu.png"), dpi=300)
    plt.close()
    print("✔ Gráfico de CPU (6 ferramentas) gerado.")

def plot_ttd_comparison(bdf, wdf):
    plt.figure(figsize=(9.5, 5.2))

    tools = ["ebpf", "zabbix", "telegraf", "netdata", "prometheus", "victoriametrics"]
    tool_names = ["eBPF\n(Beyla)", "Zabbix\n(Proxy+Ag2)", "Telegraf\n(Stream)", "Netdata\n(Agente C)", "Prometheus\n(Padrão)", "VictoriaMetrics\n(Cluster-lite)"]

    c2_vals, c3_vals, netem_vals = [], [], []
    for t in tools:
        r2 = bdf[(bdf["pipeline"] == t) & (bdf["scenario"] == "C2_atraso_300ms")]
        r3 = bdf[(bdf["pipeline"] == t) & (bdf["scenario"] == "C3_erro_20pct")]
        rw = wdf[(wdf["pipeline"] == t) & (wdf["workload"] == "W5_UnstableNet_12pctLoss")]
        v2 = float(r2["ttd_seconds"].iloc[-1]) if not r2.empty and str(r2["ttd_seconds"].iloc[-1]) not in ("N/A", "TIMEOUT") else 0.0
        v3 = float(r3["ttd_seconds"].iloc[-1]) if not r3.empty and str(r3["ttd_seconds"].iloc[-1]) not in ("N/A", "TIMEOUT") else 0.0
        vw = float(rw["ttd_unstable_s"].iloc[-1]) if not rw.empty and float(rw["ttd_unstable_s"].iloc[-1]) > 0 else 0.0
        c2_vals.append(v2)
        c3_vals.append(v3)
        netem_vals.append(vw)

    x = np.arange(len(tools))
    width = 0.26

    plt.bar(x - width, c2_vals, width=width, label="C2: Latência Severa (+300ms | Rede Estável)", color="#1f77b4", alpha=0.88)
    plt.bar(x, c3_vals, width=width, label="C3: Taxa de Erro (20% HTTP 500 | Rede Estável)", color="#d62728", alpha=0.88)
    plt.bar(x + width, netem_vals, width=width, label="W5: Conexão Instável (12% Perda + 80ms Jitter)", color="#ff7f0e", alpha=0.88)

    for i in range(len(tools)):
        plt.text(x[i] - width, c2_vals[i] + 1.2, f"{c2_vals[i]:.1f}s", ha="center", fontsize=8.2, fontweight="bold")
        plt.text(x[i], c3_vals[i] + 1.2, f"{c3_vals[i]:.1f}s", ha="center", fontsize=8.2, fontweight="bold")
        plt.text(x[i] + width, netem_vals[i] + 1.2, f"{netem_vals[i]:.1f}s", ha="center", fontsize=8.2, fontweight="bold")

    plt.xticks(x, tool_names)
    plt.ylabel("Tempo até Detecção - TTD (segundos)")
    plt.title("Eficácia Temporal de Detecção (TTD) sob Rede Estável vs. Conexão Instável (tc netem)")
    plt.ylim(0, max(max(c2_vals), max(c3_vals), max(netem_vals)) + 14)
    plt.legend(loc="upper left")
    plt.grid(True, axis="y")
    plt.tight_layout()

    plt.savefig(os.path.join(OUTPUT_DIR, "grafico_ttd_comparativo.pdf"))
    plt.savefig(os.path.join(OUTPUT_DIR, "grafico_ttd_comparativo.png"), dpi=300)
    plt.close()
    print("✔ Gráfico de TTD (Estável + Rede Instável) gerado.")

def plot_workloads_comparison(df_std, df_wl, wdf):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.5, 4.8))

    tools = ["ebpf", "victoriametrics", "prometheus", "netdata", "telegraf", "zabbix"]
    tnames = ["eBPF", "Victoria\nMetrics", "Prometheus", "Netdata", "Telegraf", "Zabbix"]

    mem_std, mem_card, mem_netem = [], [], []
    for t in tools:
        m_s = df_std[df_std["pipeline"] == t]["mem_rss_mb"].mean()
        m_c = df_wl[df_wl["run_id"] == f"wl_card1000_{t}"]["mem_rss_mb"].mean()
        m_n = df_wl[df_wl["run_id"] == f"wl_netem_{t}"]["mem_rss_mb"].mean()
        mem_std.append(m_s)
        mem_card.append(m_c)
        mem_netem.append(m_n)

    x = np.arange(len(tools))
    w = 0.26
    ax1.bar(x - w, mem_std, width=w, label="W2: Carga Padrão (50 RPS)", color="#1f77b4", alpha=0.85)
    ax1.bar(x, mem_card, width=w, label="W4: Alta Cardinalidade (1.000 Séries)", color="#2ca02c", alpha=0.85)
    ax1.bar(x + w, mem_netem, width=w, label="W5: Rede Instável (Buffer Retries)", color="#d62728", alpha=0.85)
    ax1.set_xticks(x)
    ax1.set_xticklabels(tnames)
    ax1.set_ylabel("Memória RSS Média (MB)")
    ax1.set_title("(a) Comportamento de RAM por Workload")
    ax1.legend(fontsize=8.5, loc="upper left")
    ax1.grid(True, axis="y")

    # Painel B: Impacto na Latência p95 da Aplicação a 200 RPS (Efeito Observador)
    all_t = ["baseline", "prometheus", "netdata", "telegraf", "victoriametrics", "zabbix", "ebpf"]
    all_names = ["Baseline\n(B0)", "Prom.", "Netdata", "Telegraf", "Victoria\nMetrics", "Zabbix", "eBPF\n(Beyla)"]
    p95_lat = []
    for t in all_t:
        r = wdf[(wdf["pipeline"] == t) & (wdf["workload"] == "W3_Stress_200RPS")]
        p95_lat.append(float(r["k6_p95_ms"].iloc[-1]) if not r.empty else 0.0)

    bars = ax2.bar(np.arange(len(all_t)), p95_lat, width=0.52,
                   color=["#7f7f7f", "#1f77b4", "#d62728", "#9467bd", "#2ca02c", "#8c564b", "#ff7f0e"], alpha=0.85)
    for idx, v in enumerate(p95_lat):
        ax2.text(idx, v + 0.03, f"{v:.2f}ms", ha="center", fontsize=8.5, fontweight="bold")
    ax2.set_xticks(np.arange(len(all_t)))
    ax2.set_xticklabels(all_names)
    ax2.set_ylim(6.0, 7.15)
    ax2.set_ylabel("Latência HTTP p95 da Aplicação (ms)")
    ax2.set_title("(b) Efeito Observador na Aplicação sob Estresse (200 RPS)")
    ax2.grid(True, axis="y")

    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, "grafico_workloads_fase2.pdf"))
    plt.savefig(os.path.join(OUTPUT_DIR, "grafico_workloads_fase2.png"), dpi=300)
    plt.close()
    print("✔ Gráfico Multi-Workload (Fase 2) gerado.")

def plot_host_saturation(sdf):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.8, 4.8))
    tools = ["telegraf", "ebpf", "prometheus", "netdata", "victoriametrics", "zabbix"]
    tnames = ["Telegraf", "eBPF\n(Beyla)", "Prometheus", "Netdata", "Victoria\nMetrics", "Zabbix\n(Full)"]
    modes = [
        ("S1_CPU_Saturation", "S1: Saturação CPU (100%)", "#1f77b4"),
        ("S2_RAM_Pressure", "S2: Pressão RAM (Swap)", "#ff7f0e"),
        ("S3_Disk_IO_Saturation", "S3: Saturação Disco (fsync)", "#d62728"),
    ]
    x = np.arange(len(tools))
    w = 0.25

    for idx, (m_key, m_label, m_col) in enumerate(modes):
        deliv_vals = []
        ttd_vals = []
        for t in tools:
            row = sdf[(sdf["pipeline"] == t) & (sdf["stress_mode"] == m_key)]
            d_val = float(row["delivery_rate_pct"].iloc[-1]) if not row.empty else 0.0
            t_val = float(row["ttd_seconds"].iloc[-1]) if not row.empty and float(row["ttd_seconds"].iloc[-1]) > 0 else 0.0
            deliv_vals.append(d_val)
            ttd_vals.append(t_val)
        offset = (idx - 1) * w
        ax1.bar(x + offset, deliv_vals, width=w, label=m_label, color=m_col, alpha=0.86)
        ax2.bar(x + offset, ttd_vals, width=w, label=m_label, color=m_col, alpha=0.86)

    ax1.set_xticks(x)
    ax1.set_xticklabels(tnames)
    ax1.set_ylim(0, 115)
    ax1.set_ylabel("Taxa de Entrega da API de Alerta (%)")
    ax1.set_title("(a) Disponibilidade de Entrega no Limite do Host")
    ax1.legend(fontsize=8.5, loc="lower left")
    ax1.grid(True, axis="y")

    ax2.set_xticks(x)
    ax2.set_xticklabels(tnames)
    ax2.set_ylim(0, 80)
    ax2.set_ylabel("Tempo até Detecção - TTD (s) [0 = Colapso/Timeout]")
    ax2.set_title("(b) Degradação do TTD sob Saturação de CPU, RAM e Disco")
    ax2.legend(fontsize=8.5, loc="upper left")
    ax2.grid(True, axis="y")

    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, "grafico_saturacao_host.pdf"))
    plt.savefig(os.path.join(OUTPUT_DIR, "grafico_saturacao_host.png"), dpi=300)
    plt.close()
    print("✔ Gráfico de Saturação do Host gerado.")

def plot_orthogonal_matrix_25pct(odf):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.2, 5.2), gridspec_kw={"width_ratios": [1.0, 1.55]})

    # Painel A: Degradação média de TTD nos degraus de 25%, 50%, 75%, 100% (média dos 15 grupos por degrau)
    steps_num = [25, 50, 75, 100]
    steps_lbl = ["25%", "50%", "75%", "100%"]

    for tool, label, color, marker in [
        ("ebpf", "eBPF / Beyla (60/60 detecções)", "#ff7f0e", "o"),
        ("zabbix", "Zabbix Full (58/60 detecções)", "#8c564b", "D"),
        ("telegraf", "Telegraf Stream (56/60 detecções)", "#9467bd", "s"),
        ("netdata", "Netdata Agente C (57/60 detecções)", "#d62728", "v"),
        ("prometheus", "Prometheus Padrão (59/60 detecções)", "#1f77b4", "^"),
    ]:
        vals = []
        for s in steps_num:
            sub = odf[(odf["pipeline"] == tool) & (odf["profile"].str.endswith(f"_{s}")) & (odf["ttd_seconds"] > 0)]
            vals.append(sub["ttd_seconds"].mean() if not sub.empty else np.nan)
        ax1.plot(steps_lbl, vals, marker=marker, linewidth=2.0, label=label, color=color)
        for i, v in enumerate(vals):
            if not np.isnan(v):
                ax1.annotate(f"{v:.1f}s", (steps_lbl[i], v), textcoords="offset points", xytext=(0, 5), ha="center", fontsize=7.8)

    ax1.set_xlabel("Degrau de Carga Aplicada nos 15 Grupos (%)")
    ax1.set_ylabel("TTD Médio nos 15 Grupos (segundos)")
    ax1.set_title("(a) TTD Médio por Degrau (25% a 100%) nas 15 Combinações")
    ax1.set_ylim(16, 46)
    ax1.legend(fontsize=8.0, loc="upper left")
    ax1.grid(True)

    # Painel B: Heatmap dos 15 Grupos Combinatórios (Taxa de Entrega no topo 100% de cada grupo)
    sel_profs = [
        "CPU_100", "RAM_100", "DISK_100", "NET_100",
        "CPU_RAM_100", "CPU_DISK_100", "CPU_NET_100", "RAM_DISK_100", "RAM_NET_100", "DISK_NET_100",
        "HOST3_100", "CPU_RAM_NET_100", "CPU_DISK_NET_100", "RAM_DISK_NET_100",
        "ALL4_100"
    ]
    sel_labels = [
        "{1} CPU", "{2} RAM", "{3} DISK", "{4} NET",
        "{1,2} C+R", "{1,3} C+D", "{1,4} C+N", "{2,3} R+D", "{2,4} R+N", "{3,4} D+N",
        "{1,2,3} C+R+D", "{1,2,4} C+R+N", "{1,3,4} C+D+N", "{2,3,4} R+D+N",
        "{1,2,3,4} ALL4"
    ]
    tools_order = ["ebpf", "zabbix", "prometheus", "victoriametrics", "netdata", "telegraf"]
    tools_names = ["eBPF (Beyla)", "Zabbix", "Prometheus", "VictoriaMetrics", "Netdata", "Telegraf"]

    mat = np.zeros((len(tools_order), len(sel_profs)))
    for i, t in enumerate(tools_order):
        for j, p in enumerate(sel_profs):
            r = odf[(odf["pipeline"] == t) & (odf["profile"] == p)]
            mat[i, j] = float(r["delivery_rate_pct"].iloc[-1]) if not r.empty else 0.0

    im = ax2.imshow(mat, cmap="RdYlGn", aspect="auto", vmin=20, vmax=100)
    ax2.set_xticks(np.arange(len(sel_profs)))
    ax2.set_xticklabels(sel_labels, rotation=40, ha="right", fontsize=8.0)
    ax2.set_yticks(np.arange(len(tools_order)))
    ax2.set_yticklabels(tools_names, fontsize=9.0)
    ax2.set_title("(b) Mapa de Calor: Taxa de Entrega (%) nos 15 Grupos Combinatórios")

    for i in range(len(tools_order)):
        for j in range(len(sel_profs)):
            val = mat[i, j]
            txt_color = "white" if val < 55 else "black"
            ax2.text(j, i, f"{val:.0f}%", ha="center", va="center", color=txt_color, fontsize=7.2, fontweight="bold")

    plt.colorbar(im, ax=ax2, fraction=0.046, pad=0.04, label="Entrega HTTP (%)")
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, "grafico_matriz_ortogonal_25pct.pdf"))
    plt.savefig(os.path.join(OUTPUT_DIR, "grafico_matriz_ortogonal_25pct.png"), dpi=300)
    plt.close()
    print("✔ Gráfico da Matriz Ortogonal Completa (360 células / 15 grupos) gerado.")

def main():
    global OUTPUT_DIR
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default=str(GRAPH_DIR), help="Pasta para os gráficos derivados dos CSVs")
    args = parser.parse_args()
    OUTPUT_DIR = args.output_dir
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    df = pd.read_csv(TELEMETRY_CSV)
    bdf = pd.read_csv(BENCHMARK_CSV)
    df_wl = pd.read_csv(WORKLOADS_TELEM_CSV)
    wdf = pd.read_csv(WORKLOADS_SUM_CSV)
    sdf = pd.read_csv(SATURATION_CSV)
    odf = pd.read_csv(ORTHO_CSV)
    plot_memory_comparison(df)
    plot_cpu_comparison(df)
    plot_ttd_comparison(bdf, wdf)
    plot_workloads_comparison(df, df_wl, wdf)
    plot_host_saturation(sdf)
    plot_orthogonal_matrix_25pct(odf)
    print("🎉 Todos os 6 gráficos gerados com sucesso!")

if __name__ == "__main__":
    main()
