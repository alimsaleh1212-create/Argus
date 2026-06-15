"""Redaction seam — sanitize sensitive data before it leaves a trust boundary.

Implements the Redactor Protocol with two composed strategies:
  1. Deterministic secret scrubber: explicit regex patterns (AWS keys, bearer/JWT,
     PEM private-key blocks, secret=/token=/apikey= kv) + Shannon-entropy heuristic
     for high-entropy tokens that don't match an explicit pattern.
  2. Microsoft Presidio: PII entities (email, IP, credit card, IBAN, phone, person)
     via in-process AnalyzerEngine. Toggle via ObservabilitySettings.presidio_enabled.

Redaction is fail-closed (FR-003): if detection/anonymization raises, the output is
the fail_closed_placeholder, never the raw value.

Redaction applies the class×boundary policy from domain/redaction.py (FR-006a/b):
  - CREDENTIAL: scrubbed at every Boundary.
  - PII + OPERATIONAL_IDENTIFIER: scrubbed at output boundaries only.

The Presidio engine and scrubber are process singletons built once via
RedactorProvider (T010) and registered via the container seam.
"""

from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from backend.domain.redaction import Boundary

# ── Redactor Protocol (C1 fix: boundary param added) ─────────────────────────


@runtime_checkable
class Redactor(Protocol):
    """Sanitizes text/mappings; returns a redacted copy. Never mutates input."""

    def redact_text(self, text: str, boundary: Boundary) -> str: ...

    def redact_mapping(self, data: dict, boundary: Boundary) -> dict: ...


# ── Secret scrubber patterns ─────────────────────────────────────────────────

_PLACEHOLDER_RE = re.compile(r"\[REDACTED:[A-Z_]+\]|\[REDACTION-FAILED\]")

# AWS access key ID  (20 upper-alpha-num starting AKIA)
_AWS_KEY_RE = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
# Bearer / JWT tokens
_BEARER_RE = re.compile(
    r"\bBearer\s+[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\b",
    re.IGNORECASE,
)
# Raw JWT (three base64url segments)
_JWT_RE = re.compile(r"\b[A-Za-z0-9\-_]{10,}\.[A-Za-z0-9\-_]{10,}\.[A-Za-z0-9\-_]{10,}\b")
# PEM private key blocks
_PEM_RE = re.compile(
    r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----.*?-----END (?:RSA |EC )?PRIVATE KEY-----",
    re.DOTALL,
)
# kv patterns: apikey=, token=, secret=, password=, api_key=
_KV_SECRET_RE = re.compile(
    r"(?i)(?:api[_-]?key|token|secret|password|passwd|pwd|auth)\s*[=:]\s*([^\s,;\"']{6,})"
)

_CREDENTIAL_PATTERNS: list[tuple[re.Pattern[str], str | None]] = [
    (_AWS_KEY_RE, None),
    (_BEARER_RE, None),
    (_JWT_RE, None),
    (_PEM_RE, None),
    (_KV_SECRET_RE, 1),  # group 1 is the value
]


def _shannon_entropy(token: str) -> float:
    if not token:
        return 0.0
    freq = {}
    for ch in token:
        freq[ch] = freq.get(ch, 0) + 1
    length = len(token)
    return -sum((c / length) * math.log2(c / length) for c in freq.values())


def _scrub_text(text: str, placeholder: str, threshold: float) -> str:
    """Apply regex patterns + entropy heuristic; return scrubbed text."""
    result = text

    # Apply each explicit pattern
    for pattern, group in _CREDENTIAL_PATTERNS:
        if group is None:
            result = pattern.sub(placeholder, result)
        else:
            # Replace only the captured group (the value), not the key
            def _replace_group(m: re.Match[str], _grp: int = int(group)) -> str:
                full = m.group(0)
                val = m.group(_grp)
                # Don't double-redact placeholders
                if _PLACEHOLDER_RE.search(val):
                    return full
                return full.replace(val, placeholder, 1)

            result = pattern.sub(_replace_group, result)

    # Entropy heuristic: tokenize by whitespace/punctuation, flag high-entropy tokens
    parts = re.split(r"([\s,;\"'()\[\]{}<>])", result)
    out_parts = []
    for part in parts:
        if (
            len(part) >= 12
            and not _PLACEHOLDER_RE.fullmatch(part)
            and _shannon_entropy(part) >= threshold
        ):
            out_parts.append(placeholder)
        else:
            out_parts.append(part)
    return "".join(out_parts)


# ── Composite Redactor implementation ────────────────────────────────────────


