from __future__ import annotations

from html import escape

from legal_core.index.chunks import validity_text
from legal_core.index.store import KnowledgeIndex
from legal_core.kb.models import Document
from legal_core.pipeline.fragments import relation_lines, unit_notes
from legal_core.text import fmt_date

DOC_TYPES = {"contract": "Договор", "supplement": "Дополнительное соглашение", "regulation": "Внутренний регламент", "memo": "Служебная записка", "letter": "Письмо", "order": "Приказ"}
REL_KINDS = {"amends": "Изменяет", "amended_by": "Изменён документом", "supplements": "Дополняет", "supplemented_by": "Дополнен документом", "replaces": "Заменяет редакцию", "replaced_by": "Заменён редакцией", "refers": "Ссылается на"}
GROUPS = (("company", "Документы организации"), ("law", "Нормативные правовые акты"))

CSS = """
:root{--hue:18;--g50:oklch(.98 0 0);--g100:oklch(.94 0 0);--g200:oklch(.92 0 0);--g300:oklch(.85 0 0);--g500:oklch(.69 0 0);--g600:oklch(.51 0 0);--g700:oklch(.42 0 0);--g900:oklch(.2 0 0);
color-scheme:light;--bg:#fff;--text:var(--g700);--strong:var(--g900);--muted:var(--g600);--line:oklch(.92 0 0/.7);--card:oklch(1 0 0/.8);--card-line:oklch(.92 0 0/.6);--shadow:0 10px 15px -3px oklch(0 0 0/.05),0 4px 6px -4px oklch(0 0 0/.05);--chip:var(--g100);--hover:oklch(.94 0 0/.7);--link-line:var(--g300);
--accent:oklch(.6 .21 var(--hue));--hl-bg:oklch(.64 .2 var(--hue)/.1);--hl-ring:oklch(.64 .2 var(--hue)/.22);--hl-chip:oklch(.64 .2 var(--hue)/.16);--hl-text:oklch(.5 .19 var(--hue));
--warn-bg:oklch(.8 .17 85/.16);--warn-line:oklch(.8 .17 85/.5);--warn-text:oklch(.48 .12 62);--warn-dot:oklch(.77 .18 72);--ok-dot:oklch(.72 .19 150);--danger:oklch(.5 .2 27);--danger-bg:oklch(.64 .22 25/.1)}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--bg:var(--g900);--text:var(--g200);--strong:var(--g50);--muted:var(--g500);--line:oklch(.32 0 0/.7);--card:oklch(.27 0 0/.45);--card-line:oklch(.32 0 0/.6);--shadow:0 10px 15px -3px oklch(0 0 0/.25);--chip:oklch(.32 0 0/.8);--hover:oklch(.32 0 0/.5);--link-line:var(--g600);
--accent:oklch(.72 .17 var(--hue));--hl-bg:oklch(.58 .2 var(--hue)/.2);--hl-ring:oklch(.7 .18 var(--hue)/.3);--hl-chip:oklch(.62 .2 var(--hue)/.32);--hl-text:oklch(.9 .06 var(--hue));
--warn-bg:oklch(.8 .17 85/.12);--warn-line:oklch(.8 .17 85/.28);--warn-text:oklch(.92 .1 95);--danger:oklch(.8 .11 20);--danger-bg:oklch(.64 .22 25/.18)}}
*,::before,::after{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;text-size-adjust:100%}
body{margin:0;min-height:100vh;background:var(--bg);color:var(--text);font:16px/1.6 -apple-system,BlinkMacSystemFont,Inter,ui-sans-serif,system-ui,"Segoe UI",Roboto,Ubuntu,Cantarell,"Noto Sans",sans-serif,"Helvetica Neue",Arial;-webkit-font-smoothing:antialiased;overflow-wrap:break-word}
::selection{background:oklch(.64 .2 var(--hue)/.25)}
.bg{position:fixed;inset:0;z-index:0;overflow:hidden;pointer-events:none}
.bg::before,.bg::after,.bg>span,.bg>i{content:"";position:absolute;border-radius:50%;filter:blur(90px);opacity:.22}
.bg::before{width:min(60vw,90%);height:min(60vw,90%);top:-20%;right:-10%;background:radial-gradient(circle,oklch(.5 .28 var(--hue)) 0%,oklch(.4 .24 calc(var(--hue) - 10)) 45%,transparent 72%)}
.bg::after{width:min(50vw,80%);height:min(50vw,80%);bottom:-15%;left:0;background:radial-gradient(circle,oklch(.46 .27 calc(var(--hue) - 8)) 0%,oklch(.36 .22 calc(var(--hue) - 23)) 45%,transparent 72%)}
.bg>span{width:min(40vw,70%);height:min(40vw,70%);top:35%;right:15%;background:radial-gradient(circle,oklch(.55 .25 calc(var(--hue) + 4)) 0%,transparent 68%)}
.bg>i{width:min(35vw,65%);height:min(35vw,65%);bottom:10%;right:-5%;background:radial-gradient(circle,oklch(.43 .28 calc(var(--hue) - 13)) 0%,transparent 68%)}
.wrap{position:relative;z-index:1;max-width:880px;margin:0 auto;padding:32px 24px 72px}
a{color:var(--strong);text-decoration:underline 1px var(--link-line);text-underline-offset:3px;transition:text-decoration-color .15s,background-color .15s}
a:hover{text-decoration-color:var(--accent)}
a:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:6px}
h1{margin:0;color:var(--strong);font-size:28px;line-height:1.25;font-weight:600;letter-spacing:-.015em;text-wrap:balance}
.card{background:var(--card);border:1px solid var(--card-line);border-radius:24px;box-shadow:var(--shadow);-webkit-backdrop-filter:blur(24px);backdrop-filter:blur(24px)}
.top{margin-bottom:24px}
.back{display:inline-flex;align-items:center;gap:6px;padding:6px 14px;border-radius:9999px;font-size:14px;line-height:20px;color:var(--text);text-decoration:none}
.back:hover{background:var(--hover);color:var(--strong)}
.head{margin:0 0 20px}
.meta{margin:8px 0 0;color:var(--muted);font-size:14px}
.status{display:flex;align-items:flex-start;gap:10px;margin:0 0 12px;padding:12px 18px;border-radius:18px;font-size:14px;color:var(--strong)}
.status::before{content:"";flex:none;width:8px;height:8px;margin-top:7px;border-radius:50%;background:var(--ok-dot)}
.status.warn{background-image:linear-gradient(var(--warn-bg),var(--warn-bg));border-color:var(--warn-line);color:var(--warn-text);font-weight:500}
.status.warn::before{background:var(--warn-dot)}
.status .k{color:var(--muted);font-weight:400}
.status.warn .k{color:inherit}
.info{margin:0 0 16px;padding:4px 20px;border-radius:18px;font-size:14px}
.row{display:grid;grid-template-columns:168px minmax(0,1fr);gap:4px 16px;padding:12px 0}
.row+.row{border-top:1px solid var(--line)}
.row>.k{color:var(--muted)}
.rel{display:grid;gap:6px;margin:0;padding:0;list-style:none}
.rk,.cond{color:var(--muted)}
.url{overflow-wrap:anywhere}
.doc{--inset:14px;padding:20px 28px 24px}
.hdr{margin:4px 0 8px;padding-bottom:14px;border-bottom:1px solid var(--line);color:var(--muted);font-size:13px;line-height:1.55;white-space:pre-wrap}
.sec{margin:22px 0 4px;color:var(--strong);font-size:16px;font-weight:600;line-height:1.4}
.unit{position:relative;margin:2px calc(var(--inset)*-1);padding:8px var(--inset);border-radius:14px;white-space:pre-wrap;scroll-margin-top:12vh}
.lab{display:inline-block;margin-right:8px;padding:0 8px;border-radius:9999px;background:var(--chip);color:var(--muted);font-size:12px;font-weight:500;line-height:22px;white-space:nowrap;vertical-align:1px;font-variant-numeric:tabular-nums}
.unit.hl{background:var(--hl-bg);box-shadow:inset 0 0 0 1px var(--hl-ring)}
.unit.hl::before{content:"";position:absolute;left:5px;top:12px;bottom:12px;width:3px;border-radius:3px;background:var(--accent)}
.unit.hl .lab{background:var(--hl-chip);color:var(--hl-text);font-weight:600}
.unit.rep{opacity:.55}
.note{display:block;margin:2px 0;color:var(--muted);font-size:13px;font-style:italic;line-height:1.5}
.inj{display:block;width:fit-content;margin:0 0 6px;padding:2px 10px;border-radius:10px;background:var(--danger-bg);color:var(--danger);font-size:13px;font-weight:600;line-height:1.5}
.group{margin:0 0 16px;padding:8px}
.group h2{display:flex;align-items:center;gap:8px;margin:0;padding:10px 14px 6px;color:var(--muted);font-size:13px;font-weight:600;line-height:20px}
.count{padding:0 8px;border-radius:9999px;background:var(--chip);font-size:12px;font-weight:500;font-variant-numeric:tabular-nums}
.docs{margin:0;padding:0;list-style:none}
.docs a{display:block;padding:10px 14px;border-radius:16px;text-decoration:none}
.docs a:hover{background:var(--hover)}
.docs .t{display:block;color:var(--strong);font-size:15px;font-weight:500;line-height:1.45}
.st{display:flex;align-items:flex-start;gap:6px;margin-top:2px;color:var(--muted);font-size:13px;line-height:1.45}
.st::before{content:"";flex:none;width:6px;height:6px;margin-top:7px;border-radius:50%;background:var(--ok-dot)}
.st.warn{color:var(--warn-text)}
.st.warn::before{background:var(--warn-dot)}
@media (max-width:640px){.wrap{padding:16px 16px 48px}.top{margin-bottom:16px}.back{margin-left:-8px;padding:6px 8px}h1{font-size:22px}.card{border-radius:20px}.info{padding:2px 16px}.row{grid-template-columns:minmax(0,1fr);gap:2px;padding:10px 0}.doc{--inset:12px;padding:12px 14px 16px}.group{padding:4px}.group h2{padding:10px 12px 4px}.docs a{padding:10px 12px}}
"""

