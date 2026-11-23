import os

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
EMBEDDINGS = os.getenv("EMBEDDINGS", "on").lower() in ("1", "on", "true", "yes")
EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
IMPLIED_THRESHOLD = float(os.getenv("IMPLIED_THRESHOLD", "0.78"))
SERVICE_API_KEY = os.getenv("SERVICE_API_KEY", "")
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
MAX_UPLOAD_MB = float(os.getenv("MAX_UPLOAD_MB", "5"))
MAX_JOBS = int(os.getenv("MAX_JOBS", "15"))
MAX_TEXT_CHARS = int(os.getenv("MAX_TEXT_CHARS", "60000"))      # per CV text / job description
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "20"))  # API calls per client IP per minute
LLM_DAILY_LIMIT = int(os.getenv("LLM_DAILY_LIMIT", "200"))       # Claude calls per day across all users; 0 = unlimited