class _CompositeRedactor:
    """Composes the secret scrubber and Presidio behind the Redactor Protocol."""

    def __init__(
        self,
        policy: RedactionPolicy,  # type: ignore[name-defined]  # noqa: F821
        entropy_threshold: float = 4.0,
        presidio_analyzer: object | None = None,
        presidio_anonymizer: object | None = None,
    ) -> None:

        self._policy = policy
        self._threshold = entropy_threshold
        self._analyzer = presidio_analyzer
        self._anonymizer = presidio_anonymizer

    # -- internal helpers ---------------------------------------------------

    def _scrub(self, text: str) -> str:
        return _scrub_text(
            text,
            self._policy.fail_closed_placeholder,
            self._threshold,
        )

    def _presidio_redact(self, text: str) -> str:
        if self._analyzer is None or self._anonymizer is None:
            return text
        try:
            results = self._analyzer.analyze(text=text, language="en")
            if not results:
                return text
            from presidio_anonymizer.entities import OperatorConfig

            anonymized = self._anonymizer.anonymize(
                text=text,
                analyzer_results=results,
                operators={"DEFAULT": OperatorConfig("replace", {"new_value": "[REDACTED:PII]"})},
            )
            return anonymized.text
        except Exception:
            return self._policy.fail_closed_placeholder

    def _redact_str(self, text: str, boundary: Boundary) -> str:
        from backend.domain.redaction import SensitiveClass

        # Credentials — always (handled by scrubber which runs unconditionally)
        scrubbed = self._scrub(text)
        # PII / OPERATIONAL_IDENTIFIER — only at output boundaries
        if self._policy.should_redact(SensitiveClass.PII, boundary):
            scrubbed = self._presidio_redact(scrubbed)
        return scrubbed

    def _redact_value(self, value: object, boundary: Boundary) -> object:
        if isinstance(value, str):
            return self._redact_str(value, boundary)
        if isinstance(value, dict):
            return {k: self._redact_value(v, boundary) for k, v in value.items()}
        if isinstance(value, list):
            return [self._redact_value(item, boundary) for item in value]
        return value

    # -- Protocol surface ---------------------------------------------------

    def redact_text(self, text: str, boundary: Boundary) -> str:
        try:
            return self._redact_str(text, boundary)
        except Exception:
            return self._policy.fail_closed_placeholder

    def redact_mapping(self, data: dict, boundary: Boundary) -> dict:
        try:
            result = {}
            for k, v in data.items():
                try:
                    result[k] = self._redact_value(v, boundary)
                except Exception:
                    result[k] = self._policy.fail_closed_placeholder
            return result
        except Exception:
            return dict.fromkeys(data, self._policy.fail_closed_placeholder)


# ── Factory ──────────────────────────────────────────────────────────────────


def build_redactor(
    presidio_enabled: bool = True,
    entropy_threshold: float = 4.0,
    spacy_model: str = "en_core_web_sm",
) -> _CompositeRedactor:
    """Build and return a configured Redactor (call once at startup)."""
    from backend.domain.redaction import DEFAULT_POLICY

    analyzer = None
    anonymizer = None
    if presidio_enabled:
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider
        from presidio_anonymizer import AnonymizerEngine

        # Pin the spaCy model so Presidio uses the one baked into the image
        # (deploy/api/Dockerfile). Without this it defaults to en_core_web_lg and
        # downloads ~382MB at runtime on every fresh container.
        nlp_engine = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "en", "model_name": spacy_model}],
            }
        ).create_engine()
        analyzer = AnalyzerEngine(nlp_engine=nlp_engine)
        anonymizer = AnonymizerEngine()

    return _CompositeRedactor(
        policy=DEFAULT_POLICY,
        entropy_threshold=entropy_threshold,
        presidio_analyzer=analyzer,
        presidio_anonymizer=anonymizer,
    )


# ── Module-level helper for the logging chain (T013) ─────────────────────────


def _redact_str(text: str, boundary: Boundary) -> str:
    """Scrub credentials from *text* at the LOG boundary — callable by the structlog chain.

    Runs the deterministic scrubber only (no Presidio model load on-chain).
    Full PII redaction happens when callers use the full Redactor via the DI seam.
    """
    from backend.domain.redaction import DEFAULT_POLICY

    return _scrub_text(
        text,
        placeholder=DEFAULT_POLICY.fail_closed_placeholder,
        threshold=4.0,
    )


# ── Provider seam (T010) ─────────────────────────────────────────────────────


class RedactorProvider:
    """Lifespan singleton provider — builds the Redactor once on startup."""

    name = "redactor"

    def __init__(self, settings: object) -> None:
        self._settings = settings
        self._redactor: _CompositeRedactor | None = None

    async def start(self, state: object) -> None:
        obs = getattr(self._settings, "observability", None)
        self._redactor = build_redactor(
            presidio_enabled=getattr(obs, "presidio_enabled", True),
            entropy_threshold=getattr(obs, "entropy_threshold", 4.0),
            spacy_model=getattr(obs, "spacy_model", "en_core_web_sm"),
        )
        if hasattr(state, "__dict__"):
            state.__dict__["redactor"] = self._redactor

    async def stop(self) -> None:
        self._redactor = None

    def get(self) -> _CompositeRedactor:
        if self._redactor is None:
            raise RuntimeError("RedactorProvider not started")
        return self._redactor


def get_redactor() -> _CompositeRedactor:
    """Return the configured Redactor. Use RedactorProvider at startup instead."""
    raise NotImplementedError(
        "Redaction is a reserved seam; implemented in SPEC-observability (#2)."
        " Obtain the Redactor via Depends(get_redactor_dep) or RedactorProvider."
    )
