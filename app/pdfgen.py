"""Renders a CV as a clean, single-column, ATS-friendly A4 PDF."""
from __future__ import annotations

import io
import re
from xml.sax.saxutils import escape

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, KeepTogether, ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer

from .models import CV

INK, GREY = HexColor("#151515"), HexColor("#5a5a5a")
S = {
    "name": ParagraphStyle("name", fontName="Helvetica-Bold", fontSize=19, leading=23, textColor=INK),
    "headline": ParagraphStyle("headline", fontName="Helvetica-Bold", fontSize=10.5, leading=14, textColor=INK),
    "contact": ParagraphStyle("contact", fontName="Helvetica", fontSize=8.8, leading=12, textColor=GREY),
    "h": ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=11, leading=14, textColor=INK, spaceBefore=9, spaceAfter=2),
    "eh": ParagraphStyle("eh", fontName="Helvetica-Bold", fontSize=9.8, leading=12.5, textColor=INK, spaceBefore=4),
    "es": ParagraphStyle("es", fontName="Helvetica", fontSize=8.8, leading=11.5, textColor=GREY),
    "p": ParagraphStyle("p", fontName="Helvetica", fontSize=9.3, leading=12.4, textColor=INK, spaceAfter=1.5),
    "b": ParagraphStyle("b", fontName="Helvetica", fontSize=9.3, leading=12.4, textColor=INK),
}
_WIN = set("€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ")


def safe(t: str) -> str:
    t = t.replace("→", "->").replace("≥", ">=").replace("≤", "<=")
    t = "".join(c if ord(c) < 256 or c in _WIN else "" for c in t)
    t = escape(t)
    # "Label: rest" in skills lines -> bold label
    return t


def render(cv: CV) -> tuple[bytes, int]:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
                            title=f"{cv.name} CV", author=cv.name)
    story = [Paragraph(safe(cv.name), S["name"])]
    if cv.headline:
        story.append(Paragraph(safe(cv.headline), S["headline"]))
    if cv.contact:
        story.append(Paragraph(safe("  ·  ".join(cv.contact)), S["contact"]))
    for sec in cv.sections:
        story += [Paragraph(safe(sec.title), S["h"]), HRFlowable(width="100%", thickness=0.6, color=HexColor("#b5b5b5"), spaceAfter=3)]
        for e in sec.entries:
            block = []
            if e.heading:
                block.append(Paragraph(safe(e.heading), S["eh"]))
            if e.subheading:
                block.append(Paragraph(safe(e.subheading), S["es"]))
            if e.text:
                txt = safe(e.text)
                m = re.match(r"^([A-Za-z &/]{2,28}):\s", e.text)
                if m:
                    txt = f"<b>{safe(m.group(1))}:</b>" + safe(e.text[len(m.group(1)) + 1:])
                block.append(Paragraph(txt, S["p"]))
            if e.bullets:
                block.append(ListFlowable([ListItem(Paragraph(safe(b), S["b"]), leftIndent=11) for b in e.bullets],
                                          bulletType="bullet", start="•", leftIndent=11, bulletFontSize=8))
            if block:
                story.append(KeepTogether(block[:3]) if len(block) > 1 else block[0])
                story += block[3:]
        story.append(Spacer(1, 2))
    pages = []
    doc.build(story, onLaterPages=lambda c, d: pages.append(1), onFirstPage=lambda c, d: pages.append(1))
    return buf.getvalue(), len(pages)
