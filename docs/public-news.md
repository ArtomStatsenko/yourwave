# Experimental Reddit and Telegram news

**Reddit news** and **Telegram news** are independent, initially disabled experiments. Enable the source, then open **Sources → News sources** and expand **Details** for that source to edit its subreddits or public channels. Enter one name or link and choose **Add**, or press Enter. Only the new entry is checked; duplicates are ignored. **Remove** deletes one entry without a network request. Errors appear beside the field and preserve the draft. Individual-post controls are not exposed in the UI; existing `posts` configuration remains supported.

Lists are saved in `settings.json` and applied at the next source check without a restart. Values in `config.toml` provide the defaults:

```toml
[sources.reddit]
communities = ["python", "technology"]
posts = []  # links to individual threads or posts

[sources.telegram]
channels = ["telegram"]
posts = []  # links such as https://t.me/telegram/83
```

The UI accepts bare names, `r/subreddit`, `@channel`, and HTTPS links to the corresponding service. It normalizes links and removes duplicates. Each list allows up to 20 entries; the add field accepts one name or link of up to 512 characters. The `communities` and `channels` arrays in TOML still use bare names. Editing `config.toml` requires a restart; saving through the UI does not. Set a separate `RADIO_DATA_DIR` for tests to protect the live station's settings and history.

## Reddit posts and comments

Reddit requires no account, Client ID or API access. The adapter reads titles and available text from the latest 25 posts in `/r/NAME/new/.rss`. For up to three new eligible posts per check, it also reads the thread's RSS and selects up to five distinct short comments from the first 25 entries returned with Reddit Top sorting (`sort=top`). Duplicate, promotional, deleted and overly long comments are skipped. Reddit supplies the popularity order; the adapter preserves that order among eligible comments. This is a limited sample, not the view of the whole community. Imported posts are not fetched again just to refresh comments, and a comment never changes the post's publication date.

A post and its comments form one source item and reach the editor together. Saved individual-thread links use the same grouping: a comment cannot become a standalone news item. Comment links and dates remain in provenance. If comments are unavailable, the original post remains usable; `comments_status` distinguishes `available`, `none` and `unavailable` in the metadata. Substantive arguments can appear in the bulletin as individual participants' reactions. The editorial rules distinguish sarcasm, rumors, speculation and host opinion from established facts. A comment does not verify an unseen image; a post that cannot be understood without the image is still withheld. Comment analysis shares the normal editorial request, without an additional LLM call.

Adding a subreddit checks only that new entry's RSS. Existing entries are not checked again. Access errors, missing feeds, rate limits and network failures appear beside the field while preserving the saved list and draft. Empty lists are saved without requests. This validates access, not subreddit discovery: a failed request cannot reliably distinguish a private source from a blocked request. TOML validates format; configured sources are checked during import.

This adapter is temporary: [Reddit announced that RSS support ends on November 13, 2026](https://support.reddithelp.com/hc/en-us/articles/54353370049684-Changelog-October-8-2026). If access is denied, the importer reports the failure without proxies, alternate domains or restriction bypasses.

## Telegram public previews

Adding a channel checks its public preview first; unavailable channels are not saved. No account is required, and removal works without Telegram access. The adapter uses the [official public channel preview](https://telegram.org/blog/privacy-discussions-web-bots#view-public-channels) at `https://t.me/s/NAME`, without an API key or bot. An individual-post URL reads `t.me/s/CHANNEL/ID` and selects that ID alone. Private posts (`t.me/c/...`), invitations and forum-topic links with extra path segments are unsupported.

Channel imports read the available page of recent posts. They do not download media, comments or full history, or bypass sign-in and regional/age restrictions. A missing preview is a source error; posts without text or dates are skipped. Preview HTML is not a stable API, so markup changes may require adapter repairs. The Bot API is not used to read arbitrary channels' history.

## Scheduling and editorial preparation

Automatic checks happen before an upcoming edition, at most once every 30 minutes per source, including failures. Reddit HTTP 429 sets a shared UI/import cooldown using `Retry-After` seconds or its HTTP date; without that header, the cooldown is 30 minutes. No further requests are sent during the cooldown. The state lives in `run/reddit-rss.lock` and survives restarts; the displayed local retry time does not promise that Reddit will accept the next request.

Saving a changed list permits another check before an edition. Responses for sources removed during a request are discarded. One unavailable feed does not block others. Each check imports at most three items from each community, channel or selected thread. Editing lists does not delete collected material. Existing freshness, `blocked_words` and speech-budget checks remain in force; a saved post link does not make an old story fresh.

Adapters preserve source text without translation. The shared editor prepares concise Russian speech using the model selected in Settings; the current broadcast language remains Russian. The original stays available, and history displays the approved spoken text. A failed or rejected rewrite cannot send the raw source to TTS. Normal LLM limits, caching and text checks apply; adapters make no model requests themselves.

Deduplication persists by post ID, URL or identical normalized text, including across adapters. This is exact matching, not semantic similarity. Existing `digest.md` deduplication remains unchanged. The pipeline is `items → packages → TTS → Liquidsoap queue → broadcast events → timeline`. Disabling a source prevents new preparation and scheduling; a segment already playing may finish.

## Verification

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_public_news.py -v
```

Tests use temporary data, fixed source responses and MockTTS synthetic audio, with real FFmpeg and Liquidsoap. They cover parsing, dates, durable deduplication, input/host validation, switches, failure isolation, request cadence, comment grouping and unchanged digest imports. The end-to-end test schedules two editions and checks both sources reaching `PLAYED` / `PACKAGE_COMPLETED`, their history/provenance, and decoding of the recorded WAV. It does not verify internet availability, voice quality or human listening to the live station.
