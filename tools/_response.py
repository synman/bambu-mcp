"""tools/_response.py — Response size utilities for MCP tools."""

from __future__ import annotations

import base64
import gzip
import json
import math
import os
import tempfile
import threading
from pathlib import Path


def _max_response_chars() -> int:
    tokens = int(os.environ.get("MAX_MCP_OUTPUT_TOKENS", "25000"))
    return tokens * 4  # Copilot CLI: JG1() * 4 → 100,000 chars default


# Compress whenever payload exceeds this many chars — gzip is net-positive above ~250 chars.
# Separate from _max_response_chars(): that value is the ResponseSizeTracker overflow signal
# (used for auto-tuning MAX_MCP_OUTPUT_TOKENS); this is the compression trigger.
_MIN_COMPRESS_SIZE: int = 300


# ---------------------------------------------------------------------------
# ResponseSizeTracker — persistent high-water-mark + config auto-tune
# ---------------------------------------------------------------------------

_TRACKER_DIR = Path.home() / ".bambu-mcp"
_TRACKER_FILE = _TRACKER_DIR / "response_size_tracker.json"
_TRACKER_LOCK = threading.Lock()

# Session baseline: MAX_MCP_OUTPUT_TOKENS at server startup. The in-session
# threshold never rises (the CLI still truncates at the old value until restart).
_SESSION_TOKENS: int = int(os.environ.get("MAX_MCP_OUTPUT_TOKENS", "25000"))

_MCP_CONFIG_PATH = Path.home() / ".copilot" / "mcp-config.json"


def _load_tracker() -> dict:
    try:
        return json.loads(_TRACKER_FILE.read_text())
    except Exception:
        return {"max_chars_seen": 0, "max_label": "", "recommended_tokens": 25000}


def _save_tracker(data: dict) -> None:
    _TRACKER_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _TRACKER_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(_TRACKER_FILE)


def _update_mcp_config(tokens: int) -> None:
    """Write MAX_MCP_OUTPUT_TOKENS into ~/.copilot/mcp-config.json env block."""
    try:
        cfg = json.loads(_MCP_CONFIG_PATH.read_text()) if _MCP_CONFIG_PATH.exists() else {}
        servers = cfg.setdefault("mcpServers", {})
        bmc = servers.setdefault("bambu-mcp", {})
        env = bmc.setdefault("env", {})
        env["MAX_MCP_OUTPUT_TOKENS"] = str(tokens)
        tmp = _MCP_CONFIG_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cfg, indent=2))
        tmp.replace(_MCP_CONFIG_PATH)
    except Exception:
        pass  # config update is best-effort


def record_response_size(chars: int, label: str = "") -> None:
    """
    Record the char count of a response. Updates the persistent high-water-mark
    and rewrites mcp-config.json when a new max is observed.

    The in-session MAX_MCP_OUTPUT_TOKENS never changes — the config update takes
    effect only on the next MCP server restart.
    """
    with _TRACKER_LOCK:
        state = _load_tracker()
        if chars <= state.get("max_chars_seen", 0):
            return
        recommended = math.ceil(chars / 4)
        state["max_chars_seen"] = chars
        state["max_label"] = label
        state["recommended_tokens"] = recommended
        _save_tracker(state)
        if recommended > _SESSION_TOKENS:
            _update_mcp_config(recommended)


def _has_binary_data(data: dict) -> bool:
    """Return True if any top-level or one-level-deep value starts with 'data:'."""
    for v in data.values():
        if isinstance(v, str) and v.startswith("data:"):
            return True
        if isinstance(v, dict):
            for vv in v.values():
                if isinstance(vv, str) and vv.startswith("data:"):
                    return True
    return False


def compress_if_large(data: dict) -> dict:
    """
    Return data as-is if it fits within the client's response threshold.
    Otherwise compress with gzip+base64 and return a compression envelope.

    Binary responses (containing data: URIs — JPEG/PNG base64) are never gzip'd:
    image data is already compressed; gzip yields < 5% reduction while adding
    latency and complexity. Binary responses are recorded in the size tracker and
    returned as-is regardless of size.

    Compression trigger: _MIN_COMPRESS_SIZE (300 chars). Gzip is net-positive above
    ~250 chars; below that threshold the base64 envelope is larger than the raw JSON.

    Dual-threshold model:
    - _MIN_COMPRESS_SIZE (300): compression trigger — any non-binary response above
      this size is gzip+base64 encoded.
    - _max_response_chars(): ResponseSizeTracker signal only — used to detect when a
      response would overflow the MCP token budget and auto-tune MAX_MCP_OUTPUT_TOKENS
      in mcp-config.json. This threshold no longer drives the compress decision.

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
    char_count = len(serialized)

    # Binary responses: skip gzip, record size, return as-is
    if _has_binary_data(data):
        record_response_size(char_count, label="binary")
        return data

    record_response_size(char_count)

    if char_count <= _MIN_COMPRESS_SIZE:
        return data
    gz = gzip.compress(serialized.encode("utf-8"), compresslevel=6)
    return {
        "compressed": True,
        "encoding": "gzip+base64",
        "original_size_bytes": char_count,
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
