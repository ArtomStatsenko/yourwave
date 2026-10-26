# Suno: проверка готовых адаптеров по исходному коду

Дата: 26 октября 2026. Это дополнительный технический отчёт к исследованию интеграции PersonalRadio. Проверены снимки публичного исходного кода; сторонний код **не выполнялся**, cookie/токены пользователя **не читались**. Проверка кода показывает реализованные механизмы, но не заменяет живое воспроизведение с текущей сессией Suno.

## Вывод

Из четырёх проверенных проектов только `sunyujingmusic-alt/unofficial-suno-api` действительно содержит обработку нового `media_urls` / `encoding=1.0.0`. Raydio и Suno Radio Bot по-прежнему опираются на старый `audio_url` / MP3 CDN, поэтому сами по себе не исправляют наблюдаемую проблему свежих треков. Популярный `gcui-art/suno-api` тоже не содержит нового playback-механизма. При этом актуальный неофициальный fork — не настоящий потоковый прокси: он полностью получает медиа и лишь затем возвращает ответ. Для радио это означает обязательную предварительную подготовку следующего трека.

## Зафиксированные версии

Данные GitHub API получены 26.10.2026. Число звёзд — приблизительный индикатор размера сообщества, не доказательство надёжности.

| Проект | Commit | Последний commit | Звёзды / forks | Лицензия |
|---|---|---|---|---|
| [hellodword/raydio](https://github.com/hellodword/raydio) | `4ae4b1690cf0c53b09511e3e8eebc15ee8772092` | 2026-07-18 | 1 / 1 | GitHub `license: null`; LICENSE в снимке не найден |
| [sunyujingmusic-alt/unofficial-suno-api](https://github.com/sunyujingmusic-alt/unofficial-suno-api) | `333a59984cd38a8fa0553bdddcb66ae7cb700ef9` | 2026-09-14 | 7 / 1 | LGPL-3.0; package.json уточняет `LGPL-3.0-or-later` |
| [gcui-art/suno-api](https://github.com/gcui-art/suno-api) | `a2e6a823428903af715d3835d1cb44ffa336021d` | 2026-03-06 | 3279 / 922 | LGPL-3.0; package.json `LGPL-3.0-or-later` |
| [kphillipsdev/suno-radio-bot](https://github.com/kphillipsdev/suno-radio-bot) | `eccc3c96385cf07c43cf3b753701e67e3c9b6109` | 2026-08-10 | 2 / 0 | GitHub `license: null`; README говорит MIT «see repository license if present», отдельный LICENSE не найден |

Источники метаданных: [Raydio API](https://api.github.com/repos/hellodword/raydio), [fork API](https://api.github.com/repos/sunyujingmusic-alt/unofficial-suno-api), [gcui API](https://api.github.com/repos/gcui-art/suno-api), [bot API](https://api.github.com/repos/kphillipsdev/suno-radio-bot). Версии исходников ниже закреплены commit SHA.

## 1. Raydio

### Что действительно реализовано

Это отдельная радиосистема: HTTP-сервер, SQLite, общий временной план для слушателей, worker нормализации аудио и отдельный Suno sync worker. Для готового автономного MP3-радио архитектура подходящая; для PersonalRadio она дублирует уже существующий проигрыватель, очередь, каталог и историю. [README](https://github.com/hellodword/raydio/blob/4ae4b1690cf0c53b09511e3e8eebc15ee8772092/README.md).

Suno worker обращается к `https://studio-api-prod.suno.com/api/playlist/<uuid>` без cookie/Authorization и берёт только `complete` clips. Структура ответа знает `audio_url`, но не `media_urls`, `encoding`, Mango. Он скачивает `audio_url` в файл с расширением `.mp3`; последующий worker нормализует его FFmpeg. Поэтому ответ `/api/forbidden` или защищённый контейнер этот код не исправляет. [suno.go, Playlist](https://github.com/hellodword/raydio/blob/4ae4b1690cf0c53b09511e3e8eebc15ee8772092/internal/suno/suno.go#L96), [syncClip](https://github.com/hellodword/raydio/blob/4ae4b1690cf0c53b09511e3e8eebc15ee8772092/internal/suno/suno.go#L237), [структура ответа](https://github.com/hellodword/raydio/blob/4ae4b1690cf0c53b09511e3e8eebc15ee8772092/internal/suno/suno.go#L297).

### Ограничения для активного эфира

- По умолчанию синхронизация раз в **30 минут**, HTTP timeout **30 секунд**. Это обновляемая локальная копия плейлиста, не поток напрямую из Suno. [config.example.yaml](https://github.com/hellodword/raydio/blob/4ae4b1690cf0c53b09511e3e8eebc15ee8772092/config.example.yaml#L117).
- В `Playlist()` один запрос и обработка одного `playlist_clips`; цикл пагинации отсутствует. Полнота больших плейлистов зависит от ответа внутреннего API. Дополнительно worker удаляет ранее управляемые файлы, отсутствующие в полученном списке; неполная выдача важна при оценке безопасности синхронизации. Ручные файлы защищаются manifest-учётом. [Playlist](https://github.com/hellodword/raydio/blob/4ae4b1690cf0c53b09511e3e8eebc15ee8772092/internal/suno/suno.go#L96), [deleteStale](https://github.com/hellodword/raydio/blob/4ae4b1690cf0c53b09511e3e8eebc15ee8772092/internal/suno/suno.go#L596).
- Требуются Go 1.26, SQLite cgo, FFmpeg/ffprobe; три собственных процесса. [go.mod](https://github.com/hellodword/raydio/blob/4ae4b1690cf0c53b09511e3e8eebc15ee8772092/go.mod).
- Docker Compose содержит четвёртый сервис `cloudflared`, публикующий сервер через туннель; запускать пример как есть для локального исследования излишне. При этом у контейнеров есть read-only filesystem, non-root user и dropped capabilities. [docker-compose.yml](https://github.com/hellodword/raydio/blob/4ae4b1690cf0c53b09511e3e8eebc15ee8772092/docker-compose.yml).
- Лицензия на заимствование кода в снимке не определена.

**Вердикт:** интересный пример радиосервера и синхронизации, но не подходящий готовый playback-адаптер для новых защищённых треков Suno. Разворачивать целиком рядом с PersonalRadio нецелесообразно.

## 2. sunyujingmusic-alt/unofficial-suno-api

### Что подтверждено кодом

Fork содержит `GET /api/playback_audio?id=<UUID>`. Код проверяет UUID, получает clip через авторизованную сессию, выбирает progressive `media_urls`, поддерживает именно `encoding=1.0.0`, получает playback rights и возвращает декодируемые байты. Это реальная реализация, а не одно обещание в README. [route.ts](https://github.com/sunyujingmusic-alt/unofficial-suno-api/blob/333a59984cd38a8fa0553bdddcb66ae7cb700ef9/src/app/api/playback_audio/route.ts), [SunoApi.ts getPlaybackAudio](https://github.com/sunyujingmusic-alt/unofficial-suno-api/blob/333a59984cd38a8fa0553bdddcb66ae7cb700ef9/src/lib/SunoApi.ts#L1720), [playbackAudio.ts](https://github.com/sunyujingmusic-alt/unofficial-suno-api/blob/333a59984cd38a8fa0553bdddcb66ae7cb700ef9/src/lib/playbackAudio.ts).

Но выбор источника намеренно **не** откатывается на старый `audio_url`: clip без подходящего `media_urls` отвергается. Поддержка текущего защищённого формата не равна совместимости со всей старой библиотекой. [selector](https://github.com/sunyujingmusic-alt/unofficial-suno-api/blob/333a59984cd38a8fa0553bdddcb66ae7cb700ef9/src/lib/playbackAudio.ts#L57), [тест legacy rejection](https://github.com/sunyujingmusic-alt/unofficial-suno-api/blob/333a59984cd38a8fa0553bdddcb66ae7cb700ef9/tests/playback_audio.test.ts).

### Это не progressive streaming

`getPlaybackAudio()` делает Axios request с `responseType: arraybuffer`, лимитом **100 MiB** и timeout **120 секунд**. Только после полной загрузки и обработки создаётся `Response(result.data)` с `Content-Length`. В route нет обработки `Range`, streaming body или выдачи ранних частей. Возможный `delivery: progressive` описывает upstream media, а не поведение локального endpoint. Следовательно, трек надо загрузить/проверить до момента переключения. [getPlaybackAudio](https://github.com/sunyujingmusic-alt/unofficial-suno-api/blob/333a59984cd38a8fa0553bdddcb66ae7cb700ef9/src/lib/SunoApi.ts#L1720), [response route](https://github.com/sunyujingmusic-alt/unofficial-suno-api/blob/333a59984cd38a8fa0553bdddcb66ae7cb700ef9/src/app/api/playback_audio/route.ts#L20).

### Авторизация и поверхность доступа

- Нужна полноценная действующая Suno-сессия через `SUNO_COOKIE`, затем внутренние Clerk/Suno токены. Это не официальный developer API key. Cookie нельзя передавать внешнему hosted proxy. [constructor](https://github.com/sunyujingmusic-alt/unofficial-suno-api/blob/333a59984cd38a8fa0553bdddcb66ae7cb700ef9/src/lib/SunoApi.ts#L816).
- Compose по умолчанию привязывает API к `127.0.0.1`, что правильно для локального инструмента. Но HTTP routes не имеют отдельного client API key, CORS разрешает `*`, а запрос без cookie использует env-сессию владельца. В полном приложении присутствуют generation/export/studio routes. Изоляция только IP-binding не заменяет ограничение операций. [compose](https://github.com/sunyujingmusic-alt/unofficial-suno-api/blob/333a59984cd38a8fa0553bdddcb66ae7cb700ef9/docker-compose.yml), [CORS](https://github.com/sunyujingmusic-alt/unofficial-suno-api/blob/333a59984cd38a8fa0553bdddcb66ae7cb700ef9/src/lib/utils.ts#L35), [generate route](https://github.com/sunyujingmusic-alt/unofficial-suno-api/blob/333a59984cd38a8fa0553bdddcb66ae7cb700ef9/src/app/api/generate/route.ts).
- Request interceptor автоматически добавляет Bearer token ко всем запросам общего Axios client, включая используемый им CDN request; cookie при этом фильтруются отдельно. Для узкого адаптера токен следует ограничить точными API origins и использовать отдельный чистый client для CDN. Это находка кода, не доказательство произошедшей утечки. [interceptor](https://github.com/sunyujingmusic-alt/unofficial-suno-api/blob/333a59984cd38a8fa0553bdddcb66ae7cb700ef9/src/lib/SunoApi.ts#L847).
- Есть allowlist HTTPS media hosts и проверка неизвестных encodings. Но allowlist по suffix включает все `cloudfront.net` и `amazonaws.com`; не следует превращать этот endpoint в общий публичный proxy. [playbackAudio.ts](https://github.com/sunyujingmusic-alt/unofficial-suno-api/blob/333a59984cd38a8fa0553bdddcb66ae7cb700ef9/src/lib/playbackAudio.ts#L22).
- В runtime dependencies отсутствуют Playwright/Electron; HTTP playback может работать без браузера. Однако приложение в целом — Next.js/React/TypeScript, а не маленькая Python-библиотека. [package.json](https://github.com/sunyujingmusic-alt/unofficial-suno-api/blob/333a59984cd38a8fa0553bdddcb66ae7cb700ef9/package.json).

### Что доказывают тесты

Файл `playback_audio.test.ts` содержит четыре unit-теста выбора URL, запрета forbidden/legacy источников и синтетического криптографического вектора. Это полезная регрессия, но **не** подтверждение актуальной Suno-авторизации, настоящего аудиоконтейнера, радиоэфира, пагинации или 24/7 устойчивости. В этой проверке тесты не запускались. [tests](https://github.com/sunyujingmusic-alt/unofficial-suno-api/blob/333a59984cd38a8fa0553bdddcb66ae7cb700ef9/tests/playback_audio.test.ts).

**Вердикт:** единственный проверенный исходник с релевантным современным playback-механизмом. Возможен как референс для изолированного прототипа после проверки допустимости использования; не рекомендован полный сервер как готовый доверенный источник эфира.

## 3. gcui-art/suno-api

Большое сообщество не означает актуального playback. Зафиксированный main содержит возврат `audio.audio_url`; поддержки `media_urls`, Mango и encoding в `src` не найдено. Значит, именно наблюдаемую проблему он не решает. [SunoApi.ts](https://github.com/gcui-art/suno-api/blob/a2e6a823428903af715d3835d1cb44ffa336021d/src/lib/SunoApi.ts#L632).

Дополнительно runtime dependencies включают Electron, Chromium, Playwright, ghost-cursor и 2Captcha, помимо Next.js/React. Это существенная поверхность поддержки ради одного источника музыки. Авторизация через `SUNO_COOKIE` и внутреннюю сессию, не официальный API. [package.json](https://github.com/gcui-art/suno-api/blob/a2e6a823428903af715d3835d1cb44ffa336021d/package.json), [cookie resolution](https://github.com/gcui-art/suno-api/blob/a2e6a823428903af715d3835d1cb44ffa336021d/src/lib/SunoApi.ts#L853).

В открытом issue описаны те же симптомы: `/api/forbidden`, CDN MP3 403, `media_urls` HTTP 200 с нераспознаваемым контейнером и `moov atom not found`; это независимое сообщение пользователя проекта, не гарантия для каждого трека. [Issue #289](https://github.com/gcui-art/suno-api/issues/289).

**Вердикт:** не выбирать для нового playback-эксперимента. Форк выше актуальнее по нужной части; звёзды исходного проекта не снимают форматный блокер.

## 4. Suno Radio Bot

Это готовый **Discord bot**, а не HTTP источник для Liquidsoap. Он умеет autofill, очередь, запросы, историю, конкурсы и использует Discord FFmpegPCMAudio. Большая часть функций уже есть в PersonalRadio или не нужна ему. [README](https://github.com/kphillipsdev/suno-radio-bot/blob/eccc3c96385cf07c43cf3b753701e67e3c9b6109/README.md), [playback](https://github.com/kphillipsdev/suno-radio-bot/blob/eccc3c96385cf07c43cf3b753701e67e3c9b6109/src/cogs/music/cog.py#L2397).

Suno metadata берётся с публичного внутреннего clip API. `clip_to_song_info()` использует `audio_url`, а при его отсутствии строит `https://cdn1.suno.ai/<id>.mp3`. Непустой `/api/forbidden` не считается ошибкой в этом mapper; `media_urls`/encoding обработчика нет. Поэтому новые защищённые треки остаются проблемой. [smart_scraper.py](https://github.com/kphillipsdev/suno-radio-bot/blob/eccc3c96385cf07c43cf3b753701e67e3c9b6109/src/utils/smart_scraper.py#L85).

Есть пагинация публичных плейлистов через `?page=N`, полезная как описание механизма discovery, но она не даёт звук. Требуются Python, discord.py voice, FFmpeg, PyNaCl, yt-dlp; необязательная панель добавляет FastAPI и Discord OAuth. [playlist scraper](https://github.com/kphillipsdev/suno-radio-bot/blob/eccc3c96385cf07c43cf3b753701e67e3c9b6109/src/utils/playlist_fast_scraper.py), [requirements](https://github.com/kphillipsdev/suno-radio-bot/blob/eccc3c96385cf07c43cf3b753701e67e3c9b6109/requirements.txt).

**Вердикт:** пригоден по назначению для Discord при доступных старых аудиоссылках; как адаптер PersonalRadio не подходит и текущий форматный блокер не устраняет.

## Рекомендация для PersonalRadio

Если официальный API действительно разрешает существующую библиотеку и выдаёт пригодное аудио, он предпочтительнее. До такого подтверждения не следует считать наличие API генерации решением streaming-задачи.

Из рассмотренных community-вариантов технически разумен только **узкий Suno playback experiment**, использующий актуальный протокол fork как исследовательский референс, при необходимости с учётом LGPL. Не разворачивать второй радиосервер/Discord bot и не открывать весь Next.js API с владельческой сессией.

Минимальная архитектура эксперимента:

1. Discovery отдельно от аудио: выбранный плейлист/подборка, устойчивые clip IDs, пагинация, обновление без очистки при частичном ответе или ошибке; новые треки подхватываются после `complete`.
2. Playback resolver получает метаданные и подготавливает один-два следующих трека до границы переключения; проверяет реальный контейнер/длительность и отсутствие пустого аудио перед передачей Liquidsoap.
3. PersonalRadio остаётся владельцем очереди, ведущего, истории, переключения источников и fallback. У Suno нет отдельного конкурирующего «эфирного времени».
4. Эксперимент по умолчанию выключен; никаких генераций, платных export/stems/Download операций или публикаций. Cookie живёт только в приватном локальном состоянии; лог содержит безопасный ID/ошибку, не URL с подписями или токены.
5. Перед интеграцией обязателен живой ограниченный smoke: авторизованное чтение, три различных свежих трека → проверенный аудиофайл → изолированный Liquidsoap output → подтверждённый ненулевой звук; истечение сессии/429/timeout не останавливают обычный эфир. Затем проверка обновления плейлиста и корректной смены источника на границе трека.

Ни один из четырёх проектов не проверен здесь на пользовательской сессии или в 24/7 эфире; реализация и включение эксперимента не выполнялись.
