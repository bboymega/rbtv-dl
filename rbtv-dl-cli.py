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
from concurrent.futures import ThreadPoolExecutor, as_completed

locale_list = [
    "us","gb","de","at","ch","fr","it","es","nl","se","no","dk","fi",
    "ca","au","nz","jp","kr","sg","ae","be","ie","pt","cz","pl","hu",
    "ro","gr","tr","tw","mx","br","ar","cl","co","pe","za","hk","th",
    "my","id","ph","vn","in","pk","bd","lk","np","ke","ng","gh","tz",
    "ug","ua","kz","rs","bg","hr","sk","si","sa","qa","kw","om","jo",
    "lb","ma","tn","dz","ad","ag","ai","al","am","ao","aq","as","aw",
    "ax","az","ba","bb","bf","bh","bi","bj","bl","bm","bn","bo","bq",
    "bs","bt","bv","bw","bz","cc","cd","cf","cg","ci","ck","cm","cv",
    "cw","cx","cy","dj","dm","do","ec","ee","eg","eh","fj","fk","fm",
    "fo","ga","gd","ge","gf","gg","gi","gl","gm","gn","gp","gq","gs",
    "gt","gu","gw","gy","hm","hn","ht","il","im","io","iq","is","je",
    "jm","kg","kh","ki","km","kn","ky","la","lc","li","lr","ls","lt",
    "lu","lv","ly","mc","md","me","mf","mg","mh","mk","ml","mn","mo",
    "mp","mq","mr","ms","mt","mu","mv","mw","mz","na","nc","ne","nf",
    "ni","nr","nu","pa","pf","pg","pm","pn","pr","ps","pw","py","re",
    "rw","sb","sc","sd","sh","sj","sl","sm","sn","so","sr","ss","st",
    "sv","sx","sz","tc","td","tf","tg","tj","tk","tl","to","tt","tv",
    "um","uy","uz","va","vc","vg","vi","vu","wf","ws","ye","yt","zm",
    "zw","cn","ir","kp","sy","tm","er","cu","ve","ru","by","af","mm"
]

def sanitize_video_title(video_title: str) -> str:
    video_title = unicodedata.normalize("NFC", video_title)
    video_title = re.sub(r'[\\/*?:"<>|]', '_', video_title)
    video_title = re.sub(r'_+', '_', video_title)
    video_title = video_title.strip(". ")
    return video_title[:150]

def format_size(num_bytes):
    for unit in ["B", "KB", "MB", "GB"]:
        if num_bytes < 1024:
            return f"{num_bytes:.2f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.2f} TB"

