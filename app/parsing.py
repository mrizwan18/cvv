"""PDF/text CV parsing into a structured CV, with parseability warnings."""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from datetime import date

import pymupdf

from .models import CV, Entry, Section

HEADING_RE = re.compile(
    r"^(professional summary|summary|profile|about me|objective|experience|work experience|"
    r"professional experience|employment( history)?|career history|skills|technical skills|core skills|"
    r"key skills|core competencies|education|projects|personal projects|certifications?|awards?( & honors)?|"
    r"achievements|leadership( & mentorship)?|languages|interests|publications|volunteering)\s*:?$",
    re.I,
)
BULLET_RE = re.compile(r"^[•▪●◦‣\-\*–·]\s*")
CONTACT_RE = re.compile(r"@|\+?\d[\d\s()\-]{7,}|linkedin|github|https?://|www\.", re.I)
PROSE_SECTIONS = re.compile(r"skill|summary|profile|objective|language|interest|competenc", re.I)


@dataclass
class Line:
    text: str
    x0: float
    x1: float
    size: float
    bold: bool
    page: int
    page_width: float
    gap: bool = False  # row holds a right-aligned field (dates, location)


PUA_RE = re.compile("[\ue000-\uf8ff]")


def _is_bold(span: dict) -> bool:
    return bool(span["flags"] & 16 or "bold" in span["font"].lower())


def is_heading(text: str) -> bool:
    return bool(HEADING_RE.match(text.strip()))


def pdf_lines(data: bytes, max_pages: int = 6) -> tuple[list[Line], list[str]]:
    warnings: list[str] = []
    doc = pymupdf.open(stream=data, filetype="pdf")
    if doc.page_count > max_pages:
        raise ValueError(f"CV has {doc.page_count} pages; the limit is {max_pages}.")
    if doc.page_count > 2:
        warnings.append(f"Your CV runs to {doc.page_count} pages. Most recruiters expect two at most.")
    lines: list[Line] = []
    images = 0
    for pno, page in enumerate(doc):
        images += len(page.get_images())
        spans = []
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            for ln in block["lines"]:
                for s in ln["spans"]:
                    if s["text"].strip():
                        spans.append(s)
        # regroup spans by baseline: PDF "lines" often mix right-aligned dates with body text
        rows: list[list[dict]] = []
        for s in sorted(spans, key=lambda s: (round(s["origin"][1], 1), s["bbox"][0])):
            y = s["origin"][1]
            if rows and abs(rows[-1][0]["origin"][1] - y) < s["size"] * 0.4:
                rows[-1].append(s)
            else:
                rows.append([s])
        for row in rows:
            row.sort(key=lambda s: s["bbox"][0])
            text, end, gapped = "", None, False
            row = [s for s in row if PUA_RE.sub("", s["text"]).strip()]
            if not row:
                continue
            for s in row:
                gap = s["bbox"][0] - end if end is not None else 0
                if end is not None:
                    gapped = gapped or gap > s["size"] * 2
                    text += " · " if gap > s["size"] * 2 else ("" if text.endswith(" ") or s["text"].startswith(" ") or gap < s["size"] * 0.15 else " ")
                text += PUA_RE.sub("", s["text"])
                end = s["bbox"][2]
            chars = sum(len(s["text"].strip()) for s in row) or 1
            bold_chars = sum(len(s["text"].strip()) for s in row if s["flags"] & 16 or "bold" in s["font"].lower())
            lines.append(Line(
                text=re.sub(r"\s+", " ", text).strip(" ·"),
                x0=row[0]["bbox"][0], x1=row[-1]["bbox"][2],
                size=max(s["size"] for s in row),
                bold=bold_chars / chars > 0.6 or (_is_bold(row[0]) and bold_chars / chars > 0.2),
                page=pno, page_width=page.rect.width, gap=gapped,
            ))
    words = sum(len(l.text.split()) for l in lines)
    if words < 120:
        warnings.append("Very little text could be read. If the PDF is a scan or image, trackers can't read it either.")
    if images and words < 300:
        warnings.append("Parts of your CV may be images; trackers ignore text inside images.")
    right = [l for l in lines if l.x0 > l.page_width * 0.42 and len(l.text) > 15]
    if len(right) > max(6, len(lines) * 0.15):
        warnings.append("Your CV looks like it uses two columns. Many trackers read columns out of order; a single column is safer.")
    return _merge_wrapped(lines), warnings


def _merge_wrapped(lines: list[Line]) -> list[Line]:
    if not lines:
        return lines
    body = statistics.median(l.size for l in lines)
    right_edge = max(l.x1 for l in lines)
    out: list[Line] = []
    for l in lines:
        prev = out[-1] if out else None
        wrapped = (
            prev is not None and prev.page == l.page
            and not BULLET_RE.match(l.text) and not is_heading(l.text) and not is_heading(prev.text)
            and abs(l.size - body) < body * 0.15 and abs(prev.size - body) < body * 0.15
            and prev.x1 > right_edge * 0.85 and not prev.text.endswith((".", ":"))
            and l.bold == prev.bold and not prev.gap and not prev.bold
        )
        if wrapped:
            prev.text += " " + l.text
            prev.x1 = l.x1
        else:
            out.append(Line(**l.__dict__))
    return out


