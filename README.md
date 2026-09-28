# ClipMania

**Multi-platform video clipping pipeline** — capture, clip, transform, and export video from Twitch, YouTube, Kick, TikTok, and X.

Powered by [Streamlink](https://streamlink.github.io/), [yt-dlp](https://github.com/yt-dlp/yt-dlp), and [FFmpeg](https://ffmpeg.org/).

---

## Features

- **Live stream clipping** — rolling HLS buffer via Streamlink + FFmpeg with instant "Clip Now" extraction
- **VOD section clipping** — frame-accurate timestamp extraction from YouTube, Twitch VODs, Kick VODs
- **Full short-form download** — grab complete TikTok and X video posts
- **Smart layout transforms** — 4 modes for converting 16:9 → 9:16 vertical content
- **EBU R128 audio normalization** — two-pass precision loudness targeting (-14 LUFS)
- **Platform-aware export** — validates output against TikTok, YouTube Shorts, Kick, Twitch, and X specs

## Prerequisites

Install these tools and ensure they are on your `PATH`:

| Tool | Install |
|------|---------|
| **FFmpeg** (+ ffprobe) | [ffmpeg.org/download](https://ffmpeg.org/download.html) |
| **yt-dlp** | `pip install yt-dlp` |
| **Streamlink** (for live) | `pip install streamlink` |

## Installation

```bash
# Clone and install
git clone <repo-url> clipmania
cd clipmania
pip install -e .

# Or with dev dependencies
pip install -e ".[dev]"
```

## Quick Start

### Clip a VOD section

```bash
# Clip 45 seconds from a YouTube video, convert to vertical TikTok format
clipmania clip "https://youtube.com/watch?v=dQw4w9WgXcQ" \
  --start 1:30 --end 2:15 \
  --layout vertical-blur \
  --platform tiktok
```

### Download a TikTok / X post

```bash
clipmania download "https://tiktok.com/@user/video/1234567890"
```

### Live stream clipping

```bash
# 1. Start the live buffer
clipmania live-start "https://twitch.tv/shroud"

# 2. When something cool happens, grab the last 60 seconds
clipmania clip-now --seconds 60 --layout vertical-blur

# 3. Stop the buffer when done
clipmania live-stop
```

### Convert an existing file

```bash
clipmania convert raw_gameplay.mp4 --layout vertical-blur --platform youtube_shorts
```

## CLI Reference

```
clipmania [OPTIONS] COMMAND [ARGS]...

Options:
  --version            Show version
  --config PATH        Path to clipmania.toml
  -o, --output-dir     Output directory
  -v, --verbose        Show verbose output

Commands:
  clip        Clip a section from a VOD or saved video
  download    Download a complete video
  live-start  Start a rolling live-stream buffer
  clip-now    Extract last N seconds from live buffer
  live-stop   Stop the live buffer
  convert     Convert an existing video file
```

## Layout Modes

| Layout | Description |
|--------|-------------|
| `horizontal` | Pass-through / scale to 1920×1080 |
| `vertical-blur` | Blurred background fill, 1080×1920 (recommended for shorts) |
| `vertical-stacked` | Facecam top + gameplay bottom |
| `vertical-crop` | Simple centre crop to 9:16 |

### vertical-blur
Centres the 16:9 video and fills the top/bottom with a blurred, scaled version:

```
┌──────────────┐
│  ▓▓▓▓▓▓▓▓▓▓  │ ← blurred fill
│ ┌──────────┐ │
│ │  VIDEO   │ │ ← original, centred
│ └──────────┘ │
│  ▓▓▓▓▓▓▓▓▓▓  │ ← blurred fill
└──────────────┘
```

### vertical-stacked
Crops facecam and gameplay into two panels:

```
┌──────────────┐
│   FACECAM    │ ← cropped from source
├──────────────┤
│              │
│   GAMEPLAY   │ ← cropped from source
│              │
└──────────────┘
```

Configure crop regions with `--cam-crop` and `--game-crop` (format: `w:h:x:y`).

## Platform Target Specs

| Platform | Aspect | Resolution | Max Duration | Audio |
|----------|--------|-----------|-------------|-------|
| TikTok | 9:16 | 1080×1920 | 60s | -14 LUFS |
| YouTube Shorts | 9:16 | 1080×1920 | 60s | -14 LUFS |
| Kick Clips | 16:9 / 9:16 | 1080p | 60s | -14 LUFS |
| Twitch Highlights | 16:9 | 1920×1080 | Unlimited | -14 LUFS |
| X (Twitter) | 16:9 / 9:16 | 1080p | 140s | -14 LUFS |

## Configuration

Copy `clipmania.toml` to your working directory and customise:

```toml
[output]
dir = "./output"

[defaults]
layout = "vertical-blur"

[encoding]
crf = 20
preset = "fast"

[audio]
target_lufs = -14
lra = 11
true_peak = -1.5
```

Config is loaded from (first match wins):
1. `--config` CLI flag
2. `CLIPMANIA_CONFIG` environment variable
3. `./clipmania.toml` in CWD
4. Built-in defaults

## Architecture

```
                  ┌─────────────────────────────────────┐
                  │           INGESTION STAGE            │
                  └─────────────────────────────────────┘
                    │                                   │
         [Live Streams]                        [Saved Videos / VODs]
     Twitch / Kick / YouTube             YouTube / Twitch / Kick / TikTok / X
                    │                                   │
                    ▼                                   ▼
           Streamlink Engine                      yt-dlp Engine
      (Rolling HLS Buffer)               (Timestamp Section Extract)
                    │                                   │
                    └───────────────┬───────────────────┘
                                    ▼
                  ┌─────────────────────────────────────┐
                  │       PROCESSING & ENCODING         │
                  │              (FFmpeg)                │
                  ├─────────────────────────────────────┤
                  │ • Layout transform (4 modes)        │
                  │ • Two-pass EBU R128 normalization    │
                  │ • Platform spec validation          │
                  └─────────────────────────────────────┘
                                    ▼
                  ┌─────────────────────────────────────┐
                  │        DISTRIBUTION & EXPORT        │
                  ├─────────────────────────────────────┤
                  │ TikTok / Shorts / Kick / Twitch / X │
                  └─────────────────────────────────────┘
```

## Development

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v
```

## License

MIT
