# Theo autopilot (cloud)

GitHub runs this every day at 10 AM Central. It needs no Mac: it works even when your laptop is dead or closed.
Each day Theo picks a Home Fixes product and makes a short vertical **video** from its photos: big text on screen and
an ElevenLabs voiceover. He writes an Instagram caption and a TikTok caption, checks everything, and puts the video
in your Buffer queue (an Instagram Reel and a TikTok). If the video step fails, he posts the photo instead.

**Your own videos and Gemini photos go first:** add a line to `media.txt` (instructions are inside it).

**Cost: $0.**
- GitHub Actions is free for public repos. This uses about 3 minutes a day.
- Claude runs on your normal Claude plan, so it uses a little of your usage limit.
- Buffer's free plan is enough.
- ElevenLabs: about 150–200 characters a day from your paid plan.
- The repo is public so Buffer can download the videos (GitHub Pages). Keys stay hidden in Secrets.

## Files
- `autopilot.py`: the script
- `video.py`: makes the video (photos + text + ElevenLabs voiceover)
- `media.txt`: videos and photos you add
- `.github/workflows/theo.yml`: the daily schedule
- `store.md`: store facts Theo must stick to. Edit it when prices or policies change.
- `products-snapshot.json`: a backup copy of your products, used only if the store can't be reached

## Safety
Captions are checked by code. A caption is never posted if it has any of these:
- a price that isn't real
- a shipping or delivery promise
- "free shipping" or Christmas delivery
- a link
- a sale or code
- a health claim

Theo gets one try to fix a blocked caption. If it still fails, that post is skipped and GitHub emails you that
the run failed.

## Turn it off
Go to github.com/thegiftwren/theo → **Actions** → **Theo autopilot** → **⋯** → **Disable workflow**.
To stop a single post, delete it in Buffer before its time.
