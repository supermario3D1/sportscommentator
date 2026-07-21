# Local voice cloning adapter

This optional adapter runs Coqui XTTS v2 from the Node API server. It is consent-gated: the API rejects synthesis unless the app sends a consent receipt confirming that the speaker gave permission.

## Install

```bash
python3 -m venv .venv-voice
source .venv-voice/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r server/voice/requirements.txt
```

Then run the API server:

```bash
npm run dev:server
```

In another terminal run the UI:

```bash
npm run dev
```

The first synthesis request downloads the configured model. By default:

```text
tts_models/multilingual/multi-dataset/xtts_v2
```

Override with:

```bash
VOICE_MODEL_NAME="tts_models/multilingual/multi-dataset/xtts_v2" npm run dev:server
VOICE_CLONE_DEVICE=cuda npm run dev:server
```

For API smoke tests without loading a model, run:

```bash
VOICE_CLONE_DRY_RUN=1 npm run dev:server
```

Dry run creates a silent WAV timeline and does not clone a voice.

## Notes

- Keep voice samples and generated audio private unless the speaker explicitly permits distribution.
- Review the generated script before synthesis.
- XTTS model licensing and commercial-use permissions are your responsibility.