def text_lines(text: str) -> list[Line]:
    return [Line(t.strip(), 0, 0, 10, False, 0, 600) for t in text.splitlines() if t.strip()]


def build_cv(lines: list[Line]) -> CV:
    if not lines:
        return CV()
    body = statistics.median(l.size for l in lines)
    top = lines[: min(8, len(lines))]
    name_line = max(top, key=lambda l: l.size) if top[0].size != top[-1].size else top[0]
    cv = CV(name=name_line.text)
    i = lines.index(name_line) + 1
    while i < min(len(lines), 8) and not _looks_heading(lines[i], body):
        t = lines[i].text
        if CONTACT_RE.search(t):
            cv.contact.append(t)
        elif not cv.headline:
            cv.headline = t
        else:
            cv.contact.append(t)
        i += 1
    sec: Section | None = None
    entry: Entry | None = None
    has_fonts = any(x.bold for x in lines)
    for l in lines[i:]:
        t = l.text
        if _looks_heading(l, body):
            sec = Section(title=t.rstrip(":").strip())
            cv.sections.append(sec)
            entry = None
            continue
        if sec is None:
            sec = Section(title="Summary")
            cv.sections.append(sec)
        if BULLET_RE.match(t):
            b = BULLET_RE.sub("", t).strip()
            if entry is None:
                entry = Entry()
                sec.entries.append(entry)
            entry.bullets.append(b)
            continue
        if PROSE_SECTIONS.search(sec.title):
            sec.entries.append(Entry(text=t))
            entry = None
            continue
        meta = bool(RANGE_RE.search(t) or YEAR_RE.search(t)) and len(t) < 90 or (len(t) < 70 and not t.endswith("."))
        if has_fonts:
            if l.bold:
                if entry is not None and entry.heading and not entry.bullets and not entry.subheading:
                    entry.subheading = t
                else:
                    entry = Entry(heading=t)
                    sec.entries.append(entry)
            elif entry is not None and not entry.bullets and meta:
                entry.subheading = (entry.subheading + " · " + t).strip(" ·")
            else:
                if entry is None:
                    entry = Entry()
                    sec.entries.append(entry)
                entry.bullets.append(t)
            continue
        if entry is None or entry.bullets:
            entry = Entry(heading=t)
            sec.entries.append(entry)
        elif not entry.subheading and meta:
            entry.subheading = t
        else:
            entry.bullets.append(t)
    return cv


def _looks_heading(l: Line, body: float) -> bool:
    if is_heading(l.text):
        return True
    t = l.text.strip()
    return (
        len(t) < 40 and not BULLET_RE.match(t) and not CONTACT_RE.search(t) and not re.search(r"\d", t)
        and l.size > body * 1.18
    )


# ---------- experience years ----------
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_D = r"(?:(?P<m{n}>jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+|(?P<n{n}>\d{{1,2}})/)?(?P<y{n}>(?:19|20)\d{{2}})"
RANGE_RE = re.compile(
    _D.format(n=1) + r"\s*(?:–|—|-|to)\s*(?:" + _D.format(n=2) + r"|(?P<now>present|current|now|date|today))",
    re.I,
)
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
EDU_RE = re.compile(r"university|college|bachelor|master|\bbs\b|\bbsc\b|\bms\b|\bmsc\b|degree|school|nuces", re.I)


def _ym(m, n, y) -> float:
    month = MONTHS.get((m or "")[:3].lower()) or (int(n) if n else 1)
    return int(y) + (month - 1) / 12


def experience_years(cv: CV, today: date | None = None) -> float | None:
    today = today or date.today()
    now = today.year + (today.month - 1) / 12
    spans = []
    for s in cv.sections:
        if re.search(r"education|certif", s.title, re.I):
            continue
        for e in s.entries:
            blob = " ".join([e.heading, e.subheading, e.text])
            if EDU_RE.search(blob):
                continue
            for m in RANGE_RE.finditer(blob):
                a = _ym(m["m1"], m["n1"], m["y1"])
                b = now if m["now"] else _ym(m["m2"], m["n2"], m["y2"]) + 1 / 12
                if 0 < b - a < 50:
                    spans.append((a, min(b, now)))
    computed = None
    if spans:
        spans.sort()
        total, cur_a, cur_b = 0.0, *spans[0]
        for a, b in spans[1:]:
            if a <= cur_b:
                cur_b = max(cur_b, b)
            else:
                total += cur_b - cur_a
                cur_a, cur_b = a, b
        total += cur_b - cur_a
        computed = round(total * 2) / 2 if total % 0.5 > 0.25 else int(total * 2) / 2
    stated = None
    m = re.search(r"(\d{1,2}(?:\.\d)?)\s*\+?\s*years", cv.text(), re.I)
    if m:
        stated = float(m.group(1))
    if stated is not None and computed is not None:
        return max(stated, computed)
    return stated if stated is not None else computed
