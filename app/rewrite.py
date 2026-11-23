"""Applies accepted changes to a CV, with an LLM rewrite guarded by fact checks
and a deterministic fallback that never touches facts."""
from __future__ import annotations

import json
import re

from rapidfuzz import fuzz

from . import llm, taxonomy
from .models import CV, Entry, Section, Suggestion
from .matcher import SKILL_SEC

NUM_RE = re.compile(r"\d+(?:[.,]\d+)?%?")


def _numbers(t: str) -> set[str]:
    return set(NUM_RE.findall(t))


def fact_check(old: CV, new: CV, allowed_extra: list[str]) -> list[str]:
    """Problems if the rewrite invented numbers or dropped roles/employers."""
    problems = []
    allowed = _numbers(" ".join(allowed_extra))
    added = _numbers(new.text()) - _numbers(old.text()) - allowed
    if added:
        problems.append(f"new numbers appeared: {', '.join(sorted(added)[:6])}")
    new_low = new.text().lower()
    for s in old.sections:
        if SKILL_SEC.search(s.title):
            continue
        for e in s.entries:
            if e.heading and len(e.heading) > 6 and fuzz.partial_ratio(e.heading.lower()[:40], new_low) < 80:
                problems.append(f"entry may be lost: {e.heading[:50]}")
    return problems


def _skills_section(cv: CV) -> Section:
    for s in cv.sections:
        if SKILL_SEC.search(s.title):
            return s
    sec = Section(title="Skills")
    idx = next((i for i, s in enumerate(cv.sections) if re.search(r"experience|employment", s.title, re.I)), len(cv.sections))
    cv.sections.insert(idx, sec)
    return sec


def add_to_skills(cv: CV, terms: list[str]) -> list[str]:
    text = cv.text()
    todo = [t for t in terms if not taxonomy.term_regex(t).search(text)]
    if not todo:
        return []
    sec = _skills_section(cv)
    leftovers = []
    placed: dict[int, list[str]] = {}
    for t in todo:
        sk = taxonomy.lookup(t)
        target = None
        if sk:
            for i, e in enumerate(sec.entries):
                line = e.text or " ".join(e.bullets)
                if any(o.category == sk.category for o in taxonomy.find_skills(line)):
                    target = i
                    break
        if target is None:
            leftovers.append(t)
        else:
            placed.setdefault(target, []).append(t)
    for i, ts in placed.items():
        e = sec.entries[i]
        if e.text:
            e.text = e.text.rstrip(". ") + ", " + ", ".join(ts)
        else:
            e.bullets.append(", ".join(ts))
    if leftovers:
        sec.entries.append(Entry(text="Additional: " + ", ".join(leftovers)))
    return [f"Added to {sec.title}: {', '.join(todo)}"]


def apply_rules(cv: CV, confirmed: list[str], accepted: list[Suggestion], headline: str | None) -> tuple[CV, list[str]]:
    cv = cv.model_copy(deep=True)
    changes = []
    if headline and headline.strip() and headline.strip() != cv.headline:
        changes.append(f"Headline: “{cv.headline or 'none'}” → “{headline.strip()}”")
        cv.headline = headline.strip()
    for s in accepted:
        if s.kind == "implied" and s.evidence:
            for sec in cv.sections:
                for e in sec.entries:
                    for i, b in enumerate(e.bullets):
                        if b == s.evidence and not taxonomy.term_regex(s.term).search(b):
                            e.bullets[i] = b.rstrip(". ") + f" ({s.term})."
                            changes.append(f"Named {s.term} in: “{b[:60]}…”")
    changes += add_to_skills(cv, confirmed + [s.term for s in accepted if s.kind in ("missing", "version")])
    return cv, changes


PROMPT = """You update a CV so applicant tracking systems match it better, without making anything up.
RULES
- Never invent or change employers, job titles held, dates, education, numbers, metrics or achievements. Do not add any number that isn't already in the CV.
- CONFIRMED lists skills the candidate confirmed they have. Add every one, using the exact wording given: put each in the skills section (grouped with similar skills), and also in the summary or an existing bullet only where that bullet already describes related work.
- NAME_IN_BULLET lists skills already shown by a specific bullet: rephrase that bullet so it names the skill, keeping its meaning and numbers.
- MENTION_IN_ROLE lists skills that appear only in the skills list: weave each into the most relevant existing bullet of a recent role, only where it is truthful from the bullet's content; otherwise skip it.
- If HEADLINE is given, use it exactly.
- Keep every section, role and project. You may tighten wording so the CV fits two A4 pages (about 650-850 words).
- Plain text, no markdown.
Return ONLY JSON of the same shape as the input CV, plus "changes": [short plain sentences, one per change].

HEADLINE: {headline}
CONFIRMED: {confirmed}
NAME_IN_BULLET: {implied}
MENTION_IN_ROLE: {context}
CV JSON:
{cv}"""


def apply(cv: CV, confirmed: list[str], accepted: list[Suggestion], headline: str | None, use_llm: bool) -> tuple[CV, list[str], str]:
    if use_llm and llm.available():
        implied = [{"skill": s.term, "bullet": s.evidence} for s in accepted if s.kind == "implied"]
        context = [s.term for s in accepted if s.kind == "context"]
        all_confirmed = sorted(set(confirmed + [s.term for s in accepted if s.kind in ("missing", "version")]))
        r = llm.ask_json(PROMPT.format(headline=headline or "(keep current)", confirmed=json.dumps(all_confirmed),
                                       implied=json.dumps(implied), context=json.dumps(context),
                                       cv=cv.model_dump_json()), max_tokens=8000)
        if isinstance(r, dict) and r.get("sections"):
            try:
                changes = [str(c) for c in r.pop("changes", [])]
                new = CV.model_validate(r)
                allowed = all_confirmed + [headline or ""] + [s.term for s in accepted]
                problems = fact_check(cv, new, allowed)
                if not problems:
                    changes += add_to_skills(new, all_confirmed)  # guarantee every confirmed term landed
                    return new, changes, "llm"
            except Exception:
                pass
    new, changes = apply_rules(cv, confirmed, accepted, headline)
    return new, changes, "rules"
