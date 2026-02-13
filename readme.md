# Red Bull TV Downloader
Red Bull TV Downloader in Cli and Single-Page Application

# Single-Page Application:
React + Flask. Documentation in progress.

# CLI Usage: 
```
python3 rbtv-dl-cli.py url [-o OUTPUT]

positional arguments:
  url                   URL of Video Page

optional arguments:
  -o OUTPUT, --output OUTPUT
                        Set Output Path
```

Requirements:

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
