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
from urllib.parse import urlparse
from flask import Flask, request, jsonify, send_file
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv
import hashlib

load_dotenv()
redis_host = os.getenv('REDIS_HOST', 'localhost')
redis_port = int(os.getenv('REDIS_PORT', 6379))
redis_db = int(os.getenv('REDIS_DB', 0))
retention_period = int(os.getenv('RETENTION_PERIOD', 21600))
r = redis.Redis(host=redis_host, port=redis_port, db=redis_db, decode_responses=True)

def create_app():
    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    return app

app = create_app()
active_processes = {}

def get_url_hash(url):
    return hashlib.md5(url.encode()).hexdigest()

def log_error(message, remote_addr="RBTV-DL"):
    timestamp = datetime.now().strftime('[%d/%b/%Y %H:%M:%S]')
    sys.stderr.write(f"\033[31m{timestamp} {remote_addr} \"ERROR: {message}\"\033[0m\n")

def log_info(message, remote_addr="RBTV-DL"):
    timestamp = datetime.now().strftime('[%d/%b/%Y %H:%M:%S]')
    print(f"{timestamp} {remote_addr} \"INFO: {message}\"", flush=True)

def set_task(title, data):
    r.set(f"task:{title}", json.dumps(data), ex=retention_period)

def get_task(title):
    data = r.get(f"task:{title}")
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
    video_title = unicodedata.normalize("NFKD", video_title)
    video_title = video_title.replace("–", "-").encode("ascii", "ignore").decode("ascii")
    video_title = re.sub(r'[^A-Za-z0-9._\- ]', '_', video_title)
    video_title = re.sub(r'_+', '_', video_title).strip("._")
    return video_title[:150]

def get_title_from_url(base_url, remote_addr):
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
    try:
        loc_res = session.get("https://www.redbull.com/v3/config/pages?url=" + url, timeout=10)
        locales = loc_res.json().get("data", {}).get("domainConfig", {}).get("supportedLocales", [])
        selected_locale = next((l for l in locales if "en" in l.lower()), locales[0])
        meta_url = f"https://www.redbull.com/v3/api/graphql/v1/v3/feed/{selected_locale}?disableUsageRestrictions=true&filter[type]={category}&filter[uriSlug]={url.split('/')[-1]}&rb3Schema=v1:pageConfig&rb3PageUrl=/{url}"
        json_data = session.get(meta_url, timeout=10).json()
        video_id = json_data.get('data').get('id')
        video_thumbnail = json_data.get('data').get('pageMeta').get('og:image')
        video_url_api = f"https://api-player.redbull.com/rbcom/videoresource?videoId={video_id}&localeMixing={selected_locale}"
        json_data = session.get(video_url_api, timeout=10).json()
        video_url = json_data.get('videoUrl')
        video_title_raw = json_data.get('title')
        title = sanitize_video_title(video_title_raw) if video_title_raw else 'rbtv-' + ''.join(random.choices(string.ascii_letters, k=8))
        return title, video_url, video_thumbnail
    except Exception as e:
        log_error(f"Unable to fetch metadata, {e}", remote_addr)
        return None, None, None

def get_video_duration(url, headers):
    try:
        probe = ffmpeg.probe(url, headers=headers)
        return float(probe['format']['duration'])
    except:
        return None

def monitor_progress(process, video_title, final_output_path, remote_addr):
    total_frag_regex = re.compile(r"Total fragments:\s+(\d+)")
    prefix_regex = re.compile(r"Destination: .*/(tmp[^.]+)")
    
    total_fragments = 0
    temp_dir = os.path.join(tempfile.gettempdir(), "rbtv-dl")
    file_prefix = ""
    stream_count = 0
    merging_started = False

    for line in iter(process.stdout.readline, ''):
        if not line: break
        line = line.strip()

        if not file_prefix:
            pf = prefix_regex.search(line)
            if pf: 
                file_prefix = pf.group(1)

        tm = total_frag_regex.search(line)
        if tm:
            new_total = int(tm.group(1))
            if new_total > 0:
                total_fragments = new_total
                stream_count += 1
            continue
            
        if total_fragments > 0 and file_prefix and not merging_started:
            try:
                all_files = os.listdir(temp_dir)
                completed_count = len([
                    f for f in all_files 
                    if f.startswith(file_prefix) 
                    and "-Frag" in f 
                    and not f.endswith(".aria2")
                ])

                if total_fragments == 0:
                    continue
                    
                ratio = completed_count / total_fragments

                if stream_count <= 1:
                    total_percent = ratio * 90
                    if ratio >= 1.0:
                        total_percent = 99.9
                else:
                    total_percent = 90 + (ratio * 9)

                task = get_task(video_title)
                if task:
                    if total_percent >= 99.9:
                        task["status"] = "finalizing"
                        task["message"] = "consolidating"
                        task["percent"] = 99.9
                        set_task(video_title, task)
                    elif total_percent > task.get("percent", 0):
                        task["status"] = "converting"
                        task["percent"] = total_percent
                        set_task(video_title, task)

            except Exception:
                pass

        if "[Merger]" in line or "Merging formats" in line:
            if not merging_started:
                merging_started = True
                task = get_task(video_title)
                if task:
                    task["status"] = "finalizing"
                    task["message"] = "merging tracks"
                    task["percent"] = 99.9
                    set_task(video_title, task)
                log_info(f"Merging tracks for '{video_title}'", remote_addr)

    return_code = process.wait()
    process.stdout.close()

    task = get_task(video_title)
    if not task:
        return

    if return_code == 0 and os.path.exists(final_output_path):
        task["percent"] = 100.0
        task["status"] = "completed"
        task["completed_at"] = datetime.now().timestamp()
        task["mp4_path"] = final_output_path
        task["pid"] = None
        log_info(f"Task '{video_title}' is completed", remote_addr)
    else:
        task["status"] = "failed"
        task["pid"] = None
        if return_code in [-2, -15, 130]:
            log_error(f"Task '{video_title}' was interrupted", remote_addr)
        else:
            log_error(f"Task '{video_title}' failed (Code: {return_code})", remote_addr)

    set_task(video_title, task)

