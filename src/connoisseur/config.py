"""Backwards-compatible module-level constants. New code should use `connoisseur.settings.get_settings()`."""
from connoisseur.settings import PROJECT_DIR, get_settings

_settings = get_settings()

DATA_DIR = _settings.data_dir
RESTAURANT_DATA_PATH = _settings.restaurant_data_path
REVIEW_DATA_PATH = _settings.review_data_path
CULINARY_MAP_PATH = _settings.culinary_map_path

CHAT_MODEL = _settings.chat_model
EXTRACTION_MODEL = _settings.extraction_model

__all__ = [
    "CHAT_MODEL",
    "CULINARY_MAP_PATH",
    "DATA_DIR",
    "EXTRACTION_MODEL",
    "PROJECT_DIR",
    "RESTAURANT_DATA_PATH",
    "REVIEW_DATA_PATH",
]
