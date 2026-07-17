# Running Good-Badminton on Windows

This folder already contains the project code (cloned from
[qwpyyx/Good-Badminton](https://github.com/qwpyyx/Good-Badminton)) plus two
fixes for Windows:

- `app.py` now finds the venv interpreter as `.venv\Scripts\python.exe`
  (the original assumed a Linux `.venv/bin/python3`).
- `app.py` now locates `ffmpeg` from your PATH instead of a hardcoded Linux path.

## Fastest path: double-click `run.bat`

`run.bat` does everything for you:

1. Creates a virtual environment in `.venv\`
2. Installs all Python dependencies (~1–2 GB the first time)
3. Downloads the two model weights into `weights\`
4. Starts the Flask server and opens http://127.0.0.1:5050 in your browser

The first run takes a while (large downloads). Later runs are fast — it skips
steps that are already done. To stop the server, close the console window or
press `Ctrl+C` in it.

## Prerequisites

- **Python 3.8+** — https://www.python.org/downloads/ (tick "Add to PATH").
- **FFmpeg** — needed so output video plays in the browser. Install with:
  ```
  winget install Gyan.FFmpeg
  ```
  then open a new terminal so it's on PATH.

## Manual steps (if you prefer)

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

mkdir weights
curl -L -o weights\yolo11s-ball.pt https://github.com/yo-WASSUP/Good-Badminton/releases/latest/download/yolo11s-ball.pt
curl -L -o weights\yolox_nano_8xb8-300e_humanart-40f6f0d0.onnx https://github.com/yo-WASSUP/Good-Badminton/releases/latest/download/yolox_nano_8xb8-300e_humanart-40f6f0d0.onnx

python app.py
```

Then open http://127.0.0.1:5050

## Using it

1. Drag a badminton video onto the page (or pick an existing one).
2. Click **自动检测球场** (auto-detect court) — or **手动修正** to mark the
   four court corners yourself.
3. Click **开始分析** (start analysis) and watch the progress bar.
4. View the annotated video, heatmaps, and scatter plots; optionally generate
   rally highlight clips.

## Notes

- `yolo11n-pose.pt` and the RTMPose ONNX models download automatically on first
  analysis, so the initial analysis run is slower.
- This is CPU-only by default (per `requirements.txt`). A 20-second 4K clip can
  take ~10–20 minutes on CPU. A CUDA GPU is much faster but needs a matching
  PyTorch build.
- The fork's added code is licensed CC BY-NC 4.0 (non-commercial).
