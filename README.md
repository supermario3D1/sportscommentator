# AI Sports Commentator — CPU-first local batch application

A complete local application that samples a match video, detects players and the ball, derives conservative event candidates with rules, writes short local-LLM commentary, synthesizes speech, ducks the source audio, and muxes a final MP4. It is intentionally **offline batch processing**, not a live system.

- No cloud API and no CUDA requirement.
- Streams one frame/event/audio chunk at a time; it never loads a match into RAM.
- Every stage writes to disk and has an atomic JSON checkpoint.
- Models run sequentially and are explicitly released at stage boundaries.
- Pause and resume from the UI or terminal.
- The video stream is copied during export; only audio is encoded.

> Event calls are geometry-based candidates from sparse 1 FPS footage. They are not VAR-grade decisions. Review the event table and commentary before synthesis when accuracy matters.

## Target hardware

Primary profile: Ryzen 7 7840U, Radeon 780M iGPU, 32 GB RAM, 15–20 GB free, Fedora/Ubuntu/Windows. Low-resource mode turns on automatically at **16 GB RAM or less**: batch size 50, 320 px YOLO input, TinyLlama, and built-in Piper speech. An AMD/NVIDIA GPU is optional.

A standard `onnxruntime` wheel is CPU-only. The app uses `ROCMExecutionProvider`, `VulkanExecutionProvider`, or `CUDAExecutionProvider` only when the installed ONNX Runtime build actually exposes it; a driver alone is not treated as acceleration. Provider priority is ROCm → Vulkan → CUDA → CPU. Radeon 780M ROCm support varies by OS/driver, so CPU is the safe default.

## Easy installation

See **[INSTALL.md](INSTALL.md)** for beginner-friendly, step-by-step instructions, ZIP-download instructions, manual recovery, updating, and troubleshooting for every supported operating system.

### Supported operating systems

- Fedora Linux 40+, including Fedora 44
- Ubuntu 22.04+
- Debian 12+
- Linux Mint 21+ and current Ubuntu derivatives
- Windows 10/11 64-bit
- Other x86-64 Linux distributions with manual dependency installation

No dedicated GPU, CUDA, ROCm, or Vulkan setup is required. CPU mode is the default.

### Fastest first launch

**Fedora, Ubuntu, Debian, or Linux Mint:**

```bash
git clone https://github.com/supermario3D1/sportscommentator.git
cd sportscommentator
bash run.sh
```

**Windows 10/11:** download the repository ZIP, extract it, and double-click **`run.bat`**.

The launchers detect a first run and automatically start setup. Setup installs or checks Python, FFmpeg, and Ollama; creates `.venv`; installs CPU-only dependencies; exports YOLOv8n ONNX; downloads four Piper voices; pulls Phi-3 Mini and TinyLlama; verifies model files; and starts the application. The first run downloads several gigabytes and can take 10–60 minutes. Running the same launcher again safely continues an interrupted setup.

### Models only / interrupted download

Downloads are resumable by rerunning the installer. Existing files are SHA-256 checked; Hugging Face LFS hashes are used when exposed by the server. The ONNX graph and Piper JSON/model pairs receive structural validation too.

```bash
source .venv/bin/activate
python install_models.py
```

The default installs four voices because all four appear in the UI. For only the two defaults:

```bash
python install_models.py --default-voices-only
```

Optional OpenVoice V2 is deliberately separate because it adds roughly 400 MB of checkpoints plus dependencies:

```bash
python install_models.py --skip-yolo --skip-llm --skip-voices --openvoice
```

Select **Quality — clone uploaded sample with OpenVoice** in the UI. At 16 GB RAM or less, cloning is automatically disabled. If OpenVoice is unavailable or fails, valid Piper clips are retained and used.

## Use the web UI

Run `./run.sh` (`run.bat` on Windows), then open <http://localhost:7860>.

1. Upload MP4/MKV/AVI/MOV and optionally a clean WAV/MP3/FLAC voice sample.
2. Select sport, style, built-in voice, commentary frequency, and source-audio duck level.
3. Press **START PROCESSING**.
4. With review enabled (default), the pipeline unloads Ollama and pauses before TTS. Edit the Commentary column, press **SAVE COMMENTARY EDITS**, then **RESUME**.
5. Download the final MP4. Use cleanup only after checking the output.

**PAUSE** writes a control file. The active stage stops after its current frame/event/60-second audio chunk, leaving valid partial files. **RESUME** reuses them. The web upload is persisted under `uploads/`; a hard link avoids a second physical copy when the filesystem permits it.

Adaptive options are under *Low-resource and workflow options*:

- one frame every 2 seconds instead of every second;
- TinyLlama instead of Phi-3 Mini;
- key events only;
- built-in Piper instead of voice cloning.

## Terminal use

Run a full job without the browser and without the commentary review gate:

```bash
./run.sh --process /path/to/match.mp4 --no-review
```

Useful variants:

```bash
./run.sh --process match.mkv --half-fps --tinyllama --key-events-only --no-review
./run.sh --process match.mp4 --voice-sample voice.wav --clone-voice
./run.sh --resume
```

If a checkpoint exists, terminal mode asks `Previous progress found. Resume from [stage]?`. On the review gate, edit `temp/commentary/commentary.json`, run `./run.sh --resume`, and approve synthesis when prompted.

