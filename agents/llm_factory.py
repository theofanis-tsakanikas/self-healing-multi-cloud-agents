import os

# Model-name prefix → provider
_PREFIX_TO_PROVIDER = {
    "gpt-":    "openai",
    "o1":      "openai",
    "o3":      "openai",
    "claude-": "anthropic",
    "gemini-": "vertexai",
}


def _infer_provider(model: str) -> str:
    for prefix, provider in _PREFIX_TO_PROVIDER.items():
        if model.startswith(prefix):
            return provider
    raise ValueError(
        f"Cannot infer LLM provider from model name '{model}'. "
        f"Set LLM_PROVIDER explicitly or use a recognised model prefix: "
        f"{list(_PREFIX_TO_PROVIDER.keys())}"
    )


def get_llm(temperature=0):
    model       = os.getenv("LLM_MODEL", "gpt-4o")
    timeout     = float(os.getenv("LLM_TIMEOUT_SEC", "120"))
    max_retries = int(os.getenv("LLM_MAX_RETRIES", "1"))

    # LLM_PROVIDER can override auto-detection (edge cases / private endpoints)
    provider = (os.getenv("LLM_PROVIDER") or _infer_provider(model)).lower()

    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model,
            temperature=temperature,
            timeout=timeout,
            max_retries=max_retries,
        )

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=model,
            temperature=temperature,
            timeout=timeout,
            max_retries=max_retries,
        )

    if provider == "vertexai":
        from langchain_google_vertexai import ChatVertexAI
        return ChatVertexAI(
            model=model,
            temperature=temperature,
            max_retries=max_retries,
            # timeout handled by Google SDK internally
        )

    raise ValueError(
        f"Unsupported LLM_PROVIDER '{provider}'. "
        "Valid options: 'openai', 'anthropic', 'vertexai'."
    )
