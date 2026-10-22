# Локальные TTS-модели для русского радио на Mac

Обзор моделей синтеза речи для PersonalRadio. Задача: русские реплики ведущего и новости по 2–30 с, локально на Apple Silicon, только личное некоммерческое использование.

Данные собраны **22.10.2026**. Звёзды, даты и релизы взяты из GitHub API (`https://api.github.com/repos/OWNER/REPO` и `/releases/latest`) в тот же день. Цифры качества (WER/CER/SIM) каждый автор публикует сам о себе. Где источник один и сторонний, так и написано. Что проверить не удалось, помечено **не проверено**. Модели не запускались.

Сокращения: WER/CER — доля ошибок распознавания синтезированной речи (меньше — лучше). SIM — сходство с голосом-образцом (больше — лучше). RTF — время синтеза, делённое на длительность аудио (меньше 1 — быстрее реального времени). NC — лицензия «только некоммерческое использование».

---

## 0. Что важно знать про текущую схему (OmniVoice через VoiceStudio)

Три факта из первоисточников объясняют то, что мы наблюдаем:

1. **Голос по описанию у OmniVoice обучен только на китайском и английском.** README: модель «primarily trained on the voice cloning task, so voice cloning is the most stable mode. Voice design is trained on Chinese and English data only» ([README](https://github.com/k2-fsa/OmniVoice#voice-design)). Наш голос сделан именно описанием (`instruct`) и звучит по-русски, то есть работает в нештатном режиме. Отсюда вероятные шумы.
2. **VoiceStudio по умолчанию делает 16 шагов, «качественный» режим модели — 32.** Это сказано в коде OpenAI-совместимого эндпоинта: `num_step` «app default 16; 32 = the model's documented quality preset» ([openai_compat.py](https://github.com/debpalash/VoiceStudio/blob/main/backend/api/routers/openai_compat.py)). В статье OmniVoice при 16 шагах WER на английском 1.50 против 1.30 при 32 ([arXiv 2604.00688, Table 9](https://arxiv.org/html/2604.00688)). Параметр `num_step` можно передать в `/v1/audio/speech`.
3. **Эмоций у OmniVoice нет по замыслу.** Документация VoiceStudio: эмоции вида `[happy]`/`[sad]` базовая модель не принимает; из стилей валидатор пропускает только `whisper`; большинство тегов-междометий (`-ah`, `-yi`, `-hnn`) рассчитаны на речь с мандаринским оттенком; управления силой нет ([expressive-speech.md](https://github.com/debpalash/VoiceStudio/blob/main/docs/expressive-speech.md)). Полный список тегов есть в [README OmniVoice](https://github.com/k2-fsa/OmniVoice#non-verbal--pronunciation-control).

Вывод: прежде чем менять модель, стоит дёшево попробовать (а) `num_step: 32` и (б) клонирование из одного удачного эталонного клипа вместо описания. Подробнее в разделе 5.

---

## 1. Модели с официальной поддержкой русского

### 1.1 OmniVoice (k2-fsa) — текущая

- **Русский:** официально, 600+ языков. В обучении около 20 338 часов русской речи (таблица языков в [статье](https://arxiv.org/html/2604.00688)). На тесте MiniMax-Multilingual-24 у русского WER **2.233** (у MiniMax 4.281, у ElevenLabs 3.878), SIM 0.783 ([arXiv 2604.00688, §4.2](https://arxiv.org/html/2604.00688)). Это лучший опубликованный русский WER среди всех моделей обзора, но измерили его сами авторы.
- **Голос:** клонирование по образцу (основной режим), описание атрибутами (пол, возраст, высота, акцент, шёпот), случайный голос ([README](https://github.com/k2-fsa/OmniVoice)).
- **Эмоции:** только теги-междометия (`[laughter]`, `[sigh]`, `[surprise-ah]` и др.). Управления эмоциями нет (см. раздел 0).
- **Размер:** основа — Qwen3-0.6B ([статья, §3.2](https://arxiv.org/html/2604.00688)). На HF около 613 M параметров ([HF API](https://huggingface.co/api/models/k2-fsa/OmniVoice)).
- **Mac:** PyTorch MPS (`device_map="mps"`, [README](https://github.com/k2-fsa/OmniVoice)). Есть MLX-порт в mlx-audio ([README порта](https://github.com/Blaizzy/mlx-audio/blob/main/mlx_audio/tts/models/omnivoice/README.md), веса `mlx-community/OmniVoice-bf16`). Скорость на Mac авторы не публикуют. RTF 0.025 указан для GPU ([README](https://github.com/k2-fsa/OmniVoice)).
- **Стриминг:** в README не описан (**не проверено**).
- **Лицензия:** код Apache-2.0, **веса CC-BY-NC** из-за данных Emilia ([HF card, License](https://huggingface.co/k2-fsa/OmniVoice)). Для личного использования подходит.
- **Активность:** 15 602★, последний push 19.10.2026, релиз 0.2.1 от 16.07.2026, не в архиве.

### 1.2 Qwen3-TTS (QwenLM)

- **Русский:** официально, один из 10 языков у всех пяти моделей ([README, таблица моделей](https://github.com/QwenLM/Qwen3-TTS#model-architecture)). Русский WER на MiniMax-Multilingual: 12Hz-1.7B-Base **3.212**, 12Hz-0.6B-Base 4.458 (MiniMax 4.281, ElevenLabs 3.878); SIM 0.792 / 0.781 ([README, Evaluation](https://github.com/QwenLM/Qwen3-TTS#evaluation)).
- **Голос:** три пути. (1) 9 готовых голосов в CustomVoice; русского среди «родных» нет, но каждый голос может говорить на любом поддерживаемом языке. (2) Голос по свободному описанию на естественном языке (1.7B-VoiceDesign). (3) Клонирование по 3 секундам образца (Base). Авторы советуют связку «спроектировать голос → сделать из клипа клон-промпт → переиспользовать», ровно наш сценарий постоянного ведущего ([README, Voice Design then Clone](https://github.com/QwenLM/Qwen3-TTS#voice-design-then-clone)).
- **Эмоции:** инструкции на естественном языке (тон, эмоция, темп) в 1.7B-CustomVoice и 1.7B-VoiceDesign. У 0.6B-CustomVoice и Base инструкций нет ([README, таблица](https://github.com/QwenLM/Qwen3-TTS#model-architecture)).
- **Размер:** 0.6B и 1.7B. Safetensors: около 0.91 B и 1.92 B ([HF API 0.6B-Base](https://huggingface.co/api/models/Qwen/Qwen3-TTS-12Hz-0.6B-Base), [HF API 1.7B-VoiceDesign](https://huggingface.co/api/models/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign)).
- **Mac:** MLX через mlx-audio, это пример по умолчанию в README mlx-audio ([README](https://github.com/Blaizzy/mlx-audio)); есть VoiceDesign, CustomVoice и Base, вплоть до 8-bit. В официальном README про MPS ничего нет (**не проверено**). Скорость на Mac **не проверено**.
- **Стриминг:** да, первый пакет от 97 мс ([README](https://github.com/QwenLM/Qwen3-TTS#introduction)).
- **Лицензия:** код Apache-2.0, веса Apache-2.0 ([HF](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign)).
- **Активность:** 13 929★, последний push 17.03.2026 (7 месяцев назад), GitHub-релизов нет.

### 1.3 Chatterbox Multilingual V3 (Resemble AI)

- **Русский:** официально, 23 языка, включая `ru` ([README, Supported Languages](https://github.com/resemble-ai/chatterbox#supported-languages)). Отдельной русской модели в «Single Language Pack» нет. Метрик по русскому авторы не публикуют (**не проверено**).
- **Голос:** клонирование по образцу. Описания голоса нет.
- **Эмоции:** ползунок `exaggeration` (по умолчанию 0.5; для драматичной речи 0.7+ и `cfg_weight≈0.3`) ([README, Tips](https://github.com/resemble-ai/chatterbox#original-chatterbox-tips)). Теги `[laugh]` есть только у английской Turbo/Nano.
- **Размер:** 0.5B ([README](https://github.com/resemble-ai/chatterbox)).
- **Mac:** PyTorch `"mps"` указан в примере ([README](https://github.com/resemble-ai/chatterbox)). Есть MLX-порт V3 `mlx-community/chatterbox-multilingual-v3` ([HF](https://huggingface.co/mlx-community/chatterbox-multilingual-v3)). Скорость на Mac **не проверено**.
- **Стриминг:** в README не описан (**не проверено**).
- **Водяной знак:** каждый файл получает нейронный водяной знак PerTh ([README](https://github.com/resemble-ai/chatterbox#built-in-perth-watermarking-for-responsible-ai)).
- **Лицензия:** код MIT, веса MIT ([HF API](https://huggingface.co/api/models/ResembleAI/chatterbox)).
- **Активность:** 27 149★, push 21.07.2026, последний GitHub-релиз v0.1.2 от 13.06.2025.

### 1.4 VoxCPM2 (OpenBMB)

- **Русский:** официально, 30 языков ([README](https://github.com/OpenBMB/VoxCPM)). Русский WER на MiniMax-MLS **3.634**, SIM 81.1. В той же таблице Qwen3-TTS 3.212 и Fish S2 2.400. На CV3-eval русский 5.21; во внутреннем тесте 0.90% ([README, бенчмарки](https://github.com/OpenBMB/VoxCPM)).
- **Голос:** описание на естественном языке, клонирование («controllable cloning» с подсказкой стиля), продолжение по образцу с транскриптом ([README](https://github.com/OpenBMB/VoxCPM)).
- **Эмоции:** инструкция в скобках в начале текста, например `(speaking fast, out of breath) ...`. VoiceStudio называет это подсказкой, а не гарантией: соблюдение зависит от голоса и языка ([expressive-speech.md](https://github.com/debpalash/VoiceStudio/blob/main/docs/expressive-speech.md)).
- **Размер:** 2B, около 2.29 B в safetensors ([HF API](https://huggingface.co/api/models/openbmb/VoxCPM2)). Выход 48 кГц.
- **Mac:** PyTorch `mps` (`auto` выбирает MPS, [README](https://github.com/OpenBMB/VoxCPM)). MLX в mlx-audio (`mlx-community/VoxCPM2-8bit`, [README модели](https://github.com/Blaizzy/mlx-audio/blob/main/mlx_audio/tts/models/voxcpm2/README.md)). Это курированный ключ `voxcpm2` в движке mlx-audio у VoiceStudio ([docs](https://github.com/debpalash/VoiceStudio/blob/main/docs/engines/mlx-audio.md)). RTF около 0.3 опубликован только для RTX 4090; для Mac **не проверено**.
- **Стриминг:** да, `generate_streaming` ([README](https://github.com/OpenBMB/VoxCPM)).
- **Лицензия:** код и веса Apache-2.0 ([README](https://github.com/OpenBMB/VoxCPM)).
- **Активность:** 39 912★, push 08.10.2026, релиз 2.0.3 от 11.05.2026.

### 1.5 Fish Audio S2-Pro / Drama 3 / OpenAudio S1-mini (fishaudio)

- **Русский:** у S2-Pro русский во втором ярусе качества ([HF card](https://huggingface.co/fishaudio/s2-pro)). Русский WER на MiniMax-MLS **2.400**, лучший в сводной таблице VoxCPM ([README VoxCPM](https://github.com/OpenBMB/VoxCPM)).
- **Голос:** клонирование по 10–30 с образца; несколько дикторов в одном тексте ([README](https://github.com/fishaudio/fish-speech)).
- **Эмоции:** свободные теги в тексте `[excited]`, `[whisper]`, `[pause]` и т. п. ([README](https://github.com/fishaudio/fish-speech)). Это самое богатое управление выразительностью среди русскоязычных моделей.
- **Размер:** S2-Pro 4B (около 4.56 B). Drama 3 — MoE 35B, из них 3B активны ([README](https://github.com/fishaudio/fish-speech#models)). Для нашей задачи тяжело.
- **Mac:** официально только описание CUDA-сервера. В mlx-audio есть Drama 3 в 4-bit ([README](https://github.com/Blaizzy/mlx-audio)). Порт S2-Pro **не проверено**.
- **Лицензия:** код Fish Audio Community License. Веса S2-Pro под Fish Audio Research License: исследования и некоммерческое использование бесплатны ([HF card](https://huggingface.co/fishaudio/s2-pro)). S1-mini под CC-BY-NC-SA-4.0 ([HF API](https://huggingface.co/api/models/fishaudio/s1-mini)).
- **Активность:** 33 358★, push 14.10.2026. Последний GitHub-релиз v1.5.1 от 31.05.2025 (модели выходят на HF).

### 1.6 Higgs Audio v3 TTS 4B (Boson AI) — дополнительный кандидат

- **Русский:** в ярусе «WER/CER < 5, production-quality» ([HF card](https://huggingface.co/bosonai/higgs-audio-v3-tts-4b)). Точное число по русскому в карточке **не проверено**.
- **Голос:** клонирование без обучения.
- **Эмоции:** 21 эмоциональный тег вида `<|emotion:...|>` (elation, amusement, enthusiasm, surprise, sadness…), плюс стиль, просодия, паузы и звуковые эффекты внутри фразы ([HF card, Control Tokens](https://huggingface.co/bosonai/higgs-audio-v3-tts-4b)).
- **Размер:** около 4B (4.65 B).
- **Mac:** есть в mlx-audio (`higgs_audio_v3`, [README](https://github.com/Blaizzy/mlx-audio)). Скорость **не проверено**.
- **Лицензия:** веса под Boson Higgs TTS 3 Research and Non-Commercial License (личное использование допустимо). Код [boson-ai/higgs-audio](https://github.com/boson-ai/higgs-audio) под Apache-2.0: 8 367★, push 05.06.2026.

### 1.7 CosyVoice 3 (FunAudioLLM, теперь QwenAudio/CosyVoice)

- **Русский:** официально, один из 9 языков ([README](https://github.com/FunAudioLLM/CosyVoice)). CV3-eval, русский: CosyVoice3-1.5B **6.64** по [таблице в README VoxCPM](https://github.com/OpenBMB/VoxCPM) (сторонний источник).
- **Голос:** клонирование, инструкции.
- **Эмоции:** инструкции (язык, диалект, эмоция, скорость, громкость) ([README](https://github.com/FunAudioLLM/CosyVoice)); теги `[breath]`, `[laughter]`, `<strong>` ([expressive-speech.md](https://github.com/debpalash/VoiceStudio/blob/main/docs/expressive-speech.md)).
- **Размер:** Fun-CosyVoice3-0.5B.
- **Mac:** VoiceStudio пишет «CUDA or CPU; MPS is unverified upstream», установка непростая ([expressive-speech.md](https://github.com/debpalash/VoiceStudio/blob/main/docs/expressive-speech.md)). Порта в mlx-audio нет.
- **Стриминг:** двусторонний, от 150 мс.
- **Лицензия:** код и веса Apache-2.0 ([HF](https://huggingface.co/FunAudioLLM/Fun-CosyVoice3-0.5B-2512)).
- **Активность:** репозиторий переехал в `QwenAudio/CosyVoice`: 24 093★, push 25.05.2026.

### 1.8 Silero TTS (snakers4) — лёгкая русская классика

- **Русский:** родной. `v5_6_ru` с голосами aidar, baya, kseniya, xenia, eugene: автоударения, омографы, вопросы, эмфаза и восклицания ([README, V5](https://github.com/snakers4/silero-models#v5)). Метрики UTMOS и скорости вынесены в wiki проекта.
- **Голос:** 5 готовых голосов. Клонирования и описания нет.
- **Эмоции:** только SSML (паузы, темп, высота) и интонация вопроса или восклицания.
- **Mac:** PyTorch на CPU. Проект подчёркивает скорость на CPU.
- **Лицензия:** `v5_ru` под CC-NC-BY. Базовые CIS-модели под MIT, но без автоударений ([README, Licence](https://github.com/snakers4/silero-models#licence)).
- **Активность:** 6 141★, push 15.10.2026, релиз v5.6 от 04.06.2026.

### 1.9 XTTS-v2 (Coqui → форк idiap)

- **Русский:** официально, 17 языков ([HF card](https://huggingface.co/coqui/XTTS-v2)). Метрик по русскому нет.
- **Голос:** клонирование по 6 с образца. Эмоции и стиль переносятся только из образца.
- **Стриминг:** менее 200 мс ([idiap README](https://github.com/idiap/coqui-ai-TTS)).
- **Лицензия:** код MPL-2.0, веса Coqui Public Model License (некоммерческая).
- **Активность:** оригинал `coqui-ai/TTS` заброшен (последний push 16.08.2024). Форк `idiap/coqui-ai-TTS` живой: 2 336★, релиз v0.28.0 от 19.10.2026. Модель 2023 года, по качеству уступает новым.

### 1.10 Лёгкие варианты с русским

- **MOSS-TTS-Nano-100M:** 20 языков, включая русский, клонирование, Apache-2.0 ([HF card](https://huggingface.co/OpenMOSS-Team/MOSS-TTS-Nano-100M)). Есть в mlx-audio. Качество по русскому **не проверено**.
- **Piper:** 4 русских голоса (denis, dmitri, irina, ruslan) ([HF piper-voices](https://huggingface.co/rhasspy/piper-voices/tree/main/ru/ru_RU)). Старый репозиторий `rhasspy/piper` в архиве, развитие перенесено в `OHF-Voice/piper1-gpl` (GPL-3.0, релиз v1.8.0 от 04.09.2026). Эмоций нет.
- **KugelAudio 0 (7B):** русский в списке ([README mlx-audio](https://github.com/Blaizzy/mlx-audio)). Тестировали на M4 Max 36 ГБ, «RTF approximately 5-7x» ([README модели](https://github.com/Blaizzy/mlx-audio/blob/main/mlx_audio/tts/models/kugelaudio/README.md); направление RTF там неоднозначно, **не проверено**). Для коротких реплик слишком тяжёл.

---

## 2. Модели без официального русского (для радио не подходят)

| Модель | Языки по первоисточнику | Примечание |
|---|---|---|
| Kokoro-82M ([VOICES.md](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md)) | en, ja, zh, es, fr, hi, it, pt-br | Apache-2.0, 82M; русского нет. 9 585★, push 06.08.2025 |
| F5-TTS ([README](https://github.com/SWivid/F5-TTS)) | официально zh+en | Есть **сторонний** русский файнтюн `hotstone228/F5-TTS-Russian` под CC-BY-NC ([SHARED.md](https://github.com/SWivid/F5-TTS/blob/main/src/f5_tts/infer/SHARED.md)); веса оригинала CC-BY-NC; MLX-порт f5-tts-mlx. 15 436★ |
| ChatTTS ([README](https://github.com/2noise/ChatTTS)) | en, zh | Код AGPL, веса CC BY-NC 4.0; управление только `[laugh]`, `[uv_break]`, `[lbreak]` |
| OpenVoice V2 ([README](https://github.com/myshell-ai/OpenVoice)) | en, es, fr, zh, ja, ko | MIT; клонирование тембра для других языков через MeloTTS. Push 19.04.2025 |
| IndexTTS 2 / 2.5 ([README](https://github.com/index-tts/index-tts)) | zh, en (+ ja, es, ar в 2.5) | Лучшее управление эмоциями (вектор из 8 эмоций, `emo_alpha`), но русского нет. Лицензия bilibili Model Use License |
| Orpheus multilingual ([коллекция HF](https://huggingface.co/collections/canopylabs/orpheus-multilingual-research-release-67f5894cd16794db163786ba)) | zh, hi, ko, es/it, fr, de | Русской модели нет |
| PocketTTS ([README](https://github.com/kyutai-labs/pocket-tts)) | en, fr, de, pt, it, es, nl | Около 6× быстрее реального времени на CPU MacBook Air M4, но без русского |
| VibeVoice ([README](https://github.com/microsoft/VibeVoice)) | en, zh; Realtime: экспериментально 9 языков без ru | MIT |
| Sesame CSM, Dia, Spark-TTS | en (Spark — en/zh) ([README mlx-audio](https://github.com/Blaizzy/mlx-audio)) | — |
| Zonos ([README](https://github.com/Zyphra/Zonos)) | фонемизация через eSpeak; русский **не проверено** | Apache-2.0; push 05.03.2025 |
| Supertonic | — | Репозиторий в архиве (`supertone-oss-archive/supertonic`) |

---

## 3. VoiceStudio (debpalash/VoiceStudio)

**Что это.** Electron-приложение с локальным Python-бэкендом на порту 3900. Внутри: клонирование и дизайн голоса, дубляж, диктовка, ASR, MCP и OpenAI-совместимый API ([README](https://github.com/debpalash/VoiceStudio)).

**Что добавляет поверх прямого вызова модели:**
- **Переключение движков:** OmniVoice по умолчанию, VoxCPM2, MLX-Audio, CosyVoice 3, IndexTTS 2.5, MOSS-TTS, PocketTTS, KittenTTS и др. ([engines/README.md](https://github.com/debpalash/VoiceStudio/blob/main/docs/engines/README.md)).
- **OpenAI-совместимый `/v1/audio/speech`** с расширениями `instruct`, `seed`, `num_step`, `guidance_scale`, `duration`, `denoise`, стриминг `stream_format: audio|sse`. Поле `model` — это **ID движка VoiceStudio** (или алиас `tts-1` для активного движка), а не конкретная модель внутри движка ([openai_compat.py](https://github.com/debpalash/VoiceStudio/blob/main/backend/api/routers/openai_compat.py)).
- **Голосовые профили:** эталон + транскрипт + seed. Профиль можно передать как `voice`. Эталонный клип кешируется, от длинного клипа OmniVoice берёт лучший 15-секундный отрезок ([engines/README.md](https://github.com/debpalash/VoiceStudio/blob/main/docs/engines/README.md)).
- **Постобработка:** обрезка тишины, мастеринг и нормализация громкости, водяной знак (`_run_tts` в [openai_compat.py](https://github.com/debpalash/VoiceStudio/blob/main/backend/api/routers/openai_compat.py)).
- **ASR:** WhisperX, MLX Whisper, Parakeet MLX и др.; управление памятью (выгрузка моделей на unified memory) ([performance.md](https://github.com/debpalash/VoiceStudio/blob/main/docs/performance.md)).

**Ограничения движка mlx-audio внутри VoiceStudio** ([engines/mlx-audio.md](https://github.com/debpalash/VoiceStudio/blob/main/docs/engines/mlx-audio.md)):
- модель внутри движка выбирается в UI или через переменную `OMNIVOICE_MLX_AUDIO_MODEL` (курированный ключ или любой HF id), а **не полем запроса**;
- **клонирование работает только с CSM** (английская модель). Остальные модели «silently ignore reference audio». Значит, Chatterbox, Qwen3-TTS Base или VoxCPM2 с нашим эталоном так не проверить;
- выход приводится к 24 кГц моно, поэтому 48 кГц у VoxCPM2 через этот путь теряются.

**Стоимость:**
- **Водяной знак AudioSeal** встраивается в каждый синтезированный файл через `mark_synthetic`. Его можно выключить настройкой `watermark.invisible` (по умолчанию включена) ([watermark.py](https://github.com/debpalash/VoiceStudio/blob/main/backend/services/watermark.py)). Влияние на звук **не проверено**.
- **Объём:** CPU-сборка PyTorch требует около 5 ГБ диска ([README](https://github.com/debpalash/VoiceStudio)). Каждый движок-сайдкар с другой сборкой torch добавляет ещё несколько ГБ ([disk-usage.md](https://github.com/debpalash/VoiceStudio/blob/main/docs/engines/disk-usage.md)).
- **Скорость:** опубликованных замеров нет, таблица бенчмарков пуста ([benchmarks.md](https://github.com/debpalash/VoiceStudio/blob/main/docs/benchmarks.md)). На Apple Silicon генерации идут строго по одной.
- **Лицензия:** AGPL-3.0 на приложение. Права на веса моделей отдельные ([LICENSE-NOTICE.md](https://github.com/debpalash/VoiceStudio/blob/main/LICENSE-NOTICE.md)).

**Активность:** 81 430★ (так отдаёт GitHub API), push 22.10.2026 (сегодня), релиз v0.5.6 от 23.09.2026. Проект очень активный, интерфейсы меняются часто: Tauri уже заменён на Electron, старый control-сервер на 3902 убран ([speech-platform.md](https://github.com/debpalash/VoiceStudio/blob/main/docs/speech-platform.md)).

**Насколько он важен радио.** Радио пользуется одним эндпоинтом `/v1/audio/speech` с одним голосом. Если заменить VoiceStudio на `mlx_audio.server`:
- *теряем:* голосовые профили и кеш эталона, мастеринг и нормализацию, обрезку тишины, удобный UI для подбора голоса и seed, переключение движков без кода;
- *получаем:* меньше слоёв и памяти, нет водяного знака, полный доступ к параметрам модели (`instruct`, `ref_audio`, `ref_text`, `temperature`, выбор модели по HF id в каждом запросе), клонирование для любой модели mlx-audio.

Практично держать VoiceStudio как основной сервис, а A/B-тесты новых моделей делать через отдельный `mlx_audio.server`.

---

## 4. Как запускать модели на Mac

### 4.1 Blaizzy/mlx-audio

- MIT, 8 090★, push 22.10.2026, релиз v0.6.1 от 20.10.2026 ([GitHub](https://github.com/Blaizzy/mlx-audio)).
- **TTS с русским** (по таблице README и карточкам): OmniVoice, Qwen3-TTS (все 5 вариантов), Chatterbox Multilingual V3, VoxCPM2, Higgs Audio v3, Fish Drama 3, MOSS-TTS / MOSS-TTS-Nano, KugelAudio. Kokoro в mlx-audio без русского ([README, Supported Models](https://github.com/Blaizzy/mlx-audio#supported-models)).
- **Сервер:** `mlx_audio.server --host 127.0.0.1 --port 8000`, эндпоинт `POST /v1/audio/speech`. Поля запроса: `model` (HF id), `input`, `voice`, `instruct`, `speed`, `lang_code`, `ref_audio`, `ref_text`, `temperature`, `response_format`, `stream` ([server.py](https://github.com/Blaizzy/mlx-audio/blob/main/mlx_audio/server.py)). Модель выбирается в каждом запросе.
- Есть ещё STT (Whisper, Parakeet v3, Canary с ru) и квантование до 4/8 бит.

### 4.2 Прямой PyTorch

- Пакеты `omnivoice`, `qwen-tts`, `chatterbox-tts`, `voxcpm` и Silero (`torch.hub`). На Mac работают на MPS или CPU, где авторы это указывают (см. раздел 1).
- Плюс: официальные параметры и самые свежие версии. Минус: каждая модель в своём окружении, нужен свой HTTP-сервер.

### 4.3 Другие локальные серверы с OpenAI-совместимым speech-эндпоинтом

- [speaches-ai/speaches](https://github.com/speaches-ai/speaches): MIT, 3 732★, push 21.10.2026. Набор TTS-моделей и наличие русского **не проверено**.
- [remsky/Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI): только Kokoro, русского нет.
- [matatonic/openedai-speech](https://github.com/matatonic/openedai-speech): в архиве.
- Nano-vLLM-VoxCPM и vLLM-Omni (Qwen3-TTS): ориентированы на CUDA ([README VoxCPM](https://github.com/OpenBMB/VoxCPM), [README Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS#vllm-usage)).

---

## 5. Сводная таблица

| Модель | Русский | Эмоции | Голос: дизайн / клон / пресеты | Размер | Путь на Mac | Лицензия весов | Последняя активность |
|---|---|---|---|---|---|---|---|
| OmniVoice | офиц.; WER 2.23 (MiniMax-24, авторы) | нет; теги-междометия | атрибуты (обучено на zh/en) / да / — | ~0.6B | MPS; MLX (mlx-audio) | CC-BY-NC | 19.10.2026 |
| Qwen3-TTS | офиц.; WER 3.21 (1.7B), 4.46 (0.6B) | инструкции текстом (1.7B) | свободное описание / да / 9 голосов | 0.6B, 1.7B | MLX (mlx-audio); MPS не проверено | Apache-2.0 | 17.03.2026 |
| Chatterbox Multi V3 | офиц.; метрик нет | ползунок `exaggeration` | — / да / — | 0.5B | MPS; MLX | MIT | 21.07.2026 |
| VoxCPM2 | офиц.; WER 3.63 | инструкция в скобках | описание / да / — | 2B | MPS; MLX | Apache-2.0 | 08.10.2026 |
| Fish S2-Pro | офиц.; WER 2.40 | свободные `[теги]` | — / да / — | 4B | MLX только Drama 3 (35B MoE) | NC research | 14.10.2026 |
| Higgs Audio v3 | офиц.; ярус WER<5 | 21 тег эмоций + стиль | — / да / — | 4B | MLX | NC research | 05.06.2026 |
| CosyVoice 3 | офиц.; CV3-eval 6.64 (сторонний) | инструкции, `[breath]` | — / да / — | 0.5B | CPU; MPS не проверено | Apache-2.0 | 25.05.2026 |
| Silero v5_ru | родной, ударения | SSML, вопрос/восклицание | — / — / 5 голосов | малый | CPU | CC-NC-BY | 15.10.2026 |
| XTTS-v2 | офиц.; метрик нет | из образца | — / да / — | — | PyTorch (MPS не проверено) | Coqui CPML (NC) | форк 19.10.2026 |
| MOSS-TTS-Nano | офиц.; метрик нет | нет данных | — / да / — | 0.1B | MLX | Apache-2.0 | 06.09.2026 (MOSS-TTS) |
| Piper ru | 4 голоса | нет | — / — / 4 | малый | CPU | по голосу (не проверено) | piper1-gpl 17.10.2026 |
| F5-TTS | только сторонний файнтюн | нет | — / да / — | 0.3B | MLX-порт | CC-BY-NC | 14.10.2026 |
| Kokoro, ChatTTS, OpenVoice, IndexTTS2, PocketTTS, Orpheus | **нет** | — | — | — | — | — | — |

Все русские WER, кроме CosyVoice, взяты с одного теста MiniMax-Multilingual, но посчитаны разными авторами. Сравнивать их можно лишь ориентировочно.

---

## 6. Рекомендация для этого радио

Критерии: русский, выразительность, лёгкость, Mac, личное использование.

**Шаг 0 — бесплатно, на текущем голосе.**
1. Передавать в `/v1/audio/speech` параметр `num_step: 32` (сейчас 16). Мерить и шум, и время.
2. Отобрать один удачный дубль текущего «дизайн»-голоса (8–15 с чистой речи) и сделать из него профиль клонирования (эталон + транскрипт + фиксированный seed). Генерировать через клон, а не через `instruct`. Это штатный и самый стабильный режим OmniVoice (раздел 0).

**Ранжирование кандидатов на замену:**
1. **Qwen3-TTS 1.7B** (VoiceDesign → клон из Base; или CustomVoice с `instruct` для эмоций). Официальный русский, эмоции по инструкции, Apache-2.0, путь через MLX, стриминг. Минус: репозиторий давно без коммитов.
2. **Chatterbox Multilingual V3.** 0.5B, MIT, ползунок выразительности, есть MPS и MLX. Минусы: нет метрик по русскому, свой водяной знак PerTh.
3. **VoxCPM2.** Русский, описание и клон, эмоции в скобках, Apache-2.0, 48 кГц. Минус: 2B, тяжелее.
4. Если нужна сильная эмоциональность и устраивает NC-лицензия: **Higgs Audio v3** (4B, 21 эмоция) или Fish S2-Pro.
5. Запасной лёгкий вариант без выразительности: **Silero v5_6_ru** (на CPU, правильные ударения).

**Что A/B-тестировать первым:** Qwen3-TTS 1.7B, Chatterbox Multilingual V3, VoxCPM2 против текущего голоса OmniVoice.

**Как тестировать:**
- **Через отдельный `mlx_audio.server`** на другом порту (например 8000), не трогая VoiceStudio. Причина: внутри VoiceStudio модель mlx-audio выбирается переменной окружения или в UI, а клонирование там работает только с CSM (раздел 3). В отдельном сервере модель задаётся в каждом запросе:
  ```json
  {"model": "mlx-community/chatterbox-multilingual-v3", "input": "…", "lang_code": "ru",
   "ref_audio": "/path/host_ref.wav", "ref_text": "…", "response_format": "wav"}
  ```
  Для Qwen3-TTS VoiceDesign вместо `ref_audio` передаётся `instruct` с описанием голоса. Точное значение `lang_code` для русского у каждой модели **не проверено**.
- **Контроль:** тот же OmniVoice в MLX (`mlx-community/OmniVoice-bf16`) с тем же эталоном. Так видно, сколько даёт смена бэкенда MPS→MLX, а сколько смена модели.
- **Набор фраз:** 20–30 реальных реплик ведущего и новостей, включая короткие (2–3 с), числа, аббревиатуры, «…», «—», «!?», а также фразы, где сейчас слышен шум.
- **Метрики:** время синтеза на фразу (тёплый старт, со второй генерации); доля дублей с шумом или артефактами; ошибки ударений; слепая оценка на слух 1–5 по «естественность» и «живость». Для простого WER можно прогнать результат через MLX Whisper.
- VoxCPM2 можно дополнительно проверить внутри VoiceStudio: курированный ключ `voxcpm2` в движке mlx-audio или нативный движок VoxCPM2 с клонированием.

---

## 7. Не проверено / открытые вопросы

- Реальная скорость (RTF) на нашем Mac для всех моделей. Для Qwen3-TTS, Chatterbox, VoxCPM2, Higgs и MLX-порта OmniVoice замеров на Apple Silicon в первоисточниках нет.
- Работает ли `instruct` (дизайн голоса) у MLX-порта OmniVoice так же, как у PyTorch-версии. README порта называет параметр «model-specific».
- Качество русского у Chatterbox Multilingual V3, XTTS-v2, MOSS-TTS-Nano: опубликованных метрик нет.
- Точный русский WER у Higgs Audio v3 (в карточке только ярус «<5»).
- Насколько инструкции Qwen3-TTS и VoxCPM2 соблюдаются на русском. Примеры у авторов на китайском и английском.
- Стриминг у OmniVoice и Chatterbox: в README не описан.
- Влияет ли водяной знак AudioSeal на слышимое качество, и становится ли звук чище при `watermark.invisible = false`.
- Поддержка MPS в PyTorch-версиях Qwen3-TTS и CosyVoice 3.
- Значение `lang_code` для русского в каждой модели mlx-audio при запросах через сервер.
- Лицензии отдельных русских голосов Piper.
- Порт Fish S2-Pro в MLX: в таблице mlx-audio его нет, есть только Drama 3.
- Связан ли шум на коротких фразах с самой моделью, с 16 шагами или с режимом дизайна голоса. Разделить поможет шаг 0 из раздела 6.
