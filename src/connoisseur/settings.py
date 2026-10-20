"""Typed application settings, read from environment variables and an optional .env file."""
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Runtime
    environment: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    log_json: bool = False

    # Data
    data_dir: Path = Field(default=PROJECT_DIR / "data", validation_alias="CONNOISSEUR_DATA_DIR")
    chroma_dir: Path = Field(default=PROJECT_DIR / ".chroma")

    # LLM provider: Ollama locally and in CI, Bedrock on AWS
    llm_provider: Literal["ollama", "bedrock"] = "ollama"
    chat_model: str = "llama3.1"
    extraction_model: str = "llama3"
    ollama_base_url: str = "http://localhost:11434"
    bedrock_model_id: str = "us.amazon.nova-lite-v1:0"
    aws_region: str = "us-east-1"
    llm_temperature: float = 0.3
    llm_max_tokens: int = 1024
    llm_timeout_s: float = 120.0

    # Agent behaviour
    max_react_steps: int = 6
    enable_expert_analysis: bool = True
    min_local_results: int = 3

    # Embeddings (ONNX models served by fastembed)
    text_embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    clip_text_model: str = "Qdrant/clip-ViT-B-32-text"
    clip_image_model: str = "Qdrant/clip-ViT-B-32-vision"
    embedding_cache_dir: Path | None = None
    fusion_w_text: float = 0.6
    fusion_w_img: float = 0.4

    # Web search: OpenStreetMap + Tavily
    nominatim_url: str = "https://nominatim.openstreetmap.org"
    # Public Overpass instances are often at capacity: try them in order, with backoff
    overpass_urls: list[str] = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.private.coffee/api/interpreter",
        "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    ]
    # Nominatim's usage policy requires an identifying User-Agent: set OSM_USER_AGENT with a contact URL
    osm_user_agent: str = "connoisseur-companion/0.2 (portfolio demo)"
    tavily_api_key: SecretStr | None = None
    tavily_url: str = "https://api.tavily.com/search"
    web_search_radius_m: int = 2000
    web_max_places: int = 8
    http_timeout_s: float = 20.0
    cache_dir: Path = Field(default=PROJECT_DIR / ".cache")
    cache_ttl_s: int = 24 * 3600

    # Optional local developer UI mounted on the API
    enable_dev_ui: bool = True

    @property
    def restaurant_data_path(self) -> Path:
        return self.data_dir / "structured_restaurant_data.json"

    @property
    def review_data_path(self) -> Path:
        return self.data_dir / "augmented_user_review.json"

    @property
    def culinary_map_path(self) -> Path:
        # Raw IBM text, fetched by scripts/download_data.py (not redistributed with the repo)
        return self.data_dir / "California-Culinary-Map.txt"

    @property
    def recipes_path(self) -> Path:
        return self.data_dir / "Recipes.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
