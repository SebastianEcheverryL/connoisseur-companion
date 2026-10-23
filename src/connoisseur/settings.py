"""Typed application settings, read from environment variables and an optional .env file."""
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    # CONNOISSEUR_ENV_FILE lets tests (and containers) opt out of reading a developer's .env
    model_config = SettingsConfigDict(
        env_file=os.environ.get("CONNOISSEUR_ENV_FILE", ".env"), env_file_encoding="utf-8", extra="ignore"
    )

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
    # Verify the tool agent's answer against the tool results and remove unsupported claims (1 extra LLM call).
    # On by default: in repeated runs it cut invented facts from 27% to 7% (docs/evals/report.md)
    enable_grounding_check: bool = True

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
    overpass_timeout_s: float = 12.0
    # Fallback when every Overpass server is busy: Komoot's free OSM search with location bias
    photon_url: str = "https://photon.komoot.io/api/"
    # Nominatim's usage policy requires an identifying User-Agent: set OSM_USER_AGENT with a contact URL
    osm_user_agent: str = "connoisseur-companion/0.2 (portfolio demo)"
    tavily_api_key: SecretStr | None = None
    tavily_url: str = "https://api.tavily.com/search"
    # Ratings research: one Tavily search per top OSM place, cached for a week
    rating_lookups: int = 3
    rating_cache_ttl_s: int = 7 * 24 * 3600
    rating_domains: list[str] = [
        "tripadvisor.com",
        "tripadvisor.com.co",
        "tripadvisor.com.mx",
        "tripadvisor.es",
        "restaurantguru.com",
        "yelp.com",
        "foursquare.com",
    ]
    web_search_radius_m: int = 2000
    web_max_places: int = 8
    http_timeout_s: float = 20.0
    cache_dir: Path = Field(default=PROJECT_DIR / ".cache")
    cache_ttl_s: int = 24 * 3600

    # Observability: Langfuse (self-hosted locally with infra/langfuse, Langfuse Cloud in the demo)
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "http://localhost:3000"
    langfuse_sample_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    # Fetch agent prompts from Langfuse Prompt Management (local .md files remain the fallback)
    langfuse_prompts: bool = False
    langfuse_prompt_label: str = "production"

    # Guardrails: "local" (Llama Guard via Ollama + Prompt Guard ONNX), "bedrock" (Bedrock Guardrails) or "none"
    guardrail_provider: Literal["local", "bedrock", "none"] = "local"
    # When a guardrail model is unavailable: True = block (safe default in prod), False = allow and log
    guardrail_fail_closed: bool = False
    prompt_guard_repo: str = "gravitee-io/Llama-Prompt-Guard-2-22M-onnx"
    prompt_guard_file: str = "model.quant.onnx"
    prompt_injection_threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    llama_guard_model: str = "llama-guard3:1b"
    # Llama Guard hazard categories that do NOT block. S6 (specialised advice) covers allergy/diet questions.
    # S7 (privacy) is allowed on input only: asking a restaurant's phone or address was flagged by the 1B model,
    # and the agent holds no data about private people; answers are still checked for S7.
    llama_guard_allowed_input_categories: list[str] = ["S6", "S7"]
    llama_guard_allowed_output_categories: list[str] = ["S6"]
    bedrock_guardrail_id: str | None = None
    bedrock_guardrail_version: str = "DRAFT"

    # Abuse and cost controls
    tool_timeout_s: float = 30.0
    request_timeout_s: float = 240.0
    daily_message_limit: int = 50
    per_minute_message_limit: int = 8
    # Optional shared secrets. API_KEY protects /chat and /feedback (the Vercel BFF will hold it);
    # MCP_TOKEN protects /mcp. Empty = open, for local development only.
    api_key: SecretStr | None = None
    mcp_token: SecretStr | None = None

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
