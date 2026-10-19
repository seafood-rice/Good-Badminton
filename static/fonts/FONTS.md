# Fonts

| File | Family | Source | License |
|---|---|---|---|
| `SpaceGrotesk-Variable.woff2` | Space Grotesk, variable, weight 400-700, Latin subset | https://fonts.gstatic.com/s/spacegrotesk/v22/V8mDoQDjQSkFtoMM3T6r8E7mPbF4Cw.woff2 | SIL Open Font License 1.1 |
| `InstrumentSerif-Regular.woff2` | Instrument Serif, regular, Latin subset | https://fonts.gstatic.com/s/instrumentserif/v5/jizBRFtNs2ka5fXjeivQ4LroWlx-6zUTjg.woff2 | SIL Open Font License 1.1 |
| `InstrumentSerif-Italic.woff2` | Instrument Serif, italic, Latin subset | https://fonts.gstatic.com/s/instrumentserif/v5/jizHRFtNs2ka5fXjeivQ4LroWlx-6zAjjH7M.woff2 | SIL Open Font License 1.1 |
| `IBMPlexMono-{400,500,600}.ttf` | IBM Plex Mono | pre-existing in this repository | SIL Open Font License 1.1 |

The three woff2 files were listed by the Google Fonts CSS API (2026-10-19), taking the `/* latin */`
subset entries of:
`https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400..700&family=Instrument+Serif:ital@0;1&display=swap`

Latin only: CJK text falls back to the system `PingFang SC` / `Microsoft YaHei` stack
(see `static/kestrel/tokens.css`).

Space Grotesk and Instrument Serif are licensed under the SIL Open Font License 1.1
(https://openfontlicense.org), which permits bundling and redistribution with this application.
