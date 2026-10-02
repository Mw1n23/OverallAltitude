"""Download the course tracks of the race manifest from their sources.

The GPX files are third-party material and are not part of the repository. Each course
in ``RawMaterial/races/races.json`` names where its track comes from; this script puts
the files next to the manifest so that ``overall-altitude-races`` can analyse them.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import io
import re
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Sequence

from .race_comparison import DEFAULT_MANIFEST, CourseSpec, load_manifest


USER_AGENT = "Mozilla/5.0 (compatible; OverallAltitude/2.0; +https://github.com/Mw1n23/OverallAltitude)"
TIMEOUT_SECONDS = 60.0
PAUSE_SECONDS = 1.5
HDSPORTS_ATTEMPTS = 3
HDSPORTS_TOKEN_PATTERN = re.compile(r'"csrf\.token":"([0-9a-f]{32})"')


class TrackDownloadError(RuntimeError):
    """Raised when a course track cannot be downloaded from its source."""


def read_url(
    opener: urllib.request.OpenerDirector,
    url: str,
    referer: str | None = None,
) -> bytes:
    headers = {"User-Agent": USER_AGENT}
    if referer is not None:
        headers["Referer"] = referer
    request = urllib.request.Request(url, headers=headers)
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise TrackDownloadError(f"Cannot download {url}: {exc}") from exc


def looks_like_gpx(payload: bytes) -> bool:
    return b"<gpx" in payload[:2000]


def fetch_file(opener: urllib.request.OpenerDirector, url: str) -> bytes:
    payload = read_url(opener, url)
    if not looks_like_gpx(payload):
        raise TrackDownloadError(f"{url} did not return a GPX file.")
    return payload


def fetch_zip(opener: urllib.request.OpenerDirector, url: str) -> bytes:
    """GPX file from a zip archive that contains exactly one."""
    with zipfile.ZipFile(io.BytesIO(read_url(opener, url))) as archive:
        members = [name for name in archive.namelist() if name.lower().endswith(".gpx")]
        if len(members) != 1:
            raise TrackDownloadError(f"{url} holds {len(members)} GPX files, expected one.")
        return archive.read(members[0])


def fetch_hdsports(opener: urllib.request.OpenerDirector, page_url: str) -> bytes:
    """Guest download from a track page of hdsports.org.

    The download link carries the form token of the page. The page can come from a cache
    with an outdated token; the site then answers "please try again" and the next page
    load carries the token of the session.
    """
    for _ in range(HDSPORTS_ATTEMPTS):
        page = read_url(opener, page_url).decode("utf-8", errors="replace")
        token = HDSPORTS_TOKEN_PATTERN.search(page)
        if token is None:
            raise TrackDownloadError(f"No download token found on {page_url}.")
        time.sleep(PAUSE_SECONDS)
        payload = read_url(
            opener,
            f"{page_url}?task=track.download&format=gpx&{token.group(1)}=1",
            referer=page_url,
        )
        if looks_like_gpx(payload):
            return payload
        time.sleep(PAUSE_SECONDS)
    raise TrackDownloadError(f"{page_url} did not return a GPX file.")


FETCHERS = {"file": fetch_file, "zip": fetch_zip, "hdsports": fetch_hdsports}


def fetch_tracks(specs: Sequence[CourseSpec], overwrite: bool = False) -> list[tuple[Path, str]]:
    """Download every missing track once; returns the files with what happened to them."""
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )
    results: list[tuple[Path, str]] = []
    handled: set[Path] = set()
    for spec in specs:
        if spec.gpx_file in handled:
            continue
        handled.add(spec.gpx_file)
        if spec.download_type is None or spec.download_url is None:
            if not spec.gpx_file.exists():
                raise FileNotFoundError(
                    f"Course {spec.course_id}: {spec.gpx_file} is missing and has no download source."
                )
            results.append((spec.gpx_file, "part of the repository"))
            continue
        if spec.gpx_file.exists() and not overwrite:
            results.append((spec.gpx_file, "already there"))
            continue
        payload = FETCHERS[spec.download_type](opener, spec.download_url)
        spec.gpx_file.parent.mkdir(parents=True, exist_ok=True)
        spec.gpx_file.write_bytes(payload)
        results.append((spec.gpx_file, "downloaded"))
        time.sleep(PAUSE_SECONDS)
    return results


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download the course tracks listed in the race manifest from their sources."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help=f"Course manifest (default: {DEFAULT_MANIFEST}).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Download again even if a track file is already there.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    for gpx_file, status in fetch_tracks(load_manifest(args.manifest), overwrite=args.overwrite):
        print(f"{gpx_file.name}: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
