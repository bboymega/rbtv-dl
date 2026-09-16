import os
import re
import sys
import json
import ffmpeg
import string
import random
import requests
import tempfile
import unicodedata
import threading
import redis
import psutil
import time
import subprocess
from datetime import datetime
from urllib.parse import urlparse, unquote
from flask import Flask, request, jsonify, send_file
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv
import xxhash
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter

load_dotenv()
redis_host = os.getenv('REDIS_HOST', 'localhost')
redis_port = int(os.getenv('REDIS_PORT', 6379))
redis_db = int(os.getenv('REDIS_DB', 0))
retention_period = int(os.getenv('RETENTION_PERIOD', 21600))
source_addr = os.getenv('SOURCE_ADDRESS')
r = redis.Redis(host=redis_host, port=redis_port, db=redis_db, decode_responses=True)

class SourceAddressAdapter(HTTPAdapter):
    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        if source_addr:
            pool_kwargs['source_address'] = (source_addr, 0)
        return super().init_poolmanager(connections, maxsize, block, **pool_kwargs)

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

def create_app():
    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    return app

app = create_app()

def log_error(message, remote_addr="RBTV-DL"):
    timestamp = datetime.now().strftime('[%d/%b/%Y %H:%M:%S]')
    sys.stderr.write(f"\033[31m{timestamp} {remote_addr} \"ERROR: {message}\"\033[0m\n")

def log_info(message, remote_addr="RBTV-DL"):
    timestamp = datetime.now().strftime('[%d/%b/%Y %H:%M:%S]')
    print(f"{timestamp} {remote_addr} \"INFO: {message}\"", flush=True)

def set_task(video_id, data):
    r.set(f"task:{video_id}", json.dumps(data), ex=retention_period)

def get_task(video_id):
    data = r.get(f"task:{video_id}")
    return json.loads(data) if data else None

def is_pid_alive(pid):
    if pid is None:
        return False
    return psutil.pid_exists(int(pid))

def cleanup_orphaned_tasks():
    target_dir = os.path.join(tempfile.gettempdir(), "rbtv-dl")
    if not os.path.exists(target_dir):
        return

    protected_base_ids = set()
    now = time.time()
    
    try:
        keys = r.keys("task:*")
    except Exception as e:
        log_error(f"Redis connection error during cleanup: {e}", "RBTV-DL")
        return

    for key in keys:
        try:
            raw_data = r.get(key)
            if not raw_data:
                continue

            task = json.loads(raw_data)
            status = task.get("status")
            path = task.get("mp4_path")
            recorded_pid = task.get("pid")
            title = task.get("title", "Unknown")

            is_protected_status = status in ["converting", "finalizing", "completed"]

            is_ongoing = status in ["converting", "finalizing"] and is_pid_alive(recorded_pid)

            if (is_ongoing or status == "completed") and path:
                base_id = os.path.basename(path).split('.')[0]
                protected_base_ids.add(base_id)

            elif status in ["converting", "finalizing"]:
                log_info(f"Process {recorded_pid} for '{title}' is dead. Marking as failed.", "RBTV-DL")
                task.update({
                    "status": "failed", 
                    "pid": None,
                    "error": "Process terminated unexpectedly"
                })
                r.set(key, json.dumps(task), ex=retention_period)

        except Exception as e:
            log_error(f"Cleanup error processing key {key}: {e}", "RBTV-DL")

    try:
        for filename in os.listdir(target_dir):
            full_path = os.path.normpath(os.path.join(target_dir, filename))
            
            file_base_id = filename.split('.')[0]

            if file_base_id in protected_base_ids:
                continue

            try:
                mtime = os.path.getmtime(full_path)
                if (now - mtime) > 60:
                    if (os.path.isfile(full_path) or os.path.islink(full_path)) and os.path.exists(full_path):
                        os.remove(full_path)
                        log_info(f"Removed orphaned file/fragment: {filename}", "RBTV-DL")
                    elif os.path.isdir(full_path) and os.path.exists(full_path):
                        import shutil
                        shutil.rmtree(full_path)
                        log_info(f"Removed orphaned directory: {filename}", "RBTV-DL")
            except Exception as e:
                log_error(f"Failed to remove {filename}: {e}", "RBTV-DL")
                
    except Exception as e:
        log_error(f"Filesystem sweep failed: {e}", "RBTV-DL")

    

