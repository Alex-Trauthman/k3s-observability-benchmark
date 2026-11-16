#!/usr/bin/env python3
"""Publica as figuras e registra hashes das fontes, sem copiar os datasets."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
NAMES = ("memoria_rss", "cpu_mcpu", "ttd_comparativo", "workloads_fase2", "saturacao_host", "matriz_ortogonal_25pct")

def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--escrita", type=Path, default=ROOT.parent / "tcc_escrita")
    parser.add_argument("--verificar", action="store_true", help="Confere arquivos e hashes sem escrever")
    args = parser.parse_args()
    writing = args.escrita.resolve()
    images = writing / "monografia" / "imagens"
    if not (writing / "monografia" / "main.tex").is_file():
        parser.error(f"Monografia não encontrada em {writing / 'monografia'}")
    figures = [ROOT / "laboratorio/resultados/graficos" / f"grafico_{name}.{ext}" for name in NAMES for ext in ("pdf", "png")]
    missing = [str(p) for p in figures if not p.is_file()]
    if missing:
        parser.error("Figuras ausentes: " + ", ".join(missing))
    sources = sorted((ROOT / "laboratorio/benchmark").glob("*.csv"))
    sources += sorted((ROOT / "laboratorio/benchmark").glob("*.py"))
    sources += sorted((ROOT / "laboratorio/benchmark").glob("requirements*.txt"))
    sources += [ROOT / "laboratorio/logs/SHA256SUMS.txt"]
    records = [{"origem": p.relative_to(ROOT).as_posix(), "destino": (Path("monografia/imagens") / p.name).as_posix(), "sha256": sha256(p)} for p in figures]
    manifest = {
        "descricao": "Hashes das figuras publicadas e das fontes disponíveis na exportação. Não certifica execução dos gráficos ou validade científica dos dados.",
        "figuras": records,
        "fontes": [{"arquivo": p.relative_to(ROOT).as_posix(), "sha256": sha256(p)} for p in sources],
    }
    target = writing / "integracao" / "manifesto_resultados.json"
    if args.verificar:
        if not target.is_file():
            parser.error(f"Manifesto ausente: {target}")
        if json.loads(target.read_text()) != manifest:
            parser.error("Fontes ou gráficos mudaram desde a exportação. Gere e exporte novamente após revisão.")
        for rec in records:
            p = writing / rec["destino"]
            if not p.is_file() or sha256(p) != rec["sha256"]:
                parser.error(f"Figura publicada divergente: {p}")
        print("OK: 12 figuras publicadas e hashes das fontes conferem.")
        return
    images.mkdir(parents=True, exist_ok=True)
    for p in figures:
        shutil.copy2(p, images / p.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"12 figuras exportadas para {images}; fontes registradas em {target}")

if __name__ == "__main__":
    main()