HEAD = "<meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><meta name=color-scheme content='light dark'><link rel=icon href='data:,'><meta name=theme-color media='(prefers-color-scheme: light)' content='#ffffff'><meta name=theme-color media='(prefers-color-scheme: dark)' content='#161616'>"


def _page(title: str, body: str) -> str:
    return f"<!doctype html><html lang=ru><head>{HEAD}<title>{escape(title)}</title><style>{CSS}</style></head><body><div class=bg aria-hidden=true><span></span><i></i></div><main class=wrap>{body}</main></body></html>"


def _tone(doc: Document) -> str:
    return "warn" if doc.valid_to else "ok"


def _relations(index: KnowledgeIndex, doc: Document) -> list[str]:
    seen: set[tuple[str, str, str]] = set()
    items = []
    for r in doc.relations:
        key = (r.kind, r.target_doc, r.condition)
        other = index.by_id.get(r.target_doc)
        if other is None or other.doc_id == doc.doc_id or key in seen:
            continue
        seen.add(key)
        cond = f"<span class=cond> — {escape(r.condition)}</span>" if r.condition else ""
        items.append(f"<li><span class=rk>{escape(REL_KINDS.get(r.kind, r.kind))}:</span> <a href='/kb/{escape(other.doc_id)}'>{escape(other.title)}</a>{cond}</li>")
    return items


