"""Elevation lookup from the open "Terrain Tiles" dataset (Mapzen/Tilezen on AWS).

The tiles are Web-Mercator PNGs in "terrarium" encoding. At zoom 14 they carry the
best open terrain model per region (e.g. 10 m lidar DTM in Austria, 2 m lidar in
England, 10 m 3DEP in the USA), which is far less noisy along roads than the 25-30 m
surface models served by the public OpenTopoData endpoint.

Only the standard library is required. Pillow, which comes with the ``plot`` extra,
decodes the tiles about twenty times faster; without it a pure-Python decoder is used.
"""

from __future__ import annotations

import http.client
import io
import math
import os
import struct
import tempfile
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Sequence

from .track_types import ElevationServiceError, TrackPoint

try:
    from PIL import Image
except ImportError:
    Image = None


TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{zoom}/{x}/{y}.png"
TILE_SIZE = 256
DEFAULT_ZOOM = 14
DOWNLOAD_WORKERS = 8
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# Errors of a broken or unsupported tile file, from either decoder.
TILE_DECODE_ERRORS = (ValueError, OSError, struct.error, zlib.error)


def _paeth(left: int, up: int, up_left: int) -> int:
    estimate = left + up - up_left
    distance_left = abs(estimate - left)
    distance_up = abs(estimate - up)
    distance_up_left = abs(estimate - up_left)
    if distance_left <= distance_up and distance_left <= distance_up_left:
        return left
    if distance_up <= distance_up_left:
        return up
    return up_left


def decode_rgb_png(payload: bytes) -> tuple[int, int, bytes]:
    """Decode a non-interlaced 8-bit RGB or RGBA PNG into packed RGB bytes."""
    if payload[:8] != PNG_SIGNATURE:
        raise ValueError("Not a PNG file.")

    width = height = channels = None
    compressed = bytearray()
    offset = 8
    while offset < len(payload):
        length, chunk_type = struct.unpack(">I4s", payload[offset : offset + 8])
        body = payload[offset + 8 : offset + 8 + length]
        offset += 12 + length
        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, _, _, interlace = struct.unpack(">IIBBBBB", body)
            if bit_depth != 8 or color_type not in (2, 6) or interlace != 0:
                raise ValueError(
                    f"Unsupported PNG layout (bit depth {bit_depth}, color type {color_type}, "
                    f"interlace {interlace})."
                )
            channels = 3 if color_type == 2 else 4
        elif chunk_type == b"IDAT":
            compressed.extend(body)
        elif chunk_type == b"IEND":
            break

    if width is None or height is None or channels is None:
        raise ValueError("PNG file has no IHDR chunk.")

    raw = zlib.decompress(bytes(compressed))
    stride = width * channels
    if len(raw) != height * (stride + 1):
        raise ValueError("PNG image data has an unexpected size.")

    rows = bytearray(height * stride)
    previous = bytes(stride)
    for row_index in range(height):
        start = row_index * (stride + 1)
        filter_type = raw[start]
        line = bytearray(raw[start + 1 : start + 1 + stride])
        if filter_type == 1:
            for index in range(channels, stride):
                line[index] = (line[index] + line[index - channels]) & 0xFF
        elif filter_type == 2:
            for index in range(stride):
                line[index] = (line[index] + previous[index]) & 0xFF
        elif filter_type == 3:
            for index in range(stride):
                left = line[index - channels] if index >= channels else 0
                line[index] = (line[index] + ((left + previous[index]) >> 1)) & 0xFF
        elif filter_type == 4:
            for index in range(stride):
                left = line[index - channels] if index >= channels else 0
                up_left = previous[index - channels] if index >= channels else 0
                line[index] = (line[index] + _paeth(left, previous[index], up_left)) & 0xFF
        elif filter_type != 0:
            raise ValueError(f"Unknown PNG filter type {filter_type}.")
        rows[row_index * stride : (row_index + 1) * stride] = line
        previous = bytes(line)

    if channels == 3:
        return width, height, bytes(rows)
    rgb = bytearray(width * height * 3)
    rgb[0::3] = rows[0::4]
    rgb[1::3] = rows[1::4]
    rgb[2::3] = rows[2::4]
    return width, height, bytes(rgb)


def decode_tile_png(payload: bytes) -> tuple[int, int, bytes]:
    """Decode a tile like ``decode_rgb_png``, with Pillow when it is installed."""
    if Image is None:
        return decode_rgb_png(payload)
    with Image.open(io.BytesIO(payload)) as image:
        if image.format != "PNG" or image.mode not in ("RGB", "RGBA"):
            raise ValueError(f"Unsupported tile image ({image.format}, mode {image.mode}).")
        return image.width, image.height, image.convert("RGB").tobytes()


def terrarium_elevation_m(red: int, green: int, blue: int) -> float:
    return red * 256 + green + blue / 256 - 32768


