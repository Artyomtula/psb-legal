from __future__ import annotations

import re
from datetime import date

from ..text import fold
from .fragments import Fragment
from .numbers import NUM_RE, known_numbers, num_forms
from .render import BARE_SID_RE

QUOTE_RE = re.compile(r"«([^«»]{12,})»")
LAWREF_RE = re.compile(r"(\d{1,4}-ФЗ|(?:ст\.|статья|статьи|статье)\s*\d+(?:\.\d+)?)", re.I)
SID_RE = re.compile(r"S\d+")
ANSWERING = {"answer", "partial", "false_premise", "clarify"}
DIRECT = {"answer", "partial", "false_premise"}
INTERROGATIVE_RE = re.compile(r"\b(?:как\w*|скольк\w*|когда|куда|где|кто|кого|кому|кем|ком|что|чего|чему|чем|чём|почему|зачем|откуда|чей|чья|чьё|чьи|чьих)\b|\bили\b", re.I)
YES_NO_RE = re.compile(r"^\W*(?:да|нет|только)\b", re.I)
YES_BUT_RE = re.compile(r"^\W*да\W+(?:но|однако)\b", re.I)
REFUSAL_RE = re.compile(r"не\s+(?:могу|буду|помогу|окажу|стану)|отказ", re.I)
MAX_ANSWER_CHARS = 900
MAX_CLARIFY_CHARS = 1200


def is_yes_no(question: str) -> bool:
    asked = [s for s in re.split(r"(?<=[?.!])\s+", question.strip()) if s.rstrip().endswith("?")]
    return bool(asked) and not INTERROGATIVE_RE.search(asked[-1])


def _names_company(plain: str, company: str, frags: list[Fragment]) -> bool:
    name = fold(company)
    text = fold(plain)
    for f in frags:
        for title in (f.doc.title, f.doc.short):
            text = text.replace(fold(title), " ")
    return name in text


def _strip_markers(text: str) -> str:
    return re.sub(r"\[S\d+(?:\s*[,;]\s*S\d+)*\]", " ", text)


def deterministic_issues(answer: str, status: str, frags: list[Fragment], context_text: str, question: str = "", today: date | None = None, company: str = "", required: list[list[str]] | None = None) -> list[str]:
    issues: list[str] = []
    valid = {f.sid for f in frags}
    sids = SID_RE.findall(" ".join(re.findall(r"\[[^\]]*S\d+[^\]]*\]", answer)))
    unknown = sorted(set(sids) - valid)
    if unknown:
        issues.append(f"Маркеры {', '.join(unknown)} не соответствуют приложенным фрагментам.")
    if status in ANSWERING and not sids:
        issues.append("В ответе нет ни одного маркера фрагмента (например [S1]): каждое юридически значимое утверждение должно ссылаться на фрагмент.")
    plain = _strip_markers(answer)
    if status in DIRECT and is_yes_no(question) and not YES_NO_RE.match(plain):
        issues.append("Вопрос предполагает ответ «да» или «нет»: начни ответ прямо со слова «Да» или «Нет» (как следует из фрагментов) либо с «Только при условии…», затем коротко поясни.")
    if status in DIRECT and YES_BUT_RE.match(plain):
        issues.append("Ответ начинается с «Да, но…»: если действие допустимо только при соблюдении условия, а из вопроса не следует, что оно соблюдено, начни с «Нет, если не…» или «Только при условии…».")
    limit = MAX_CLARIFY_CHARS if status == "clarify" else MAX_ANSWER_CHARS
    length = len(" ".join(plain.split()))
    if length > limit:
        issues.append(f"Ответ слишком длинный ({length} знаков): сократи до 1–3 предложений (до 500 знаков) — прямой ответ и только те условия, без которых его нельзя применить; убери сведения, о которых не спрашивали.")
    if status == "refusal_illegal" and not REFUSAL_RE.search(plain[:200]):
        issues.append("Начни ответ с прямого отказа помогать, например: «Помочь с этим не могу…», затем коротко объясни требования по фрагментам.")
    if company and _names_company(plain, company, frags):
        issues.append(f"В ответе названа организация пользователя ({company}), хотя в вопросе её нет: говори «ваша организация» или называй её по роли в договоре.")
    for group in required or []:
        if status in DIRECT and sids and not set(group) & set(sids):
            issues.append(f"Среди фрагментов есть другая редакция того же положения ({', '.join(f'[{s}]' for s in group)}): одной фразой назови, чем она отличается, и скажи, применяется ли она к ситуации из вопроса.")
    bare = sorted(set(BARE_SID_RE.findall(plain)))
    if bare:
        issues.append(f"В тексте упомянуты идентификаторы фрагментов ({', '.join(bare)}) вне маркеров: называй документы и редакции словами, а маркеры ставь только в квадратных скобках, например [S1].")
    allowed = known_numbers([context_text, *(f"{f.text} {f.doc.title} {f.unit.label}" for f in frags)], today or date.today())
    missing_nums = sorted({m.group(0) for m in NUM_RE.finditer(plain) if not num_forms(m.group(0)) & allowed})
    if missing_nums:
        issues.append(f"Числа {', '.join(missing_nums)} не встречаются во фрагментах и вопросе и не выводятся из них простым расчётом: если это результат расчёта по данным фрагментов — оставь и покажи расчёт, иначе убери или замени данными из фрагментов.")
    corpus = fold(" ".join(f.text for f in frags))
    for m in QUOTE_RE.finditer(plain):
        q = fold(m.group(1)).strip(" .,;")
        if len(q.split()) >= 4 and q not in corpus:
            issues.append(f"Цитата «{m.group(1)[:80]}» не совпадает дословно с текстом фрагментов: перескажи без кавычек или процитируй точно.")
    titles = f"{corpus} " + fold(" ".join(f"{f.doc.title} {f.doc.short} {f.unit.label}" for f in frags) + " " + context_text)
    for m in LAWREF_RE.finditer(plain):
        ref = fold(m.group(1))
        num = re.search(r"\d+(?:\.\d+)?", ref).group(0)
        if num not in titles:
            issues.append(f"Ссылка «{m.group(1)}» не подтверждается приложенными фрагментами.")
    return issues
