"""Skill taxonomy: canonical term -> aliases, grouped by category.

Aliases are the spellings a CV or posting might use. Matching is
case-insensitive with word boundaries that respect C++, C#, .NET etc.
"""
from __future__ import annotations

import re
from functools import lru_cache

_RAW: dict[str, str] = {
    "language": "Java|Java 8|Java 11|Java 17|Java 21;Kotlin;Scala;Python;JavaScript|JS;TypeScript;Go|Golang;Rust;C++;C#;.NET|dotnet|.NET Core;PHP;Ruby;Swift;SQL;PL/SQL;Bash|shell scripting;Groovy",
    "framework": "Spring Boot;Spring Framework|Spring MVC;Spring Security;Spring Cloud;Spring Data|Spring Data JPA;Spring Batch;Spring WebFlux|WebFlux;Hibernate;JPA;jOOQ;MyBatis;Quarkus;Micronaut;Vert.x;Jakarta EE|Java EE|J2EE;Node.js;Express.js;NestJS;Django;Flask;FastAPI;Ruby on Rails|Rails;Laravel;React;Angular;Vue.js|Vue;Next.js",
    "architecture": "REST APIs|REST|RESTful|REST API;GraphQL;gRPC;SOAP;OpenAPI|Swagger;WebSockets|WebSocket;Microservices|microservice;Event-driven architecture|event-driven|event driven;CQRS;Saga pattern|SAGA;Domain-driven design|DDD;Distributed systems|distributed system;System design;Design patterns;OOP|object-oriented|object oriented;SOLID;Clean code;Clean architecture|hexagonal architecture;Concurrency|multithreading|multi-threading;Reactive programming;Caching;API design;Idempotency|idempotent",
    "messaging": "Kafka|Apache Kafka;RabbitMQ;ActiveMQ;Amazon SQS|SQS;Amazon SNS|SNS;Google Pub/Sub|Pub/Sub;Kafka Streams;Apache Flink|Flink;Apache Spark|Spark",
    "data": "MySQL;PostgreSQL|Postgres;Oracle Database|Oracle DB;SQL Server|MSSQL;MongoDB|Mongo;Cassandra;DynamoDB;Redis;Elasticsearch|OpenSearch;ClickHouse;Snowflake;BigQuery;NoSQL;Liquibase;Flyway;Query optimization|query tuning|performance tuning;Database design|data modelling|data modeling",
    "cloud": "AWS|Amazon Web Services;GCP|Google Cloud|Google Cloud Platform;Azure|Microsoft Azure;AWS Lambda;Amazon S3;Amazon EC2;Amazon ECS;Amazon EKS|EKS;GKE;Serverless;Docker;Kubernetes|k8s;Helm;OpenShift;Terraform;Infrastructure as code|IaC;Ansible;CloudFormation",
    "delivery": "CI/CD|continuous integration|continuous delivery;Jenkins;GitHub Actions;GitLab CI|GitLab;CircleCI;Harness;Argo CD|ArgoCD;Git;Maven;Gradle;SonarQube;Linux",
    "testing": "JUnit|JUnit 5;Mockito;Testcontainers;TDD|test-driven development|test-driven;BDD|Cucumber;Integration testing|integration tests;Unit testing|unit tests;Performance testing|load testing|JMeter|Gatling;Contract testing|Pact",
    "security": "OAuth2|OAuth|OAuth 2.0;OpenID Connect|OIDC;JWT;Keycloak;SAML;Identity and access management|IAM;OWASP;Application security|secure coding;HashiCorp Vault|Vault;Encryption;PCI DSS|PCI;SOC 2;GDPR;KYC;AML",
    "observability": "Prometheus;Grafana;Datadog;New Relic;OpenTelemetry;ELK stack|ELK|Kibana|Logstash;Splunk;Sentry;Observability|monitoring",
    "practice": "Agile;Scrum;Kanban;Jira;Confluence;Code review|code reviews;Mentoring|mentorship;Technical leadership|tech lead;Stakeholder management;On-call|on call;Incident management|incident response;Post-mortems|postmortem",
    "domain": "Payments|payment processing|payment systems;Fintech|financial technology;Core banking|banking;Ledger|ledgers;Card processing|card issuing;Insurance;E-commerce|ecommerce;Lending;Fraud detection|fraud prevention;Reconciliation",
    "ai": "Machine learning;LLM|LLMs|large language models;Generative AI|GenAI;AI-assisted development|Claude Code|GitHub Copilot;RAG|retrieval-augmented generation;Prompt engineering",
}


class Skill:
    __slots__ = ("term", "aliases", "category")

    def __init__(self, term: str, aliases: list[str], category: str):
        self.term, self.aliases, self.category = term, aliases, category


SKILLS: list[Skill] = []
for _cat, _blob in _RAW.items():
    for _item in _blob.split(";"):
        _parts = [p.strip() for p in _item.split("|") if p.strip()]
        SKILLS.append(Skill(_parts[0], _parts[1:], _cat))

BY_NAME: dict[str, Skill] = {}
for _s in SKILLS:
    for _n in [_s.term, *_s.aliases]:
        BY_NAME.setdefault(_n.lower(), _s)


def lookup(term: str) -> Skill | None:
    return BY_NAME.get(term.lower().strip())


@lru_cache(maxsize=4096)
def term_regex(*words: str) -> re.Pattern:
    alts = []
    for w in words:
        w = w.strip()
        if not w:
            continue
        alts.append(r"[\s\-]+".join(re.escape(p) for p in w.split()))
    alts.sort(key=len, reverse=True)
    return re.compile(r"(?<![A-Za-z0-9+#])(?:" + "|".join(alts) + r")(?![A-Za-z0-9+#])", re.I)


def find_skills(text: str) -> list[Skill]:
    return [s for s in SKILLS if term_regex(s.term, *s.aliases).search(text)]
