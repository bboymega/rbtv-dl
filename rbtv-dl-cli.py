import ffmpeg
import requests
from urllib.parse import urlparse, quote
from pathlib import Path
import random
import string
import re
import unicodedata
import sys
import json
import argparse
import os
import time

def sanitize_video_title(video_title: str) -> str:
    video_title = unicodedata.normalize("NFKD", video_title)
    video_title = video_title.replace("–", "-")
    video_title = video_title.encode("ascii", "ignore").decode("ascii")
    video_title = re.sub(r'[^A-Za-z0-9._\- ]', '_', video_title)
    video_title = re.sub(r'_+', '_', video_title)
    video_title = video_title.strip("._")
    return video_title[:150]

def download_stream(base_url, output_file=None):
    path = urlparse(base_url).path
    url = path.lstrip('/')
    category_raw = path.rstrip('/').split('/')[-2]
    category_map = {
        "live": "live-videos",
        "episodes": "episode-videos",
        "films": "films",
        "videos": "videos"
    }
    category = category_map.get(category_raw, category_raw)

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Accept": "application/json, text/plain, */*",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "sec-ch-ua": '"Chromium";v="144", "Not(A:Brand";v="24", "Google Chrome";v="144"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Upgrade-Insecure-Requests": "1",
        "Referer": base_url,
        "Origin": "https://www.redbull.com"
    })

    if not url:
        sys.stderr.write("\033[31m" + f"ERROR: Missing url parameter" + "\033[0m\n")
        return 1
    
    base_endpoint = "https://www.redbull.com/v3/api/graphql/v1/v3/feed/"
    locale_endpoint = "https://www.redbull.com/v3/config/pages?url="  + url

    response = session.get(locale_endpoint)
    selected_locale = ""
    if response.status_code == 200:
        json_data = response.json()
        locales = json_data.get("data", {}).get("domainConfig", {}).get("supportedLocales", [])
        if len(locales) == 0:
            sys.stderr.write("\033[31m" + f"ERROR: No locales available for {base_url}" + "\033[0m\n")
            return 1
        selected_locale = next((l for l in locales if "en" in l.lower()), locales[0] if locales else None)
        base_endpoint = base_endpoint + selected_locale
    else:
        sys.stderr.write(f"\033[31mERROR: Unable to fetch locales for {base_url}\033[0m\n")
        return 1

    metadata_url = base_endpoint + "?disableUsageRestrictions=true&filter[type]=" + category + "&filter[uriSlug]=" + url.split('/')[-1] + "&rb3Schema=v1:pageConfig&rb3PageUrl=/" + url
    metadata_url_backup = "?disableUsageRestrictions=true&filter[type]=" + category + "&filter[uriSlug]=" + url.split('/')[-1] + "&rb3Schema=v1:pageConfig&rb3PageUrl=/" + url
    response = session.get(metadata_url)
    if response.status_code == 200:
        json_data = response.json()
        video_id = json_data.get('data').get('id')
        if not video_id:

            sys.stderr.write("\033[31m" + f"ERROR: Unable to fetch video metadata for {base_url}" + "\033[0m\n")
            return 1
    else:
        sys.stderr.write("\033[31m" + f"ERROR: Unable to fetch video metadata for {base_url}" + "\033[0m\n")
        return 1
    
    video_url_api_url = "https://api-player.redbull.com/rbcom/videoresource?videoId=" + video_id  + "&localeMixing=" + selected_locale
    response = session.get(video_url_api_url)
    if response.status_code == 200:
        json_data = response.json()
        video_url = json_data.get('videoUrl')
        video_title_raw = json_data.get('title')
        if not video_title_raw:
            video_title = 'rbtv-' + ''.join(random.choices(string.ascii_letters, k=8))
        else:
            video_title = sanitize_video_title(video_title_raw)

        if not video_url:
            sys.stderr.write("\033[31m" + f"ERROR: Unable to fetch streaming url for {base_url}" + "\033[0m\n")
            return 1
    else:
        sys.stderr.write("\033[31m" + f"ERROR: Unable to fetch streaming url for {base_url}" + "\033[0m\n")
        return 1

    if not output_file:
        output_file = video_title
    path = Path(output_file)
    if path.is_dir():
        path = path / video_title
    if path.suffix.lower() != ".mp4":
        path = path.with_suffix(".mp4")
    output_file = str(path)

    headers_str = (
        "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36\r\n"
        "Accept: */*\r\n"
        "Accept-Language: en-US,en;q=0.9\r\n"
        "Origin: https://www.redbull.com\r\n"
        "Referer: https://www.redbull.com/\r\n"
        "Sec-Fetch-Dest: empty\r\n"
        "Sec-Fetch-Mode: cors\r\n"
        "Sec-Fetch-Site: same-site\r\n"
        "sec-ch-ua: \"Chromium\";v=\"140\", \"Not=A?Brand\";v=\"24\", \"Google Chrome\";v=\"140\"\r\n"
        "sec-ch-ua-mobile: ?0\r\n"
        "sec-ch-ua-platform: \"Windows\"\r\n"
    )

    print(f"INFO: M3U stream found at {video_url}")
    print(f"INFO: Converting and saving to {output_file}")

    temp_ts = str(Path(output_file).with_suffix(".ts"))
    
    process = (
        ffmpeg
        .input(video_url, fflags='nobuffer', flags='low_delay', headers=headers_str)
        .output(
            temp_ts, 
            format='mpegts',
            c='copy',
            loglevel='error'
        )
        .overwrite_output()
        .run_async(pipe_stderr=True)
    )

    try:
        while process.poll() is None:
            if os.path.exists(temp_ts):
                received_bytes = os.path.getsize(temp_ts)
                print(f"\rStatus: Receiving... | Downloaded Size: {received_bytes / 1024 / 1024:.2f} MB", end="", flush=True)
            time.sleep(0.5)

        if process.returncode == 0:
            print(f"\nINFO: Finalizing MP4...")
            (
                ffmpeg
                .input(temp_ts)
                .output(output_file, **{'c': 'copy', 'bsf:a': 'aac_adtstoasc'}, movflags='faststart', loglevel='error')
                .overwrite_output()
                .run()
            )
            os.remove(temp_ts)
            final_size = os.path.getsize(output_file) / (1024 * 1024)
            print(f"INFO: Successfully saved {output_file} ({final_size:.2f} MB)")
        else:
            stderr_data = process.stderr.read().decode(errors='replace')
            sys.stderr.write(f"\n\033[31mERROR: FFmpeg failed: {stderr_data}\033[0m\n")

    except KeyboardInterrupt:
        if process.poll() is None:
            process.terminate()
        print(f"\nINFO: Interrupted. Partial file saved as {temp_ts} (Playable in VLC).")
    except Exception as e:
        sys.stderr.write(f"\n\033[31mERROR: File writing failed: {e}\033[0m\n")
    finally:
        if hasattr(process, 'stderr') and process.stderr:
            process.stderr.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RBTV Downloader")
    parser.add_argument("url", help="The URL of the video to download")
    parser.add_argument("-o", "--output", 
                        dest="output_file",
                        help="Optional: Output file path")
    args = parser.parse_args()
    download_stream(args.url, args.output_file)
