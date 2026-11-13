from __future__ import annotations

import re

from ..kb.models import Document
from ..text import ORDINALS, fold

_NUM = r"(\d+(?:\.\d+)*(?:-\d+)?)"
_SUBP_RE = re.compile(rf"(?:пп\.?|подпункт\w*|подп\.?)\s*{_NUM}")
_POINT_RE = re.compile(rf"(?<![а-я])(?:п\.?|пункт\w*)\s*{_NUM}")
_PART_RE = re.compile(rf"(?:ч\.?|част\w*)\s*{_NUM}")
_PART_WORD_RE = re.compile(r"част\w*\s+(" + "|".join(ORDINALS) + r"|" + "|".join(w[:-2] + r"\w*" for w in ORDINALS) + r")")
_PRIM_RE = re.compile(rf"примечани\w*\s*{_NUM}?")
_ABZ_RE = re.compile(rf"(?:абз\.?|абзац\w*)\s*{_NUM}")


def _ordinal(word: str) -> int | None:
    for k, v in ORDINALS.items():
        if word.startswith(k[:-2]):
            return v
    return None


def resolve_unit(doc: Document, unit_text: str) -> str | None:
    t = fold(unit_text)
    anchors = {u.anchor for u in doc.units}
    sub = _SUBP_RE.search(t)
    point = _POINT_RE.search(t.replace(sub.group(0), " ") if sub else t)
    part = _PART_RE.search(t)
    part_no = part.group(1) if part else None
    if not part_no:
        pw = _PART_WORD_RE.search(t)
        if pw:
            n = _ordinal(pw.group(1))
            part_no = str(n) if n else None
    prim = _PRIM_RE.search(t)
    candidates: list[str] = []
    if prim and "примечан" in t:
        candidates.append(f"prim-{prim.group(1)}" if prim.group(1) else "prim-1")
    if doc.unit_term == "часть":
        if part_no and point:
            candidates.append(f"ch-{part_no}-p-{point.group(1)}")
        if part_no:
            candidates.append(f"ch-{part_no}")
        if point and not part_no:
            matches = [u.anchor for u in doc.units if u.anchor.endswith(f"-p-{point.group(1)}")]
            if len(matches) == 1:
                candidates.append(matches[0])
    else:
        if point and sub:
            candidates.append(f"p-{point.group(1)}-pp-{sub.group(1)}")
        if point:
            candidates.append(f"p-{point.group(1)}")
        if sub and not point:
            matches = [u.anchor for u in doc.units if u.anchor.endswith(f"-pp-{sub.group(1)}")]
            if len(matches) == 1:
                candidates.append(matches[0])
    abz = _ABZ_RE.search(t)
    if abz:
        candidates.append(f"abz-{abz.group(1)}")
    for c in candidates:
        if c in anchors:
            return c
    for u in doc.units:
        if u.label and fold(u.label) == t.strip():
            return u.anchor
    return None
