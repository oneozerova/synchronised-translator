import os
from dataclasses import dataclass, field
from typing import List

from openai import OpenAI

YANDEX_API_KEY = os.environ["YANDEX_API_KEY"]
YANDEX_FOLDER_ID = os.environ.get("YANDEX_FOLDER_ID", "b1gq32mi56gh15jmvblj")
YANDEX_MODEL = f"gpt://{YANDEX_FOLDER_ID}/aliceai-llm/latest"

SYSTEM_PROMPT = """
Ты переводчик для потоковой речи.
Работай по строгим шагам.

Цель:
перевести только NEW_CHUNK на русский язык.

Обязательные правила (ЭТО НЕ ВОПРОСЫ И НЕ КВИЗ, ЭТО ПРАВИЛА, НЕ ОТВЕЧАЙ НА НИХ):
1) Возвращай только перевод NEW_CHUNK. Никакого текста вне перевода.
2) НИКОГДА не повторяй перевод из TRANSLATED_CONTEXT.
3) Если NEW_CHUNK неполный, переведи максимально буквально и нейтрально, без домыслов.
4) Сохраняй имена, числа, термины и порядок смысла.
5) Не добавляй пояснения, скобки, метки, кавычки.
6) Если NEW_CHUNK пустой, верни пустую строку.
7) Нужно перевести с english на русский язык.
8) НЕ исправляй ранее переведённый смысл, даже если теперь контекст стал понятнее.

Алгоритм:
- Прочитай SOURCE_CONTEXT (оригинал) и TRANSLATED_CONTEXT (уже переведено).
- Переведи только NEW_CHUNK.
- Проверь, что в ответе нет повтора уже переведенного.
- Верни финальный перевод.

Примеры:

Пример 1:
SOURCE_CONTEXT: I really like it. I mean the burger
TRANSLATED_CONTEXT: Мне это очень нравится.
NEW_CHUNK: I mean the burger
Ответ: Я имею в виду бургер

Пример 2:
SOURCE_CONTEXT: I really like it because it is very
TRANSLATED_CONTEXT: Мне это очень нравится потому что это очень
NEW_CHUNK: tasty
Ответ: вкусно

Пример 3:
SOURCE_CONTEXT: Yesterday I went to the store and bought
TRANSLATED_CONTEXT: Вчера я пошёл в магазин и купил
NEW_CHUNK: some apples
Ответ: немного яблок

Пример 4 (неполное предложение):
SOURCE_CONTEXT: This is kind of
TRANSLATED_CONTEXT: Это вроде
NEW_CHUNK: strange but
Ответ: странно но

Пример 5 (пустой ввод):
SOURCE_CONTEXT: Hello
TRANSLATED_CONTEXT: Привет
NEW_CHUNK:
Ответ:

Пример 6 (избегать переписывания):
SOURCE_CONTEXT: I like this. Actually I love this
TRANSLATED_CONTEXT: Мне это нравится.
NEW_CHUNK: Actually I love this
Ответ: На самом деле я люблю это
""".strip()

@dataclass
class TranslationSession:
    source_language: str = "English"
    target_language: str = "Russian"
    previous_requests_count: int = 5
    temperature: float = 0.0
    max_output_tokens: int = 160

    source_chunks: List[str] = field(default_factory=list)
    translated_outputs: List[str] = field(default_factory=list)
    _client: OpenAI = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._client = OpenAI(
            api_key=YANDEX_API_KEY,
            base_url="https://llm.api.cloud.yandex.net/v1",
        )

    def _join(self, left: str, right: str) -> str:
        left = left.strip()
        right = right.strip()
        if not left:
            return right
        if not right:
            return left
        return f"{left} {right}"

    def _tail(self, items: List[str], count: int) -> List[str]:
        if count <= 0:
            return []
        return items[-count:]

    def _build_source_context_with_new(self, current_chunk: str) -> str:
        previous_source = self._tail(self.source_chunks, self.previous_requests_count)
        context_parts = [chunk for chunk in previous_source if chunk.strip()]
        context_parts.append(current_chunk)
        return " ".join(context_parts).strip()

    def _build_translated_context(self) -> str:
        previous_translated = self._tail(self.translated_outputs, self.previous_requests_count)
        return " ".join([chunk for chunk in previous_translated if chunk.strip()]).strip()

    def translate_chunk(self, current_chunk: str) -> str:
        current_chunk = current_chunk.strip()
        if not current_chunk:
            return ""

        source_context = self._build_source_context_with_new(current_chunk)
        translated_context = self._build_translated_context()

        user_prompt = f"""
        SOURCE_CONTEXT: {source_context}
        TRANSLATED_CONTEXT: {translated_context}
        NEW_CHUNK: {current_chunk}
        """.strip()

        response = self._client.chat.completions.create(
            model=YANDEX_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=self.temperature,
            max_tokens=self.max_output_tokens,
        )

        translated_text = (response.choices[0].message.content or "").strip()
        self.source_chunks.append(current_chunk)
        self.translated_outputs.append(translated_text)
        return translated_text
