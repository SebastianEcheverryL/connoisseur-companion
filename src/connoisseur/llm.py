"""LLM provider factory: the rest of the code never imports a provider SDK directly."""
from langchain_core.language_models.chat_models import BaseChatModel

from connoisseur.settings import get_settings


def get_chat_model(
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> BaseChatModel:
    """Return a chat model for the configured provider.

    `model` overrides the default model name (Ollama tag or Bedrock model id).
    """
    s = get_settings()
    temperature = s.llm_temperature if temperature is None else temperature
    max_tokens = max_tokens or s.llm_max_tokens

    if s.llm_provider == "bedrock":
        # Imported lazily so the local install does not need the AWS extras
        from langchain_aws import ChatBedrockConverse

        return ChatBedrockConverse(
            model=model or s.bedrock_model_id,
            region_name=s.aws_region,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    from langchain_ollama import ChatOllama

    return ChatOllama(
        model=model or s.chat_model,
        base_url=s.ollama_base_url,
        temperature=temperature,
        num_predict=max_tokens,
        client_kwargs={"timeout": s.llm_timeout_s},
    )


def message_text(content) -> str:
    """Flatten a LangChain message content (str or list of blocks) into plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            block.get("text", "") if isinstance(block, dict) else str(block) for block in content
        ).strip()
    return str(content)
