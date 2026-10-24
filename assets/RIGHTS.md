# Базовые звуки: права и происхождение

Набор Base обновлён 24 октября 2026: четыре фрагмента **Tech Live** — **Kevin MacLeod**. Код остаётся MIT; эти аудиофайлы распространяются отдельно под **CC BY 4.0**.

> "Tech Live" Kevin MacLeod (incompetech.com)
> Licensed under Creative Commons: By Attribution 4.0 License
> https://creativecommons.org/licenses/by/4.0/

[Страница автора](https://incompetech.com/music/royalty-free/index.html?isrc=USUAN1700030) · [FAQ и правила атрибуции](https://incompetech.com/music/royalty-free/faq.html) · [Текст лицензии](LICENSE-CC-BY-4.0.txt).

Изменения: фрагменты по границам долей при 124 BPM, нормализация, ограничение пиков и плавные края; формат PCM S16LE, stereo, 44.1 kHz. Подложка содержит 8 тактов; на стыке её повторов применены края по 5 мс. Автор не связан с проектом и не заявлен как его сторонник.

| Файл | Роль | Длительность | SHA-256 |
| --- | --- | ---: | --- |
| [stinger_in.wav](stinger_in.wav) | Вступление | 1.935 с | `23adedf570f72066e7da5c4b098c5742d6a53a1599ce95ae6f089e3e5f9c2e70` |
| [bed.wav](bed.wav) | Подложка | 15.484 с | `46eb91eaba6132d8059b73cf67577c4ea09ad3396e423e3efe5a4ffd57c2d27d` |
| [separator.wav](separator.wav) | Разделитель / напоминание | 0.968 с | `cb9c37833e1116096d0d3fd4e67ceec082daee39e024b5eb9fd3202ea7ca6711` |
| [stinger_out.wav](stinger_out.wav) | Концовка | 1.935 с | `c10271d6d6c6c1f1938db0c554f5954f6be1d8c7f06edef59a6b9b0a1d1906c7` |

## Установка

Аудио не хранится в Git. Манифест [base-audio.json](base-audio.json) закрепляет версию `base-audio-v1.0.0`, URL, размер и SHA-256 архива и четырёх WAV.

Из корня клона с доступом к публичному релизу:

```sh
python3 tools/install_base_audio.py
```

Для приватного репозитория сначала авторизуйтесь в GitHub CLI, затем скачайте архив и установите его локально:

```sh
gh release download base-audio-v1.0.0 --repo ArtomStatsenko/PersonalRadio --pattern personalradio-base-audio-v1.0.0.zip --dir /tmp/personalradio-base-audio
python3 tools/install_base_audio.py /tmp/personalradio-base-audio/personalradio-base-audio-v1.0.0.zip
```

Тот же ZIP можно передать установщику без сети. Проверяются закреплённые хеши, размер, точный состав и формат WAV до записи. Повторная установка сохраняет совпадающие файлы; изменённый локальный WAV или символьная ссылка вызывают ошибку без замены. Личные пресеты не меняются; при ошибке записи новые файлы удаляются. Свою замену Base заранее перенесите в Personal или сохраните отдельно.

## Повторная сборка

[Скачать оригинальный MP3 у автора](https://incompetech.com/music/royalty-free/mp3-royaltyfree/Tech%20Live.mp3), затем из корня репозитория:

```sh
python3 tools/build_base_audio.py "/path/to/Tech Live.mp3"
```

Исходный SHA-256: `3fee678a805937e736449fe98347bbf3497258f6387b5569e49c97bae4c1072e`. Скрипт проверяет источник до записи и проверяет длительности, PCM-формат, громкость и пики перед заменой файлов. Хеши WAV в таблице получены с FFmpeg 9.0.2; другая версия FFmpeg может дать иные байты. Точные смещения и обработка сохранены в [каталоге](audio-manifest.json).

При распространении сохраняйте название, автора, ссылку на источник/лицензию и сведения об обработке. В приложении они доступны в Settings → Audio preset. Личные аудиопресеты имеют собственные условия.

## Предыдущие файлы

Прежние четыре WAV с неподтверждённым происхождением заменены в текущем дереве; их хеши сохранены в `replaced_base` каталога. Все аудиофайлы удалены из публикуемой Git-истории через `git-filter-repo`; резервная копия прежней истории хранится только локально и приватно. Перенос в Releases не меняет лицензию CC BY 4.0.

[Исторический аудит](../docs/audio.md) · [Права расширенного локального набора](jingles/RIGHTS.md).
