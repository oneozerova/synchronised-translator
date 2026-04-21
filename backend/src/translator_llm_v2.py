from __future__ import annotations

import math
import re
import time
from collections import deque
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Deque

from openai import OpenAI

from src.settings import settings

YANDEX_API_KEY = settings.yandex_api_key
YANDEX_FOLDER_ID = settings.yandex_folder_id
YANDEX_MODEL = f"gpt://{YANDEX_FOLDER_ID}/aliceai-llm/latest"

WHITESPACE_RE = re.compile(r"\s+")
CONTROL_RE = re.compile(r"[\u0000-\u001f\u007f-\u009f]")
PUNCT_EDGE_RE = re.compile(r"(^[^\w]+|[^\w]+$)")
SERVICE_TEXT_RE = re.compile(
    r"(source_context|translated_context|new_chunk|translation|translate|answer:|ответ:|```)",
    re.IGNORECASE,
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _normalize_word(word: str) -> str:
    cleaned = PUNCT_EDGE_RE.sub("", word.lower())
    return cleaned


def _normalized_words(text: str) -> list[str]:
    return [word for word in (_normalize_word(part) for part in text.split()) if word]


@dataclass
class Segment:
    source_text: str
    translated_text: str
    is_final: bool = True
    timestamp_ms: int = field(default_factory=_now_ms)


@dataclass
class TranslationDraft:
    source_text: str
    source_context: str
    translated_context: str
    raw_translation: str = ""


@dataclass
class ValidationResult:
    text: str
    accepted: bool
    reason: str = ""


def build_system_prompt(source_language: str, target_language: str) -> str:
    return f"""
Ты переводчик для потоковой речи.
Работай по строгим шагам.

Цель:
перевести только NEW_CHUNK с {source_language} на {target_language}.

Обязательные правила:
1) Возвращай только перевод NEW_CHUNK. Никакого текста вне перевода.
2) Не повторяй уже выданный перевод из TRANSLATED_CONTEXT.
3) Если NEW_CHUNK неполный, переведи максимально буквально и нейтрально, без домыслов.
4) Сохраняй имена, числа, термины и порядок смысла.
5) Не добавляй пояснения, скобки, метки, кавычки и служебный текст.
6) Не исправляй и не переписывай ранее переведённый смысл.
7) Если контекст неполный, переводи только то, что явно есть в NEW_CHUNK.
""".strip()


@dataclass
class TranslationSession:
    source_language: str = "English"
    target_language: str = "Russian"
    previous_requests_count: int = 5
    unstable_tail_limit: int = 2
    temperature: float = 0.0
    max_output_tokens: int = 160
    request_timeout_sec: float = 8.0
    request_retries: int = 1
    retry_backoff_sec: float = 0.35
    max_length_ratio: float = 2.2
    max_length_buffer_words: int = 4
    overlap_tail_words: int = 12
    duplicate_similarity_threshold: float = 0.9

    committed_segments: Deque[Segment] = field(default_factory=deque)
    unstable_source_tail: Deque[str] = field(default_factory=deque)
    _client: OpenAI = field(init=False, repr=False)
    _system_prompt: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not YANDEX_API_KEY:
            raise RuntimeError(
                "YANDEX_API_KEY is not set. Add it to backend/.env or export it before starting backend."
            )

        history_limit = max(self.previous_requests_count, 0)
        unstable_limit = max(self.unstable_tail_limit, 0)
        self.committed_segments = deque(self.committed_segments, maxlen=history_limit)
        self.unstable_source_tail = deque(self.unstable_source_tail, maxlen=unstable_limit)
        self._client = OpenAI(
            api_key=YANDEX_API_KEY,
            base_url="https://llm.api.cloud.yandex.net/v1",
        )
        self._system_prompt = build_system_prompt(self.source_language, self.target_language)

    def _normalize_source_chunk(self, text: str) -> str:
        text = CONTROL_RE.sub(" ", text)
        text = WHITESPACE_RE.sub(" ", text).strip()
        return text

    def _normalize_model_output(self, text: str) -> str:
        text = CONTROL_RE.sub(" ", text)
        text = WHITESPACE_RE.sub(" ", text).strip()
        return text

    def _build_source_context_with_new(self, current_chunk: str) -> str:
        context_parts = [segment.source_text for segment in self.committed_segments if segment.is_final]
        context_parts.extend(chunk for chunk in self.unstable_source_tail if chunk)
        context_parts.append(current_chunk)
        return " ".join(part for part in context_parts if part).strip()

    def _build_translated_context(self) -> str:
        return " ".join(
            segment.translated_text for segment in self.committed_segments if segment.is_final and segment.translated_text
        ).strip()

    def _build_draft(self, current_chunk: str) -> TranslationDraft:
        return TranslationDraft(
            source_text=current_chunk,
            source_context=self._build_source_context_with_new(current_chunk),
            translated_context=self._build_translated_context(),
        )

    def _dynamic_max_output_tokens(self, current_chunk: str) -> int:
        source_words = max(1, len(current_chunk.split()))
        target_word_budget = math.ceil(source_words * self.max_length_ratio) + self.max_length_buffer_words
        estimated_tokens = max(24, target_word_budget * 3)
        return min(self.max_output_tokens, estimated_tokens)

    def _request_translation(self, draft: TranslationDraft) -> str:
        user_prompt = f"""
        SOURCE_CONTEXT: {draft.source_context}
        TRANSLATED_CONTEXT: {draft.translated_context}
        NEW_CHUNK: {draft.source_text}
        """.strip()

        last_error: Exception | None = None
        for attempt in range(self.request_retries + 1):
            try:
                response = self._client.chat.completions.create(
                    model=YANDEX_MODEL,
                    messages=[
                        {"role": "system", "content": self._system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=self.temperature,
                    max_tokens=self._dynamic_max_output_tokens(draft.source_text),
                    timeout=self.request_timeout_sec,
                )
                return self._normalize_model_output(response.choices[0].message.content or "")
            except Exception as exc:
                last_error = exc
                if attempt >= self.request_retries:
                    break
                time.sleep(self.retry_backoff_sec * (2 ** attempt))

        if last_error is not None:
            print(f"[TranslationSessionV2] translation request failed: {type(last_error).__name__}: {last_error}")
        return ""

    def _trim_context_overlap(self, translated_context: str, candidate: str) -> str:
        context_words = _normalized_words(translated_context)
        candidate_words = candidate.split()
        candidate_norm_words = [_normalize_word(word) for word in candidate_words]

        max_overlap = min(self.overlap_tail_words, len(context_words), len(candidate_words))
        for overlap in range(max_overlap, 0, -1):
            if context_words[-overlap:] == candidate_norm_words[:overlap]:
                return " ".join(candidate_words[overlap:]).strip()
        return candidate

    def _is_duplicate_of_tail(self, translated_context: str, candidate: str) -> bool:
        context_words = _normalized_words(translated_context)
        candidate_words = _normalized_words(candidate)
        if not context_words or not candidate_words:
            return False

        tail = " ".join(context_words[-max(self.overlap_tail_words, len(candidate_words)):])
        probe = " ".join(candidate_words)
        if not tail or not probe:
            return False

        if tail.endswith(probe):
            return True
        return SequenceMatcher(a=tail, b=probe).ratio() >= self.duplicate_similarity_threshold

    def _contains_service_text(self, text: str) -> bool:
        return bool(SERVICE_TEXT_RE.search(text))

    def _max_allowed_output_words(self, source_text: str) -> int:
        source_words = max(1, len(source_text.split()))
        return math.ceil(source_words * self.max_length_ratio) + self.max_length_buffer_words

    def _validate_translation(self, draft: TranslationDraft) -> ValidationResult:
        candidate = draft.raw_translation
        if not candidate:
            return ValidationResult(text="", accepted=False, reason="empty_response")

        if self._contains_service_text(candidate):
            return ValidationResult(text="", accepted=False, reason="service_text")

        candidate = self._trim_context_overlap(draft.translated_context, candidate)
        if not candidate:
            return ValidationResult(text="", accepted=False, reason="full_overlap")

        candidate_words = candidate.split()
        if len(candidate_words) > self._max_allowed_output_words(draft.source_text):
            return ValidationResult(text="", accepted=False, reason="too_long")

        if self._is_duplicate_of_tail(draft.translated_context, candidate):
            return ValidationResult(text="", accepted=False, reason="duplicate_tail")

        return ValidationResult(text=candidate, accepted=True)

    def _remember_unstable_source(self, source_text: str) -> None:
        if not source_text:
            return
        if self.unstable_source_tail and self.unstable_source_tail[-1] == source_text:
            return
        self.unstable_source_tail.append(source_text)

    def _commit_translation(self, source_text: str, translated_text: str) -> None:
        self.committed_segments.append(
            Segment(
                source_text=source_text,
                translated_text=translated_text,
                is_final=True,
            )
        )
        self.unstable_source_tail.clear()

    def translate_chunk(self, current_chunk: str) -> str:
        current_chunk = self._normalize_source_chunk(current_chunk)
        if not current_chunk:
            return ""

        draft = self._build_draft(current_chunk)
        draft.raw_translation = self._request_translation(draft)
        validation = self._validate_translation(draft)

        if not validation.accepted:
            self._remember_unstable_source(current_chunk)
            print(f"[TranslationSessionV2] skipped translation commit: {validation.reason}")
            return ""

        self._commit_translation(current_chunk, validation.text)
        return validation.text
