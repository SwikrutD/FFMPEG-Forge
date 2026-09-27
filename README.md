# FFMPEG-Forge

A desktop app that drives ffmpeg through a window instead of a command line.
Drop files in, pick a task, press Render.

## What it does

The sidebar has two sections, Video and Photos. Switching section swaps the task
list and re-checks the queue: anything that doesn't belong to the active section
is dimmed and marked, and the header shows how many files apply. Video and photo
files can sit in the queue together without getting in each other's way.

### Video

| Task | What it covers |
|---|---|
| **Convert** | Container and codec changes, resolution, frame rate, audio, rotate/flip, web fast-start. "Copy" re-muxes without re-encoding, which is instant and lossless. |
| **Compress** | Either a quality target (CRF) or a file size target in MB. The size target runs a real two-pass encode. The resolution cap never upscales a smaller source. |
| **Extract audio** | MP3, AAC, WAV, FLAC, Opus, or a straight stream copy. Optional EBU R128 loudness normalization, channel and sample-rate control. |
| **Trim** | Fast mode stream-copies (cuts on the nearest keyframe, no quality loss). Precise mode re-encodes for an exact cut. |
| **Resize** | Scale, letterbox-pad, or crop-to-fill, from presets or a custom width. |
| **Make GIF** | Two-stage palette generation, so colors stay clean instead of the usual banded mess. Three dither modes. |
| **Frames** | Stills every N seconds, a fixed number spread across the clip, or one frame at a timestamp. PNG or JPEG. |

### Photos

| Task | What it covers |
|---|---|
| **Convert** | JPEG, PNG, WebP, TIFF, BMP and AVIF where the build supports it. Quality slider, lossless WebP, an optional long-edge cap that only ever shrinks, and a switch to strip EXIF and location data. |
| **Resize** | By long edge, width, height, a percentage, or an exact box with pad, crop or stretch. Four resampling filters, and an "never enlarge" guard that is on by default. |
| **Crop** | To a ratio (square, 4:5, 2:3, 9:16, 3:2, 4:3, 16:9) anchored centre, top, bottom, left or right, or to an exact box you set yourself. |
| **Adjust** | Brightness, contrast, saturation, gamma, three sharpening strengths, black and white, plus rotate and flip. |

Everything runs as a batch: drop in twenty files and they process one after another.

## Size estimates and ETA

Before you press Render, the header above the queue shows a predicted output
size for the whole batch. For a stream copy or a file-size target that number is
exact. For a CRF encode it is a bitrate model, so treat it as a ballpark: dense
or noisy footage lands above it, flat footage below.

Once a render starts, each row shows percentage, encode speed as a multiple of
real time, time remaining, and a projected final size taken from the bytes
actually written so far. That projection converges on the real number well
before the end. The footer carries an overall estimate for the whole queue.

Photo jobs don't show a size estimate. A single image finishes too fast for the
number to be useful, and guessing at JPEG output size would mislead more than
it helps.

## Setup on Windows

1. **Double-click `FFMPEG-Forge.bat`.** The first run creates a private `.venv`
   folder next to the app and installs PySide6 into it. That takes a minute or
   two and only happens once. It does not touch your system Python.

2. **If you don't have ffmpeg**, open Settings and press **Install ffmpeg**. It
   downloads an official release build, checks it against the publisher's
   SHA-256, and puts it in an `ffmpeg` folder beside the app. Nothing is
   installed system-wide and PATH is left alone.

3. After the first run, use **`FFMPEG-Forge (no console).vbs`** for a launch
   with no console window at all. Right-click it, Send to, Desktop (create
   shortcut), and you have a normal desktop icon.

If Python isn't installed, the batch file will tell you and print the winget
command for it.

## Keeping ffmpeg current

FFMPEG-Forge checks for a newer ffmpeg release once a day in the background.
When one exists, a red dot appears on the Settings button and the header pill
reads "update ready". Settings then tells you which version you have and which
is current, and offers a one-press update. When nothing is pending it says it
is up to date.

How the update behaves depends on where your ffmpeg came from:

- **Installed by FFMPEG-Forge** (the `ffmpeg` folder beside the app): the update
  replaces it. The old copy is held aside until the new build is confirmed to
  run, and is restored automatically if it isn't.
- **Installed elsewhere** (winget, Chocolatey, a folder on PATH): FFMPEG-Forge
  will not touch it. It offers to install its own copy alongside the app and
  switch to that, leaving your system install exactly as it is.

Every download is verified against the publisher's published SHA-256 before
anything is replaced. A mismatch aborts and changes nothing. Updates are
blocked while a render is running.

On macOS and Linux the panel still reports your version against the current
release, but installing is left to your package manager, since builds there
are distribution-specific.

## Setup on macOS or Linux

```
./run.sh
```

Same idea: creates `.venv` on first run, then launches.

## Notes

- **Your GPU.** The 1070 Ti supports NVENC, so H.264 (NVENC) and H.265 (NVENC)
  appear in the encoder lists whenever your ffmpeg build includes them. NVENC is
  several times faster than x264 and noticeably less efficient per bit, so it's
  the right pick for long files and the wrong pick when you care most about size.
  The header pill tells you whether it was detected.
- **The command preview** at the bottom shows the exact ffmpeg invocation before
  you run it, and Copy puts it on the clipboard.
- **Output location** defaults to the same folder as the source. Set a fixed
  folder in the field or in Settings. FFMPEG-Forge never overwrites: a name
  collision becomes `name (1).mp4`.
- **Stop** kills the running encode and deletes the empty output.
- Settings live in the registry under `HKCU\Software\FFMPEG-Forge` on Windows.

## Optional: a single .exe

```
.venv\Scripts\pip install pyinstaller
.venv\Scripts\pyinstaller --noconsole --onefile --name FFMPEG-Forge ffmpeg_forge.py
```

The result lands in `dist\FFMPEG-Forge.exe`. It starts a little slower than the
venv launcher and is about 60 MB, which is why it isn't the default.