def pixel_position(latitude: float, longitude: float, zoom: int) -> tuple[float, float]:
    """Fractional global pixel coordinates (pixel centres at integer values)."""
    world_px = TILE_SIZE * 2**zoom
    x = (longitude + 180.0) / 360.0 * world_px - 0.5
    y = (1.0 - math.asinh(math.tan(math.radians(latitude))) / math.pi) / 2.0 * world_px - 0.5
    return x, y


class TerrainTileClient:
    provider_name = "terrain-tiles"

    def __init__(
        self,
        cache_dir: Path,
        zoom: int = DEFAULT_ZOOM,
        url_template: str = TILE_URL,
        timeout_seconds: float = 30.0,
    ):
        self.cache_dir = cache_dir
        self.zoom = zoom
        self.url_template = url_template
        self.timeout_seconds = timeout_seconds
        self._tiles: dict[tuple[int, int], bytes] = {}

    def _tile_path(self, tile_x: int, tile_y: int) -> Path:
        return self.cache_dir / str(self.zoom) / str(tile_x) / f"{tile_y}.png"

    def _decode(self, payload: bytes, source: str) -> bytes:
        try:
            width, height, pixels = decode_tile_png(payload)
        except TILE_DECODE_ERRORS as exc:
            raise ElevationServiceError(f"Terrain tile {source} cannot be decoded: {exc}") from exc
        if width != TILE_SIZE or height != TILE_SIZE:
            raise ElevationServiceError(f"Terrain tile {source} is not {TILE_SIZE}x{TILE_SIZE} pixels.")
        return pixels

    def _download(self, tile_x: int, tile_y: int) -> bytes:
        """Fetch a tile, check that it decodes and store it in the cache; returns its pixels."""
        url = self.url_template.format(zoom=self.zoom, x=tile_x, y=tile_y)
        request = urllib.request.Request(url, headers={"User-Agent": "OverallAltitude/2.0"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = response.read()
        except (OSError, http.client.HTTPException) as exc:
            raise ElevationServiceError(f"Cannot fetch terrain tile {url}: {exc}") from exc
        pixels = self._decode(payload, url)

        # Write through a temporary file so an interrupted run never leaves a broken tile.
        path = self._tile_path(tile_x, tile_y)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=path.parent, suffix=".part")
        try:
            with os.fdopen(handle, "wb") as file:
                file.write(payload)
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        return pixels

    def _load_tile(self, tile_x: int, tile_y: int) -> bytes:
        key = (tile_x, tile_y)
        if key not in self._tiles:
            path = self._tile_path(tile_x, tile_y)
            if path.exists():
                self._tiles[key] = self._decode(path.read_bytes(), f"{path} (delete it to download it again)")
            else:
                self._tiles[key] = self._download(tile_x, tile_y)
        return self._tiles[key]

    def _clamp_pixel(self, pixel: int) -> int:
        return min(max(pixel, 0), TILE_SIZE * 2**self.zoom - 1)

    def _pixel_elevation(self, pixel_x: int, pixel_y: int) -> float:
        pixel_x = self._clamp_pixel(pixel_x)
        pixel_y = self._clamp_pixel(pixel_y)
        pixels = self._load_tile(pixel_x // TILE_SIZE, pixel_y // TILE_SIZE)
        index = ((pixel_y % TILE_SIZE) * TILE_SIZE + pixel_x % TILE_SIZE) * 3
        return terrarium_elevation_m(pixels[index], pixels[index + 1], pixels[index + 2])

    def elevation(self, latitude: float, longitude: float) -> float:
        x, y = pixel_position(latitude, longitude, self.zoom)
        x0 = math.floor(x)
        y0 = math.floor(y)
        weight_x = x - x0
        weight_y = y - y0
        return (
            (1 - weight_x) * (1 - weight_y) * self._pixel_elevation(x0, y0)
            + weight_x * (1 - weight_y) * self._pixel_elevation(x0 + 1, y0)
            + (1 - weight_x) * weight_y * self._pixel_elevation(x0, y0 + 1)
            + weight_x * weight_y * self._pixel_elevation(x0 + 1, y0 + 1)
        )

    def _prefetch(self, points: Sequence[TrackPoint]) -> None:
        """Download the tiles that the points need and that are not cached, in parallel."""
        needed: set[tuple[int, int]] = set()
        for point in points:
            x, y = pixel_position(point.latitude, point.longitude, self.zoom)
            for pixel_x in (math.floor(x), math.floor(x) + 1):
                for pixel_y in (math.floor(y), math.floor(y) + 1):
                    needed.add(
                        (self._clamp_pixel(pixel_x) // TILE_SIZE, self._clamp_pixel(pixel_y) // TILE_SIZE)
                    )
        missing = sorted(
            key for key in needed if key not in self._tiles and not self._tile_path(*key).exists()
        )
        if len(missing) < 2:
            return
        with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:
            downloads = [pool.submit(self._download, *key) for key in missing]
            for key, download in zip(missing, downloads):
                self._tiles[key] = download.result()

    def lookup(self, points: Sequence[TrackPoint]) -> list[float]:
        self._prefetch(points)
        return [self.elevation(point.latitude, point.longitude) for point in points]