def doc_page(index: KnowledgeIndex, doc: Document, hl: str | None) -> str:
    parts = ["<nav class=top><a class=back href='/kb'>← Все документы базы знаний</a></nav>"]
    if doc.category == "law":
        meta = ["Нормативный правовой акт, действующая редакция"]
        if doc.exported:
            meta.append(f"выгрузка от {fmt_date(doc.exported)}")
    else:
        meta = [DOC_TYPES.get(doc.doc_type, "Документ")]
        if doc.doc_date:
            meta.append(f"от {fmt_date(doc.doc_date)}")
    parts.append(f"<header class=head><h1>{escape(doc.title)}</h1><p class=meta>{escape(' · '.join(meta))}</p></header>")
    v = validity_text(doc)
    if v:
        parts.append(f"<div class='card status {_tone(doc)}'><span><span class=k>Статус:</span> {escape(v)}</span></div>")
    rows = []
    rels = _relations(index, doc)
    if rels:
        rows.append(f"<div class=row><span class=k>Связанные документы</span><ul class=rel>{''.join(rels)}</ul></div>")
    if doc.source_url:
        url = escape(doc.source_url)
        rows.append(f"<div class=row><span class=k>Первоисточник</span><a class=url href='{url}' target=_blank rel=noopener>{url}</a></div>")
    if rows:
        parts.append(f"<section class='card info'>{''.join(rows)}</section>")
    body = []
    if doc.header:
        body.append(f"<div class=hdr>{escape(chr(10).join(doc.header))}</div>")
    body.extend(f"<span class=note>{escape(n.text)}</span>" for n in doc.notes)
    section = None
    for u in doc.units:
        if u.heading and u.heading != section:
            section = u.heading
            body.append(f"<h2 class=sec>{escape(section)}</h2>")
        hit = bool(hl) and u.anchor == hl
        cls = "unit" + (" hl" if hit else "") + (" rep" if u.repealed else "")
        notes = "".join(f"<span class=note>{escape(n.text)}</span>" for n in u.notes)
        inj = "<span class=inj>⚠ Фрагмент содержит инструкцию для ИИ-ассистента и исключён из поиска</span>" if u.injection else ""
        lead = f"<span class=note>{escape(u.lead[:200])}</span>" if u.lead and hit else ""
        lab = f"<span class=lab>{escape(u.label)}</span>" if u.label else ""
        body.append(f"<div id='{escape(u.anchor)}' class='{cls}'>{notes}{inj}{lead}{lab}{escape(u.text)}</div>")
    parts.append(f"<article class='card doc'>{''.join(body)}</article>")
    return _page(doc.title, "".join(parts))


