# Red Bull TV Downloader
Your all-in-one, no-nonsense wingman for downloading Red Bull TV videos.

Works both as a CLI tool for the terminal grinders and a Web UI for the point-and-click enjoyers (we don’t judge).

Built with **React/Next.js**, **Python-Flask** and just enough rizz to make it work.

![RBTV-DL](screenshot-rbtv-dl-4_1.jpeg)

# Quick Start: Web UI (Docker):
Spin it up like it’s Friday night:
```
docker run --rm -d -p 8080:8080 bboymega/rbtv-dl:4.1
```

Then open:
```
http://127.0.0.1:8080
```

Or:
```
http://[server-ip]:8080
```

If it boots without errors, just enjoy the moment.

# CLI Usage: 
For those who trust the terminal more than container spaghetti:

```
python3 rbtv-dl-cli.py url [-o OUTPUT]

positional arguments:
  url                   URL of Video Page

optional arguments:
  -o OUTPUT, --output OUTPUT
                        Set Output Path
```

Requirements (for CLI):

```
ffmpeg
```

Installing prerequisite packages:
```
apt install ffmpeg (For Debian & Ubuntu)
python3 -m venv venv
source venv/bin/activate
pip3 install -r requirements.txt
```

# Changlog
```
## 2026-03-17
- Added locale auto-detection.

## 2026-03-11
- Updated compatibility for Red Bull TV API v5.1.

## 2026-03-10
- Fixed a bug where yt-dlp was required but not installed when building the image.

## 2026-03-09
- Fixed issues with the search filter.
- Fixed a bug where the trailer was downloaded instead of the main video when a trailer was available.
```