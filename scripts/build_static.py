"""Publica os assets na CDN da Vercel, preservando as URLs /static/ locais."""
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]


def main():
    target = ROOT / "public" / "static"
    # Este diretório é gerado; limpar evita arquivos obsoletos em builds locais.
    if target.exists():
        shutil.rmtree(target)
    for directory in ("css", "js", "images"):
        shutil.copytree(
            ROOT / "static" / directory,
            target / directory,
            ignore=shutil.ignore_patterns(".*", "__pycache__", "*.pyc"),
        )
    print("Assets publicados em public/static (CSS, JavaScript e imagens).")


if __name__ == "__main__":
    main()