def _index_item(d: Document) -> str:
    v = validity_text(d)
    st = f"<span class='st {_tone(d)}'>{escape(v)}</span>" if v else ""
    return f"<li><a href='/kb/{escape(d.doc_id)}'><span class=t>{escape(d.title)}</span>{st}</a></li>"


def index_page(index: KnowledgeIndex) -> str:
    groups: dict[str, list[Document]] = {key: [] for key, _ in GROUPS}
    for d in index.docs:
        groups.setdefault(d.category, []).append(d)
    html = [f"<header class=head><h1>База знаний</h1><p class=meta>Документов: {len(index.docs)}</p></header>"]
    for key, title in GROUPS:
        docs = sorted(groups[key], key=lambda d: d.title)
        items = "".join(_index_item(d) for d in docs)
        html.append(f"<section class='card group'><h2>{title}<span class=count>{len(docs)}</span></h2><ul class=docs>{items}</ul></section>")
    return _page("База знаний", "".join(html))


def unit_payload(index: KnowledgeIndex, doc: Document, anchor: str) -> dict:
    u = doc.unit(anchor)
    if u is None:
        return {}
    return {"doc_id": doc.doc_id, "title": doc.title, "label": u.label, "text": u.text, "notes": [n.text for n in unit_notes(doc, u)], "relations": relation_lines(index, doc, u)}
