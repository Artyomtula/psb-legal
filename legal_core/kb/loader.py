from __future__ import annotations

import hashlib
import io
import sys
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

SUPPORTED = {".txt", ".md", ".docx"}
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


@dataclass(frozen=True)
class RawDoc:
    name: str
    text: str


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _docx_text(raw: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(raw)) as doc:
        root = ET.fromstring(doc.read("word/document.xml"))
    lines = []
    for para in root.iter(_W + "p"):
        parts = []
        for node in para.iter():
            if node.tag == _W + "t" and node.text:
                parts.append(node.text)
            elif node.tag == _W + "tab":
                parts.append("\t")
            elif node.tag in (_W + "br", _W + "cr"):
                parts.append("\n")
        lines.append("".join(parts))
    return "\n".join(lines)


def _read(name: str, raw: bytes) -> str | None:
    suffix = Path(name).suffix.lower()
    try:
        return _docx_text(raw) if suffix == ".docx" else _decode(raw)
    except (KeyError, ValueError, zipfile.BadZipFile, ET.ParseError) as exc:
        print(f"Пропущен нечитаемый документ {name}: {type(exc).__name__}", file=sys.stderr)
        return None


def _accept(name: str) -> bool:
    base = Path(name).name
    return (
        not name.endswith("/")
        and not name.startswith("__MACOSX/")
        and not base.startswith((".", "~$"))
        and Path(name).suffix.lower() in SUPPORTED
    )


def _zip_name(info: zipfile.ZipInfo) -> str:
    if info.flag_bits & 0x800:
        return info.filename
    raw = info.filename.encode("cp437", errors="replace")
    for enc in ("utf-8", "cp866"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return info.filename


def load_kb(path: str | Path) -> list[RawDoc]:
    path = Path(path)
    docs: list[RawDoc] = []
    if path.is_dir():
        for file in sorted(path.rglob("*")):
            rel = file.relative_to(path).as_posix()
            if file.is_file() and _accept(rel):
                text = _read(rel, file.read_bytes())
                if text and text.strip():
                    docs.append(RawDoc(unicodedata.normalize("NFC", rel), text))
        return docs
    with zipfile.ZipFile(path) as archive:
        for info in sorted(archive.infolist(), key=lambda i: i.filename):
            name = _zip_name(info)
            if not _accept(name):
                continue
            text = _read(name, archive.read(info))
            if text and text.strip():
                docs.append(RawDoc(unicodedata.normalize("NFC", name), text))
    return docs


def kb_fingerprint(docs: list[RawDoc]) -> str:
    h = hashlib.sha256()
    for d in sorted(docs, key=lambda d: d.name):
        h.update(d.name.encode("utf-8"))
        h.update(b"\0")
        h.update(d.text.encode("utf-8"))
        h.update(b"\1")
    return h.hexdigest()[:16]
