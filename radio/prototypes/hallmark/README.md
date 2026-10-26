# Yourwave / Hallmark design prototype

Three dark-only alternatives for the existing player and broadcast timeline:

- `?variant=A`: Ultraviolet, split player and timeline.
- `?variant=B`: Darkroom, compact horizontal console.
- `?variant=C`: Ribbon, a quiet single column.

Run from the repository root:

```sh
python3 -m http.server 18943 --bind 127.0.0.1 --directory radio/prototypes/hallmark
```

Open http://127.0.0.1:18943/?variant=C. Use the bottom controls or left/right keys to compare. Settings, integration toggles, edition disclosures, sample next-track changes and program editing are local demonstrations. No station API, audio, account access or persistence is connected. Track names, times and integration states are sample data.

The initial document, all three variants and native controls use a dark colour scheme. There is no light variant, automatic colour-scheme selection, animation, flashing or automatic audio. This describes the implementation, not a claim about medical suitability.

Production files are untouched. This is throwaway presentation code: after a choice, implement the selected direction against the real UI and archive the alternatives separately. Functional production scenarios and physical-phone testing are outside this prototype.

## Provenance and checks

Themes were dealt and scaffolded using [Hallmark v2](https://github.com/Nutlope/hallmark), in app scope. Palettes were adapted to the user's dark-only requirement, with subdued text and accents; Darkroom uses Instrument Sans for body text. Local fonts are from Google Fonts, with their OFL licenses beside the files.

Hallmark browser checks and prototype interaction captures live in the ignored `.hallmark/` directory. Two static warnings are expected: repeated sample track duration across mutually exclusive variants, and a heuristic classifying the unfilled Edit/+15 controls as primary buttons. These are not production functional checks.
