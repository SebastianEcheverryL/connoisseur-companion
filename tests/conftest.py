"""Test configuration: no network caches, no dev UI, no index unless a test builds one."""
import os

# Must run before connoisseur.settings is imported anywhere (settings are cached)
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("ENABLE_DEV_UI", "false")
os.environ.setdefault("CHROMA_DIR", os.path.join(os.path.dirname(__file__), ".no-index"))
os.environ.pop("TAVILY_API_KEY", None)
