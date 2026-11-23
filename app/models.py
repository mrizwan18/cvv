from __future__ import annotations

from pydantic import BaseModel, Field


class Entry(BaseModel):
    heading: str = ""
    subheading: str = ""
    text: str = ""
    bullets: list[str] = Field(default_factory=list)


class Section(BaseModel):
    title: str
    entries: list[Entry] = Field(default_factory=list, max_length=80)


class CV(BaseModel):
    name: str = ""
    headline: str = ""
    contact: list[str] = Field(default_factory=list)
    sections: list[Section] = Field(default_factory=list, max_length=40)

    def text(self) -> str:
        out = [self.name, self.headline, *self.contact]
        for s in self.sections:
            out.append(s.title)
            for e in s.entries:
                out += [e.heading, e.subheading, e.text]
                out += [f"• {b}" for b in e.bullets]
        return "\n".join(x for x in out if x)


class Term(BaseModel):
    term: str
    aliases: list[str] = Field(default_factory=list)
    kind: str = "required"  # required | preferred
    weight: float = 1.0


class JobIn(BaseModel):
    title: str = Field(default="", max_length=300)
    text: str = Field(default="", max_length=60000)
    url: str = Field(default="", max_length=2000)


class JobAnalysis(BaseModel):
    title: str = Field(max_length=300)
    text: str = Field(max_length=60000)
    min_years: float | None = None
    terms: list[Term] = Field(default_factory=list, max_length=80)
    responsibilities: list[str] = Field(default_factory=list, max_length=40)
    source: str = "rules"  # rules | llm


class Suggestion(BaseModel):
    id: str
    kind: str  # missing | implied | title | experience | format | version
    priority: str  # required | preferred | info
    term: str = ""
    message: str
    evidence: str = ""  # CV bullet that already shows the skill
    needs_confirmation: bool = False


class JobScore(BaseModel):
    total: int
    keywords: float
    semantic: float
    title: float
    experience: float
    format: float
    cv_years: float | None
    matched: int
    total_terms: int
    term_results: list[dict]
    suggestions: list[Suggestion]


class AnalyzeResult(BaseModel):
    cv: CV
    warnings: list[str]
    jobs: list[dict]
    average: int
    common_gaps: list[dict]
    engine: dict


class ApplyIn(BaseModel):
    cv: CV
    jobs: list[JobAnalysis] = Field(max_length=15)
    confirmed_terms: list[str] = Field(default_factory=list, max_length=100)
    accepted: list[Suggestion] = Field(default_factory=list, max_length=200)
    headline: str | None = None
    use_llm: bool = True


class PdfIn(BaseModel):
    cv: CV
