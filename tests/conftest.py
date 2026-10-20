"""Test configuration: no developer .env, no network caches, no dev UI, no tracing, no index."""
import os

# Must run before connoisseur.settings is imported anywhere (settings are cached)
os.environ["CONNOISSEUR_ENV_FILE"] = os.path.join(os.path.dirname(__file__), ".env.does-not-exist")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("ENABLE_DEV_UI", "false")
# Guardrail models are exercised in tests/test_security.py with fakes; the rest of the suite skips them
os.environ.setdefault("GUARDRAIL_PROVIDER", "none")
os.environ.setdefault("PER_MINUTE_MESSAGE_LIMIT", "1000")
os.environ.setdefault("DAILY_MESSAGE_LIMIT", "1000")
os.environ.setdefault("CHROMA_DIR", os.path.join(os.path.dirname(__file__), ".no-index"))
for var in ("TAVILY_API_KEY", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LLM_PROVIDER", "API_KEY", "MCP_TOKEN"):
    os.environ.pop(var, None)
