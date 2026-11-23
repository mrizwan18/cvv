"""Job description analysis (LLM or rules) and URL fetching."""
from __future__ import annotations

import ipaddress
import json
import re
import socket

import httpx
from bs4 import BeautifulSoup

from . import llm, taxonomy
from .models import JobAnalysis, JobIn, Term

PREF_RE = re.compile(r"nice[\s-]to[\s-]have|bonus|preferred|is a plus|are a plus|would be a plus|good to have|desirable|nice if|ideally|extra credit|pluses", re.I)
REQ_RE = re.compile(r"requirements|must[\s-]have|what you('ll)? (bring|need)|qualifications|you have|you bring|required|essential|minimum|what we('re)? looking for|skills", re.I)
YEARS_RE = re.compile(r"(\d{1,2})\s*\+?\s*(?:-|to|–)?\s*(?:\d{1,2}\s*)?\+?\s*(?:years|yrs)", re.I)


def title_of(job: JobIn) -> str:
    if job.title.strip():
        return job.title.strip()
    m = re.search(r"^(?:job title|position|role)\s*[:\-]\s*(.+)$", job.text, re.I | re.M)
    if m:
        return m.group(1).strip()
    first = job.text.strip().split("\n")[0].strip()
    return first if len(first) < 90 else "Opening"


def years_asked(text: str) -> float | None:
    vals = [int(m.group(1)) for m in YEARS_RE.finditer(text) if 0 < int(m.group(1)) < 25]
    return float(min(vals)) if vals else None


def analyse_rules(job: JobIn) -> JobAnalysis:
    found: dict[str, Term] = {}
    counts: dict[str, int] = {}
    mode = "required"
    for line in job.text.splitlines():
        short = len(line.strip()) < 60
        if short and PREF_RE.search(line):
            mode = "preferred"
        elif short and REQ_RE.search(line):
            mode = "required"
        kind = "preferred" if (not short and PREF_RE.search(line)) else mode
        for s in taxonomy.find_skills(line):
            t = found.get(s.term)
            if t is None:
                found[s.term] = Term(term=s.term, aliases=s.aliases, kind=kind)
            elif kind == "required":
                t.kind = "required"
            counts[s.term] = counts.get(s.term, 0) + 1
    for k, t in found.items():
        t.weight = 1.5 if counts[k] > 1 else 1.0
    resp = [l.strip(" •-*\t") for l in job.text.splitlines() if 30 < len(l.strip()) < 220][:25]
    return JobAnalysis(title=title_of(job), text=job.text, min_years=years_asked(job.text),
                       terms=list(found.values()), responsibilities=resp, source="rules")


LLM_PROMPT = """You extract the screening criteria an applicant tracking system and a recruiter would use from a job posting.
Return ONLY JSON: {{"title": string, "min_years": number|null, "required": [{{"term": string, "aliases": [string]}}], "preferred": [{{"term": string, "aliases": [string]}}], "responsibilities": [string]}}
- Terms are concrete, matchable keywords: languages, frameworks, tools, platforms, databases, protocols, practices (TDD, code review, Agile), certifications, domain terms (payments, KYC). 1-4 words, as written in the posting, no version numbers unless essential.
- Skip soft skills and generic words (communication, team player, passion).
- Up to 25 required, 15 preferred. required = must-haves and core duties; preferred = nice-to-have, bonus, plus.
- aliases = alternative spellings a CV may use (Kubernetes -> k8s, PostgreSQL -> Postgres).
- responsibilities = up to 10 short phrases (5-12 words) describing what the person will actually do.
Posting title (may be blank): {title}
Posting:
<<<
{text}
>>>"""