def download_stream(base_url_init, output_file=None, source_address=None):
    base_url = base_url_init
    session = requests.Session()
    if source_address:
        adapter = SourceAddressAdapter(source_address)

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

    
    r = session.head(base_url, allow_redirects=True)

    if r.url != base_url:
        base_url = r.url
        print(f"INFO: Redirected to [{base_url}]")
    
    path = urlparse(base_url).path

    # V5.1 API Fix:
    # If the URL path already contains an `rrn:content` identifier, we can skip
    # the usual lookup logic and directly query the Red Bull TV player API
    # using that content ID to retrieve the video metadata.
    video_title= None
    video_url = None
    subheading = None
    segments = [s for s in path.split('/') if s]
    video_id = next((s for s in segments if re.search(r'rrn:content', s)), None)

    if video_id:
        tv_api = f"https://tv-api.redbull.com/products/dynamic/v5.1/rbtv/en/int/{video_id}"
        json_data = session.get(tv_api, timeout=10).json()
        stream_id = (json_data.get('links') or [{}])[0].get('id')
        locale = "int"

        def scan_stream_id_locale(locale, video_id):
            tv_api = f"https://tv-api.redbull.com/products/dynamic/v5.1/rbtv/en/{locale}/{video_id}"
            try:
                response = session.get(tv_api, timeout=5) 
                if response.ok:
                    json_data = response.json()
                    links = json_data.get('links')
                    if links and len(links) > 0:
                        s_id = links[0].get('id')
                        if s_id:
                            return {"locale": locale, "stream_id": s_id}
            except Exception:
                pass
            return None
            
        def scan_title_locale(locale, video_id):
            tv_api = f"https://tv-api.redbull.com/products/v5.1/rbtv/en/{locale}/{stream_id}"
            try:
                response = session.get(tv_api, timeout=5)
                if response.ok:
                    json_data = response.json()
                    title = json_data.get('title')
                    video_thumbnail = json_data.get('media_resources', {}).get('rbtv_display_art_landscape', {}).get('url')
                    subheading_raw = json_data.get('subheading')
                    if title:
                        return {"locale": locale, "video_title_raw": title, "video_thumbnail": video_thumbnail, "subheading_raw": subheading_raw}
            except Exception:
                pass
            return None
        
        # If INT is not available detect regional versions for stream_id.
        if not stream_id:
            with ThreadPoolExecutor(max_workers=16) as executor:
                future_to_locale = {executor.submit(scan_stream_id_locale, l, video_id): l for l in locale_list}
                for future in as_completed(future_to_locale):
                    result = future.result()
                    if result:
                        stream_id = result['stream_id']
                        locale = result['locale']
                        executor.shutdown(wait=False, cancel_futures=True)
                        break

        video_url_api = f"https://play.redbull.com/init/v1/rbtv/en/{locale}/personal_computer/http/{stream_id}"
        json_data = session.get(video_url_api, timeout=10).json()
        video_url = json_data.get('manifest_url')
        video_thumbnail = None
        video_title_raw = None
        subheading = None

        try:
            meta_url_api = f"https://tv-api.redbull.com/products/v5.1/rbtv/en/{locale}/{stream_id}"
            response = session.get(meta_url_api, timeout=10)

            # If INT is not available detect regional versions for metadata.
            if not response.ok:
                with ThreadPoolExecutor(max_workers=16) as executor:
                    future_to_locale = {executor.submit(scan_title_locale, l, video_id): l for l in locale_list}
                    for future in as_completed(future_to_locale):
                        result = future.result()
                        if result:
                            locale = result['locale']
                            video_title_raw = result['video_title_raw']
                            video_thumbnail = result['video_thumbnail']
                            subheading_raw = result['subheading_raw']
                            executor.shutdown(wait=False, cancel_futures=True)
                            break
            else:
                meta_json = response.json()
                video_title_raw = meta_json.get('title')
                video_thumbnail = meta_json.get('media_resources', {}).get('rbtv_display_art_landscape', {}).get('url')
                subheading_raw = meta_json.get('subheading')

            subheading = sanitize_video_title(subheading_raw) if subheading_raw else None
        except Exception:
            log_error(f"Unable to fetch metadata for [{video_id}], using randomized title", remote_addr)

        video_title = sanitize_video_title(video_title_raw) if video_title_raw else 'rbtv-' + ''.join(random.choices(string.ascii_letters, k=8))

    else:
        # Falling back to legacy API (V3)
        print(f"INFO: V5.1 API lookup failed for [{base_url}], falling back to legacy API")
        
        url = path.lstrip('/')
        category_raw = path.rstrip('/').split('/')[-2]
        category_map = {
            "live": "live-videos",
            "episodes": "episode-videos",
            "films": "films",
            "videos": "videos"
        }
        category = category_map.get(category_raw, category_raw)

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

    if subheading:
        video_title = video_title + ' - ' + subheading
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

    print(f"INFO: M3U stream found at [{video_url}]")
    print(f"INFO: Converting and saving to [{output_file}]")
    print(f"\r\033[KStatus: Preparing Conversion...", end="", flush=True)

    temp_ts = str(Path(output_file).with_suffix(".temp.ts"))
    
    process = (
        ffmpeg
        .input(video_url, probesize=32, analyzeduration=0, fflags='nobuffer', flags='low_delay', headers=headers_str)
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
                print(f"\r\033[KStatus: Receiving... | Downloaded Size: {format_size(received_bytes)}", end="", flush=True)
            time.sleep(0.5)

        if process.returncode == 0:
            print(f"\r\033[KStatus: Finalizing MP4...")
            (
                ffmpeg
                .input(temp_ts)
                .output(output_file, **{'c': 'copy', 'bsf:a': 'aac_adtstoasc'}, movflags='faststart', loglevel='error')
                .overwrite_output()
                .run()
            )
            os.remove(temp_ts)
            final_size = os.path.getsize(output_file)
            print(f"\r\033[KINFO: Successfully saved [{output_file}] ({format_size(final_size)})")
        else:
            stdout_data, stderr_data = process.communicate()
            error_msg = stderr_data.decode(errors='replace') if stderr_data else "No error message captured."
            sys.stderr.write(f"\n\033[31mERROR: FFmpeg failed: {error_msg}\033[0m\n")

    except KeyboardInterrupt:
        if process.poll() is None:
            process.terminate()
        print(f"\nINFO: Interrupted. Partial file saved as [{temp_ts}] (Playable in VLC).")
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
