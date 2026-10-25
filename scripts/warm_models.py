"""Download every ONNX model the API loads at runtime into EMBEDDING_CACHE_DIR.

Used when building the container image, so a cold Lambda start never downloads models
(the function then runs with HF_HUB_OFFLINE=1 on a read-only filesystem).
"""
from connoisseur.retrieval import embeddings
from connoisseur.security.prompt_guard import get_prompt_guard
from connoisseur.settings import get_settings


def main() -> None:
    cache = get_settings().embedding_cache_dir
    if cache is None:
        raise SystemExit("Set EMBEDDING_CACHE_DIR to the directory baked into the image")
    embeddings.embed_texts(["warm up"])
    embeddings.embed_query_clip_text("warm up")
    if get_prompt_guard() is None:
        raise SystemExit("Prompt Guard could not be downloaded")
    print(f"models cached in {cache}")


if __name__ == "__main__":
    main()
