"""Scores a structured CV against an analysed job.

Each requirement is matched in stages, strongest first:
  exact/alias in experience or summary 1.0 · exact in skills only 0.9 ·
  version variant 0.75 · fuzzy spelling 0.8 · implied by a bullet (semantic) 0.5
Score = required keywords 45 + nice-to-haves 10 + responsibilities/semantic fit 10
        + title 15 + experience 10 + format 10.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from . import embed, taxonomy
from .models import CV, JobAnalysis, JobScore, Suggestion
from .parsing import experience_years

WORK_SEC = re.compile(r"experience|employment|career|project|summary|profile|leadership|achievement", re.I)
SKILL_SEC = re.compile(r"skill|competenc|technolog|tools", re.I)
TITLE_STOP = {"remote", "the", "and", "for", "with", "full", "time", "contract", "hybrid", "job", "role", "m/f/d", "f/m/d"}


@dataclass
class CVContext:
    cv: CV
    text: str
    work_text: str
    skills_text: str
    evidence: list[str]
    evidence_vecs: list = field(default_factory=list)
    doc_vec: object = None
    years: float | None = None
    warnings: list[str] = field(default_factory=list)


def context(cv: CV, warnings: list[str] | None = None) -> CVContext:
    work, skills, evidence = [], [], []
    for s in cv.sections:
        bucket = skills if SKILL_SEC.search(s.title) else work
        for e in s.entries:
            chunk = " ".join(x for x in [e.heading, e.subheading, e.text, *e.bullets] if x)
            bucket.append(chunk)
            if not SKILL_SEC.search(s.title):
                evidence += [b for b in e.bullets if len(b) > 25]
                if e.text and len(e.text) > 40:
                    evidence.append(e.text)
    work.append(cv.headline)
    ctx = CVContext(cv=cv, text=cv.text(), work_text="\n".join(work), skills_text="\n".join(skills),
                    evidence=evidence[:120], years=experience_years(cv), warnings=warnings or [])
    vecs = embed.vectors(ctx.evidence + [ctx.text[:6000]])
    ctx.evidence_vecs, ctx.doc_vec = vecs[:-1], vecs[-1]
    return ctx


def _strip_version(s: str) -> str:
    return re.sub(r"\s{2,}", " ", re.sub(r"\bv?\d+(\.\d+)*\+?", "", s)).strip()


def match_term(term, ctx: CVContext, term_vec=None) -> tuple[float, str, str]:
    """Returns (credit, how, evidence)."""
    words = [term.term, *term.aliases]
    rx = taxonomy.term_regex(*words)
    if rx.search(ctx.work_text):
        return 1.0, "exact", ""
    if rx.search(ctx.text):
        return 0.9, "skills_only", ""
    base = _strip_version(term.term)
    if base and base != term.term and len(base) > 1 and taxonomy.term_regex(base).search(ctx.text):
        return 0.75, "version", ""
    low = ctx.text.lower()
    if len(term.term) >= 6:
        best = max((fuzz.partial_ratio(w.lower(), low) for w in words if len(w) >= 6), default=0)
        if best >= 93:
            return 0.8, "fuzzy", ""
    if term_vec is not None and ctx.evidence_vecs:
        sims = [embed.cosine(term_vec, v) for v in ctx.evidence_vecs]
        i = max(range(len(sims)), key=sims.__getitem__)
        if sims[i] >= embed.implied_threshold():
            return 0.5, "implied", ctx.evidence[i]
    return 0.0, "missing", ""


def _title_score(title: str, ctx: CVContext) -> tuple[float, str]:
    clean = re.split(r"\s[—–|@-]\s|,|\bat\b", re.sub(r"\(.*?\)|\[.*?\]", " ", title), maxsplit=1, flags=re.I)[0].strip()
    toks = [t for t in re.findall(r"[a-z+#.]{2,}", clean.lower()) if t not in TITLE_STOP]
    if not toks:
        return 1.0, clean
    top = "\n".join([ctx.cv.headline] + [e.heading for s in ctx.cv.sections if WORK_SEC.search(s.title) for e in s.entries[:2]]).lower()
    low = ctx.text.lower()
    got = 0.0
    for t in toks:
        rx = taxonomy.term_regex(t)
        got += 1.0 if rx.search(top) else (0.6 if rx.search(low) else 0.0)
    return got / len(toks), clean


def _format_checks(ctx: CVContext) -> list[tuple[bool, str]]:
    t, titles = ctx.text, [s.title for s in ctx.cv.sections]
    words = len(t.split())
    has = lambda rx: any(re.search(rx, x, re.I) for x in titles)
    checks = [
        (bool(re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", t)), "Add an email address near the top."),
        (bool(re.search(r"\+?\d[\d\s()\-]{8,}\d", t)), "Add a phone number near the top."),
        (has(r"experience|employment|career"), "Use a plain “Experience” heading so parsers find your roles."),
        (has(SKILL_SEC.pattern), "Add a “Skills” section; most trackers match keywords there first."),
        (has(r"education"), "Add an “Education” section."),
        (300 <= words <= 1100, "Your CV is very short; trackers have little to match." if words < 300 else "Your CV is long; aim for two pages."),
    ]
    checks += [(False, w) for w in ctx.warnings]
    return checks


def score(job: JobAnalysis, ctx: CVContext, job_index: int = 0) -> JobScore:
    term_vecs = embed.vectors([t.term for t in job.terms]) if embed.mode() == "embeddings" and job.terms else [None] * len(job.terms)
    results, sug = [], []
    for t, v in zip(job.terms, term_vecs):
        credit, how, ev = match_term(t, ctx, v)
        results.append({"term": t.term, "kind": t.kind, "weight": t.weight, "credit": credit, "how": how, "evidence": ev})

    def cov(kind):
        rs = [r for r in results if r["kind"] == kind]
        w = sum(r["weight"] for r in rs)
        return sum(r["weight"] * r["credit"] for r in rs) / w if w else None

    rc, pc = cov("required"), cov("preferred")
    if rc is None and pc is None:
        kw_pts, kw = None, None  # nothing to match: fall back to semantic fit below
    elif pc is None:
        kw_pts, kw = 55 * rc, rc
    elif rc is None:
        kw_pts, kw = 55 * pc, pc
    else:
        kw_pts, kw = 45 * rc + 10 * pc, (45 * rc + 10 * pc) / 55

    # semantic fit: responsibilities vs CV evidence, plus whole-document similarity
    jd_vec = embed.vectors([job.text[:6000]])[0]
    doc = embed.doc_score(embed.cosine(jd_vec, ctx.doc_vec))
    if job.responsibilities and ctx.evidence_vecs:
        rv = embed.vectors(job.responsibilities)
        best = [max(embed.cosine(r, e) for e in ctx.evidence_vecs) for r in rv]
        lo, span = (0.55, 0.30) if embed.mode() == "embeddings" else (0.05, 0.30)
        resp = sum(max(0.0, min(1.0, (b - lo) / span)) for b in best) / len(best)
        sem = 0.6 * resp + 0.4 * doc
    else:
        sem = doc

    if kw_pts is None:
        kw, kw_pts = sem, 55 * sem
        sug.append(Suggestion(id=f"{job_index}:noterms", kind="keywords", priority="info",
                              message="No concrete skills could be read from this posting, so keywords are scored on overall similarity. Paste the full description for a sharper score."))

    tscore, tclean = _title_score(job.title, ctx)
    if job.min_years is None:
        exp = 1.0
    elif ctx.years is None:
        exp = 0.5
    else:
        exp = min(1.0, ctx.years / job.min_years)
    checks = _format_checks(ctx)
    fmt = sum(ok for ok, _ in checks) / len(checks)
    total = round(kw_pts + 10 * sem + 15 * tscore + 10 * exp + 10 * fmt)

    # suggestions
    pid = lambda kind, term="": f"{job_index}:{kind}:{term.lower()}"
    order = sorted(results, key=lambda r: (r["kind"] != "required", -r["weight"]))
    for r in order:
        pr = r["kind"]
        if r["how"] == "implied":
            sug.append(Suggestion(id=pid("implied", r["term"]), kind="implied", priority=pr, term=r["term"], evidence=r["evidence"], needs_confirmation=True,
                                  message=f"Your experience points to {r['term']}, but the CV never names it. Say it explicitly in the bullet below, if accurate."))
        elif r["how"] == "version":
            sug.append(Suggestion(id=pid("version", r["term"]), kind="version", priority=pr, term=r["term"], needs_confirmation=True,
                                  message=f"The posting asks for {r['term']}; your CV names {_strip_version(r['term'])} without the version. Add it if you've used it."))
        elif r["how"] == "skills_only" and pr == "required":
            sug.append(Suggestion(id=pid("context", r["term"]), kind="context", priority="info", term=r["term"],
                                  message=f"{r['term']} only appears in your skills list. Mention it in a recent role's bullet to show where you used it."))
        elif r["how"] == "missing":
            sug.append(Suggestion(id=pid("missing", r["term"]), kind="missing", priority=pr, term=r["term"], needs_confirmation=True,
                                  message=f"Add {r['term']} if you've used it."))
    if tscore < 0.8:
        sug.insert(0, Suggestion(id=pid("title"), kind="title", priority="info", term=tclean,
                                 message=f"Your headline doesn't echo “{tclean}”. Recruiters search on job titles; add it if it fits your experience."))
    if job.min_years is not None and (ctx.years is None or ctx.years < job.min_years):
        msg = (f"The posting asks for {job.min_years:g}+ years; your CV reads as about {ctx.years:g}. If your real total is higher, state it in your summary."
               if ctx.years is not None else f"The posting asks for {job.min_years:g}+ years and your CV doesn't state a total. Put “X+ years” in your summary.")
        sug.append(Suggestion(id=pid("experience"), kind="experience", priority="info", message=msg))
    for ok, m in checks:
        if not ok:
            sug.append(Suggestion(id=pid("format", m[:20]), kind="format", priority="info", message=m))

    return JobScore(total=max(0, min(100, total)), keywords=round(kw, 3), semantic=round(sem, 3), title=round(tscore, 3),
                    experience=round(exp, 3), format=round(fmt, 3), cv_years=ctx.years,
                    matched=sum(1 for r in results if r["credit"] >= 0.9), total_terms=len(results),
                    term_results=results, suggestions=sug)
