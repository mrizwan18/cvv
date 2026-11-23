from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, embed, jd, limits, llm, matcher, parsing, pdfgen, rewrite
from .models import ApplyIn, CV, JobAnalysis, JobIn, PdfIn

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="CV Matcher", version="1.0.0",
              description="Score a CV against job openings, get fixes, apply them and download an ATS-friendly PDF.")
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"],
                   expose_headers=["X-Pages"])
STATIC = Path(__file__).parent / "static"
MAX_BODY = int((config.MAX_UPLOAD_MB + 1) * 1024 * 1024)


@app.middleware("http")
async def guard(request: Request, call_next):
    if int(request.headers.get("content-length") or 0) > MAX_BODY:
        return JSONResponse({"detail": "Request is too large."}, status_code=413)
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    return resp


def auth(request: Request, x_api_key: str | None = Header(default=None)):
    if config.SERVICE_API_KEY and x_api_key != config.SERVICE_API_KEY:
        raise HTTPException(401, "Missing or wrong X-API-Key")
    limits.rate_limit(request)


@app.get("/healthz", include_in_schema=False)
def healthz():
    """Cheap liveness probe for uptime pingers: never loads models or calls Claude."""
    return {"ok": True}


@app.get("/api/health")
def health():
    return {"ok": True, "llm": llm.available(), "llm_left_today": limits.llm_left(), "similarity": embed.mode(),
            "auth": bool(config.SERVICE_API_KEY)}


def _summarise(cv: CV, warnings: list[str], analyses: list[JobAnalysis]) -> dict:
    ctx = matcher.context(cv, warnings)
    jobs, gaps = [], {}
    for i, a in enumerate(analyses):
        s = matcher.score(a, ctx, i)
        jobs.append({"analysis": a.model_dump(), "score": s.model_dump()})
        for r in s.term_results:
            if r["credit"] < 0.9:
                g = gaps.setdefault(r["term"].lower(), {"term": r["term"], "openings": 0, "required_in": 0})
                g["openings"] += 1
                g["required_in"] += r["kind"] == "required"
    common = sorted(gaps.values(), key=lambda g: (-g["required_in"], -g["openings"]))[:20]
    avg = round(sum(j["score"]["total"] for j in jobs) / len(jobs)) if jobs else 0
    return {"cv": cv.model_dump(), "warnings": warnings, "jobs": jobs, "average": avg, "common_gaps": common,
            "engine": {"llm": llm.available(), "similarity": embed.mode()}}


@app.post("/api/analyze", dependencies=[Depends(auth)])
def analyze(cv_file: UploadFile | None = File(default=None), cv_text: str = Form(default=""),
                  jobs: str = Form(..., description='JSON list of {"title","text","url"}'), use_llm: bool = Form(default=True)):
    try:
        job_list = [JobIn.model_validate(j) for j in json.loads(jobs)]
    except Exception:
        raise HTTPException(422, "jobs must be a JSON list of {title, text, url}")
    if not job_list:
        raise HTTPException(422, "Add at least one job opening.")
    if len(job_list) > config.MAX_JOBS:
        raise HTTPException(422, f"At most {config.MAX_JOBS} openings per request.")
    warnings: list[str] = []
    if cv_file is not None and cv_file.filename:
        data = cv_file.file.read(int(config.MAX_UPLOAD_MB * 1024 * 1024) + 1)
        if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024:
            raise HTTPException(413, f"CV file is over {config.MAX_UPLOAD_MB:g} MB.")
        try:
            lines, warnings = parsing.pdf_lines(data)
        except ValueError as e:
            raise HTTPException(422, str(e))
        except Exception:
            raise HTTPException(422, "Couldn't read that PDF. Export it again or paste the text.")
        cv = parsing.build_cv(lines)
    elif cv_text.strip():
        if len(cv_text) > config.MAX_TEXT_CHARS:
            raise HTTPException(413, "CV text is too long.")
        cv = parsing.build_cv(parsing.text_lines(cv_text))
    else:
        raise HTTPException(422, "Upload a CV PDF or paste its text.")
    for j in job_list:
        if not j.text.strip() and j.url:
            try:
                fetched = jd.fetch_job(j.url)
                j.text, j.title = fetched.text, j.title or fetched.title
            except Exception as e:
                raise HTTPException(422, f"Couldn't fetch {j.url}: {e}")
        if len(j.text.strip()) < 40:
            raise HTTPException(422, "Each opening needs a job description (or a URL we can read).")
    analyses = [jd.analyse(j, use_llm) for j in job_list]
    return _summarise(cv, warnings, analyses)


class RescoreIn(BaseModel):
    cv: CV
    jobs: list[JobAnalysis]


@app.post("/api/score", dependencies=[Depends(auth)])
def rescore(body: RescoreIn):
    return _summarise(body.cv, [], body.jobs)


@app.post("/api/apply", dependencies=[Depends(auth)])
def apply(body: ApplyIn):
    before = _summarise(body.cv, [], body.jobs)
    new, changes, how = rewrite.apply(body.cv, body.confirmed_terms, body.accepted, body.headline, body.use_llm)
    after = _summarise(new, [], body.jobs)
    _, pages = pdfgen.render(new)
    return {"cv": new.model_dump(), "changes": changes, "rewritten_by": how, "pages": pages,
            "before": [j["score"]["total"] for j in before["jobs"]], "after": after}


class UrlIn(BaseModel):
    url: str


@app.post("/api/fetch-job", dependencies=[Depends(auth)])
def fetch_job(body: UrlIn):
    try:
        return jd.fetch_job(body.url).model_dump()
    except Exception as e:
        raise HTTPException(422, str(e))


@app.post("/api/pdf", dependencies=[Depends(auth)])
def pdf(body: PdfIn):
    data, pages = pdfgen.render(body.cv)
    name = "".join(c for c in body.cv.name if c.isalnum() or c in " -_").strip().replace(" ", "_") or "CV"
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}_CV.pdf"', "X-Pages": str(pages)})


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
