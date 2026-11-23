import json
import os

os.environ.setdefault("EMBEDDINGS", "off")
os.environ.pop("ANTHROPIC_API_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402

from app import pdfgen, parsing  # noqa: E402
from app.main import app  # noqa: E402
from app.models import CV, Entry, Section  # noqa: E402

client = TestClient(app)

SAMPLE = CV(
    name="Ayesha Khan", headline="Senior Backend Engineer",
    contact=["ayesha@example.com", "+92 300 1234567", "linkedin.com/in/ayesha"],
    sections=[
        Section(title="Summary", entries=[Entry(text="Backend engineer with 6+ years building payment systems in Java and Spring Boot.")]),
        Section(title="Experience", entries=[
            Entry(heading="Acme Pay — Senior Backend Engineer", subheading="Lahore · Mar 2022 – Present",
                  bullets=["Built Kafka consumers that process 2M card events a day with exactly-once handling.",
                           "Cut p95 latency 40% by tuning MySQL queries and adding Redis caching."]),
            Entry(heading="Bitsoft — Software Engineer", subheading="Karachi · Jul 2019 – Feb 2022",
                  bullets=["Shipped REST APIs and microservices on Docker and Kubernetes."]),
        ]),
        Section(title="Skills", entries=[Entry(text="Backend: Java, Spring Boot, Kafka, MySQL, Redis"), Entry(text="Platform: Docker, Kubernetes, Git")]),
        Section(title="Education", entries=[Entry(heading="FAST NUCES — BS Computer Science", subheading="2015 – 2019")]),
    ],
)
JD = {"title": "Senior Java Engineer", "text": """About the role
We build payments infrastructure.
Requirements
- 5+ years of Java and Spring Boot
- PostgreSQL and REST APIs, microservices
- Kafka, Docker, Kubernetes
- JUnit and Git
Nice to have
- Terraform, Prometheus, OAuth2"""}


def test_parse_roundtrip_and_years():
    pdf, pages = pdfgen.render(SAMPLE)
    assert pages == 1
    lines, warnings = parsing.pdf_lines(pdf)
    cv = parsing.build_cv(lines)
    assert cv.name == "Ayesha Khan"
    assert any(s.title == "Experience" for s in cv.sections)
    assert 6 <= parsing.experience_years(cv) <= 8


def test_analyze_apply_pdf():
    pdf, _ = pdfgen.render(SAMPLE)
    r = client.post("/api/analyze", files={"cv_file": ("cv.pdf", pdf, "application/pdf")},
                    data={"jobs": json.dumps([JD]), "use_llm": "false"})
    assert r.status_code == 200, r.text
    body = r.json()
    job = body["jobs"][0]
    missing = {t["term"] for t in job["score"]["term_results"] if t["credit"] == 0}
    assert {"PostgreSQL", "JUnit", "Terraform"} <= missing
    assert "Kafka" not in missing
    before = job["score"]["total"]
    ap = client.post("/api/apply", json={"cv": body["cv"], "jobs": [job["analysis"]], "confirmed_terms": ["PostgreSQL", "JUnit"],
                                         "headline": "Senior Java Backend Engineer", "use_llm": False})
    assert ap.status_code == 200, ap.text
    a = ap.json()
    assert a["after"]["jobs"][0]["score"]["total"] > before
    assert "PostgreSQL" in json.dumps(a["cv"])
    p = client.post("/api/pdf", json={"cv": a["cv"]})
    assert p.status_code == 200 and p.content[:4] == b"%PDF"


def test_fact_check_blocks_invented_numbers():
    from app.rewrite import fact_check
    bad = SAMPLE.model_copy(deep=True)
    bad.sections[1].entries[0].bullets[0] = "Built Kafka consumers processing 9M events, saving $3M."
    assert fact_check(SAMPLE, bad, [])


def test_fetch_refuses_internal_addresses():
    for url in ["http://127.0.0.1/", "http://localhost:8000/admin", "http://169.254.169.254/latest/meta-data/",
                "http://10.0.0.5/", "http://[::1]/", "file:///etc/passwd"]:
        r = client.post("/api/fetch-job", json={"url": url})
        assert r.status_code == 422, (url, r.text)


def test_no_extracted_terms_is_not_a_free_pass():
    r = client.post("/api/score", json={"cv": {"name": "x"}, "jobs": [{"title": "Chef", "text": "cook food"}]})
    assert r.status_code == 200
    assert r.json()["jobs"][0]["score"]["total"] < 40


def test_oversized_inputs_rejected():
    r = client.post("/api/analyze", data={"cv_text": "x " * 40000, "jobs": json.dumps([JD])})
    assert r.status_code == 413
    r = client.post("/api/analyze", content=b"0" * (8 * 1024 * 1024), headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_rate_limit(monkeypatch):
    from app import config, limits
    monkeypatch.setattr(config, "RATE_LIMIT_PER_MIN", 3)
    limits._hits.clear()
    codes = [client.post("/api/score", json={"cv": SAMPLE.model_dump(), "jobs": []}).status_code for _ in range(5)]
    limits._hits.clear()
    assert codes[:3] == [200] * 3 and codes[3:] == [429, 429]


def test_llm_daily_budget(monkeypatch):
    from app import config, limits
    monkeypatch.setattr(config, "LLM_DAILY_LIMIT", 2)
    monkeypatch.setattr(limits, "_llm_used", 0)
    assert limits.take_llm() and limits.take_llm() and not limits.take_llm()


def test_healthz_is_cheap():
    assert client.get("/healthz").json() == {"ok": True}