def purge_expired_tasks():
    keys = r.keys("task:*")
    now = datetime.now().timestamp()

    for key in keys:
        try:
            task = json.loads(r.get(key))
            if task.get("status") == "completed" and "completed_at" in task:
                if now - task["completed_at"] > retention_period:

                    mp4_path = task.get("mp4_path")
                    title = task.get("title", key)

                    if mp4_path and os.path.exists(mp4_path):
                        os.remove(mp4_path)
                        log_info(f"Purged expired task: {mp4_path}", "RBTV-DL")

                    r.delete(key)
                    log_info(f"Cleared record for {title}", "RBTV-DL")

        except Exception as e:
            log_error(f"Purge error for {key}: {e}", "RBTV-DL")

def sanitize_video_title(video_title: str) -> str:
    video_title = unicodedata.normalize("NFC", video_title)
    video_title = re.sub(r'[\\/*?:"<>|]', '_', video_title)
    video_title = re.sub(r'_+', '_', video_title)
    video_title = video_title.strip(". ")
    return video_title[:150]

def follow_redirect(base_url, remote_addr):
    session = requests.Session()
    if source_addr:
        adapter = SourceAddressAdapter()
        session.mount("http://", adapter)
        session.mount("https://", adapter)
    
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
        log_info(f"Redirected to [{r.url}]", remote_addr)
        return r.url
    return base_url

