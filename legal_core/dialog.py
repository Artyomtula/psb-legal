from __future__ import annotations

import re
from dataclasses import dataclass

_ROLE_RE = re.compile(r"^\s*(Пользователь|Юрист|Клиент|Сотрудник|User|Ассистент|Assistant|Бот|ИИ|AI)\s*:\s*", re.I)
_ASSISTANT = {"ассистент", "assistant", "бот", "ии", "ai"}


@dataclass
class Turn:
    role: str
    content: str


def parse_dialog(text: str) -> list[Turn]:
    turns: list[Turn] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        m = _ROLE_RE.match(line)
        if m:
            role = "assistant" if m.group(1).lower() in _ASSISTANT else "user"
            turns.append(Turn(role, line[m.end():].strip()))
        elif turns:
            turns[-1].content = (turns[-1].content + "\n" + line).strip()
        elif line.strip():
            turns.append(Turn("user", line.strip()))
    if not turns:
        return [Turn("user", text.strip())]
    if turns[-1].role != "user":
        last_user = max((i for i, t in enumerate(turns) if t.role == "user"), default=None)
        if last_user is None:
            return [Turn("user", text.strip())]
        turns = turns[: last_user + 1]
    return turns


def render_history(turns: list[Turn], limit: int = 6000) -> str:
    lines = [("Пользователь" if t.role == "user" else "Ассистент") + ": " + t.content for t in turns[:-1]]
    out = "\n".join(lines)
    return out[-limit:]
