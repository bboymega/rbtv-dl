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
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "Referer": base_url
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
        sys.stderr.write("ERROR: Unable to fetch locales for {base_url}" + "\033[0m\n")
        return 1

    metadata_url = base_endpoint + "?disableUsageRestrictions=true&filter[uriSlug]=" + url.split('/')[-1] + "&rb3Schema=v1:pageConfig&rb3PageUrl=/" + url
    response = session.get(metadata_url)
    if response.status_code == 200:
        json_data = response.json()
        video_id = json_data.get('data').get('id')
        video_thumbnail = json_data.get('data').get('pageMeta').get('og:image')
        if not video_thumbnail:
            video_thumbnail = ""
        if video_id == "" or not video_id:
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
        video_title = json_data.get('title')
        if video_title == "" or not video_title:
            video_title = 'rbtv-'.join(random.choices(string.ascii_letters, k=8))
        else:
            video_title = sanitize_video_title(video_title)

        if video_url == "" or not video_url:
            sys.stderr.write("\033[31m" + f"ERROR: Unable to fetch streaming url for {base_url}" + "\033[0m\n")
            return 1
    else:
        sys.stderr.write("\033[31m" + f"ERROR: Unable to fetch streaming url for {base_url}" + "\033[0m\n")
        return 1

    headers = (
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
    process = (
        ffmpeg
        .input(video_url, fflags='nobuffer', flags='low_delay', headers=headers)
        .output(
            'pipe:', 
            format='mp4',
            vcodec='copy',
            acodec='copy',
            movflags='frag_keyframe+empty_moov+default_base_moof',
            loglevel='error'
        )
        .run_async(pipe_stdout=True, pipe_stderr=True)
    )

    def generate():
        try:
            while True:
                chunk = process.stdout.read(8192)
                if not chunk:
                    break
                yield chunk
        except (GeneratorExit, ConnectionResetError):
            pass
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=0.5)
                except:
                    process.kill()

            try:
                if not process.stderr.closed:
                    stderr_data = process.stderr.read()
                    if stderr_data:
                        sys.stderr.write(f"\n\033[91mFFmpeg Error: {stderr_data.decode(errors='replace')}\033[0m")
            except (ValueError, OSError):
                pass
            finally:
                process.stdout.close()
                process.stderr.close()

    print(f"INFO: M3U stream found for {base_url}")
    received_bytes = 0
    try:
        print(f"INFO: Converting and saving {video_url}")
        
        if not output_file:
            output_file = video_title
        elif output_file == "":
            output_file = video_title
        
        path = Path(output_file)
        if path.suffix.lower() != ".mp4":
            path = path.with_suffix(".mp4")
        output_file = str(path)

        with open(output_file, 'wb') as f:
            for chunk in generate():
                f.write(chunk)
                received_bytes += len(chunk)
                
                mb_received = received_bytes / (1024 * 1024)
                
                print(f"\rStatus: Receiving... | Total Size: {mb_received:.2f} MB", end="", flush=True)

        print(f"\nINFO: Successfully saved {output_file} ({received_bytes / 1024 / 1024 :.2f} MB)")

    except KeyboardInterrupt:
        print("INFO: Process interrupted by user. File saved up to interruption.")
    except Exception as e:
        sys.stderr.write("\n\033[31m" + f"ERROR: File writing failed: {e}" + "\033[0m\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RBTV Downloader")
    parser.add_argument("url", help="The URL of the video to download")
    parser.add_argument("-o", "--output", 
                        dest="output_file",
                        help="Optional: Output file path")
    args = parser.parse_args()
    download_stream(args.url, args.output_file)