def get_title_from_url(base_url_init, remote_addr):
    base_url = follow_redirect(base_url_init, remote_addr)
    path = urlparse(base_url).path

    session = requests.Session()
    if source_addr:
        adapter = SourceAddressAdapter()
        session.mount("http://", adapter)
        session.mount("https://", adapter)

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

    segments = [s for s in path.split('/') if s]

    # Detect the complete rrn:content ID directly from the Red Bull TV page.
    content_match = re.search(
        r'(rrn:content:(?:films|event-profiles|shows|episode-videos|videos|live-videos):[A-Za-z0-9-]+)',
        path
    )
    content_id = content_match.group(1) if content_match else None

    if content_id:
        content_type = content_id.split(':')[2]

        try:
            # ----------------------------------------------------------
            # Helpers
            # ----------------------------------------------------------

            def get_metadata_url(locale, content_id):
                """
                Return the latest metadata endpoint for the content type.
                Event profiles and shows use dynamic/v5.2.
                Playable video resources use products/v5.3.
                """
                if content_type in ("event-profiles", "shows"):
                    return (
                        f"https://tv-api.redbull.com/products/dynamic/v5.2/"
                        f"rbtv/en/{locale}/{content_id}"
                    )

                return (
                    f"https://tv-api.redbull.com/products/v5.3/"
                    f"rbtv/en/{locale}/{content_id}"
                )

            def get_player_url(locale, content_id):
                """
                Build the player init endpoint.

                Event-profile pages are resolved to a live-video ID before
                reaching this helper, so all IDs passed here are playable.
                """
                return (
                    f"https://play.redbull.com/init/v1/rbtv/"
                    f"en/{locale}/personal_computer/http/{content_id}"
                )

            def get_stream_id_from_dynamic(locale, content_id):
                """
                Resolve event-profile/show -> playable child ID.

                event-profiles:
                    default_live_program / links[].id -> live-videos

                shows:
                    links[].id -> episode-videos
                """
                try:
                    api_url = (
                        f"https://tv-api.redbull.com/products/dynamic/v5.2/"
                        f"rbtv/en/{locale}/{content_id}"
                    )

                    response = session.get(api_url, timeout=5)

                    if response.ok:
                        json_data = response.json()

                        # Event profile explicitly exposes the playable
                        # program here.
                        default_live_program = json_data.get(
                            "default_live_program"
                        )

                        if (
                            content_type == "event-profiles"
                            and default_live_program
                            and default_live_program.startswith(
                                "rrn:content:live-videos:"
                            )
                        ):
                            return default_live_program

                        # Shows expose the playable episode through links.
                        links = json_data.get("links") or []

                        expected_type = {
                            "event-profiles": "rrn:content:live-videos:",
                            "shows": "rrn:content:episode-videos:"
                        }.get(content_type)

                        if expected_type:
                            for link in links:
                                link_id = link.get("id")

                                if (
                                    link_id
                                    and link_id.startswith(expected_type)
                                ):
                                    return link_id

                except Exception:
                    pass

                return None

            def scan_stream_id_locale(locale, content_id):
                """
                Preserve the original locale scanning behavior.

                For dynamic content, scan for a playable child ID.
                For directly playable content, the content ID itself is
                already the stream ID.
                """
                try:
                    if content_type in ("event-profiles", "shows"):
                        stream_id = get_stream_id_from_dynamic(
                            locale,
                            content_id
                        )

                        if stream_id:
                            return {
                                "locale": locale,
                                "stream_id": stream_id
                            }

                    else:
                        api_url = (
                            f"https://tv-api.redbull.com/products/v5.3/"
                            f"rbtv/en/{locale}/{content_id}"
                        )

                        response = session.get(api_url, timeout=5)

                        if response.ok:
                            json_data = response.json()

                            returned_id = json_data.get("id")

                            if returned_id:
                                return {
                                    "locale": locale,
                                    "stream_id": returned_id
                                }

                except Exception:
                    pass

                return None

            def scan_title_locale(locale, stream_id):
                """
                Preserve the original title/thumbnail locale scan.

                Metadata is always requested using the actual playable
                stream ID. This is important for event profiles and shows,
                where the page ID itself isn't the playable ID.
                """
                try:
                    api_url = (
                        f"https://tv-api.redbull.com/products/v5.3/"
                        f"rbtv/en/{locale}/{stream_id}"
                    )

                    response = session.get(api_url, timeout=5)

                    if response.ok:
                        json_data = response.json()

                        title = json_data.get("title")

                        video_thumbnail = (
                            json_data.get("media_resources", {})
                            .get("rbtv_display_art_landscape", {})
                            .get("url")
                        )

                        subheading_raw = json_data.get("subheading")

                        if title:
                            return {
                                "locale": locale,
                                "video_title_raw": title,
                                "video_thumbnail": video_thumbnail,
                                "subheading_raw": subheading_raw
                            }

                except Exception:
                    pass

                return None

            # ----------------------------------------------------------
            # Resolve the initial locale and stream ID.
            #
            # First try INT exactly like the previous implementation.
            # If that doesn't resolve, scan locale_list concurrently.
            # ----------------------------------------------------------

            locale = "US"
            stream_id = None

            if content_type in ("event-profiles", "shows"):
                # Dynamic endpoint first, using INT.
                stream_id = get_stream_id_from_dynamic(
                    "US",
                    content_id
                )

            else:
                # Directly playable content uses its own ID.
                stream_id = content_id

                # Verify that INT actually resolves the resource.
                try:
                    api_url = (
                        f"https://tv-api.redbull.com/products/v5.3/"
                        f"rbtv/en/US/{content_id}"
                    )

                    response = session.get(api_url, timeout=10)

                    if not response.ok:
                        stream_id = None

                except Exception:
                    stream_id = None

            # ----------------------------------------------------------
            # If INT is not available, detect regional version.
            # This preserves your original ThreadPoolExecutor scan.
            # ----------------------------------------------------------

            if not stream_id:
                with ThreadPoolExecutor(max_workers=16) as executor:
                    future_to_locale = {
                        executor.submit(
                            scan_stream_id_locale,
                            l,
                            content_id
                        ): l
                        for l in locale_list
                    }

                    for future in as_completed(future_to_locale):
                        result = future.result()

                        if result:
                            stream_id = result["stream_id"]
                            locale = result["locale"].upper()

                            executor.shutdown(
                                wait=False,
                                cancel_futures=True
                            )
                            break

            if not stream_id:
                raise ValueError(
                    f"Unable to resolve stream ID for [{content_id}]"
                )

            # ----------------------------------------------------------
            # Fetch manifest.
            #
            # device_group=group_5 is retained for event-profile/live
            # resolution, matching the current endpoint behavior.
            # ----------------------------------------------------------

            video_url_api = get_player_url(
                locale,
                stream_id
            )

            if content_type == "event-profiles":
                video_url_api += "?device_group=group_5"

            video_response = session.get(
                video_url_api,
                timeout=10
            )
            video_response.raise_for_status()

            video_json = video_response.json()
            video_url = video_json.get("manifest_url")

            # ----------------------------------------------------------
            # Fetch metadata for the actual playable stream.
            # ----------------------------------------------------------

            video_thumbnail = None
            video_title_raw = None
            subheading = None
            subheading_raw = None

            try:
                meta_url = (
                    f"https://tv-api.redbull.com/products/v5.3/"
                    f"rbtv/en/{locale}/{stream_id}"
                )

                response = session.get(
                    meta_url,
                    timeout=10
                )

                # If selected locale metadata is unavailable,
                # preserve the original locale scanning behavior.
                if not response.ok:
                    with ThreadPoolExecutor(max_workers=16) as executor:
                        future_to_locale = {
                            executor.submit(
                                scan_title_locale,
                                l,
                                stream_id
                            ): l
                            for l in locale_list
                        }

                        for future in as_completed(future_to_locale):
                            result = future.result()

                            if result:
                                locale = result["locale"].upper()
                                video_title_raw = result[
                                    "video_title_raw"
                                ]
                                video_thumbnail = result[
                                    "video_thumbnail"
                                ]
                                subheading_raw = result[
                                    "subheading_raw"
                                ]

                                executor.shutdown(
                                    wait=False,
                                    cancel_futures=True
                                )
                                break

                else:
                    meta_json = response.json()

                    video_title_raw = meta_json.get("title")

                    video_thumbnail = (
                        meta_json.get("media_resources", {})
                        .get("rbtv_display_art_landscape", {})
                        .get("url")
                    )

                    subheading_raw = meta_json.get("subheading")

                subheading = (
                    sanitize_video_title(subheading_raw)
                    if subheading_raw
                    else None
                )

            except Exception:
                log_error(
                    f"Unable to fetch metadata for [{content_id}], "
                    f"using randomized title",
                    remote_addr
                )

            title = (
                sanitize_video_title(video_title_raw)
                if video_title_raw
                else 'rbtv-' + ''.join(
                    random.choices(string.ascii_letters, k=8)
                )
            )

            return (
                title,
                video_url,
                video_thumbnail,
                stream_id,
                subheading,
                base_url
            )

        except Exception as e:
            log_info(
                f"Latest API lookup failed for [{content_id}], "
                f"falling back to legacy API: {e}",
                remote_addr
            )

    else:
        log_info(
            f"Falling back to legacy API for [{base_url}]",
            remote_addr
        )

    # ------------------------------------------------------------------
    # Legacy API -- unchanged
    # ------------------------------------------------------------------

    url = path.lstrip('/')
    category_raw = path.rstrip('/').split('/')[-2]

    category_map = {
        "live": "live-videos",
        "episodes": "episode-videos",
        "films": "films",
        "videos": "videos"
    }

    category = category_map.get(category_raw, category_raw)

    try:
        loc_res = session.get(
            "https://www.redbull.com/v3/config/pages?url=" + url,
            timeout=10
        )

        locales = (
            loc_res.json()
            .get("data", {})
            .get("domainConfig", {})
            .get("supportedLocales", [])
        )

        selected_locale = next(
            (l for l in locales if "en" in l.lower()),
            locales[0]
        )

        meta_url = (
            f"https://www.redbull.com/v3/api/graphql/v1/v3/feed/"
            f"{selected_locale}"
            f"?disableUsageRestrictions=true"
            f"&filter[type]={category}"
            f"&filter[uriSlug]={url.split('/')[-1]}"
            f"&rb3Schema=v1:pageConfig"
            f"&rb3PageUrl=/{url}"
        )

        json_data = session.get(
            meta_url,
            timeout=10
        ).json()

        video_id = json_data.get('data').get('id')

        video_thumbnail = (
            json_data.get('data')
            .get('pageMeta')
            .get('og:image')
        )

        video_url_api = (
            f"https://api-player.redbull.com/rbcom/videoresource"
            f"?videoId={video_id}"
            f"&localeMixing={selected_locale}"
        )

        json_data = session.get(
            video_url_api,
            timeout=10
        ).json()

        video_url = json_data.get('videoUrl')
        video_title_raw = json_data.get('title')

        title = (
            sanitize_video_title(video_title_raw)
            if video_title_raw
            else 'rbtv-' + ''.join(
                random.choices(string.ascii_letters, k=8)
            )
        )

        return (
            title,
            video_url,
            video_thumbnail,
            video_id.rsplit(':', 1)[0],
            None,
            base_url
        )

    except Exception as e:
        log_error(
            f"Unable to fetch metadata, {e}",
            remote_addr
        )

        return (
            None,
            None,
            None,
            None,
            None,
            base_url
        )


