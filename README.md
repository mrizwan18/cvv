# CV Matcher

Scores a CV against any number of job openings the way applicant tracking systems read them, explains every gap, applies the fixes you confirm, and returns a clean ATS-friendly PDF.

## How the score works

| Part | Points | What it checks |
|---|---|---|
| Required keywords | 45 | Each requirement is matched in stages: exact or alias in your experience (full credit), only in the skills list (0.9), version variant like "Java" for "Java 17" (0.75), spelling variant (0.8), or implied by a bullet that describes the work without naming it (0.5, semantic match). |
| Nice-to-haves | 10 | Same matching. |
| Fit with the duties | 10 | Semantic similarity between the posting's responsibilities and your bullets, plus whole-document similarity. |
| Job title | 15 | Whether your headline and recent roles echo the posting's title. |
| Experience | 10 | Years computed from your dated roles (overlaps merged, education excluded) against the years asked. |
| Format | 10 | Contact details, standard headings, length, columns, image-only text. |

Requirements come from Claude reading the posting (required vs nice-to-have, aliases, responsibilities) when `ANTHROPIC_API_KEY` is set, otherwise from a built-in taxonomy of ~250 skills and section cues ("Requirements", "Nice to have").

No tool can reproduce every ATS exactly (Workday, Greenhouse and Lever rank differently and don't publish their logic). This one is transparent: every point is traceable to a matched or missing term.

## Honest rewriting

- Missing skills are only added after you tick "I have this".
- With Claude, the rewrite is fact-checked: if it adds any number that wasn't in your CV or drops a role, it's rejected and the safe rule-based edit is used instead.
- Without Claude, confirmed skills go into the matching line of your skills section, and confirmed "implied" skills are named in the bullet that shows them.

## API

| Method | Path | Body |
|---|---|---|
| GET | `/api/health` | Shows whether Claude and embeddings are active, and Claude calls left today |
| GET | `/healthz` | Cheap liveness check for uptime pingers |
| POST | `/api/analyze` | multipart: `cv_file` (PDF) or `cv_text`, `jobs` = JSON list of `{title, text, url}`, `use_llm` |
| POST | `/api/score` | JSON `{cv, jobs}` (re-score an edited CV) |
| POST | `/api/apply` | JSON `{cv, jobs, confirmed_terms, accepted, headline, use_llm}` |
| POST | `/api/pdf` | JSON `{cv}` → PDF |
| POST | `/api/fetch-job` | JSON `{url}` → `{title, text}` (reads schema.org JobPosting where available) |

Interactive docs at `/docs`. The web UI is served at `/`.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env   # add ANTHROPIC_API_KEY if you want Claude
uvicorn app.main:app --reload
pytest
```

## Running it publicly

- **Rate limit:** `RATE_LIMIT_PER_MIN` calls per visitor IP (default 20). In memory, so it assumes one instance.
- **Claude budget:** `LLM_DAILY_LIMIT` Claude calls per day across everyone (default 200). When it runs out, postings are read and CVs rewritten by the rule engine instead, so the site keeps working without running up your Anthropic bill.
- **Job links:** the fetcher only reaches public addresses on ports 80/443 and re-checks every redirect, so it can't be pointed at internal services.
- **Size caps:** uploads `MAX_UPLOAD_MB`, pasted text `MAX_TEXT_CHARS`, at most `MAX_JOBS` openings per request.
- **Privacy:** CVs are processed in memory and never stored. With Claude on, CV text is sent to Anthropic's API.

## Deploy on Render

1. Push this folder to a GitHub repo.
2. In Render: **New → Blueprint**, pick the repo. Render reads `render.yaml` and builds the Dockerfile.
3. Set `ANTHROPIC_API_KEY` (and optionally `SERVICE_API_KEY`) in the service's Environment tab.
4. Open `https://<your-service>.onrender.com`.

On the free plan the service sleeps after 15 minutes idle (the first request then takes ~30–60 s). Measured peak memory is ~320 MB with embeddings on, within the free 512 MB; if it ever runs out, set `EMBEDDINGS=off` and it falls back to lexical similarity.

Don't add a keep-awake pinger on the free plan if another free service in the same Render workspace is already kept awake: free instance hours (750/month) are shared per workspace, and two always-on services exceed them. Upgrade to Starter instead if it must stay warm.
