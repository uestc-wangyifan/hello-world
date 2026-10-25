#!/usr/bin/env python3
# Fast Douyin public-video downloader.
# Usage: python scripts/douyin_fast.py "https://v.douyin.com/..." --mode fast
#        python scripts/douyin_fast.py "https://v.douyin.com/..." --mode quality
import argparse
import html
import json
import re
import sys
import time
from pathlib import Path

import requests
from mutagen.mp4 import MP4

APP_UA = "com.ss.android.ugc.aweme/310600 (Linux; U; Android 13; zh_CN; Pixel 6; Build/TP1A.220624.014; Cronet/TTNetVersion:fb4d219f)"
WEB_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
VIDEO_ID = re.compile(r"(?:/video/|/share/video/|aweme_id=)(\d{18,20})")

def session():
    s = requests.Session()
    s.headers.update({"User-Agent": APP_UA, "Accept": "*/*"})
    return s

def extract_id(url, client):
    match = VIDEO_ID.search(url)
    if match:
        return match.group(1), 0.0
    t0 = time.perf_counter()
    with client.get(url, headers={"User-Agent": WEB_UA}, stream=True, timeout=(6, 12)) as resp:
        resp.raise_for_status()
        # Short URL generally redirects twice; no body download required.
        destinations = [resp.url] + [x.headers.get("location", "") for x in resp.history]
        for link in destinations:
            match = VIDEO_ID.search(link)
            if match:
                return match.group(1), time.perf_counter() - t0
    raise ValueError("Could not resolve a video ID from the share link")

def find_aweme(tree, video_id):
    # Iterative search: avoids assuming an exact frontend JSON nesting/version.
    stack = [tree]
    for _ in range(6000):
        if not stack:
            break
        obj = stack.pop()
        if isinstance(obj, dict):
            if str(obj.get("aweme_id") or obj.get("item_id") or "") == video_id and isinstance(obj.get("video"), dict):
                return obj
            stack.extend(x for x in obj.values() if isinstance(x, (dict, list)))
        elif isinstance(obj, list):
            stack.extend(x for x in obj if isinstance(x, (dict, list)))
    return None

def resolve_native(video_id, client):
    t0 = time.perf_counter()
    url = f"https://www.iesdouyin.com/share/video/{video_id}/"
    with client.get(url, headers={"User-Agent": APP_UA}, timeout=(6, 12)) as resp:
        resp.raise_for_status()
        text = resp.text
    marker = re.search(r"window\._ROUTER_DATA\s*=\s*", text)
    if not marker:
        raise RuntimeError("Share page did not provide embedded video information")
    payload, _ = json.JSONDecoder().raw_decode(text, marker.end())
    item = find_aweme(payload, video_id)
    if item is None:
        raise RuntimeError("Share page did not include the requested video ID")
    video = item["video"]
    author = item.get("author") or {}
    links = []
    variants = [video] + sorted(
        (x for x in video.get("bit_rate", []) if isinstance(x, dict)),
        key=lambda x: x.get("bit_rate", 0) or 0, reverse=True)
    for variant in variants:
        for name in ("play_addr", "play_addr_265", "download_addr"):
            data = variant.get(name) or {}
            for link in data.get("url_list", []) if isinstance(data, dict) else []:
                if isinstance(link, str) and link.startswith("http") and link not in links:
                    links.append(html.unescape(link))
    if not links:
        raise RuntimeError("The matching video had no public playable URL")
    return {
        "provider": "native-share",
        "id": video_id,
        "title": item.get("desc", ""),
        "author": author.get("nickname", "") if isinstance(author, dict) else "",
        "links": links,
        "resolution_seconds": round(time.perf_counter() - t0, 3),
    }

def resolve_third_party(video_id, client):
    t0 = time.perf_counter()
    # Prefer canonical ID form, avoiding additional short-link redirects on API side.
    r = client.get(
        "https://api.bugpk.com/api/douyin",
        params={"url": f"https://www.douyin.com/video/{video_id}"},
        headers={"User-Agent": WEB_UA}, timeout=(6, 13))
    r.raise_for_status()
    payload = r.json()
    item = payload.get("data") or {}
    if payload.get("code") != 200 or not isinstance(item, dict):
        raise RuntimeError("Third-party resolver did not return a successful result")
    returned_id = str(item.get("id") or item.get("aweme_id") or item.get("item_id") or "")
    if returned_id and returned_id != video_id:
        raise RuntimeError(f"Resolved a DIFFERENT video ID: {returned_id}")
    urls = [item.get("url"), item.get("video_url")]
    links = [x for x in urls if isinstance(x, str) and x.startswith("http")]
    if not links:
        raise RuntimeError("Third-party resolver returned no video URL")
    author = item.get("author") or {}
    return {
        "provider": "third-party",
        "id": video_id,
        "title": item.get("title") or item.get("desc") or "",
        "author": author.get("name", "") if isinstance(author, dict) else "",
        "links": list(dict.fromkeys(links)),
        "resolution_seconds": round(time.perf_counter() - t0, 3),
    }

