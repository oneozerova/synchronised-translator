"""
Встроенный переводчик (YandexGPT через OpenAI-совместимый API).
Используется только при USE_TRANSLATOR=true; клиент создаётся лениво при первом вызове.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import List

from openai import OpenAI

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
Ты работаешь как синхронный переводчик монолога.

Задача:
переводить только новый входящий фрагмент, используя предыдущий исходный контекст
для согласованности смысла, терминов, местоимений и времени.

Правила:
1. Возвращай только перевод текущего нового фрагмента.
2. Не повторяй ранее переведенный текст.
3. Используй предыдущий исходный контекст только для понимания.
4. Если мысль не завершена, дай устойчивый частичный перевод, не додумывая.
5. Сохраняй имена, числа, термины, стиль и смысл.
6. Не объясняй перевод, не добавляй метки.
""".strip()


def _naive_tokenize(text: str) -> List[str]:
    return text.split()


def _last_n_tokens(text: str, max_tokens: int) -> str:
    tokens = _naive_tokenize(text)
    if len(tokens) <= max_tokens:
        return text
    return " ".join(tokens[-max_tokens:])


@dataclass
class TranslationSession:
    """
    Сессионный перевод чанков с накоплением исходного контекста.
    Экспортируется из backend для переиспользования и тестов.
    """

    api_key: str | None = None
    folder_id: str = "b1gq32mi56gh15jmvblj"
    source_language: str = "Russian"
    target_language: str = "English"
    max_context_tokens: int = 300

    full_source_text: str = ""
    translated_outputs: List[str] = field(default_factory=list)

    _client: OpenAI | None = field(default=None, init=False, repr=False)

    def _model_id(self) -> str:
        return f"gpt://{self.folder_id}/yandexgpt-lite/latest"

    def _ensure_client(self) -> OpenAI:
        if self._client is not None:
            return self._client
        key = self.api_key or os.environ.get("YANDEX_API_KEY")
        if not key:
            raise RuntimeError("YANDEX_API_KEY required when translation is enabled")
        self._client = OpenAI(
            api_key=key,
            base_url="https://llm.api.cloud.yandex.net/v1",
        )
        return self._client

    def build_context_window(self) -> str:
        return _last_n_tokens(self.full_source_text, self.max_context_tokens)

    def translate_chunk(self, current_chunk: str) -> str:
        current_chunk = current_chunk.strip()
        if not current_chunk:
            return ""

        client = self._ensure_client()
        context_window = self.build_context_window()
        user_prompt = f"""
source_language: {self.source_language}
target_language: {self.target_language}
context_window: {context_window}
current_chunk: {current_chunk}
""".strip()

        t0 = time.perf_counter()
        response = client.chat.completions.create(
            model=self._model_id(),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=120,
        )
        translated_text = (response.choices[0].message.content or "").strip()
        logger.debug("translate_chunk: %.0fms", (time.perf_counter() - t0) * 1000)

        if self.full_source_text:
            self.full_source_text += " " + current_chunk
        else:
            self.full_source_text = current_chunk
        self.translated_outputs.append(translated_text)
        return translated_text


__all__ = ["TranslationSession", "SYSTEM_PROMPT"]
