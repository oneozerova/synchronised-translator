import os
import logging
import time
from dataclasses import dataclass, field
from typing import List

from openai import OpenAI

# ─────────────────────────────────────────────────────────────
# Настройка логирования
# ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s:%(lineno)d: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Ваша конфигурация и логика (без изменений)
# ─────────────────────────────────────────────────────────────
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

logger.info(f"Initializing Yandex LLM client: model={YANDEX_MODEL}, folder_id={YANDEX_FOLDER_ID}")

client = OpenAI(
    api_key=YANDEX_API_KEY,
    base_url="https://llm.api.cloud.yandex.net/v1",
)
logger.debug("OpenAI client initialized successfully")


def naive_tokenize(text: str) -> List[str]:
    """Упрощенный токенизатор. можно потом заменить"""
    tokens = text.split()
    logger.debug(
        f"naive_tokenize: input_len={len(text)}, tokens_count={len(tokens)}, tokens={tokens[:10]}{'...' if len(tokens) > 10 else ''}")
    return tokens


def last_n_tokens(text: str, max_tokens: int) -> str:
    tokens = naive_tokenize(text)
    if len(tokens) <= max_tokens:
        logger.debug(f"last_n_tokens: no truncation needed ({len(tokens)} <= {max_tokens})")
        return text
    truncated = " ".join(tokens[-max_tokens:])
    logger.debug(f"last_n_tokens: truncated from {len(tokens)} to {max_tokens} tokens")
    return truncated


@dataclass
class TranslationSession:
    source_language: str = "English"
    target_language: str = "Russian"
    max_context_tokens: int = 300

    full_source_text: str = ""
    translated_outputs: List[str] = field(default_factory=list)

    def __post_init__(self):
        logger.info(
            f"TranslationSession created: source={self.source_language}, target={self.target_language}, max_context={self.max_context_tokens}")

    def build_context_window(self) -> str:
        logger.debug(
            f"build_context_window: full_source_len={len(self.full_source_text)}, tokens={len(naive_tokenize(self.full_source_text))}")
        context = last_n_tokens(self.full_source_text, self.max_context_tokens)
        logger.debug(f"build_context_window: context_window_len={len(context)}")
        return context

    def translate_chunk(self, current_chunk: str) -> str:
        chunk_start_time = time.time()
        logger.info(
            f"translate_chunk: received chunk='{current_chunk[:100]}{'...' if len(current_chunk) > 100 else ''}' (len={len(current_chunk)})")

        current_chunk = current_chunk.strip()
        if not current_chunk:
            logger.warning("translate_chunk: empty chunk received, returning empty string")
            return ""

        # Контекст ДО добавления нового чанка
        context_window = self.build_context_window()
        logger.debug(
            f"translate_chunk: context_window='{context_window[:200]}{'...' if len(context_window) > 200 else ''}'")

        user_prompt = f"""
source_language: {self.source_language}
target_language: {self.target_language}
context_window: {context_window}
current_chunk: {current_chunk}
""".strip()
        logger.debug(f"translate_chunk: user_prompt built (len={len(user_prompt)})")

        # Логирование запроса к API (без чувствительных данных)
        logger.debug(f"translate_chunk: calling Yandex LLM API: model={YANDEX_MODEL}, temperature=0.2, max_tokens=120")
        api_start = time.time()

        try:
            response = client.chat.completions.create(
                model=YANDEX_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                max_tokens=120,
            )
            api_duration = time.time() - api_start
            logger.info(
                f"translate_chunk: API response received in {api_duration:.3f}s, usage={getattr(response, 'usage', 'N/A')}")

        except Exception as e:
            api_duration = time.time() - api_start
            logger.error(f"translate_chunk: API call failed after {api_duration:.3f}s: {type(e).__name__}: {e}",
                         exc_info=True)
            raise

        translated_text = response.choices[0].message.content.strip()
        logger.info(f"translate_chunk: translated='{translated_text}'")

        # После перевода добавляем новый исходный чанк в накопленный буфер
        old_source_len = len(self.full_source_text)
        if self.full_source_text:
            self.full_source_text += " " + current_chunk
        else:
            self.full_source_text = current_chunk
        logger.debug(
            f"translate_chunk: full_source_text updated: {old_source_len} → {len(self.full_source_text)} chars")

        self.translated_outputs.append(translated_text)
        logger.debug(f"translate_chunk: translated_outputs count={len(self.translated_outputs)}")

        chunk_duration = time.time() - chunk_start_time
        logger.info(f"translate_chunk: completed in {chunk_duration:.3f}s")

        return translated_text


if __name__ == "__main__":
    logger.info("=== Starting translation demo ===")

    session = TranslationSession()

    chunks = [
        "I like to drink juice, water,",
        "milk, and sometimes tea.",
        "When I was a child,",
        "my mother always told me to avoid too much sugar."
    ]
    logger.info(f"Processing {len(chunks)} chunks")

    for i, ch in enumerate(chunks, 1):
        logger.info(f"--- Chunk {i}/{len(chunks)} ---")
        try:
            translated = session.translate_chunk(ch)
            print(f"EN chunk: {ch}")
            print(f"RU out  : {translated}")
            print("-" * 40)
        except Exception as e:
            logger.error(f"Failed to translate chunk {i}: {e}", exc_info=True)
            print(f"ERROR: {e}")
            break

    logger.info(f"=== Demo finished: total translated chunks={len(session.translated_outputs)} ===")
    logger.debug(f"Final full_source_text: '{session.full_source_text}'")
    logger.debug(f"Final translated_outputs: {session.translated_outputs}")