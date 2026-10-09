"""Shared paths and settings, overridable through environment variables."""
import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("CONNOISSEUR_DATA_DIR", PROJECT_DIR / "data"))

RESTAURANT_DATA_PATH = DATA_DIR / "structured_restaurant_data.json"
REVIEW_DATA_PATH = DATA_DIR / "augmented_user_review.json"
# Raw IBM text, fetched by scripts/download_data.py (not redistributed with the repo)
CULINARY_MAP_PATH = DATA_DIR / "California-Culinary-Map.txt"

CHAT_MODEL = os.environ.get("CHAT_MODEL", "llama3.1")
EXTRACTION_MODEL = os.environ.get("EXTRACTION_MODEL", "llama3")
SAMPLING_MODEL = os.environ.get("SAMPLING_MODEL", "claude-sonnet-4-20250514")
