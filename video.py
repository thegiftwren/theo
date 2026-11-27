"""Theo/Owen video engine v2: fast, scroll-stopping vertical videos with a lot of motion.

What it makes (1080x1920, 30 fps, 10-15 s):
  - a new shot every ~0.8 s: punch-in zooms, push-outs, pans and slide-ins (no slow "vibrating" slideshow)
  - a shaking hook in big condensed caps on frame one
  - word-by-word kinetic captions timed exactly to the ElevenLabs voiceover (pop-in, one accent color)
  - an end card with the price and "link in bio"
Needs ffmpeg and an ElevenLabs key. Fonts: Anton + Instrument Serif Italic (free, OFL), in ./fonts.
"""
import base64
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

W, H, FPS = 1080, 1920, 30
API_KEY = ""   # set by the app, so the key never goes into the environment that other programs inherit
VOICE = os.environ.get("VOICEOVER_VOICE", "TX3LPaxmHKxFdv7VOQHJ")   # "Liam", energetic social-media voice
HERE = Path(__file__).resolve().parent
FONT_BOLD = HERE / "fonts" / "Anton-Regular.ttf"
FONT_ITALIC = HERE / "fonts" / "InstrumentSerif-Italic.ttf"
ACCENT = "0xF2B33D"   # one accent color (gold)
SHOT = 0.8            # seconds per shot: fast cuts


# ------------------------------------------------------------------ voice with word timings
def voiceover(text, out_mp3):
    """Returns a list of (word, start, end) and writes the mp3."""
    key = re.sub(r"\s+", "", API_KEY or os.environ.get("ELEVENLABS_API_KEY", ""))
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY is missing.")
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE}/with-timestamps?output_format=mp3_44100_128",
        data=json.dumps({"text": text, "model_id": "eleven_multilingual_v2",
                         "voice_settings": {"stability": 0.38, "similarity_boost": 0.8, "style": 0.45}}).encode(),
        headers={"xi-api-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"ElevenLabs said {e.code}: {e.read()[:200].decode(errors='replace')}")
    Path(out_mp3).write_bytes(base64.b64decode(data["audio_base64"]))
    al = data.get("alignment") or data.get("normalized_alignment") or {}
    chars = al.get("characters", [])
    starts = al.get("character_start_times_seconds", [])
    ends = al.get("character_end_times_seconds", [])
    words, cur, s0, e0 = [], "", None, None
    for c, s, e in zip(chars, starts, ends):
        if c.isspace():
            if cur:
                words.append((cur, s0, e0))
            cur, s0 = "", None
            continue
        if s0 is None:
            s0 = s
        cur += c
        e0 = e
    if cur:
        words.append((cur, s0, e0))
    return words


def duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    return float(r.stdout.strip())


# ------------------------------------------------------------------ shots
MOVES = ["punch", "slide_l", "push", "slide_r", "pan", "punch_fast", "slide_up"]


def shot(img, idx, frames, out, square):
    """One fast shot. square=True: product photo on a blurred background. False: full-bleed scene."""
    move = MOVES[idx % len(MOVES)]
    d = frames
    ease = f"(1-pow(1-on/{d},3))"   # fast at the start, so every cut lands with energy
    z = {"punch": f"1+0.28*{ease}", "punch_fast": f"1+0.4*{ease}", "push": f"1.32-0.3*{ease}",
         "slide_l": "1.12", "slide_r": "1.12", "pan": "1.18", "slide_up": "1.1"}[move]
    x = {"pan": f"(iw-iw/zoom)*{ease}"}.get(move, "(iw-iw/zoom)/2")
    y = "(ih-ih/zoom)/2"
    if square:
        sz = 900
        fg = (f"[0:v]scale=2000:2000:force_original_aspect_ratio=decrease,pad=2000:2000:(ow-iw)/2:(oh-ih)/2:color=white,"
              f"zoompan=z='{z}':x='{x}':y='{y}':d={d}:s={sz}x{sz}:fps={FPS}[fg]")
        bg = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},gblur=sigma=28,eq=brightness=-0.18,"
              f"zoompan=z='1.05+0.05*on/{d}':d={d}:s={W}x{H}:fps={FPS}[bg]")
        k = "max(0,1-n/5)"   # slide in during the first 5 frames
        ox = {"slide_l": f"(W-w)/2-W*{k}", "slide_r": f"(W-w)/2+W*{k}"}.get(move, "(W-w)/2")
        oy = {"slide_up": f"(H-h)/2+100+H*{k}"}.get(move, "(H-h)/2+100")
        fc = f"{fg};{bg};[bg][fg]overlay=x='{ox}':y='{oy}':eval=frame,format=yuv420p"
    else:
        fc = (f"[0:v]scale=2160:3840:force_original_aspect_ratio=increase,crop=2160:3840,"
              f"zoompan=z='{z}':x='{x}':y='{y}':d={d}:s={W}x{H}:fps={FPS},eq=saturation=1.1,format=yuv420p")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(img), "-filter_complex", fc,
                    "-frames:v", str(d), "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(out)],
                   check=True)


# ------------------------------------------------------------------ text helpers
def groups(words, max_words=2, max_chars=14):
    out, cur = [], []
    for w in words:
        cur.append(w)
        text = " ".join(x[0] for x in cur)
        if len(cur) >= max_words or len(text) >= max_chars or re.search(r"[.!?,]$", w[0]):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return [(" ".join(x[0] for x in g).upper().strip(".,"), g[0][1], g[-1][2]) for g in out]


