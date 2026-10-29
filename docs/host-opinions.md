# Host opinions

Public defaults are **Alex**, the curious enthusiast, and **Jess**, the friendly skeptic; both character descriptions are written in English. Personal station overrides stay local.

Enable **Experiments → Host opinions**. In **Settings**, open **Details** beneath each host name to edit **Host character** and **Co-host character** (up to 1200 characters each; saved when leaving the field). Defaults are a curious enthusiast and a friendly sceptic. **Two hosts** gives the co-host a separate voice; without it, new scripts use the main host. No additional model call is needed for a comment.

The character supplies interests, values and a manner of judging the facts. A news script gives the event first, then one short, clearly subjective assessment with a reason. The news-detail word budget still applies. Weather, timed reminders and authored announcements keep their wording. Musical judgements and memory of previous opinions are not implemented.

Characters affect new scripts and their cache keys. Prepared scripts retain their author/voice association through retries, database reopen and edition regrouping. Editing or disabling a character does not rewrite prepared audio. Empty character text uses the usual editorial style, including the existing Discussion comment if selected.

Reddit now retains the post's `.md` text, excluding feed navigation, and marks unavailable visual attachments. The shared editor must explain a concrete event without an image, link or prior knowledge of the thread. A subreddit name is a source, not a project. An opaque caption or question returns `SKIP`; missing visual details must not be invented. A catch-up that cannot faithfully cover every selected source is withheld as a whole, preserving its source/history attribution.

## Verification, 29 October 2026

- The configured model returned `SKIP` for both user examples, “кто будет покупать ЭТО в здравом уме?” and “Яма.”, and for a roundup containing both.
- Both characters produced a short comment on a synthetic news item about a local note editor without cloud sync. This verifies generation, not factual correctness of every possible inference or listening quality.
- Isolated tests cover switches, profile/cache changes, exact notices, persisted speaker assignment, rejection before speech, Reddit body extraction and HTML escaping.
- Real Liquidsoap integration tests exercised imported news and manual catch-up through playback/history using synthetic speech; this is not a human listening check.
- The Settings fields were inspected in the browser, including keyboard save, Reset availability and a 390-pixel viewport without horizontal overflow.
