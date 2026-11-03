# What the host says, and when

Draft for review. Covers every templated line on air (the editor's retellings of news are covered by `radio/editor.py`).
Each moment lists: when it plays, what plays now, the problem with it, and proposed lines. Nothing here is in the code
yet: the station keeps the current lines until a set of proposals is chosen.

## Rules for every line

These come from the [humanizer](https://github.com/blader/humanizer) skill, which is based on Wikipedia's "Signs of AI
writing". It was written for English prose, so these are its rules adapted to Russian that is spoken aloud.

- **Say the thing; don't stage it.** No run-up before the point: «Небольшая сверка с календарём:», «Коротко о главном»,
  «Ещё одна примета сегодняшнего дня». The host just says the reminder.
- **No closers or slogans.** «Не переключайтесь», «Оставайтесь с нами», «Отлично!». One short line can stay when it
  says what comes next.
- **No bookish, inflated or sales words.** «в исполнении», «исполнитель —», «музыкальный блок», «подборка»,
  «нон-стоп», «свежей музыки без перерыва». Say «песня», «трек», «играли», «дальше».
- **Not in threes, and not always the same shape.** A back-sell is sometimes one song, sometimes two names, sometimes
  nothing. Every moment has several variants that rotate without repeats.
- **No pairs like «не просто X, а Y»** in templates or in the editor's text.
- **Dashes are pauses for the voice.** Use them where a person would pause. A line full of them sounds read off a card.

## What the voice needs

- **No verb that agrees with the artist or the title.** «прозвучал/прозвучала», «играл/играла» guess the gender. A verb
  may agree with «песня», «трек», «мы», «вы», or a plural list of names («играли X, Y и Z»).
- **Names stay in the nominative.** «от Макс Корж» and «за carbonara» cannot be inflected correctly. Use apposition
  instead: «Это Макс Корж с песней «Неважно»», «Макс Корж, «Неважно»».
- **Latin names in «quotes».** The voice then reads them as English words, not spelled out (`host._quoted`).
- **Times and numbers in words.** `host.time_words`, `span_words`.
- **Voice tags.** `[laughter]` (a short chuckle) and `[surprise-ah]` (an «А,»). Not `[sigh]`: it turns the clip into
  noise. At most one tag in a line, and only in some variants.

## Station start

When: the station starts and the first YouTube Music song is still loading (`host.startup`).

Now: «В эфире «Yourwave». Первая песня уже на подходе — YouTube Music её догружает, а мы пока здороваемся.»

Problem: long, explains the machinery.

Proposed:
- «Привет, это {station}. Первая песня почти загрузилась.»
- «{station} в эфире. Секунду, первый трек догружается.»
- «Это {station}. Пока грузится первая песня, просто здороваюсь.»

## Time check at the start of an edition

When: a host edition opens, after the back-sell (`host.OPENERS`).

Now:
- «{time}. Вы слушаете {station}.»
- «На часах {time}, это {station}.»
- «{station}, {time}. Несколько слов между треками.»
- «{time}, с вами {station}. Коротко о главном.»

Problem: «Коротко о главном» and «Несколько слов между треками» announce the news instead of reading it.

Proposed:
- «{time}, {station}.»
- «На часах {time}. Это {station}.»
- «{station}, время {time}.»
- «{time}. Немного новостей, и снова музыка.»
- «Уже {time}. Прервёмся ненадолго.»

## Back-sell: what just played

When: before an edition, after the last song (`host.back_sell`). Per break it picks one shape: nothing, the last song,
or two or three artists. The same one-song lines open the song link below.

### One song (`LAST_SONG`)

Now:
- «Вы слушали {artist} — «{title}».»
- «Это был трек «{title}», исполнитель — {artist}.»
- «Прозвучала песня «{title}» в исполнении {artist}.»

Problem: «исполнитель —» and «в исполнении» are announcer words from the 1980s.

Proposed:
- «Это {artist}, «{title}».»
- «Вы слушали «{title}», {artist}.»
- «Это {artist} с песней «{title}».»
- «{artist}, «{title}» — вот что сейчас играло.»
- «Только что звучала песня «{title}». Это {artist}.»

### Several artists (`BACK_SELLS`), the line you disliked

Now: «Это был музыкальный блок: {names}. Последней прозвучала «{title}».», «Вы слушали подборку: {names}.», «В этом
блоке звучали песни {names}.»

Problem: «музыкальный блок», «подборка», and the «Это были… Последней прозвучала…» shape.

Proposed:
- «У нас звучали {names}.»
- «Чуть раньше играли {names}, а только что «{title}».»
- «{names}: вот кто был в эфире последние минуты.»
- «Вы слушали {names}. А закончили мы на «{title}».»
- «Перед новостями играли {names}.»

## Back to the music after an edition

When: the last line of an edition (`host.set_opener`). Sets are gone, so the «{minutes} музыки» lines no longer apply
and are removed.

Now: «Возвращаемся к музыке.», «А теперь снова музыка.», «[laughter] Ну что, возвращаемся к музыке.», plus the lines
with minutes («нон-стоп», «без перерыва, оставайтесь с нами», «Отлично!»).

Proposed:
- «И снова музыка.»
- «Всё, возвращаемся к музыке.»
- «На этом всё, дальше музыка.»
- «Новостей пока хватит.»
- «[laughter] Ну всё, хватит разговоров.»

### Naming the next song (`FIRST_SONG`)

Added to the line above in two breaks out of three.

Now: « Начинаем с {artist}, «{title}».», « Первой прозвучит «{title}», {artist}.», « Открывает сет {artist}.»

Problem: there are no sets now; «Первой прозвучит» guesses the gender of the title.

Proposed:
- « Дальше {artist}, «{title}».»
- « Сейчас будет {artist}.»
- « Следующий трек «{title}», {artist}.»
- « А сейчас {artist} с песней «{title}».»

### When the next song is new to the station

Replaces `NEW_SET_OPENERS` («новинки нашей библиотеки», «{minutes} свежей музыки») now that new songs go ahead one at a
time:
- « Дальше кое-что новое: {artist}, «{title}».»
- « Этого трека в эфире ещё не было: «{title}», {artist}.»
- « Свежее в фонотеке: {artist}.»

## Song link between songs

When: between two songs, without news (`host.music_link`). It is the back-sell of the one song, an optional verified
fact, and a short closer. It airs only right after the song it names.

Now (closers, `MUSIC_LINKS`): «А мы продолжаем.», «Продолжаем.», «Дальше — снова музыка.», «Не переключайтесь.»,
«[laughter] А мы продолжаем!»

Problem: «Не переключайтесь» is a slogan; «А мы продолжаем» four times an hour wears thin.

Proposed:
- «Едем дальше.»
- «Слушаем дальше.»
- «Дальше ещё музыка.»
- «И следующая.»
- «[laughter] Так, что там у нас дальше.»

Before a fact (`FACT_LEADS`): «», «Кстати, », «[surprise-ah] Кстати, ». Add «Между прочим, ».

## Coming up

When: an edition ends with a preview of the next two songs (`previews.py`, the Coming up experiment).

Now: «А сейчас — {first}.», «Позже в этом музыкальном блоке — {second}.»

Problem: «в этом музыкальном блоке».

Proposed:
- «Сейчас будет {first}, а чуть позже {second}.»
- «Сначала {first}, а следом {second}.»
- «Дальше {first}. И ещё до новостей {second}.»

## Reminders

When: before a calendar event or a Todoist task, at its time, and late (`reminders.py`, `TEMPLATES`). `{when}` is
spoken minutes («через пятнадцать минут»), `{title}` is the event or task.

### Before

Now (event): «Кстати, через {when} — {title}. Самое время потихоньку закругляться.», «Небольшая сверка с календарём:
через {when} у тебя — {title}.», «Пока играет музыка, напомню: через {when} — {title}.»

Proposed (event):
- «Через {when} у тебя {title}.»
- «Не забудь: через {when} {title}.»
- «Через {when} {title}. Можно потихоньку собираться.»
- «Напомню: {title} через {when}.»

Now (task): «И про дела: через {when} по плану — {title}.», «Загляну в список дел: через {when} пора браться — {title}.»,
«Через {when} в планах — {title}. Можно уже готовиться.»

Proposed (task):
- «Через {when} по плану {title}.»
- «Из списка дел: {title}, через {when}.»
- «Через {when} пора будет заняться: {title}.»

### At its time

Now: «А вот и время — {title}. Начинается прямо сейчас.», «Часы подсказывают: прямо сейчас — {title}.», «Пора: по
плану прямо сейчас — {title}.», «Самое время взяться за дело — {title}.»

Proposed:
- «{title} начинается прямо сейчас.»
- «Всё, пора: {title}.»
- «По плану сейчас {title}.»
- «Пора браться: {title}.»

### Late

Now: «Если ты ещё не там — {title} уже идёт, с начала прошло {when}.», «Маленькое напоминание вдогонку: уже {when} как
идёт — {title}.», «Время для этого подошло {when} назад, но ещё не поздно — {title}.», «Вдогонку из списка дел — {title}.
По плану было {when} назад.»

Proposed:
- «{title} идёт уже {when}. Ещё успеваешь.»
- «Вдогонку: {title}, уже {when} как началось.» (agrees with «оно», the event, not the title)
- «По плану {when} назад было {title}. Ещё не поздно.»

### Days and holidays

Now (a day mention): «Сегодня — {title}.», «К слову, сегодня — {title}.», «Ещё одна примета сегодняшнего дня — {title}.»

Proposed: «Сегодня, кстати, {title}.», «И да, сегодня {title}.», «Сегодня {title}.»

Holiday previews («Завтра — {title}. Ещё есть время придумать поздравление.») stay; add «Можно заранее подумать о
подарке.»

## Weather

When: every three hours, or inside an edition (`weather.py`).

Now: «Заглянем за окно.», «Пара слов о погоде.», «А на улице тем временем так.», «Сверимся с небом.», or nothing;
then «{city}: плюс восемнадцать градусов, ясно, без дождя. Зонт сегодня точно пригодится.»

Problem: «Сверимся с небом» and «тем временем так» try too hard.

Proposed openers: «Теперь погода.», «Что за окном:», «По погоде:», «Пара слов о погоде.», or nothing. The forecast
and the umbrella/coat tails stay.

## YouTube Music is slow

When: the next YouTube song is not loaded in time, so a local song fills in (`daemon.GAP_LINES`).

Now: three long lines («Связь с YouTube Music сегодня неторопливая: пока она догружает трек, ставлю песню из своей
коллекции.»).

Proposed:
- «YouTube Music тормозит, так что пока песня из моей фонотеки.»
- «Следующий трек ещё грузится. Чтобы не молчать, поставлю что-нибудь своё.»
- «Пока YouTube думает, послушаем вот это.»

## GitHub, news sections, charts

These are mostly fine. GitHub intros (`github_trending.py`: «В трендах GitHub сегодня — {name}.», «Небольшая находка с
GitHub — {name}.», «Новинка недели — {name}.») and «Продолжаем: {topic}.» already say what comes.

The chart intro («{День}, время хит-парада {station}: топ {size} недели по вашим отметкам.», «Номер {place}: {title}.»)
could be «{День}, а значит хит-парад. Десятка недели по вашим отметкам.»

## An experiment with humanizer

The templates above were edited by hand with these rules. The news itself is written by the script editor, a language
model, for every story, so that is where humanizer could do more. Two ways to try it:

1. **Rules in the editor's prompt.** An experiment switch («Humanized retellings») adds a short Russian block to
   `EDITOR_RULES`:
   - say the fact without a run-up;
   - no «не просто X, а Y»;
   - no closing moral and no «важно отметить», «стоит отметить», «в целом»;
   - no inflated or advertising words;
   - no lists of three for their own sake;
   - short spoken sentences.

   The cache key includes the prompt, so the switch rebuilds stories from then on. Compare a day with it on against a
   day with it off on the same sources. Costs nothing per story; the small local model may follow it only partly.
2. **A second pass.** After the editor's retelling, a second request rewrites it with the rules above. It is closer to
   the skill, but it doubles editor calls (the hourly limit is shared with news) and adds a delay per story.

Proposed: try 1 first.
