"""Graphiti component builders — embedder / LLM client / cross-encoder (reranker).

Shared by MemoryProvider (lifespan singleton) and seed_corpus (one-shot). Each builder selects
the concrete graphiti_core client per MemorySettings and wraps multi-provider chains in a
fallback adapter mirroring the app's own provider fallback. graphiti_core is imported lazily
(it is an optional dependency for this package).
"""

from __future__ import annotations

from typing import Any

from backend.infra.config import MemorySettings
from backend.infra.logging import get_logger

logger = get_logger(__name__)


def _needs_gemini(mem_settings: MemorySettings, llm_settings: Any) -> bool:
    """True if any Graphiti component (embedder, an LLM fallback slot, or a reranker
    in the cross-encoder chain) uses gemini — i.e. the secret/llm key must be fetched."""
    llm_fallback = [getattr(p, "value", p) for p in llm_settings.fallback_order]
    return "gemini" in (
        mem_settings.embedder_provider,
        *llm_fallback,
        *mem_settings.cross_encoder_order,
    )


def build_embedder(mem_settings: MemorySettings, *, gemini_key: str) -> Any:
    """Build the Graphiti embedder per ``embedder_provider``.

    WARNING: do not change embedder_provider after data has been written — vectors
    from different models are incompatible and would corrupt search.
    """
    if mem_settings.embedder_provider == "ollama":
        from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig

        return OpenAIEmbedder(
            config=OpenAIEmbedderConfig(
                api_key="ollama",  # Ollama ignores the key but the field is required
                base_url=f"{mem_settings.ollama_embedder_base_url}/v1",
                embedding_model=mem_settings.ollama_embedder_model,
                embedding_dim=mem_settings.ollama_embedder_dim,
            )
        )

    from graphiti_core.embedder.gemini import GeminiEmbedder, GeminiEmbedderConfig

    return GeminiEmbedder(
        config=GeminiEmbedderConfig(
            api_key=gemini_key,
            embedding_model=mem_settings.gemini_embedding_model,
        )
    )


def _build_single_llm_client(
    provider: Any, mem_settings: MemorySettings, llm_settings: Any, *, gemini_key: str
) -> Any:
    """Build one Graphiti LLM client for a single provider id ("ollama" | "gemini")."""
    if getattr(provider, "value", provider) == "ollama":
        from graphiti_core.llm_client.config import LLMConfig as GenericLLMConfig
        from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient

        return OpenAIGenericClient(
            config=GenericLLMConfig(
                api_key="ollama",
                base_url=f"{mem_settings.ollama_embedder_base_url}/v1",
                model=llm_settings.ollama_model,
            )
        )

    from graphiti_core.llm_client.gemini_client import GeminiClient, LLMConfig

    return GeminiClient(config=LLMConfig(api_key=gemini_key))


def _wrap_llm_fallback(clients: list[Any]) -> Any:
    """Wrap ordered Graphiti LLM clients so each is tried on failure.

    Graphiti accepts a single ``llm_client``; this gives the memory entity-extraction
    LLM the same provider fallback as the app's LlmClient (``llm.fallback_order``).

    Delegation happens at the PUBLIC ``generate_response`` level (not the protected
    ``_generate_response``) because concrete clients override ``generate_response`` to
    unpack the ``(response, input_tokens, output_tokens)`` tuple and record token
    usage — bypassing that override would return the raw tuple and break Graphiti
    downstream. Each attempt gets a deep copy of the messages because
    ``generate_response`` mutates them in place (schema injection, etc.). Defined
    lazily because graphiti_core is an optional import for this module.
    """
    from graphiti_core.llm_client.client import LLMClient
    from graphiti_core.llm_client.config import ModelSize

    class _FallbackLLMClient(LLMClient):
        def __init__(self, inner: list[Any]) -> None:
            super().__init__(config=inner[0].config, cache=False)
            self._inner = inner

        def set_tracer(self, tracer: Any) -> None:
            super().set_tracer(tracer)
            for client in self._inner:
                client.set_tracer(tracer)

        async def _generate_response(self, *args: Any, **kwargs: Any) -> Any:
            # Never called — generate_response is overridden — but required by the ABC.
            raise NotImplementedError

        async def generate_response(
            self,
            messages: Any,
            response_model: Any = None,
            max_tokens: int | None = None,
            model_size: Any = ModelSize.medium,
            group_id: str | None = None,
            prompt_name: str | None = None,
            *,
            attribute_extraction: bool = False,
        ) -> Any:
            last_exc: Exception | None = None
            for client in self._inner:
                try:
                    # Fresh copy: generate_response mutates messages (schema injection).
                    msgs = [m.model_copy(deep=True) for m in messages]
                    resp = await client.generate_response(
                        msgs,
                        response_model,
                        max_tokens,
                        model_size,
                        group_id,
                        prompt_name,
                        attribute_extraction=attribute_extraction,
                    )
                    # Confirm the shape constructs the expected model so a provider that
                    # returns a malformed response falls through to the next one.
                    if response_model is not None:
                        response_model.model_validate(resp)
                    return resp
                except Exception as exc:
                    last_exc = exc
                    logger.warning(
                        "memory_llm_provider_failed",
                        provider=type(client).__name__,
                        error=str(exc),
                    )
            assert last_exc is not None  # at least one client always present
            raise last_exc

    return _FallbackLLMClient(clients)


