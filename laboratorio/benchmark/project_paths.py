"""Caminhos locais independentes do diretório em que o comando foi iniciado."""
from pathlib import Path

BENCHMARK_DIR = Path(__file__).resolve().parent
LAB_DIR = BENCHMARK_DIR.parent
PROJECT_DIR = LAB_DIR.parent
GRAPH_DIR = LAB_DIR / "resultados" / "graficos"

def benchmark_file(name):
    return str(BENCHMARK_DIR / name)