## Sequential disk-backed pipeline

| Stage | In RAM | Durable output |
|---|---|---|
| Frame extraction | one OpenCV frame | `temp/frames/frame_00001.jpg`, `frames_manifest.json` |
| YOLOv8n ONNX | one JPEG + nano session | `temp/detections/det_00001.json` |
| ByteTrack-lite | current detections + short tracks | `temp/tracks/track_00001.json` |
| Rule events | one detection JSON + 8 samples | `temp/events.json` |
| Ollama | one event / one local LLM | `temp/commentary/commentary.json` after every line |
| Piper / OpenVoice | one line/clip; models sequential | `temp/audio_clips/*.wav`, `manifest.json` |
| Audio mixing | 60 seconds of PCM | chunk WAVs, then `temp/final_audio.wav` |
| Export | FFmpeg streams | `outputs/match_with_commentary_*.mp4` |

The tracker uses ByteTrack's high-confidence association followed by a second low-confidence recovery pass, implemented in small NumPy-free geometry to avoid scipy and a separate model. The event stage uses no neural network. Rules include likely goals (2 of 3 signals required), shots, foul candidates, corners, counters, sustained pressure, and sharp tempo changes. Frequency adjusts the confidence floor and cooldowns suppress repeats.

Checkpoint examples are written both to `checkpoints/pipeline_checkpoint.json` and one file per stage:

```json
{
  "stage": "frame_extraction",
  "status": "complete",
  "progress": 100,
  "timestamp": "2026-08-06T12:00:00+00:00",
  "data_path": "temp/frames"
}
```

Writes use a temporary file, `fsync`, and atomic rename. Partial frame, detection, commentary, voice, and audio-chunk work is reusable after power loss.

## Resource protection

- RAM is logged at every stage. At 70% use, Python collects garbage, asks glibc to trim, and pauses briefly before one cautious batch.
- Linux `hwmon`/thermal zones are checked. At 90 °C, work pauses for 30 seconds.
- Battery operation triggers a warning and halves the physical-core thread count.
- `OMP_NUM_THREADS`, `MKL_NUM_THREADS`, and `OPENBLAS_NUM_THREADS` use physical cores, not SMT threads.
- Old generated temp files are removed after 24 hours at startup. User uploads, models, checkpoints, and outputs are never part of that automatic cleanup.
- At least 3 GB must be free to start; below 15 GB produces a warning. Long/high-bitrate sources may need more.

## Expected 90-minute CPU times

| Stage | Ryzen 7 7840U / 32 GB |
|---|---:|
| Frame extraction | 3–5 min |
| YOLOv8n CPU | 15–30 min |
| Tracking | 2–5 min |
| Rules | 1–2 min |
| Phi-3 commentary | 3–10 min |
| Piper | 2–5 min |
| OpenVoice if enabled | 10–20 min |
| Audio mix / mux | 3–5 min |
| **Typical total** | **30–80 min** |

A Ryzen 5 / recent i5 with 16 GB can take 60–150 minutes. Codec, thermals, event count, and Ollama version can shift these estimates. Slow processing does not lower final video quality because the video is never re-encoded.

## Testing

After setup:

```bash
source .venv/bin/activate
python -m pip install pytest
pytest -q
python -m compileall app config pipeline utils
```

The tests cover atomic JSON, track identity, and synthetic two-of-three goal logic. For an end-to-end smoke test, use a short clip and `--no-review`.

## Troubleshooting

- **Model missing:** run `python install_models.py`; selected voice files must exist in `models/piper/`.
- **Ollama unavailable:** start `ollama serve`. The pipeline tries the configured model, then TinyLlama, then clearly records a deterministic emergency line so an overnight export is not lost.
- **CPU selected despite AMD GPU:** run `python -m config.hardware_detect`. The relevant ONNX execution provider must appear under `ONNX providers`; `vulkaninfo`/`rocminfo` alone is not enough.
- **MP4 mux failure:** FFmpeg cannot copy every source codec into MP4. Remux/transcode the source to H.264/H.265 MP4 first; the application intentionally does not silently re-encode video.
- **No source audio:** a duration-matched stereo silence track is generated and commentary is still exported.
- **False events:** lower Commentary Frequency, enable key-events-only, or edit/remove commentary during review. Sparse generic vision cannot know scoreboards or player identities.
- **Disk fills:** pause, free space, and resume. Cleanup deletes only generated `temp/` data after a successful export.

## Project layout

```text
app/                    main.py, ui.py
pipeline/               extractor, ONNX detector, tracker, rule events,
                        Ollama, Piper/OpenVoice, mixer, exporter, manager
config/                 settings and hardware/provider detection
utils/                  memory/thermal, disk, and logging helpers
checkpoints/             atomic resume state
temp/                    disk-backed intermediate artifacts
uploads/                 persisted UI inputs
outputs/                 final MP4 files
models/                  YOLO/Piper/OpenVoice files (Ollama uses its own store)
install_models.py        verified model installer
INSTALL.md               step-by-step setup for every supported OS
setup.sh / setup.bat     one-click installers
run.sh / run.bat         first-run setup and later launchers
```

All media, models, outputs, and checkpoints are git-ignored; directory markers are retained.