def build_llm_client(mem_settings: MemorySettings, llm_settings: Any, *, gemini_key: str) -> Any:
    """Build the Graphiti entity-extraction LLM following ``llm.fallback_order``.

    One provider → that client; multiple → a fallback wrapper that tries each in
    order. Decoupled from ``embedder_provider`` (generation and embedding are
    independent), mirroring the app's own provider fallback.
    """
    clients = [
        _build_single_llm_client(p, mem_settings, llm_settings, gemini_key=gemini_key)
        for p in llm_settings.fallback_order
    ]
    return clients[0] if len(clients) == 1 else _wrap_llm_fallback(clients)


# ── cross-encoder (reranker) ─────────────────────────────────────────────────


def _build_single_cross_encoder(
    provider: Any, mem_settings: MemorySettings, llm_settings: Any, *, gemini_key: str
) -> Any:
    """Build one Graphiti reranker for a single provider id ("gemini" | "ollama").

    Both are LLM-as-reranker clients (not true cross-encoders): gemini scores 0-100
    directly; ollama reuses ``llm.ollama_model`` via OpenAIRerankerClient (logprob-
    limited, hence a last-resort fallback only). Neither needs a dedicated model pull.
    """
    from graphiti_core.llm_client.config import LLMConfig

    if getattr(provider, "value", provider) == "ollama":
        from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient

        return OpenAIRerankerClient(
            config=LLMConfig(
                api_key="ollama",  # Ollama ignores the key but the field is required
                base_url=f"{mem_settings.ollama_embedder_base_url}/v1",
                model=llm_settings.ollama_model,
            )
        )

    from graphiti_core.cross_encoder.gemini_reranker_client import GeminiRerankerClient

    return GeminiRerankerClient(config=LLMConfig(api_key=gemini_key))


def _wrap_cross_encoder_fallback(clients: list[Any]) -> Any:
    """Wrap ordered Graphiti rerankers so each is tried on failure.

    Graphiti accepts a single ``cross_encoder``; this gives reranking the same
    provider fallback as the LLM (``cross_encoder_order``) — e.g. gemini primary,
    ollama only when gemini's ``rank`` raises. Defined lazily because graphiti_core
    is an optional import for this module.
    """
    from graphiti_core.cross_encoder.client import CrossEncoderClient

    class _FallbackCrossEncoder(CrossEncoderClient):
        def __init__(self, inner: list[Any]) -> None:
            self._inner = inner

        async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
            last_exc: Exception | None = None
            for client in self._inner:
                try:
                    return await client.rank(query, passages)
                except Exception as exc:
                    last_exc = exc
                    logger.warning(
                        "memory_reranker_provider_failed",
                        provider=type(client).__name__,
                        error=str(exc),
                    )
            assert last_exc is not None  # at least one client always present
            raise last_exc

    return _FallbackCrossEncoder(clients)


def build_cross_encoder(mem_settings: MemorySettings, llm_settings: Any, *, gemini_key: str) -> Any:
    """Build the Graphiti reranker following ``cross_encoder_order``.

    One provider → that client; multiple → a fallback wrapper that tries each in
    order. Always explicit so Graphiti never falls back to its default
    ``OpenAIRerankerClient`` (which requires a real ``OPENAI_API_KEY``).
    """
    clients = [
        _build_single_cross_encoder(p, mem_settings, llm_settings, gemini_key=gemini_key)
        for p in mem_settings.cross_encoder_order
    ]
    return clients[0] if len(clients) == 1 else _wrap_cross_encoder_fallback(clients)
