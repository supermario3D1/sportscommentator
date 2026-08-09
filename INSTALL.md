# Easy installation guide

This guide covers every operating system officially supported by AI Sports Commentator.

## Supported systems

| Operating system | Support | Installer |
|---|---|---|
| Fedora Linux 40 or newer, including Fedora 44 | Fully supported | `run.sh` / `setup.sh` |
| Ubuntu 22.04 or newer | Fully supported | `run.sh` / `setup.sh` |
| Debian 12 or newer | Supported | `run.sh` / `setup.sh` |
| Linux Mint 21 or newer and current Ubuntu derivatives | Supported | `run.sh` / `setup.sh` |
| Windows 10 or 11, 64-bit | Fully supported | `run.bat` / `setup.bat` |
| Other x86-64 Linux distributions | Manual installation | Instructions below |

macOS, ChromeOS, ARM Linux devices, and 32-bit operating systems do not currently have supported installers. The application is designed for x86-64 laptops and desktops.

You do **not** need an NVIDIA GPU, CUDA, ROCm, or Vulkan. CPU mode is installed by default and works with AMD, Intel, and NVIDIA graphics.

## Before installing

You need:

- a 64-bit computer;
- at least 16 GB RAM (32 GB recommended);
- at least 15 GB free disk space (20 GB recommended);
- an internet connection for the first installation and model download;
- administrator access to install Python, FFmpeg, and Ollama.

The first setup downloads several gigabytes. Depending on your connection, it may take 10–60 minutes. Do not close the terminal while files are downloading. If setup is interrupted, run the same launcher again; verified downloads are reused.

---

## Fedora Linux

### Option A: use Git

Open **Terminal** and paste these commands one at a time:

```bash
sudo dnf install -y git
git clone https://github.com/supermario3D1/sportscommentator.git
cd sportscommentator
bash run.sh
```

`run.sh` notices that this is the first launch, runs the complete setup, and then opens the application. Enter your password when Fedora asks for it.

For later launches:

```bash
cd sportscommentator
bash run.sh
```

Then open <http://localhost:7860> if the page does not open automatically.

### Option B: download a ZIP

1. Open <https://github.com/supermario3D1/sportscommentator>.
2. Select **Code → Download ZIP**.
3. Extract the ZIP.
4. Right-click inside the extracted folder and select **Open in Terminal**.
5. Run:

```bash
bash run.sh
```

### Fedora codec note

The automatic installer uses Fedora's `ffmpeg-free` package. If FFmpeg cannot decode a particular match video, install the full multimedia-enabled FFmpeg build from the repository you normally use, then rerun `bash run.sh`.

---

## Ubuntu, Debian, and Linux Mint

### Option A: use Git

Open **Terminal** and paste:

```bash
sudo apt update
sudo apt install -y git
git clone https://github.com/supermario3D1/sportscommentator.git
cd sportscommentator
bash run.sh
```

The first launch installs system packages, creates an isolated Python environment, installs Ollama, downloads models, and then starts the web interface.

For later launches:

```bash
cd sportscommentator
bash run.sh
```

Open <http://localhost:7860> if needed.

### Option B: download a ZIP

1. Open <https://github.com/supermario3D1/sportscommentator>.
2. Select **Code → Download ZIP** and extract it.
3. Open a terminal in the extracted folder.
4. Run `bash run.sh`.

---

## Windows 10 and Windows 11

### Easiest method

1. Open <https://github.com/supermario3D1/sportscommentator>.
2. Select **Code → Download ZIP**.
3. Right-click the downloaded ZIP and choose **Extract All**.
4. Open the extracted `sportscommentator` folder.
5. Double-click **`run.bat`**.

On the first launch, `run.bat` calls the installer automatically. The installer uses Windows Package Manager (`winget`) to install missing copies of:

- Python 3.12;
- FFmpeg;
- Ollama.

Approve any Windows installation prompts. When setup finishes, the local web interface starts. Open <http://localhost:7860> if it is not shown automatically.

For every later launch, double-click **`run.bat`** again.

### If Windows says `winget` is missing

Install **App Installer** from the Microsoft Store, restart the computer, and double-click `run.bat` again. Alternatively, install these manually:

1. Python 3.10–3.13 from <https://www.python.org/downloads/windows/>. During installation, enable **Add Python to PATH**.
2. Ollama from <https://ollama.com/download/windows>.
3. FFmpeg from <https://www.gyan.dev/ffmpeg/builds/> and add its `bin` folder to PATH.

Then double-click `run.bat`.

### If a newly installed command is not found

Close the Command Prompt window, open the project folder again, and double-click `run.bat`. Windows sometimes applies PATH changes only to new terminal windows.

### Windows command-line launch

From Command Prompt in the project folder:

```bat
run.bat
```

To process directly without the browser review step:

```bat
run.bat --process "C:\Videos\match.mp4" --no-review
```

---

## Other x86-64 Linux distributions

The application works when the distribution can provide Python 3.10+, Python virtual environments/development headers, FFmpeg/ffprobe, curl, Git, and a C/C++ build toolchain.

First install these packages with your distribution's package manager. Examples:

### Arch Linux / Manjaro

```bash
sudo pacman -S --needed python ffmpeg curl git base-devel pciutils
```

### openSUSE Tumbleweed / Leap

```bash
sudo zypper install python3 python3-devel ffmpeg curl git gcc gcc-c++ pciutils
```

Then, from the project folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip wheel setuptools
python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r requirements.txt
curl -fsSL https://ollama.com/install.sh | sh
python install_models.py
python -m config.hardware_detect
python -m app.main
```

If your distribution manages Ollama another way, install it from <https://ollama.com/download/linux> instead of using the curl command.

---

## Confirm that installation worked

A successful setup prints a hardware summary similar to:

```text
Selected:        CPU / CPUExecutionProvider
Disk free:       18.0 GiB
```

CPU selection is normal, including on AMD Radeon integrated graphics. The UI should be available at:

```text
http://localhost:7860
```

Test it with a short video first. Upload the video, leave the default settings selected, and press **START PROCESSING**.

## Optional voice cloning

Built-in Piper voices are installed automatically and use the fewest resources. OpenVoice V2 cloning is optional.

Linux:

```bash
source .venv/bin/activate
python install_models.py --skip-yolo --skip-llm --skip-voices --openvoice
```

Windows:

```bat
.venv\Scripts\python.exe install_models.py --skip-yolo --skip-llm --skip-voices --openvoice
```

In the UI, upload a clean voice sample and choose **Quality — clone uploaded sample with OpenVoice**. Voice cloning is automatically disabled on systems with 16 GB RAM or less.

## Updating

If installed with Git:

```bash
cd sportscommentator
git pull
bash run.sh
```

On Windows, use `git pull` in Command Prompt and then run `run.bat`. If installed from a ZIP, download and extract a new ZIP. Preserve `outputs/` if you want to keep exported videos.

After an update, rerun `setup.sh` or `setup.bat` only if `run.sh`/`run.bat` reports a missing dependency.

## Common installation problems

### Setup was interrupted

Run `bash run.sh` or `run.bat` again. Python packages are reused, completed model files are checksum-verified, and only missing files are downloaded.

### `No space left on device`

Free at least 15–20 GB. Remove partial files ending in `.part` under `models/` only if rerunning setup does not recover.

### Ollama cannot connect

Linux:

```bash
ollama serve
```

Leave that terminal open and launch the app from another terminal.

On Windows, start **Ollama** from the Start menu, then run `run.bat` again.

### FFmpeg is not found

Run `ffmpeg -version` in a new terminal. If it is not found, reinstall FFmpeg and ensure its executable directory is on PATH.

### Python is externally managed

Do not install packages into the system Python. Use the supplied `run.sh`, which creates `.venv`. If a failed setup left an incomplete environment, delete only the `.venv` folder and run `bash run.sh` again.

### A model download fails

Keep the completed files and rerun:

Linux:

```bash
source .venv/bin/activate
python install_models.py
```

Windows:

```bat
.venv\Scripts\python.exe install_models.py
```

### Start over without deleting videos

Delete `.venv` and `models`, then launch again. Do not delete `outputs` if it contains final videos. Ollama stores its language models separately; remove them only if necessary with:

```bash
ollama rm phi3:mini
ollama rm tinyllama
```
