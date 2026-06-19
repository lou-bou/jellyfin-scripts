#!/usr/bin/env python3
"""
Jellyfin Arabic Subtitle Auto-Downloader + Syncer
---------------------------------------------------
For every movie in the Jellyfin library:
  1. Search remote (OpenSubtitles) subtitles in Arabic
  2. Pick the result with the most downloads
  3. Download it via Jellyfin's API (overwrites any existing Arabic sub)
  4. Run ffsubsync on it against the movie file to fix timing

Requirements:
  requests
  ffsubsync

Usage:
  save the script in the directory of your jellyfin media files
  python3 sync_arabic_subs.py
"""

import requests
import subprocess
import time
import sys
import json
import os

# ---- CONFIG ----
JELLYFIN_URL = "http://192.168.100.19:8096" # or your jellyfin url
API_KEY = "XXXXXXXXXX" # from jellyfin dashboard then "API Keys" under advanced
LANGUAGE = "ara"          # ISO 639-2 code Jellyfin/OpenSubtitles expects for Arabic
DELAY_BETWEEN_DOWNLOADS = 5  # seconds, be nice to the API / rate limit
MAX_DOWNLOADS_PER_RUN = 18   # stay under the 20/day free OpenSubtitles cap

# Tracks which movies have been successfully synced so they're skipped on future runs.
# Delete this file (or remove entries from it) to re-process movies.
TRACKING_FILE = "./synced_movies.json" # update the path as needed

# Jellyfin is running on docker, confirm the correct paths with:
#   docker inspect jellyfin | grep -B1 -A3 '"Destination"'
PATH_MAP = {
    "/data/movies": "./movies",
    "/data/tv": "./tv",
    "/data/music": "./music",
}

HEADERS = {
    "Authorization": f'MediaBrowser Token="{API_KEY}"'
}


def translate_path(jellyfin_path):
    """Convert a Jellyfin container path to the real path on this host."""
    for container_prefix, host_prefix in PATH_MAP.items():
        if jellyfin_path.startswith(container_prefix):
            return host_prefix + jellyfin_path[len(container_prefix):]
    return jellyfin_path


def load_synced():
    """Load the set of already-synced item IDs from the tracking file."""
    if not os.path.exists(TRACKING_FILE):
        return set()
    with open(TRACKING_FILE, "r") as f:
        return set(json.load(f))


def save_synced(synced):
    """Persist the set of synced item IDs to the tracking file."""
    with open(TRACKING_FILE, "w") as f:
        json.dump(list(synced), f, indent=2)


def get_all_movies():
    """Fetch every Movie item from the Jellyfin library, including its file path."""
    url = f"{JELLYFIN_URL}/Items"
    params = {
        "IncludeItemTypes": "Movie",
        "Recursive": "true",
        "Fields": "Path,MediaSources",
    }
    resp = requests.get(url, headers=HEADERS, params=params)
    resp.raise_for_status()
    return resp.json().get("Items", [])


def search_subtitles(item_id):
    """Search remote subtitles for a given item in the configured language."""
    url = f"{JELLYFIN_URL}/Items/{item_id}/RemoteSearch/Subtitles/{LANGUAGE}"
    resp = requests.get(url, headers=HEADERS)
    resp.raise_for_status()
    return resp.json()


def download_subtitle(item_id, subtitle_id):
    """Tell Jellyfin to download a specific remote subtitle result."""
    url = f"{JELLYFIN_URL}/Items/{item_id}/RemoteSearch/Subtitles/{subtitle_id}"
    resp = requests.post(url, headers=HEADERS)
    resp.raise_for_status()


def find_arabic_srt_path(video_path):
    """
    Guess the .srt path Jellyfin will have saved next to the video.
    Jellyfin typically names it: <video_basename>.ara.srt
    """
    if video_path.lower().endswith((".mkv", ".mp4", ".avi", ".m4v", ".ts")):
        base = video_path.rsplit(".", 1)[0]
    else:
        base = video_path
    return f"{base}.ara.srt"


def run_ffsubsync(video_path, srt_path):
    """Run ffsubsync to fix timing, overwriting the same srt file."""
    print(f"    -> running ffsubsync on {srt_path}")
    try:
        result = subprocess.run(
            ["ffsubsync", video_path, "-i", srt_path, "-o", srt_path],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            print(f"    !! ffsubsync failed: {result.stderr.strip()[-500:]}")
        else:
            print("    -> ffsubsync done")
    except subprocess.TimeoutExpired:
        print("    !! ffsubsync timed out, skipping")
    except FileNotFoundError:
        print("    !! ffsubsync not found on PATH. Activate it and retry.")
        sys.exit(1)


def main():
    if API_KEY == "PUT_YOUR_API_KEY_HERE":
        print("Set your Jellyfin API key in the script first.")
        sys.exit(1)

    movies = get_all_movies()
    print(f"Found {len(movies)} movies in Jellyfin library.\n")

    synced = load_synced()
    print(f"Already synced: {len(synced)} movies (skipping these).\n")

    downloads_done = 0

    for movie in movies:
        if downloads_done >= MAX_DOWNLOADS_PER_RUN:
            print(f"\nHit daily cap of {MAX_DOWNLOADS_PER_RUN} downloads. Stopping early.")
            print("Run the script again tomorrow to continue with the rest.")
            break

        item_id = movie["Id"]
        name = movie.get("Name", "Unknown")

        if item_id in synced:
            print(f"[skip] {name}: already synced")
            continue

        media_sources = movie.get("MediaSources", [])
        raw_path = media_sources[0]["Path"] if media_sources else movie.get("Path")

        if not raw_path:
            print(f"[skip] {name}: no file path found")
            continue

        video_path = translate_path(raw_path)

        print(f"[{name}]")

        try:
            results = search_subtitles(item_id)
        except requests.HTTPError as e:
            print(f"    !! search failed: {e}")
            continue

        if not results:
            print("    -> no Arabic subtitles found, skipping")
            continue

        # Pick the result with the most downloads
        best = max(results, key=lambda r: r.get("DownloadCount", 0))
        subtitle_id = best.get("Id")
        download_count = best.get("DownloadCount", 0)
        print(f"    -> best match: {download_count} downloads")

        try:
            download_subtitle(item_id, subtitle_id)
        except requests.HTTPError as e:
            print(f"    !! download failed: {e}")
            continue

        downloads_done += 1

        # give Jellyfin a moment to write the file to disk
        time.sleep(2)

        srt_path = find_arabic_srt_path(video_path)
        run_ffsubsync(video_path, srt_path)

        synced.add(item_id)
        save_synced(synced)

        print(f"    -> waiting {DELAY_BETWEEN_DOWNLOADS}s before next download\n")
        time.sleep(DELAY_BETWEEN_DOWNLOADS)

    print("\nDone.")


if __name__ == "__main__":
    main()
