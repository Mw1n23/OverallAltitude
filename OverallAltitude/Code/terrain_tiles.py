"""Elevation lookup from the open "Terrain Tiles" dataset (Mapzen/Tilezen on AWS).

The tiles are Web-Mercator PNGs in "terrarium" encoding. At zoom 14 they carry the
best open terrain model per region (e.g. 10 m lidar DTM in Austria, 2 m lidar in
England, 10 m 3DEP in the USA), which is far less noisy along roads than the 25-30 m
surface models served by the public OpenTopoData endpoint.

Only the standard library is used, including the PNG decoding.
"""

from __future__ import annotations

import math
import struct
import urllib.error
import urllib.request
import zlib
from pathlib import Path
from typing import Sequence

from .Track_analysis_05 import ElevationServiceError, TrackPoint


TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{zoom}/{x}/{y}.png"
TILE_SIZE = 256
DEFAULT_ZOOM = 14
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


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

    def _load_tile(self, tile_x: int, tile_y: int) -> bytes:
        key = (tile_x, tile_y)
        if key in self._tiles:
            return self._tiles[key]

        path = self._tile_path(tile_x, tile_y)
        if not path.exists():
            url = self.url_template.format(zoom=self.zoom, x=tile_x, y=tile_y)
            request = urllib.request.Request(url, headers={"User-Agent": "OverallAltitude/2.0"})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    payload = response.read()
            except urllib.error.URLError as exc:
                raise ElevationServiceError(f"Cannot fetch terrain tile {url}: {exc}") from exc
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)

        width, height, pixels = decode_rgb_png(path.read_bytes())
        if width != TILE_SIZE or height != TILE_SIZE:
            raise ElevationServiceError(f"Terrain tile {path} is not {TILE_SIZE}x{TILE_SIZE} pixels.")
        self._tiles[key] = pixels
        return pixels

    def _pixel_elevation(self, pixel_x: int, pixel_y: int) -> float:
        last = TILE_SIZE * 2**self.zoom - 1
        pixel_x = min(max(pixel_x, 0), last)
        pixel_y = min(max(pixel_y, 0), last)
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

    def lookup(self, points: Sequence[TrackPoint]) -> list[float]:
        return [self.elevation(point.latitude, point.longitude) for point in points]