def get_video_duration(url, headers):
    try:
        probe = ffmpeg.probe(url, headers=headers)
        return float(probe['format']['duration'])
    except:
        return None

def monitor_progress(process, video_id, final_output_path, remote_addr):
    frag_line = re.compile(r"\(frag\s+(\d+)\s*/\s*(\d+)\)", re.IGNORECASE)
    prefix_regex = re.compile(r"Destination:\s+(.+)")
    
    total_fragments = 0
    completed_fragments = 0
    merging_started = False
    file_prefix = ""

    for line in iter(process.stdout.readline, ''):
        if not line:
            break
        line = line.strip()

        # Detect output filename
        if not file_prefix:
            m = prefix_regex.search(line)
            if m:
                file_prefix = os.path.basename(m.group(1))

        # Match "(frag 9/425)" style
        fm = frag_line.search(line)
        if fm:
            completed_fragments = int(fm.group(1))
            total_fragments = int(fm.group(2))

            if total_fragments > 0:
                ratio = completed_fragments / total_fragments
                percent = min(99.9, ratio * 95)

                task = get_task(video_id)
                if task and percent > task.get("percent", 0):
                    task["status"] = "converting"
                    task["percent"] = percent
                    set_task(video_id, task)

            continue

        # Detect merging
        if "[Merger]" in line or "Merging formats" in line:
            if not merging_started:
                merging_started = True
                task = get_task(video_id)
                if task:
                    task["status"] = "finalizing"
                    task["message"] = "merging tracks"
                    task["percent"] = 99.9
                    set_task(video_id, task)
                log_info(f"Merging tracks for [{video_id}]", remote_addr)

    return_code = process.wait()
    process.stdout.close()

    task = get_task(video_id)
    if not task:
        return

    if return_code == 0 and os.path.exists(final_output_path):
        task["percent"] = 100.0
        task["status"] = "completed"
        task["completed_at"] = datetime.now().timestamp()
        task["mp4_path"] = final_output_path
        task["pid"] = None
        log_info(f"Task [{video_id}] is completed", remote_addr)
    else:
        task["status"] = "failed"
        task["pid"] = None
        log_error(f"Task [{video_id}] failed (Code: {return_code})", remote_addr)

    set_task(video_id, task)


