import torch
import soundfile as sf
import numpy as np
import time
from faster_qwen3_tts import FasterQwen3TTS

model = FasterQwen3TTS.from_pretrained(
    "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
    device="cuda",
    dtype=torch.bfloat16,
)

speaker_wav = "my_voice.wav"
REF_TEXT = "Всем привет, с вами как всегда я, Игорь Пуртов и сегодня я буду делать грязь"


text = """
Мороз и солнце; день чудесный!
Еще ты дремлешь, друг прелестный —
Пора, красавица, проснись:
Открой сомкнуты негой взоры
Навстречу северной Авроры,
Звездою севера явись!
Вечор, ты помнишь, вьюга злилась,
На мутном небе мгла носилась;
Луна, как бледное пятно,
Сквозь тучи мрачные желтела,
И ты печальная сидела —
А нынче... погляди в окно:
Под голубыми небесами
Великолепными коврами,
Блестя на солнце, снег лежит;
Прозрачный лес один чернеет,
И ель сквозь иней зеленеет,
И речка подо льдом блестит.
Вся комната янтарным блеском
Озарена. Веселым треском
Трещит затопленная печь.
Приятно думать у лежанки.
Но знаешь: не велеть ли в санки
Кобылку бурую запречь?
Скользя по утреннему снегу,
Друг милый, предадимся бегу
Нетерпеливого коня
И навестим поля пустые,
Леса, недавно столь густые,
И берег, милый для меня.
"""

chunks = text.split(" ")

print("Прогрев...")
model.generate_voice_clone(
    text="прогрев, пригрев, прогрев, пригрев, прогрев, пригрев, прогрев, пригрев, прогрев, пригрев",
    language="Russian",
    ref_audio=speaker_wav,
    ref_text=REF_TEXT,
    xvec_only=True,
)
print("Прогрев завершен")

model_window = []
out = []
chunks_len = 0

start = time.time()

for chunk in chunks:
    model_window.append(chunk)
    if len(model_window) == 10:
        if chunks_len == 5:
            break
        wav, sr = model.generate_voice_clone(
            text=" ".join(model_window),
            language="Russian",
            ref_audio=speaker_wav,
            ref_text=REF_TEXT,
            xvec_only=True,
        )
        model_window.clear()
        chunks_len += 1
        out.append(wav[0])

print(f"Время: {time.time() - start:.2f}s")
sf.write("out.wav", np.concatenate(out), sr)
print("saved -> out.wav")