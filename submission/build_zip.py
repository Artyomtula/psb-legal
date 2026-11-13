from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from legal_core.config import Settings, load_env_file  # noqa: E402
from legal_core.index.store import KnowledgeIndex  # noqa: E402
from legal_core.kb.loader import load_kb  # noqa: E402
from legal_core.providers.embed import make_embedder  # noqa: E402

load_env_file(ROOT / ".env.dev")

RUNTIME_REQUIREMENTS = """httpx==0.28.1
numpy==2.2.6
rapidfuzz==3.14.1
pymorphy3==2.0.6
pymorphy3-dicts-ru==2.4.417150.4580142
dawg2-python==0.9.0
"""
FORBIDDEN = ("api-", ".env", ".key")


async def prebuild_index(kb: Path, out: Path) -> None:
    s = Settings.from_env()
    embedder = make_embedder(s)
    idx = await KnowledgeIndex.build(load_kb(kb), embedder)
    if not idx.dense_ready:
        print("ВНИМАНИЕ: индекс без векторов — на платформе векторы будут посчитаны через API", file=sys.stderr)
    idx.save(out)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--kb", default=str(ROOT / "data/kb.zip"))
    p.add_argument("--out", default=str(ROOT / "dist/submission.zip"))
    p.add_argument("--no-index", action="store_true")
    args = p.parse_args()
    build = ROOT / "build/submission"
    if build.exists():
        shutil.rmtree(build)
    build.mkdir(parents=True)
    shutil.copy(ROOT / "submission/solution.py", build / "solution.py")
    shutil.copytree(ROOT / "legal_core", build / "legal_core", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (build / "requirements.txt").write_text(RUNTIME_REQUIREMENTS, encoding="utf-8")
    if not args.no_index:
        asyncio.run(prebuild_index(Path(args.kb), build / "artifacts/index"))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(build.rglob("*")):
            rel = f.relative_to(build).as_posix()
            if f.is_file():
                if any(part.startswith(FORBIDDEN) for part in rel.split("/")):
                    raise SystemExit(f"В сабмит попал запрещённый файл: {rel}")
                z.write(f, rel)
    size = out.stat().st_size / 1024
    print(f"{out}: {size:.0f} КБ")


if __name__ == "__main__":
    main()
