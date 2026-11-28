"""Make a short vertical product video: photos + big text + ElevenLabs voiceover. Needs ffmpeg."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

W, H, FPS = 1080, 1920, 30
VOICE = os.environ.get("VOICEOVER_VOICE", "TX3LPaxmHKxFdv7VOQHJ")   # "Liam", energetic social-media voice
FONT_CANDIDATES = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                   "/System/Library/Fonts/Supplemental/Arial Bold.ttf"]


def font():
    for f in FONT_CANDIDATES:
        if Path(f).exists():
            return f
    raise RuntimeError("No bold font found for the video text.")


def voiceover(text, out):
    key = re.sub(r"\s+", "", os.environ.get("ELEVENLABS_API_KEY", ""))
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY is missing.")
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE}?output_format=mp3_44100_128",
        data=json.dumps({"text": text, "model_id": "eleven_multilingual_v2",
                         "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "style": 0.3}}).encode(),
        headers={"xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/mpeg"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            Path(out).write_bytes(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"ElevenLabs said {e.code}: {e.read()[:200].decode(errors='replace')}")


def duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    return float(r.stdout.strip())


def wrap(text, width=16):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    lines.append(cur)
    return "\n".join(lines[:3])


def make_video(images, lines, voice_text, out_path):
    """images: 2-4 photo URLs or paths. lines: on-screen text, one per photo. Returns out_path."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg isn't installed.")
    tmp = Path(tempfile.mkdtemp(prefix="theo-video-"))
    imgs = []
    for i, src in enumerate(images[:4]):
        dst = tmp / f"img{i}.jpg"
        if str(src).startswith("http"):
            subprocess.run(["curl", "-sS", "-f", "-L", "--max-time", "60", "-o", str(dst), src], check=True)
        else:
            shutil.copy(src, dst)
        imgs.append(dst)
    if not imgs:
        raise RuntimeError("No photos for the video.")
    lines = (lines + [""] * len(imgs))[:len(imgs)]

    audio = tmp / "voice.mp3"
    voiceover(voice_text, audio)
    total = duration(audio) + 0.8
    seg = max(2.0, total / len(imgs))
    f = font()

    parts = []
    for i, (img, line) in enumerate(zip(imgs, lines)):
        txt = tmp / f"t{i}.txt"
        txt.write_text(wrap(line.upper()))
        frames = int(seg * FPS)
        zoom = "min(zoom+0.0009,1.12)" if i % 2 == 0 else "if(eq(on,0),1.12,max(zoom-0.0009,1.0))"
        vf = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},gblur=sigma=30,eq=brightness=-0.12[bg];"
              f"[0:v]scale=1000:1000:force_original_aspect_ratio=decrease,pad=1000:1000:(ow-iw)/2:(oh-ih)/2:color=white@0,"
              f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1000x1000:fps={FPS}[fg];"
              f"[bg][fg]overlay=(W-w)/2:(H-h)/2+40:shortest=1,"
              f"drawtext=fontfile='{f}':textfile='{txt}':fontsize=88:fontcolor=white:line_spacing=14:"
              f"box=1:boxcolor=black@0.55:boxborderw=28:x=(w-text_w)/2:y=230,format=yuv420p")
        part = tmp / f"p{i}.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", str(FPS), "-t", f"{seg:.2f}",
                        "-i", str(img), "-filter_complex", vf, "-frames:v", str(frames), "-r", str(FPS),
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", str(part)], check=True)
        parts.append(part)

    lst = tmp / "list.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(audio),
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", str(out_path)], check=True)
    shutil.rmtree(tmp, ignore_errors=True)
    return out_path