def run_purge_scheduler():
    while True:
        purge_expired_tasks()
        cleanup_orphaned_tasks()
        time.sleep(300)

threading.Thread(target=run_purge_scheduler, daemon=True).start()

@app.route('/api/create', methods=['POST'])
def create_stream():
    base_url = unquote(request.json.get('url'))
    if not base_url:
        return jsonify({"status": "error", "message": "Missing url"}), 400

    parsed = urlparse(base_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    base_url = base_url.rstrip('/')
    url_hash = xxhash.xxh64(base_url).hexdigest()
    cached_data = r.get(f"url_map:{url_hash}")
    
    if cached_data:
        metadata = json.loads(cached_data)
        video_title = metadata.get('title')
        video_url = metadata.get('url')
        video_thumbnail = metadata.get('thumbnail')
        video_id = metadata.get('video_id')
        video_subheading = metadata.get('subheading')
        log_info(f"Metadata Cache Hit for [{video_id}]", request.remote_addr)
    else:
        log_info(f"Fetching metadata for [{base_url}]", request.remote_addr)
        video_title, video_url, video_thumbnail, video_id, video_subheading, base_url = get_title_from_url(base_url, request.remote_addr)
        if video_title:
            metadata = {
                "title": video_title,
                "video_id": video_id,
                "url": video_url,
                "thumbnail": video_thumbnail,
                "subheading": video_subheading
            }
            r.set(f"url_map:{url_hash}", json.dumps(metadata), ex=86400)
    
    if not video_title or not video_url:
        log_error(f"Failed to extract M3U stream for [{base_url}]", request.remote_addr)
        return jsonify({"status": "error", "message": "Unable to retrieve video stream. The source may be unavailable or geo-restricted."}), 500
    
    log_info(f"M3U Stream found for [{base_url}], Video_ID=[{video_id}] Title=[{video_title}], Subheading=[{video_subheading}] Stream=[{video_url}], Thumbnail=[{video_thumbnail}]", request.remote_addr)

    task = get_task(video_id)
    if task:
        if task["status"] == "completed" and os.path.exists(task.get("mp4_path", "")):
            task["completed_at"] = datetime.now().timestamp()
            set_task(video_id, task)
            log_info(f"Task [{video_id}] finished. File verified at: [{task.get('mp4_path')}]", request.remote_addr)
            return jsonify({"status": task["status"], "title": video_title, "subheading": video_subheading, "stream": video_url, "thumbnail": video_thumbnail}), 200
        if task["status"] in ["converting", "finalizing"] and is_pid_alive(task.get("pid")):
            log_info(f"Task [{video_id}] is {task['status']} (PID: {task.get('pid')})", request.remote_addr)
            return jsonify({"status": task["status"], "title": video_title, "subheading": video_subheading, "stream": video_url, "thumbnail": video_thumbnail}), 200

    target_dir = os.path.join(tempfile.gettempdir(), "rbtv-dl")
    os.makedirs(target_dir, exist_ok=True)
    log_info(f"Conversion started for [{video_id}]", request.remote_addr)

    tmp_mp4 = tempfile.NamedTemporaryFile(delete=True, suffix='.mp4', dir=target_dir)
    mp4_path = tmp_mp4.name

    headers_str = (
        "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36\r\n"
        "Accept: */*\r\n"
        "Accept-Language: en-US,en;q=0.9\r\n"
        "Origin: https://www.redbull.com\r\n"
        f"Referer: {base_url}\r\n"
        "Sec-Fetch-Dest: empty\r\n"
        "Sec-Fetch-Mode: cors\r\n"
        "Sec-Fetch-Site: same-site\r\n"
        "sec-ch-ua: \"Chromium\";v=\"140\", \"Not=A?Brand\";v=\"24\", \"Google Chrome\";v=\"140\"\r\n"
        "sec-ch-ua-mobile: ?0\r\n"
        "sec-ch-ua-platform: \"Windows\"\r\n"
    )
    yt_headers = {line.split(": ", 1)[0]: line.split(": ", 1)[1].strip() for line in headers_str.strip().split("\r\n") if ": " in line}
    download_connections = int(os.getenv('DOWNLOAD_CONNECTIONS', 8))

    cmd = [
        'yt-dlp',
    ]

    aria_args = f"aria2c:-x {download_connections} -s {download_connections} -k 1M --summary-interval=1"

    if source_addr:
        cmd.extend(['--source-address', source_addr])
        aria_args += f" --interface={source_addr}"

    cmd.extend([
        '--external-downloader', 'aria2c',
        '--downloader-args', aria_args,
        '-o', mp4_path,
        '--remux-video', 'mp4',
        '--newline',
        '--progress'
    ])

    for key, value in yt_headers.items():
        cmd.extend(['--add-header', f"{key}:{value}"])
    
    cmd.append('--')
    cmd.append(video_url)

    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    
    set_task(video_id, {
        "title": video_title,
        "status": "converting",
        "mp4_path": mp4_path,
        "thumbnail": video_thumbnail,
        "percent": 0,
        "pid": process.pid
    })

    threading.Thread(target=monitor_progress, args=(process, video_id, mp4_path, request.remote_addr), daemon=True).start()

    return jsonify({"status": "created", "title": video_title, "subheading": video_subheading, "stream": video_url, "thumbnail": video_thumbnail}), 201

@app.route('/api/status', methods=['GET'])
def get_status():
    base_url = request.args.get('url')
    if not base_url:
        return jsonify({"status": "error", "message": "Missing url"}), 400
    
    parsed = urlparse(base_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    base_url = base_url.rstrip('/')
    url_hash = xxhash.xxh64(base_url).hexdigest()
    cached_data = r.get(f"url_map:{url_hash}")

    video_title, video_url, video_thumbnail, video_id = None, None, None, None

    if cached_data:
        metadata = json.loads(cached_data)
        video_title = metadata.get('title')
        video_url = metadata.get('url')
        video_thumbnail = metadata.get('thumbnail')
        video_id = metadata.get('video_id')
        video_subheading = metadata.get('subheading')
    else:
        video_title, video_url, video_thumbnail, video_id, video_subheading, base_url = get_title_from_url(base_url, request.remote_addr)
        if video_title:
            r.set(f"url_map:{url_hash}", json.dumps({
                "title": video_title, "video_id": video_id, "subheading": video_subheading, "url": video_url, "thumbnail": video_thumbnail
            }), ex=86400)

    task = get_task(video_id)
    
    if not task:
        return jsonify({"status": "inactive", "title": video_title or "unknown"}), 200

    status = task["status"]

    response_data = {
        "title": video_title,
        "subheading": video_subheading,
        "status": task["status"],
        "progression": round(float(task.get("percent", 0)), 1),
        "stream": video_url,
        "thumbnail": task.get("thumbnail") or video_thumbnail
    }

    target_path = task.get("mp4_path")
    if target_path and os.path.exists(target_path):
        response_data["current_size"] = os.path.getsize(target_path)

    message = task.get("message")
    if message:
        response_data["message"] = message

    return jsonify(response_data)

@app.route('/api/download', methods=['GET'])
def download_video():
    base_url = request.args.get('url')
    if not base_url:
        return jsonify({"status": "error", "message": "Missing url"}), 400
    
    parsed = urlparse(base_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    base_url = base_url.rstrip('/')
    url_hash = xxhash.xxh64(base_url).hexdigest()
    cached_data = r.get(f"url_map:{url_hash}")
    
    video_title = None
    video_subheading = None
    video_id = None

    if cached_data:
        metadata = json.loads(cached_data)
        video_title = metadata.get('title')
        video_id = metadata.get('video_id')
        video_subheading = metadata.get('subheading')
    else:
        video_title, _, _, video_id, video_subheading, base_url = get_title_from_url(base_url, request.remote_addr)
    
    if not video_title:
        return jsonify({"status": "error", "message": "Could not resolve video title"}), 500

    task = get_task(video_id)
    
    if task and task["status"] == "completed" and os.path.exists(task["mp4_path"]):
        task["completed_at"] = datetime.now().timestamp()
        set_task(video_id, task)
        log_info(f"Download started for [{video_id}]", request.remote_addr)
        if video_subheading:
            download_filename = video_title + " - " + video_subheading
        else:
            download_filename = video_title
        return send_file(task["mp4_path"], as_attachment=True, download_name=f"{download_filename}.mp4", mimetype='video/mp4')

    log_error(f"File not found for [{video_id}]", request.remote_addr)
    return jsonify({"status": "error", "message": "File not found"}), 404

@app.errorhandler(400)
def bad_request(error):
    return jsonify({"status": "error", "message": "Bad request"}), 400

@app.errorhandler(404)
def endpoint_not_found(error):
    return jsonify({"status": "error", "message": "Endpoint not found"}), 404

@app.errorhandler(500)
def internal_server_error(error):
    return jsonify({"status": "error", "message": "Internal server error"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
