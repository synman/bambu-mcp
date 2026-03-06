"""tools/_response.py — Response size utilities for MCP tools."""

from __future__ import annotations

import base64
import gzip
import json
import os


def _max_response_chars() -> int:
    tokens = int(os.environ.get("MAX_MCP_OUTPUT_TOKENS", "25000"))
    return tokens * 4  # Copilot CLI: JG1() * 4 → 100,000 chars default


def compress_if_large(data: dict) -> dict:
    """
    Return data as-is if it fits within the client's response threshold.
    Otherwise compress with gzip+base64 and return a compression envelope.

    Threshold is derived from MAX_MCP_OUTPUT_TOKENS (default 25,000 tokens
    × 4 chars/token = 100,000 chars), matching the Copilot CLI truncation cutoff.
    If the user raises MAX_MCP_OUTPUT_TOKENS, our threshold scales automatically.

    Compressed envelope fields:
      compressed            — True
      encoding              — "gzip+base64"
      original_size_bytes   — uncompressed JSON byte count
      compressed_size_bytes — compressed payload byte count
      data                  — base64-encoded gzip bytes

    Decompression (Python one-liner):
      import gzip, json, base64
      data = json.loads(gzip.decompress(base64.b64decode(r["data"])))
    """
    serialized = json.dumps(data)
    if len(serialized) <= _max_response_chars():
        return data
    gz = gzip.compress(serialized.encode("utf-8"), compresslevel=6)
    return {
        "compressed": True,
        "encoding": "gzip+base64",
        "original_size_bytes": len(serialized),
        "compressed_size_bytes": len(gz),
        "data": base64.b64encode(gz).decode("ascii"),
    }


# Image quality tier definitions: (max_width, max_height, jpeg_quality)
IMAGE_QUALITY_TIERS: dict[str, tuple[int, int, int]] = {
    "preview":  (320,  180, 65),
    "standard": (640,  360, 75),
    "full":     (0,    0,   85),  # 0 = original dimensions
}
_DEFAULT_QUALITY = "standard"


def resize_image_to_tier(image_bytes: bytes, quality: str = _DEFAULT_QUALITY) -> tuple[bytes, int, int]:
    """
    Resize image bytes to the named quality tier and return (jpeg_bytes, width, height).

    Accepts JPEG or PNG input. Output is always JPEG.
    quality must be one of: "preview", "standard", "full".
    Unknown tier names fall back to "standard".
    """
    import io
    from PIL import Image

    tier = IMAGE_QUALITY_TIERS.get(quality, IMAGE_QUALITY_TIERS[_DEFAULT_QUALITY])
    max_w, max_h, q = tier

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    orig_w, orig_h = img.size

    if max_w > 0 and max_h > 0 and (orig_w > max_w or orig_h > max_h):
        img.thumbnail((max_w, max_h), Image.LANCZOS)

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=q, optimize=True)
    final_w, final_h = img.size
    return out.getvalue(), final_w, final_h