def download_file(video, client, out_dir):
    out_dir.mkdir(exist_ok=True, parents=True)
    out = out_dir / f"douyin_{video['id']}.mp4"
    part = out.with_suffix(".part")
    t0 = time.perf_counter()
    for index, link in enumerate(video["links"], 1):
        try:
            part.unlink(missing_ok=True)
            with client.get(link, stream=True, timeout=(8, 35),
                            headers={"User-Agent": APP_UA,
                                     "Referer": "https://www.iesdouyin.com/"}) as resp:
                resp.raise_for_status()
                media_type = resp.headers.get("Content-Type", "")
                if "text/html" in media_type:
                    raise RuntimeError("CDN returned a web page, not a video")
                expected = int(resp.headers.get("Content-Length", "0") or 0)
                total = 0
                with part.open("wb") as f:
                    for chunk in resp.iter_content(chunk_size=1024*1024):
                        if chunk:
                            f.write(chunk)
                            total += len(chunk)
            if total < 50_000 or (expected and total != expected):
                raise RuntimeError("Incomplete or empty video content")
            with part.open("rb") as f:
                header = f.read(16)
            if len(header) < 12 or header[4:8] != b"ftyp":
                raise RuntimeError("Missing MP4 file signature")
            metadata = MP4(part)
            duration = round(metadata.info.length, 2)
            if duration <= 0:
                raise RuntimeError("Invalid MP4 duration")
            part.replace(out)
            info = {"path": str(out), "size_bytes": total, "duration_seconds": duration,
                    "download_seconds": round(time.perf_counter()-t0, 3),
                    "estimated_mbps": round(total*8/1e6/max(time.perf_counter()-t0,0.001),2),
                    "provider": video["provider"], "author": video["author"],
                    "title": video["title"], "resolution_seconds": video["resolution_seconds"]}
            return info
        except (requests.RequestException, ValueError, OSError, RuntimeError) as e:
            print(f"CDN try #{index} failed: {type(e).__name__}: {e}", flush=True)
            part.unlink(missing_ok=True)
    raise RuntimeError("All candidate video CDN addresses failed")

def main():
    parser = argparse.ArgumentParser(description="Download a public Douyin video as verified MP4")
    parser.add_argument("url", help="Douyin share, canonical, or short URL")
    parser.add_argument("--id", default=None, help="Known 19-digit ID (skip short-link lookup)")
    parser.add_argument("--mode", choices=("fast", "quality"), default="fast",
                        help="fast: third-party first; quality: native share first")
    parser.add_argument("--output", default="output-fast", help="Destination directory")
    parser.add_argument("--expect-title", default="", help="Required title substring for correct-video verification")
    parser.add_argument("--expect-author", default="", help="Required author substring for correct-video verification")
    args = parser.parse_args()
    t0 = time.perf_counter()
    client = session()
    try:
        video_id, redirect_s = (args.id, 0.0) if args.id else extract_id(args.url, client)
        if not re.fullmatch(r"\d{18,20}", video_id):
            raise ValueError("Invalid Douyin video ID")
        ordered = [resolve_third_party, resolve_native] if args.mode == "fast" else [resolve_native, resolve_third_party]
        failures = []
        for resolver in ordered:
            try:
                video = resolver(video_id, client)
                if args.expect_title and args.expect_title not in video["title"]:
                    raise RuntimeError(f"Video title mismatch: {video['title']!r}")
                if args.expect_author and args.expect_author not in video["author"]:
                    raise RuntimeError(f"Video author mismatch: {video['author']!r}")
                print(f"RESOLVED {video['provider']} in {video['resolution_seconds']}s, "
                      f"{len(video['links'])} CDN candidate(s), "
                      f"author={video['author']} title={video['title'][:60]}", flush=True)
                result = download_file(video, client, Path(args.output))
                result.update({"mode": args.mode, "video_id": video_id,
                               "shortlink_seconds": round(redirect_s, 3),
                               "total_seconds": round(time.perf_counter()-t0, 3)})
                print("RESULT_JSON " + json.dumps(result, ensure_ascii=False), flush=True)
                return 0
            except (requests.RequestException, ValueError, OSError, RuntimeError, json.JSONDecodeError) as exc:
                failures.append(f"{resolver.__name__}: {type(exc).__name__}: {exc}")
                print("FALLBACK", failures[-1], flush=True)
        print("FAILED " + " | ".join(failures), flush=True)
        return 1
    finally:
        client.close()

if __name__ == "__main__":
    sys.exit(main())
