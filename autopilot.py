#!/usr/bin/env python3
"""Theo's autopilot, cloud version. GitHub runs this once a day, so your Mac can be off.

What it does:
1. Picks today's Home Fixes product and one of its studio photos (rotates by date).
2. Asks Claude (on your Claude login, via CLAUDE_CODE_OAUTH_TOKEN) for an Instagram and a TikTok caption.
3. Checks each caption with code: real prices only, no shipping promises, no links, no sales/codes, no health claims.
   A caption that fails gets one rewrite. If it still fails, it is NOT posted and the run fails,
   so GitHub emails you.
4. Puts the post in each account's Buffer queue (MODE=queue) or saves Buffer drafts (MODE=draft, for testing).

Needs: BUFFER_API_KEY and CLAUDE_CODE_OAUTH_TOKEN as GitHub secrets. Standard library only.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

from video import make_video

HERE = Path(__file__).resolve().parent
STORE = "https://thegiftwren.myshopify.com"
BUFFER_API = "https://api.buffer.com"
MODE = os.environ.get("MODE", "draft").strip() or "draft"
START = date(2026, 11, 27)

ANGLES = ["the annoying problem it fixes", "a quick before and after", "a gift idea for someone specific",
          "a 'things I wish I bought sooner' list", "how easy it is (no tools, no install)",
          "a relatable everyday moment", "who it's perfect for"]

BANNED = [
    (r"free shipping", "promises free shipping"),
    (r"guarantee", "makes a guarantee"),
    (r"\b(fast|quick|express|speedy)\s+(shipping|delivery)", "promises fast shipping"),
    (r"\b\d+\s*(business\s+)?days?\b", "names a number of days"),
    (r"\barrives?\b|\bdelivered\b", "promises a delivery time"),
    (r"(by|before|in time for)\s+(christmas|xmas|the holidays)", "promises Christmas delivery"),
    (r"https?://|www\.", "has a link"),
    (r"\b(cure|heal|medical|fda|doctor)", "makes a health claim"),
    (r"\b(best|#1|number one)\s+(in|on)\s+the\s+(world|market)", "makes a 'best in the world' claim"),
    (r"\b(sale|% off|discount|coupon|code)\b", "mentions a sale or code that may not exist"),
]


def say(*a):
    print(*a, flush=True)


# ------------------------------------------------------------------ products
def load_products():
    """Live store first. If the store can't be reached, use the saved copy in products-snapshot.json."""
    url = f"{STORE}/collections/home-fixes/products.json?limit=50"
    r = subprocess.run(["curl", "-sS", "-f", "--retry", "3", "--retry-delay", "5", "--max-time", "30", url],
                       capture_output=True, text=True)
    if r.returncode == 0:
        raw, source = json.loads(r.stdout)["products"], "live store"
    else:
        say("Couldn't read the live store, using the saved copy:", r.stderr.strip()[:150])
        raw, source = json.loads((HERE / "products-snapshot.json").read_text())["products"], "saved copy"
    prods = []
    for p in raw:
        imgs = [i["src"] for i in p["images"] if "gw-studio" in i["src"]]
        if imgs:
            prods.append({"handle": p["handle"], "title": p["title"], "images": imgs,
                          "prices": sorted({float(v["price"]) for v in p["variants"] if v.get("available", True)}),
                          "about": re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", p.get("body_html") or ""))[:1500]})
    prods.sort(key=lambda p: p["handle"])
    if not prods:
        sys.exit("No Home Fixes products with studio photos found.")
    return prods, source


# ------------------------------------------------------------------ captions
def check_caption(text, prices):
    t = text.lower()
    for pat, why in BANNED:
        if re.search(pat, t):
            return why
    shown = {round(float(x), 2) for x in re.findall(r"\$\s?(\d+(?:\.\d{1,2})?)", text)}
    lists_all = all(round(p, 2) in shown for p in prices)   # "$12.99 / $13.99" is honest too
    for m in re.finditer(r"\$\s?(\d+(?:\.\d{1,2})?)", text):
        amt = m.group(1)
        if not any(abs(float(amt) - p) < 0.005 for p in prices):
            return f"says ${amt}, which isn't a real price"
        before = text[max(0, m.start() - 14):m.start()].lower()
        if len(prices) > 1 and not lists_all and abs(float(amt) - min(prices)) < 0.005 and not re.search(r"(from|starting at|starts at|/)\s*$", before):
            return f"says ${amt} without 'from' (this product has more than one price)"
    if len(prices) > 1 and re.search(r"\b(just|only)\s+(\$|[a-z]+\s+(ninety|eighty|seventy|sixty|fifty|forty|thirty|twenty))", t):
        return "says 'just/only' a price, but this product has more than one price (say 'from')"
    if not text.strip():
        return "is empty"
    if len(text) > 2000:
        return "is too long"
    return ""


def ask_claude(prompt):
    system = ("You are Theo, the social media writer for The Gift Wren, a small online store selling "
              "home problem-solvers. You write short, honest, natural captions. You never invent facts. "
              "Store facts:\n\n" + (HERE / "store.md").read_text())
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)   # use the Claude subscription token, never a paid API key
    if env.get("CLAUDE_CODE_OAUTH_TOKEN"):
        env["CLAUDE_CODE_OAUTH_TOKEN"] = re.sub(r"\s+", "", env["CLAUDE_CODE_OAUTH_TOKEN"])
    r = subprocess.run(["claude", "-p", "--model", "haiku", "--tools", "", "--no-session-persistence",
                        "--system-prompt", system, "--output-format", "json"],
                       input=prompt, capture_output=True, text=True, env=env, timeout=300)
    try:
        out = json.loads(r.stdout)
    except ValueError:
        sys.exit("Claude didn't answer: " + (r.stderr or r.stdout)[-400:])
    if out.get("is_error"):
        sys.exit("Claude error: " + str(out.get("result"))[:400])
    text = out.get("result", "")
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except ValueError:
        return None


def write_captions(prod, angle, kind="video", note=""):
    price = " / ".join(f"${p:.2f}" for p in prod["prices"])
    what = ("a short vertical video (photos of the product, big text on screen, and a voiceover)" if kind == "video"
            else "a photo post (a clean studio shot of the product)")
    prompt = f"""Write today's post for one product. The post is {what}.
PRODUCT: {prod['title']}
PRICE(S): {price}
ABOUT: {prod['about']}
TODAY'S ANGLE: {angle}
{('NOTE FROM THE OWNER ABOUT THE MEDIA: ' + note) if note else ''}

Rules:
- Problem first, fix second. Sound like a real person, not an ad. Short lines.
- Only say things that are true from the info above. You may mention the price, written exactly as above.
- If there is more than one price, always say "from" before the lowest one (for example "from $12.99", or in the voiceover "from twelve ninety-nine"). Never say "just" or "only" with a price.
- Never mention shipping speed, delivery dates, days, Christmas delivery, free shipping, sales, discounts, codes or guarantees.
- No links or URLs. Instagram may say "link in bio". Captions end with 3 to 6 hashtags.
- "voiceover": what is said out loud, 18 to 30 words, natural and upbeat. Say prices in words ("fourteen ninety nine").
- "screen": exactly 3 lines: [a scroll-stopping HOOK of max 5 words (a question or bold claim about the problem), the fix in max 4 words, the price CTA like "FROM $12.99"].
- The voiceover must sound like an energetic creator talking to a friend, never like an ad reader.
Reply with ONLY JSON: {{"instagram": "caption", "tiktok": "caption", "tiktok_title": "max 80 characters",
 "voiceover": "...", "screen": ["...", "...", "..."]}}"""
    out = ask_claude(prompt)
    if not out:
        sys.exit("Claude's answer wasn't JSON.")
    for svc in ("instagram", "tiktok", "voiceover"):
        problem = check_caption(str(out.get(svc, "")), prod["prices"])
        if problem:
            say(f"{svc} text {problem}. Asking for one rewrite.")
            fix = ask_claude(prompt + f"\n\nYour last {svc} text {problem}. Write it again without that. "
                                      f'Reply with ONLY JSON: {{"text": "..."}}')
            out[svc] = str((fix or {}).get("text", ""))
    screen = [str(x) for x in (out.get("screen") or [])][:3]
    if any(check_caption(x, prod["prices"]) for x in screen) or len(screen) < 2:
        screen = ["", "", ""]
    out["screen"] = screen
    return out


# ------------------------------------------------------------------ media (videos the owner adds, and hosting)
REPO = os.environ.get("GITHUB_REPOSITORY", "")
PAGES = f"https://{REPO.split('/')[0]}.github.io/{REPO.split('/')[-1]}" if REPO else ""
MEDIA_DIR = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "theo-media"


def media_branch():
    """Check out the 'media' branch (videos + list of used media) into MEDIA_DIR. Returns used set."""
    tok = os.environ.get("GITHUB_TOKEN", "")
    if not (REPO and tok):
        return None
    url = f"https://x-access-token:{tok}@github.com/{REPO}.git"
    shutil.rmtree(MEDIA_DIR, ignore_errors=True)
    r = subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", "media", url, str(MEDIA_DIR)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        MEDIA_DIR.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "media"], cwd=MEDIA_DIR, check=True)
    subprocess.run(["git", "remote", "remove", "origin"], cwd=MEDIA_DIR, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", url], cwd=MEDIA_DIR, check=True)
    used = MEDIA_DIR / "used.txt"
    return set(used.read_text().split()) if used.exists() else set()


def publish_media(local_file, used):
    """Put the video on the media branch, served by GitHub Pages. Returns its public URL."""
    vdir = MEDIA_DIR / "v"
    vdir.mkdir(exist_ok=True)
    cutoff = (date.today().toordinal() - 14)
    for old in vdir.glob("*.mp4"):          # keep two weeks, so the repo stays small
        try:
            if date.fromisoformat(old.name[:10]).toordinal() < cutoff:
                old.unlink()
        except ValueError:
            pass
    shutil.copy(local_file, vdir / Path(local_file).name)
    (MEDIA_DIR / "used.txt").write_text("\n".join(sorted(used)) + "\n")
    (MEDIA_DIR / ".nojekyll").write_text("")
    git = lambda *a: subprocess.run(["git", *a], cwd=MEDIA_DIR, check=True, capture_output=True, text=True)
    git("config", "user.name", "Theo autopilot")
    git("config", "user.email", "theo-autopilot@users.noreply.github.com")
    git("checkout", "-q", "--orphan", "fresh")   # one commit only: no history, no bloat
    git("add", "-A")
    git("commit", "-q", "-m", f"media {date.today()}")
    git("push", "-q", "-f", "origin", "fresh:media")
    url = f"{PAGES}/v/{Path(local_file).name}"
    for _ in range(40):                          # Pages takes a minute or two to publish
        r = subprocess.run(["curl", "-sI", "-o", "/dev/null", "-w", "%{http_code} %{content_type}", url],
                           capture_output=True, text=True)
        if r.stdout.startswith("200") and "video" in r.stdout:
            return url
        time.sleep(10)
    raise RuntimeError(f"The video didn't come online at {url} (is GitHub Pages on, from the 'media' branch?)")


def next_owner_media(used):
    """media.txt lines: product-handle | https://link | optional note. Returns the first unused one."""
    f = HERE / "media.txt"
    if used is None or not f.exists():
        return None
    for line in f.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [x.strip() for x in line.split("|")]
        if len(parts) >= 2 and parts[1].startswith("https://") and parts[1] not in used:
            return {"handle": parts[0], "url": parts[1], "note": parts[2] if len(parts) > 2 else "",
                    "is_video": bool(re.search(r"\.(mp4|mov|m4v)(\?|$)", parts[1], re.I))}
    return None


# ------------------------------------------------------------------ buffer
def gql(query, variables=None):
    key = os.environ.get("BUFFER_API_KEY", "").strip()
    if not key:
        sys.exit("BUFFER_API_KEY secret is missing.")
    req = urllib.request.Request(BUFFER_API, data=json.dumps({"query": query, "variables": variables or {}}).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                          "User-Agent": "tate-cloud/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            out = json.loads(r.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"Buffer said {e.code}: {e.read()[:200].decode(errors='replace')}")
    if out.get("errors"):
        sys.exit("Buffer: " + "; ".join(x.get("message", "?") for x in out["errors"]))
    return out["data"]


def channels():
    orgs = gql("query { account { organizations { id } } }")["account"]["organizations"]
    out = []
    for o in orgs:
        q = "query { channels(input: {organizationId: %s}) { id name service type isDisconnected isLocked } }" % json.dumps(o["id"])
        out += [c for c in gql(q)["channels"]
                if c["service"] in ("instagram", "tiktok") and not c["isDisconnected"] and not c["isLocked"]]
    return out


def post(ch, text, media, tiktok_title, kind="image"):
    asset = {"image": {"url": media}} if kind == "image" else {"video": {"url": media, "metadata": {"thumbnailOffset": 500}}}
    inp = {"channelId": ch["id"], "text": text, "mode": "addToQueue", "assets": [asset],
           "schedulingType": "notification" if (ch["service"] == "instagram" and ch["type"] == "profile") else "automatic"}
    if ch["service"] == "instagram":
        inp["metadata"] = {"instagram": {"type": "post" if kind == "image" else "reel", "shouldShareToFeed": True}}
    elif kind == "image":
        inp["metadata"] = {"tiktok": {"title": tiktok_title[:85]}}
    if MODE == "draft":
        inp["saveToDraft"] = True
    q = """mutation($input: CreatePostInput!) { createPost(input: $input) {
             ... on PostActionSuccess { post { id dueAt status } } ... on MutationError { message } } }"""
    res = gql(q, {"input": inp})["createPost"]
    if not res.get("post"):
        raise RuntimeError(res.get("message") or "Buffer didn't make the post")
    return res["post"]


# ------------------------------------------------------------------ main
def main():
    if MODE not in ("draft", "queue"):
        sys.exit("MODE must be draft or queue")
    day = (date.today() - START).days
    prods, source = load_products()
    used = media_branch()
    owner = next_owner_media(used)
    by_handle = {p["handle"]: p for p in prods}
    prod = by_handle.get(owner["handle"]) if owner else None
    if owner and not prod:
        say(f"media.txt names '{owner['handle']}', which isn't a Home Fixes product. Skipping that line today.")
        owner = None
    prod = prod or prods[day % len(prods)]
    photo = prod["images"][(day // len(prods)) % len(prod["images"])]
    angle = ANGLES[day % len(ANGLES)]
    say(f"Mode: {MODE} | product: {prod['title']} ({source}) | angle: {angle}"
        + (f" | your media: {owner['url']}" if owner else ""))

    chans = channels()
    if not chans:
        sys.exit("No Instagram or TikTok connected in Buffer.")
    caps = write_captions(prod, angle, "video", owner["note"] if owner else "")

    # make (or take) today's video; if anything goes wrong, post the photo instead
    media, kind = photo, "image"
    try:
        if owner and owner["is_video"]:
            media, kind = owner["url"], "video"
        elif used is not None:
            frames = ([owner["url"]] if owner else []) + prod["images"]
            frames = list(dict.fromkeys(frames))[:3]
            name = f"{date.today()}-{prod['handle']}.mp4"
            out = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / name
            make_video(frames, caps["screen"], caps["voiceover"], out)
            if owner:
                used.add(owner["url"])
            media, kind = publish_media(out, used), "video"
        else:
            say("Not running on GitHub, so no video hosting: posting the photo.")
    except Exception as e:   # noqa: BLE001  a missed video should never mean a missed day
        say(f"Video step didn't work ({e}). Posting the photo instead.")
        media, kind = photo, "image"
    say(f"Posting a {kind}: {media}\nVoiceover: {caps.get('voiceover')}")

    failed = 0
    for ch in chans:
        cap = str(caps.get(ch["service"], "")).strip()
        problem = check_caption(cap, prod["prices"])
        if problem:
            say(f"\nNOT POSTED to {ch['service']} @{ch['name']}: the caption {problem}.\n{cap}")
            failed += 1
            continue
        try:
            p = post(ch, cap, media, str(caps.get("tiktok_title") or prod["title"]), kind)
            say(f"\n{'Saved as Buffer draft' if MODE == 'draft' else 'Queued'}: {ch['service']} @{ch['name']} "
                f"(due {p.get('dueAt') or 'when you schedule it'})\n{cap}")
        except RuntimeError as e:
            say(f"\nFAILED {ch['service']} @{ch['name']}: {e}")
            failed += 1
    if failed:
        sys.exit(f"{failed} account(s) didn't get a post today. See above.")


if __name__ == "__main__":
    main()