def run_purge_scheduler():
    while True:
        purge_expired_tasks()
        cleanup_orphaned_tasks()
        time.sleep(300)

threading.Thread(target=run_purge_scheduler, daemon=True).start()

@app.route('/api/create', methods=['POST'])
def create_stream():
    base_url = request.args.get('url')
    if not base_url:
        return jsonify({"status": "error", "message": "Missing url"}), 400

    url_hash = get_url_hash(base_url)
    cached_data = r.get(f"url_map:{url_hash}")
    
    if cached_data:
        metadata = json.loads(cached_data)
        video_title = metadata['title']
        video_url = metadata['url']
        video_thumbnail = metadata['thumbnail']
        log_info(f"Metadata Cache Hit for {video_title}", request.remote_addr)
    else:
        log_info(f"Fetching metadata for {base_url}", request.remote_addr)
        video_title, video_url, video_thumbnail = get_title_from_url(base_url, request.remote_addr)
        if video_title:
            metadata = {
                "title": video_title,
                "url": video_url,
                "thumbnail": video_thumbnail
            }
            r.set(f"url_map:{url_hash}", json.dumps(metadata), ex=86400)

    log_info(f"M3U Stream found for {base_url}, Title={video_title}, Stream={video_url}, Thumbnail={video_thumbnail}", request.remote_addr)
    
    if not video_title or not video_url:
        return jsonify({"status": "error", "message": "Could not fetch video data"}), 500

    task = get_task(video_title)
    if task:
        if task["status"] == "completed" and os.path.exists(task.get("mp4_path", "")):
            task["completed_at"] = datetime.now().timestamp()
            set_task(video_title, task)
            log_info(f"Task '{video_title}' finished. File verified at: {task.get('mp4_path')}", request.remote_addr)
            return jsonify({"status": task["status"], "title": video_title, "stream": video_url, "thumbnail": video_thumbnail}), 200
        if task["status"] in ["converting", "finalizing"] and is_pid_alive(task.get("pid")):
            log_info(f"Task '{video_title}' is {task['status']} (PID: {task.get('pid')})", request.remote_addr)
            return jsonify({"status": task["status"], "title": video_title, "stream": video_url, "thumbnail": video_thumbnail}), 200

    target_dir = os.path.join(tempfile.gettempdir(), "rbtv-dl")
    os.makedirs(target_dir, exist_ok=True)
    log_info(f"Conversion started for {video_title}", request.remote_addr)

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
        '--external-downloader', 'aria2c',
        '--downloader-args', f"aria2c:-x {download_connections} -s {download_connections} -k 1M --summary-interval=1",
        '-o', mp4_path,
        '--remux-video', 'mp4',
        '--newline',
        '--progress',
        video_url
    ]
    for key, value in yt_headers.items():
        cmd.extend(['--add-header', f"{key}:{value}"])

    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

    set_task(video_title, {
        "title": video_title,
        "status": "converting",
        "mp4_path": mp4_path,
        "thumbnail": video_thumbnail,
        "percent": 0,
        "pid": process.pid
    })

    active_processes[video_title] = process

    threading.Thread(target=monitor_progress, args=(process, video_title, mp4_path, request.remote_addr), daemon=True).start()

    return jsonify({"status": "created", "title": video_title, "stream": video_url, "thumbnail": video_thumbnail}), 201

@app.route('/api/status', methods=['GET'])
def get_status():
    base_url = request.args.get('url')
    if not base_url:
        return jsonify({"status": "error", "message": "Missing url"}), 400

    url_hash = get_url_hash(base_url)
    cached_data = r.get(f"url_map:{url_hash}")

    video_title, video_url, video_thumbnail = None, None, None

    if cached_data:
        metadata = json.loads(cached_data)
        video_title = metadata.get('title')
        video_url = metadata.get('url')
        video_thumbnail = metadata.get('thumbnail')
    else:
        video_title, video_url, video_thumbnail = get_title_from_url(base_url, request.remote_addr)
        if video_title:
            r.set(f"url_map:{url_hash}", json.dumps({
                "title": video_title, "url": video_url, "thumbnail": video_thumbnail
            }), ex=86400)

    task = get_task(video_title)
    
    if not task:
        return jsonify({"status": "inactive", "title": video_title or "unknown"}), 200

    status = task["status"]

    response_data = {
        "title": video_title,
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
    
    url_hash = get_url_hash(base_url)
    cached_data = r.get(f"url_map:{url_hash}")
    
    video_title = None

    if cached_data:
        metadata = json.loads(cached_data)
        video_title = metadata.get('title')
    else:
        video_title, _, _ = get_title_from_url(base_url, request.remote_addr)
    
    if not video_title:
        return jsonify({"status": "error", "message": "Could not resolve video title"}), 500

    task = get_task(video_title)
    
    if task and task["status"] == "completed" and os.path.exists(task["mp4_path"]):
        task["completed_at"] = datetime.now().timestamp()
        set_task(video_title, task)
        log_info(f"Download started for {video_title}", request.remote_addr)
        return send_file(task["mp4_path"], as_attachment=True, download_name=f"{video_title}.mp4", mimetype='video/mp4')

    log_error(f"File not found for {video_title}", request.remote_addr)
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