def wrap(text, width=12):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    lines.append(cur)
    return "\n".join(lines[:3])


def make_video(images, lines, voice_text, out_path, scene_count=0):
    """images: AI scenes first (count them in scene_count), then product photos. lines: [hook, fix, price/CTA]."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg isn't installed.")
    tmp = Path(tempfile.mkdtemp(prefix="theo-video-"))
    try:
        imgs = []
        for i, src in enumerate(images[:6]):
            dst = tmp / f"img{i}.jpg"
            if str(src).startswith("http"):
                subprocess.run(["curl", "-sS", "-f", "-L", "--max-time", "60", "-o", str(dst), src], check=True)
            else:
                shutil.copy(src, dst)
            imgs.append(dst)
        if not imgs:
            raise RuntimeError("No photos for the video.")
        lines = [str(x) for x in (lines or [])] + ["", "", ""]

        audio = tmp / "voice.mp3"
        words = voiceover(voice_text, audio)
        total = duration(audio) + 0.9

        # shots: the AI problem scenes first, then product shots cycling through different moves
        n = max(6, math.ceil(total / SHOT))
        scenes = imgs[:scene_count]
        prods = imgs[scene_count:] or imgs
        # clean studio shots first and twice as often as supplier photos
        studio = [i for i, src in enumerate(images[scene_count:6]) if "studio" in str(src)]
        if studio:
            st = [prods[i] for i in studio if i < len(prods)]
            other = [x for x in prods if x not in st]
            mix = []
            for k in range(max(len(other), 1) * 2):
                mix.append(st[k % len(st)])
                if k % 2 == 1 and other:
                    mix.append(other[(k // 2) % len(other)])
            prods = mix
        order = []
        for i in range(n):
            if i < 2 and scenes:
                order.append((scenes[i % len(scenes)], False))
            else:
                order.append((prods[(i - min(2, len(scenes))) % len(prods)], True))
        parts = []
        for i, (img, square) in enumerate(order):
            frames = int(SHOT * FPS) if i < n - 1 else int((total - SHOT * (n - 1)) * FPS) + 1
            part = tmp / f"s{i:02d}.mp4"
            shot(img, i, max(frames, 8), part, square)
            parts.append(part)
        lst = tmp / "list.txt"
        lst.write_text("".join(f"file '{p}'\n" for p in parts))
        base = tmp / "base.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(base)], check=True)

        # ---- text layers
        fb, fi = str(FONT_BOLD), str(FONT_ITALIC)
        draws = []
        hook_end = min(1.8, total)
        # hook: huge, each line centered, pops in on frame one, shakes for the first second
        for li, line in enumerate(wrap(lines[0].upper(), 12).split("\n")):
            hf = tmp / f"hook{li}.txt"
            hf.write_text(line)
            draws.append(f"drawtext=fontfile='{fb}':textfile='{hf}':fontsize='if(lt(t,0.12),70+t*600,140)':"
                         f"fontcolor={'white' if li % 2 == 0 else ACCENT}:borderw=8:bordercolor=black:"
                         f"x='(w-text_w)/2+sin(t*55)*9*lt(t,1.2)':y={250 + li * 160}:enable='lt(t,{hook_end:.2f})'")
        # word-by-word captions; every third group in the accent color
        end_s = max(total - 1.6, 0)
        gs = groups(words)
        for gi, (txt, s, e) in enumerate(gs):
            if s is None or e is None or not txt or e <= hook_end - 0.1 or s >= end_s:
                continue   # the hook says the first words; the end card says the last
            nxt = gs[gi + 1][1] if gi + 1 < len(gs) and gs[gi + 1][1] is not None else total
            s = max(s, hook_end)
            e = min(max(e + 0.08, s + 0.25), nxt, end_s)   # never overlap the next caption
            if e <= s:
                continue
            color = ACCENT if gi % 3 == 1 else "white"
            tf = tmp / f"w{gi}.txt"
            tf.write_text(txt)
            draws.append(f"drawtext=fontfile='{fb}':textfile='{tf}':fontsize='if(lt(t-{s:.2f},0.08),95+(t-{s:.2f})*560,140)':"
                         f"fontcolor={color}:borderw=9:bordercolor=black:x=(w-text_w)/2:y=400:"
                         f"enable='between(t,{s:.2f},{e:.2f})'")
        # end card: price + call to action in the last 1.6 s (above the product)
        cta_file = tmp / "cta.txt"
        cta_file.write_text(wrap((lines[2] or "LINK IN BIO").upper(), 14))
        draws.append(f"drawbox=x=0:y=280:w=iw:h=300:color=black@0.55:t=fill:enable='gte(t,{end_s:.2f})'")
        draws.append(f"drawtext=fontfile='{fb}':textfile='{cta_file}':fontsize='if(lt(t-{end_s:.2f},0.1),90+(t-{end_s:.2f})*500,140)':"
                     f"fontcolor={ACCENT}:borderw=6:bordercolor=black:x=(w-text_w)/2:y=305:enable='gte(t,{end_s:.2f})'")
        draws.append(f"drawtext=fontfile='{fi}':text='link in bio':fontsize=86:fontcolor=white:x=(w-text_w)/2:y=470:"
                     f"enable='gte(t,{end_s:.2f})'")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(base), "-i", str(audio), "-vf", ",".join(draws),
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", str(out_path)], check=True)
        return out_path
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