def analyse(job: JobIn, use_llm: bool) -> JobAnalysis:
    if use_llm and llm.available():
        r = llm.ask_json(LLM_PROMPT.format(title=job.title, text=job.text[:14000]))
        if isinstance(r, dict) and (r.get("required") or r.get("preferred")):
            terms: list[Term] = []
            seen = set()
            for kind in ("required", "preferred"):
                for x in r.get(kind) or []:
                    t = str((x or {}).get("term", "")).strip()
                    if not t or t.lower() in seen:
                        continue
                    seen.add(t.lower())
                    sk = taxonomy.lookup(t)
                    aliases = [str(a) for a in (x.get("aliases") or [])]
                    if sk:
                        aliases += [sk.term, *sk.aliases]
                    aliases = sorted({a for a in aliases if a and a.lower() != t.lower()})
                    n = len(taxonomy.term_regex(t, *aliases).findall(job.text))
                    terms.append(Term(term=t, aliases=aliases, kind=kind, weight=1.5 if n > 1 else 1.0))
            my = r.get("min_years")
            return JobAnalysis(
                title=job.title.strip() or str(r.get("title") or title_of(job)), text=job.text,
                min_years=float(my) if isinstance(my, (int, float)) else years_asked(job.text),
                terms=terms, responsibilities=[str(x) for x in (r.get("responsibilities") or [])][:10], source="llm",
            )
    return analyse_rules(job)


# ---------- URL fetching ----------
MAX_PAGE_BYTES = 3 * 1024 * 1024


def _check_public(url: str) -> None:
    """Refuse URLs that resolve to private, loopback, link-local or reserved
    addresses, so the public fetcher can't be used to reach internal services."""
    u = httpx.URL(url)
    if u.scheme not in ("http", "https") or not u.host:
        raise ValueError("URL must start with http:// or https://")
    if u.port not in (None, 80, 443):
        raise ValueError("Only standard web ports are allowed.")
    try:
        infos = socket.getaddrinfo(u.host, None)
    except socket.gaierror:
        raise ValueError(f"Couldn't resolve {u.host}.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global or ip.is_multicast:
            raise ValueError("That address isn't a public web page.")


def _get_public(url: str) -> str:
    with httpx.Client(follow_redirects=False, timeout=20, headers={"User-Agent": "Mozilla/5.0 (cv-matcher)"}) as c:
        for _ in range(6):
            _check_public(url)
            with c.stream("GET", url) as r:
                if r.is_redirect:
                    url = str(r.url.join(r.headers.get("location", "")))
                    continue
                r.raise_for_status()
                body = b""
                for chunk in r.iter_bytes():
                    body += chunk
                    if len(body) > MAX_PAGE_BYTES:
                        raise ValueError("That page is too large to read.")
                return body.decode(r.charset_encoding or "utf-8", errors="replace")
    raise ValueError("Too many redirects.")


def fetch_job(url: str) -> JobIn:
    if not re.match(r"^https?://", url):
        raise ValueError("URL must start with http:// or https://")
    soup = BeautifulSoup(_get_public(url), "html.parser")
    # Most ATS pages (Greenhouse, Lever, Workable, Zoho, BambooHR) embed schema.org JobPosting
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except Exception:
            continue
        for item in data if isinstance(data, list) else data.get("@graph", [data]):
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                desc = BeautifulSoup(item.get("description", ""), "html.parser").get_text("\n")
                return JobIn(title=item.get("title", ""), text=_clean(desc), url=url)
    for t in soup(["script", "style", "nav", "header", "footer", "noscript"]):
        t.decompose()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    title = (soup.find("h1").get_text(" ", strip=True) if soup.find("h1") else (soup.title.string if soup.title else ""))
    text = _clean(main.get_text("\n"))
    if len(text) < 200:
        raise ValueError("Couldn't read a job description from that page (it may load with JavaScript). Paste the text instead.")
    return JobIn(title=title or "", text=text, url=url)


def _clean(t: str) -> str:
    lines = [re.sub(r"\s+", " ", l).strip() for l in t.splitlines()]
    return "\n".join(l for l in lines if l)[:20000]
