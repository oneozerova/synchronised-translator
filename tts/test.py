import torch
import soundfile as sf
from qwen_tts import Qwen3TTSModel

MODEL_ID = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"

model = Qwen3TTSModel.from_pretrained(
    MODEL_ID,
    device_map="cuda:0",
    dtype=torch.float32
)

text = """Мороз и солнце; день чудесный!
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
И берег, милый для меня."""
speaker_wav = "my_voice.wav"  # 3–10 секунд чистого голоса
chunks = text.split(" ")

prompt_items = model.create_voice_clone_prompt(
    ref_audio=speaker_wav,
    ref_text="Всем привет, с вами как всегда я, Игорь Пуртов и сегодня я буду делать грязь",
    x_vector_only_mode=True,
)



model_window = []
out = []
import time 

start = time.time() ## точка отсчета времени

chunks_len = 0
for i, chunk in enumerate(chunks):
    model_window.append(chunk)
    if len(model_window) == 10:
        if chunks_len == 5:
            break
        wav, sr = model.generate_voice_clone(
            text=" ".join(model_window),
            language="Russian",
            voice_clone_prompt=prompt_items,
            #**common_gen_kwargs
        )
        model_window.clear()
        chunks_len += 1
        out.append(wav[0])

end = time.time() - start ## собственно время работы программы

print(end)
import numpy as np
sf.write("out.wav", np.concatenate(out), sr)
print("saved -> out.wav")
