# Editorial personalities for AI hosts

Checked against official pages on October 29, 2026. The products were not run and their audio was not evaluated. This compares approaches without ranking their popularity or listening quality.

## Existing references

| Product | Available capabilities | Fit for Yourwave |
| --- | --- | --- |
| Futuri AudioAI / originally RadioGPT | AI DJs tailored to a station's style, local news and events; broadcast automation integrations and audio delivery by URL. | A commercial broadcasting product with demo/onboarding. The public pages do not establish a self-service API or Russian support. Adopting it would likely be a larger change than extending the current pipeline. |
| Spotify AI DJ | Personalized music selections with short commentary about tracks, artists and genres, informed by music editors. | A useful reference for concise music introductions. The reviewed pages describe a Spotify feature rather than an embeddable component; they do not establish a commentary API, custom host voices or editorial news positions. |
| NotebookLM / Gemini Notebook Audio Overview | Deep Dive, Brief, Critique and Debate formats, including two-host discussion; language/topic customization and audio export. | Useful for a manual conversation prototype grounded in several sources. Google describes the output as a reflection of sources rather than the hosts' subjective opinions; persistent personalities and continuous radio broadcasting are not promised. |

Sources: [Futuri AudioAI](https://futurimedia.com/products/audio-ai), [RadioGPT launch, 2023](https://futurimedia.com/press-release/futuri-launches-radiogpt), [Spotify DJ design](https://newsroom.spotify.com/2023-02-22/spotify-debuts-a-new-ai-dj-right-in-your-pocket/), [Spotify DJ voice requests, 2025](https://newsroom.spotify.com/2025-05-13/dj-voice-requests/), [Audio Overview help](https://support.google.com/gemininotebook/answer/16212820?hl=en).

Futuri's Rock 108 case study is a close reference: Tori has a snarky rock persona, is openly identified as AI, and appears overnight and on weekends. This demonstrates a deployed persona; a supplier case study does not independently establish audio quality or consistent editorial views. [KFMW / Tori case study](https://futurimedia.com/case-studies/case-study-this-ai-rocks-how-kfmw-used-audioai-to-amplify-brand-attitude-and-drive-revenue).

## Recommendation

None of the reviewed products establishes a simple drop-in personal-opinion component for the current Yourwave pipeline. Start with the existing text generation and give each host a short, stable editorial profile: values, interests, musical preferences and criteria for judging a story. A fictional biography or invented life experience is unnecessary.

For news, give the event first, then a brief assessment with a reason. Hosts can notice different aspects without having to disagree. Musical assessments need audio analysis or supplied musical features; artist information needs verified source material. A track title alone cannot justify claims about its bass, vocals or arrangement. These are design proposals, not verified capabilities of the products above.

Suggested first experiment: use the same three news items and two tracks for both personalities, then assess recognizability, specificity, invented facts and natural speech before broadcast integration. The implemented scope and verification are documented in [Host opinions](host-opinions.md).
