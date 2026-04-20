import os
from dataclasses import dataclass, field
from typing import List

from openai import OpenAI

YANDEX_API_KEY = os.environ["YANDEX_API_KEY"]
YANDEX_FOLDER_ID = os.environ.get("YANDEX_FOLDER_ID", "b1gq32mi56gh15jmvblj")
YANDEX_MODEL = f"gpt://{YANDEX_FOLDER_ID}/yandexgpt-lite/latest"

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


client = OpenAI(
    api_key=YANDEX_API_KEY,
    base_url="https://llm.api.cloud.yandex.net/v1",
)


def naive_tokenize(text: str) -> List[str]:
    """
    Упрощенный токенизатор. можно потом заменить
    """
    return text.split()


def last_n_tokens(text: str, max_tokens: int) -> str:
    tokens = naive_tokenize(text)
    if len(tokens) <= max_tokens:
        return text
    return " ".join(tokens[-max_tokens:])


@dataclass
class TranslationSession:
    source_language: str = "English"
    target_language: str = "Russian"
    max_context_tokens: int = 300

    full_source_text: str = ""
    translated_outputs: List[str] = field(default_factory=list)

    def build_context_window(self) -> str:
        return last_n_tokens(self.full_source_text, self.max_context_tokens)

    def translate_chunk(self, current_chunk: str) -> str:
        current_chunk = current_chunk.strip()
        if not current_chunk:
            return ""

        # Контекст ДО добавления нового чанка
        context_window = self.build_context_window()

        user_prompt = f"""
source_language: {self.source_language}
target_language: {self.target_language}
context_window: {context_window}
current_chunk: {current_chunk}
""".strip()

        response = client.chat.completions.create(
            model=YANDEX_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=120,
        )

        translated_text = response.choices[0].message.content.strip()

        # После перевода добавляем новый исходный чанк в накопленный буфер
        if self.full_source_text:
            self.full_source_text += " " + current_chunk
        else:
            self.full_source_text = current_chunk

        self.translated_outputs.append(translated_text)
        return translated_text


if __name__ == "__main__":
    session = TranslationSession()

    chunks = [
        "I like to drink juice, water,",
        "milk, and sometimes tea.",
        "When I was a child,",
        "my mother always told me to avoid too much sugar."
    ]

    for ch in chunks:
        translated = session.translate_chunk(ch)
        print(f"EN chunk: {ch}")
        print(f"RU out  : {translated}")
        print("-" * 40)
