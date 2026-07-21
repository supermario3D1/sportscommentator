# AI Sports Commentary Generator

A consent-first web application prototype for generating broadcast-style sports commentary from uploaded match footage and an approved narrator voice sample.

## What it does

- Upload a sports video and MP3/WAV voice sample.
- Requires explicit confirmation that the speaker gave permission before voice analysis.
- Extracts acoustic voice characteristics in-browser: pitch, energy, speed, pauses, intonation, tone, and confidence notes.
- Samples the video over time to infer likely sport, field/court features, motion peaks, team colours, scoreboard/clock likelihood, and draft match events.
- Generates natural commentary lines around important moments instead of describing every movement.
- Supports styles such as TV Broadcast, Excited, Professional, Radio, Calm, Documentary, Australian, British, and American commentator.
- Provides timeline review, browser preview, script editing, line regeneration, timing changes, and exports for cloned commentary WAV audio, transcript, SRT subtitles, project JSON, and render manifest.

## Important production note

This repository implements the application workflow, UI, heuristic browser analysis, consent gate, script generation, provider contracts, and an optional local Coqui XTTS voice-cloning adapter. It does **not** commit model weights or a trained sports computer-vision model. The XTTS model is downloaded by the local Python environment on first use, and its licensing/allowed use must be reviewed before production deployment. Mixed video/audio export should still be connected through a renderer using the contracts in `src/lib/providerContracts.ts`.

The app deliberately blocks voice analysis unless the user confirms speaker permission.

## Requirements

- Node.js 20+
- npm 10+
- A modern browser with Web Audio, Canvas, and video decoding support

## Getting started

```bash
npm install
npm run dev
```

Open the local URL printed by Vite.

## Scripts

```bash
npm run dev      # start the Vite dev server
npm run dev:server # start the local voice clone API server
npm run voice:install # install optional Python XTTS dependencies
npm run build    # type-check and build production assets
npm run preview  # preview the production build
npm test         # run unit tests
```

## Workflow pages

1. **Home** - broadcast-themed landing page and capability overview.
2. **Upload** - video/audio upload, teams, players, competition, language, style, frequency, and consent confirmation.
3. **Voice Analysis** - in-browser acoustic profile with waveform and consent status.
4. **Processing** - video/sport analysis, event timeline, team colours, scoreboard/clock likelihood, momentum summary.
5. **Live Commentary Preview** - video preview, generated lines, waveform, and browser speech preview.
6. **Script Editor** - edit text, timings, emphasis, regenerate individual lines, add/delete lines.
7. **Export** - local XTTS cloned commentary WAV generation, transcript, SRT, project JSON, and render manifest downloads; video rendering is clearly marked as renderer-required.


## Optional local voice cloning model

The app now includes a consent-gated local voice cloning API that uses Coqui XTTS v2.

Install the optional Python dependencies:

```bash
npm run voice:install
```

Start the model API:

```bash
npm run dev:server
```

Then start the UI in another terminal:

```bash
npm run dev
```

After uploading a video and an approved voice sample, run analysis and open **Export**. The **Commentary audio only** card will call `/api/synthesize/voice-clone`, synthesize each edited line with the uploaded speaker sample, place each generated WAV segment on the requested timeline, and download a single commentary WAV.

Configuration:

```bash
VOICE_MODEL_NAME=tts_models/multilingual/multi-dataset/xtts_v2
VOICE_CLONE_DEVICE=cpu   # or cuda/mps when available
VOICE_CLONE_DRY_RUN=1    # smoke-test API without loading a model; creates silent WAV
```

The API enforces a consent receipt, request size limits, line limits, and temporary-file cleanup. Voice samples are written to OS temp storage for the duration of the request only.

## Extending to production AI

Recommended integration points:

- Replace `src/lib/videoAnalysis.ts` heuristics with a model-backed detector for balls, players, referees, goals, fouls, cards, jerseys, scoreboards, and OCR.
- Use the included local XTTS adapter for approved samples, or connect `VoiceSynthesisProvider` from `src/lib/providerContracts.ts` to another licensed voice synthesis provider that enforces speaker permission and permitted use.
- Connect `VideoRenderProvider` to FFmpeg/WASM, a desktop render process, or a cloud renderer for final MP4/MOV output.
- Store consent receipts and provider audit logs with project files.
- Add human review before publishing generated score, foul, card, substitution, and player-name claims.

## Safety and consent

This app is designed around user-provided permission for any cloned voice. It avoids hidden identity inference and treats visual detections as draft edit cues unless a production model verifies them.
