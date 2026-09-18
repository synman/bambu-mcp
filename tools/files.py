"""
tools/files.py — File management tools for Bambu Lab printers.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

from session_manager import session_manager


def _no_printer(name: str) -> dict:
    return {"error": f"Printer '{name}' not connected"}


def _permission_denied() -> str:
    return "Error: user_permission must be True to perform this action."


def _to_dict(o):
    """Recursively convert dataclasses/enums into plain JSON-serializable values."""
    import dataclasses
    from enum import Enum

    if isinstance(o, Enum):
        return o.name
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return {f.name: _to_dict(getattr(o, f.name)) for f in dataclasses.fields(o)}
    if isinstance(o, dict):
        return {k: _to_dict(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_to_dict(v) for v in o]
    return o


def _serialize_project_info(info, include_images: bool = False) -> dict:
    """
    Convert a single ProjectInfo (dataclass or dict) into the JSON-safe shape
    returned by get_project_info(), omitting the large topimg/thumbnail image
    fields unless include_images=True. Shared by get_project_info() and
    get_all_project_info() so both return each plate in the same shape.
    """
    import dataclasses
    import json

    if dataclasses.is_dataclass(info):
        result = json.loads(json.dumps(_to_dict(info), default=str))
    else:
        result = info if isinstance(info, dict) else {"info": str(info)}

    if not include_images:
        meta = result.get("metadata")
        if isinstance(meta, dict):
            if "topimg" in meta:
                meta["topimg"] = "[image omitted — use get_plate_topview to fetch]"
            if "thumbnail" in meta:
                meta["thumbnail"] = "[image omitted — use get_plate_thumbnail to fetch]"
    return result


def _find_file_in_tree(tree: dict, target_path: str) -> dict | None:
    """Recursively search the sdcard file tree for an entry matching target_path."""
    log.debug("_find_file_in_tree: searching for %s", target_path)
    if not tree:
        return None
    if tree.get("id") == target_path or tree.get("name") == target_path:
        return tree
    for child in tree.get("children", []):
        result = _find_file_in_tree(child, target_path)
        if result:
            return result
    return None


def list_sdcard_files(name: str, path: str = "/", cached: bool = False) -> dict:
    """
    Return the SD card directory listing for the named printer.

    When path="/" (default), returns the full top-level tree — backward compatible.
    When path is a subdirectory (e.g. "/cache", "/model"), returns only that
    subtree, which is much smaller than the full listing.

    Use this in a hierarchy:
      list_sdcard_files(name)           → full top-level tree
      list_sdcard_files(name, "/cache") → only the /cache subtree
      list_sdcard_files(name, "/model") → only the /model subtree

    cached=False (default): performs a live FTPS fetch from the printer — guaranteed
    up-to-date but requires an active connection and takes a moment. Use for
    reliable, current listings.
    cached=True: returns the in-memory cached copy immediately without contacting
    the printer. The cache is populated by the most recent list_sdcard_files() or
    refresh_sdcard() call. Returns None fields if the cache has never been populated.
    Use when stale data is acceptable and low latency matters.

    Response may be gzip+base64 compressed if the full tree is large. Decompress:
      import gzip, json, base64
      data = json.loads(gzip.decompress(base64.b64decode(r["data"])))
    If the compressed envelope itself exceeds the MCP response limit, use the HTTP fallback:
      GET /api/get_sdcard_contents?printer=<name>
    Or reduce scope by listing a specific subdirectory (e.g. path="/cache").    """
    log.debug("list_sdcard_files: called for name=%s path=%s cached=%s", name, path, cached)
    from tools._response import compress_if_large
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("list_sdcard_files: printer not connected: %s", name)
        return _no_printer(name)
    try:
        if cached:
            log.debug("list_sdcard_files: returning cached_sd_card_contents for %s", name)
            contents = printer.cached_sd_card_contents
        else:
            log.debug("list_sdcard_files: calling printer.get_sdcard_contents() for %s", name)
            contents = printer.get_sdcard_contents()
        if contents is None:
            log.debug("list_sdcard_files: → error: no contents for %s", name)
            return {"error": "Failed to retrieve SD card contents"}
        if path and path != "/":
            subtree = _find_file_in_tree(contents, path)
            if subtree is None:
                log.debug("list_sdcard_files: path not found: %s", path)
                return {"error": f"Path not found: {path}", "path": path}
            contents = subtree
        log.debug("list_sdcard_files: success for %s path=%s", name, path)
        return compress_if_large({"path": path, "contents": contents})
    except Exception as e:
        log.error("list_sdcard_files: error for %s: %s", name, e, exc_info=True)
        return {"error": f"Error listing SD card: {e}"}


def get_file_info(name: str, file_path: str) -> dict:
    """
    Return metadata for a specific file on the printer's SD card.

    Retrieves the full SD card listing and searches for the given file_path.
    Returns file attributes such as name, size, timestamp, and whether it is a directory.
    """
    log.debug("get_file_info: called for name=%s file_path=%s", name, file_path)
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("get_file_info: printer not connected: %s", name)
        return _no_printer(name)
    try:
        log.debug("get_file_info: calling printer.get_sdcard_contents() for %s", name)
        contents = printer.get_sdcard_contents()
        if contents is None:
            log.debug("get_file_info: → error: no contents for %s", name)
            return {"error": "Failed to retrieve SD card contents"}
        entry = _find_file_in_tree(contents, file_path)
        if entry is None:
            log.debug("get_file_info: → not found: %s", file_path)
            return {"error": f"File not found: {file_path}"}
        log.debug("get_file_info: → found %s", file_path)
        return {"file": entry}
    except Exception as e:
        log.error("get_file_info: error for %s: %s", name, e, exc_info=True)
        return {"error": f"Error getting file info: {e}"}


def get_3mf_entry_by_name(name: str, target_name: str) -> dict:
    """
    Search the SD card file tree for an entry matching the given filename.

    Performs a depth-first search of the cached SD card 3MF file tree
    (from get_sdcard_3mf_files()) looking for a node whose 'name' field
    matches target_name exactly. Useful when you know the filename but not
    the full SD card path.

    target_name is the filename only (not a full path). Examples:
      "my_project.gcode.3mf", "part.3mf"
    Matching is case-sensitive and exact — no wildcards or partial matches.

    Returns the matching node dict with keys: id (full SD card path), name,
    size (bytes), timestamp (epoch float), and children (if a directory).
    Returns {"error": "not found"} when no match is found.

    To search by full SD card path instead of filename, use get_3mf_entry_by_id().
    To get the full directory tree, use list_sdcard_files().
    """
    log.debug("get_3mf_entry_by_name: called for name=%s target_name=%s", name, target_name)
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("get_3mf_entry_by_name: printer not connected: %s", name)
        return _no_printer(name)
    try:
        from bpm.bambuproject import get_3mf_entry_by_name as _bpm_search
        tree = printer.get_sdcard_3mf_files()
        if tree is None:
            log.debug("get_3mf_entry_by_name: → error: no sd card contents for %s", name)
            return {"error": "Failed to retrieve SD card contents"}
        result = _bpm_search(tree, target_name)
        if result is None:
            log.debug("get_3mf_entry_by_name: → not found: %s", target_name)
            return {"error": f"Not found: {target_name}"}
        log.debug("get_3mf_entry_by_name: → found %s at %s", target_name, result.get("id"))
        return {"entry": result}
    except Exception as e:
        log.error("get_3mf_entry_by_name: error for %s: %s", name, e, exc_info=True)
        return {"error": f"Error searching SD card: {e}"}


def get_3mf_entry_by_id(name: str, target_id: str) -> dict:
    """
    Search the SD card file tree for an entry matching the given full path.

    Performs a depth-first search of the cached SD card 3MF file tree
    (from get_sdcard_3mf_files()) looking for a node whose 'id' field
    matches target_id exactly. The 'id' field is the full SD card path.

    target_id is the full SD card path as returned by list_sdcard_files().
    Examples: "/cache/my_project.gcode.3mf", "/model/part.3mf"
    Directory entries have a trailing slash: "/cache/"
    Matching is case-sensitive and exact.

    Returns the matching node dict with keys: id (full SD card path), name,
    size (bytes), timestamp (epoch float), and children (if a directory).
    Returns {"error": "not found"} when no match is found.

    To search by filename instead of full path, use get_3mf_entry_by_name().
    To get the full directory tree, use list_sdcard_files().
    """
    log.debug("get_3mf_entry_by_id: called for name=%s target_id=%s", name, target_id)
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("get_3mf_entry_by_id: printer not connected: %s", name)
        return _no_printer(name)
    try:
        from bpm.bambuproject import get_3mf_entry_by_id as _bpm_search
        tree = printer.get_sdcard_3mf_files()
        if tree is None:
            log.debug("get_3mf_entry_by_id: → error: no sd card contents for %s", name)
            return {"error": "Failed to retrieve SD card contents"}
        result = _bpm_search(tree, target_id)
        if result is None:
            log.debug("get_3mf_entry_by_id: → not found: %s", target_id)
            return {"error": f"Not found: {target_id}"}
        log.debug("get_3mf_entry_by_id: → found %s", target_id)
        return {"entry": result}
    except Exception as e:
        log.error("get_3mf_entry_by_id: error for %s: %s", name, e, exc_info=True)
        return {"error": f"Error searching SD card: {e}"}


def get_project_info(name: str, file_path: str, plate_num: int = 1, include_images: bool = False) -> dict:
    """
    Return 3MF metadata and thumbnail info for a project file on the SD card.

    Parses the .3mf file for the requested plate and returns filament info,
    AMS mapping, and bounding box objects. Uses a local cache to avoid repeated
    FTPS downloads.

    By default (include_images=False), the metadata.topimg and metadata.thumbnail
    image fields are omitted to keep the response small. Use get_plate_thumbnail()
    or get_plate_topview() to fetch images for a specific plate on demand.

    When include_images=True, both data URIs are included in the response (large).
    Note: these are raw base64 data URIs — not directly visible to a human user in
    a chat or terminal context. If the human wants to *view* all plates visually,
    call open_plate_viewer() instead. Use include_images=True only when the AI
    agent needs to process the raw image bytes directly (vision analysis, comparison,
    etc.) or is describing the image content on the human's behalf.

    Multi-level call hierarchy:
      Level 1 — get_project_info(name, file, 1)  → {plates:[1..N], ...}  (index)
      Level 2 — get_project_info(name, file, N)  → per-plate metadata, bbox_objects
      Level 3 — get_plate_thumbnail(name, file, N) → just the isometric image
               get_plate_topview(name, file, N)   → just the top-down image

    The .3mf file is created by BambuStudio or OrcaSlicer — the slicing applications
    used to prepare 3D model files for Bambu Lab printers. They convert .STL/.3MF model
    files into printable G-code and package everything into a .3mf project file.

    Key fields in the returned dict:
    - plates:           List of all plate numbers in the file (e.g. [1,2,...,14]).
                        Iterate over this list and call get_project_info once per
                        plate to retrieve all plates.
    - metadata.map.bbox_objects: List of {name, ...} dicts for objects on this plate.
                        Filter out entries whose name contains 'wipe_tower' to get
                        the human-readable part list.
    - metadata.topimg:  Present only when include_images=True. Complete base64 data
                        URI (data:image/png;base64,...). Use DIRECTLY as img src.
    - metadata.thumbnail: Present only when include_images=True. Isometric thumbnail
                        data URI. Use DIRECTLY as img src.

    Coordinate system for bbox fields:
    - bbox values are [x_min, y_min, x_max, y_max] in millimetres, absolute bed position.
    - Origin (0,0) is BOTTOM-LEFT of the build plate (slicer convention).
    - To map to image pixel coords (origin top-left): flip Y → pixel_y = img_height - (y_mm / bed_h * img_height)
    - Apply uniform scale: scale = min(img_w / bed_w, img_h / bed_h); add centring offsets.
    - Bed dimensions by model (mm, W×H): H2D/H2S=350×320, X1C/X1/X1E/P1S/P1P/P2S/A1=256×256, A1_MINI=180×180
    - Use printer.config.printer_model.value to get the model string for dimension lookup.

    Cross-tool link: bbox_objects[].id values are the identify_id integers required by skip_objects().
    Filter bbox_objects to exclude entries whose name contains 'wipe_tower' to get human-readable part names.

    Note: when include_images=True this tool returns raw base64 data URIs which may exceed
    the CLI inline display limit. If output is truncated, use the HTTP fallback:
    GET http://localhost:{api_port}/api/get_3mf_props_for_file?printer={name}&file={file_path}&plate={plate_num}
    Call kb_get('bambu-http-files') for full route docs. Pre-authorized, no human
    permission needed.
    """
    log.debug("get_project_info: called for name=%s file_path=%s plate_num=%s include_images=%s", name, file_path, plate_num, include_images)
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("get_project_info: printer not connected: %s", name)
        return _no_printer(name)
    try:
        from bpm.bambuproject import get_project_info as _get_project_info
        log.debug("get_project_info: calling _get_project_info for %s", name)
        info = _get_project_info(file_path, printer, plate_num=plate_num)
        if info is None:
            log.debug("get_project_info: → error: no info for %s plate=%s", file_path, plate_num)
            return {"error": f"Could not retrieve project info for '{file_path}'"}
        log.debug("get_project_info: info retrieved for %s", name)
        result = _serialize_project_info(info, include_images)

        log.debug("get_project_info: → result for %s plate=%s include_images=%s", file_path, plate_num, include_images)
        return result
    except Exception as e:
        log.error("get_project_info: error for %s: %s", name, e, exc_info=True)
        return {"error": f"Error getting project info: {e}"}


def get_all_project_info(name: str, file_path: str, include_images: bool = False) -> dict | list:
    """
    Return 3MF metadata for every plate in a project file, in a single call.

    Batch counterpart to get_project_info() — instead of iterating plate
    numbers one round-trip at a time, this fetches the .3mf's actual plate
    set and returns every plate's metadata in one response. Use this when
    you need all plates (e.g. surveying a multi-plate project, or building a
    plate picker) instead of calling get_project_info() once per plate.

    Plate numbers are not assumed to be contiguous — a .3mf may contain a
    sparse set of plates (e.g. [1,5,6,7,8,12,15]) if plates were deleted in
    the slicer; only plates that genuinely exist are returned.

    include_images behaves exactly as in get_project_info(): when False
    (default) the metadata.topimg/thumbnail fields are omitted per plate to
    keep the response small; when True both data URIs are included for
    every plate (large). Use get_plate_thumbnail()/get_plate_topview() to
    fetch a single plate's image on demand instead.

    Returns a list of per-plate dicts, each shaped exactly like a single
    get_project_info() response, ordered by plate number.
    """
    log.debug("get_all_project_info: called for name=%s file_path=%s include_images=%s", name, file_path, include_images)
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("get_all_project_info: printer not connected: %s", name)
        return _no_printer(name)
    try:
        from bpm.bambuproject import get_all_project_info as _get_all_project_info
        log.debug("get_all_project_info: calling _get_all_project_info for %s", name)
        infos = _get_all_project_info(file_path, printer)
        if not infos:
            log.debug("get_all_project_info: → error: no plates for %s", file_path)
            return {"error": f"Could not retrieve project info for '{file_path}'"}
        plates = [_serialize_project_info(info, include_images) for info in infos]
        log.debug("get_all_project_info: → %d plate(s) for %s", len(plates), name)
        return plates
    except Exception as e:
        log.error("get_all_project_info: error for %s: %s", name, e, exc_info=True)
        return {"error": f"Error getting all project info: {e}"}


def get_plate_thumbnail(
    name: str,
    file_path: str,
    plate_num: int = 1,
    quality: str = "standard",
) -> dict:
    """
    Return the isometric thumbnail image for a single plate in a 3MF project file.

    This is the separated visual sub-call for get_project_info — it returns only
    the thumbnail data URI, without any metadata or bbox objects.

    quality controls image size and JPEG compression:
      "preview"  — ~5 KB  (320×180, JPEG q=65)  — quick overview
      "standard" — ~16 KB (640×360, JPEG q=75)  — default, renders cleanly inline
      "full"     — ~71 KB (original resolution)  — maximum detail

    Returns:
      data_uri  — complete data:image/jpeg;base64,... (embed directly as img src)
      plate_num — the plate number
      quality   — the quality tier used

    Human viewability note: This tool returns a raw base64 data URI.

    Use this tool when the AI agent is the consumer of the image — either to
    describe or analyze the asset on the human's behalf ("what does it look like?",
    "describe the plate", "is there anything on it?") or to process the raw bytes
    directly (vision model input, pixel comparison, local image library).

    When the human user is the intended viewer — "show me", "open it", "display
    the thumbnail", "let me see it" — call open_plate_viewer() to show all plates
    or open_plate_layout() for an annotated single-plate view. Returning a raw
    data_uri to a human in a chat or terminal context is never the right choice.

    For print_file pre-flight / print job prep, always use open_plate_viewer() —
    never this tool. See print_file STEP 1 for the correct sequence.

    Note: this tool returns a raw base64 data URI which may exceed the CLI inline
    display limit. If output is truncated, call kb_get('bambu-http-files')
    for the equivalent HTTP endpoints, then use bash/curl to retrieve the data
    directly — this is pre-authorized and requires no human permission.
    """
    log.debug("get_plate_thumbnail: called for name=%s file_path=%s plate_num=%s quality=%s", name, file_path, plate_num, quality)
    return _get_plate_image(name, file_path, plate_num, quality, image_key="thumbnail")


def get_plate_topview(
    name: str,
    file_path: str,
    plate_num: int = 1,
    quality: str = "standard",
) -> dict:
    """
    Return the top-down view image for a single plate in a 3MF project file.

    This is the separated visual sub-call for get_project_info — it returns only
    the top-down view data URI, without any metadata or bbox objects.

    quality controls image size and JPEG compression:
      "preview"  — ~5 KB  (320×180, JPEG q=65)  — quick overview
      "standard" — ~16 KB (640×360, JPEG q=75)  — default, renders cleanly inline
      "full"     — ~71 KB (original resolution)  — maximum detail

    Returns:
      data_uri  — complete data:image/jpeg;base64,... (embed directly as img src)
      plate_num — the plate number
      quality   — the quality tier used

    Human viewability note: This tool returns a raw base64 data URI.

    Use this tool when the AI agent is the consumer of the image — either to
    describe or analyze the asset on the human's behalf ("what does it look like?",
    "describe the plate", "is there anything on it?") or to process the raw bytes
    directly (vision model input, pixel comparison, local image library).

    When the human user is the intended viewer — "show me", "open it", "display
    the top view", "let me see it" — call open_plate_viewer() to show all plates
    or open_plate_layout() for an annotated single-plate view. Returning a raw
    data_uri to a human in a chat or terminal context is never the right choice.

    For print_file pre-flight / print job prep, always use open_plate_viewer() —
    never this tool. See print_file STEP 1 for the correct sequence.

    Note: this tool returns a raw base64 data URI which may exceed the CLI inline
    display limit. If output is truncated, call kb_get('bambu-http-files')
    for the equivalent HTTP endpoints, then use bash/curl to retrieve the data
    directly — this is pre-authorized and requires no human permission.
    """
    log.debug("get_plate_topview: called for name=%s file_path=%s plate_num=%s quality=%s", name, file_path, plate_num, quality)
    return _get_plate_image(name, file_path, plate_num, quality, image_key="topimg")


def _get_plate_image(
    name: str,
    file_path: str,
    plate_num: int,
    quality: str,
    image_key: str,
) -> dict:
    """Shared implementation for get_plate_thumbnail and get_plate_topview."""
    printer = session_manager.get_printer(name)
    if printer is None:
        return _no_printer(name)
    try:
        import base64
        import io
        from bpm.bambuproject import get_project_info as _get_project_info
        import dataclasses
        import json
        from enum import Enum
        from tools._response import resize_image_to_tier

        def _to_dict(o):
            if isinstance(o, Enum):
                return o.name
            if dataclasses.is_dataclass(o) and not isinstance(o, type):
                return {f.name: _to_dict(getattr(o, f.name)) for f in dataclasses.fields(o)}
            if isinstance(o, dict):
                return {k: _to_dict(v) for k, v in o.items()}
            if isinstance(o, (list, tuple)):
                return [_to_dict(v) for v in o]
            return o

        info = _get_project_info(file_path, printer, plate_num=plate_num)
        if info is None:
            return {"error": f"Could not retrieve project info for '{file_path}'"}
        if dataclasses.is_dataclass(info):
            result = json.loads(json.dumps(_to_dict(info), default=str))
        else:
            result = info if isinstance(info, dict) else {}

        meta = result.get("metadata", {})
        data_uri = meta.get(image_key, "")
        if not data_uri:
            return {"error": f"No {image_key} image available for plate {plate_num}"}

        # data_uri is "data:image/png;base64,<b64>"
        raw_b64 = data_uri.split(",", 1)[1]
        img_bytes = base64.b64decode(raw_b64)
        jpeg_bytes, w, h = resize_image_to_tier(img_bytes, quality)
        jpeg_uri = "data:image/jpeg;base64," + base64.b64encode(jpeg_bytes).decode("ascii")

        log.debug("_get_plate_image: %s plate=%s quality=%s → %dx%d %d bytes", image_key, plate_num, quality, w, h, len(jpeg_bytes))
        return {"data_uri": jpeg_uri, "plate_num": plate_num, "quality": quality, "width": w, "height": h}
    except Exception as e:
        log.error("_get_plate_image: error for %s: %s", name, e, exc_info=True)
        return {"error": f"Error retrieving plate image: {e}"}


def upload_file(
    name: str,
    local_path: str,
    remote_path: str,
    user_permission: bool = False,
) -> dict:
    """
    Upload a local file to the printer's SD card.

    Requires user_permission=True. Returns the updated SD card listing after upload.
    If the file is a .3mf, project metadata is also cached automatically.
    """
    log.debug("upload_file: called for name=%s local_path=%s remote_path=%s user_permission=%s", name, local_path, remote_path, user_permission)
    if not user_permission:
        log.debug("upload_file: permission denied for %s", name)
        return {"error": _permission_denied()}
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("upload_file: printer not connected: %s", name)
        return _no_printer(name)
    try:
        log.debug("upload_file: calling printer.upload_sdcard_file for %s", name)
        result = printer.upload_sdcard_file(local_path, remote_path)
        log.debug("upload_file: success for %s remote_path=%s", name, remote_path)
        log.debug("upload_file: → remote_path=%s", remote_path)
        return {"success": True, "remote_path": remote_path, "contents": result}
    except Exception as e:
        log.error("upload_file: error for %s: %s", name, e, exc_info=True)
        return {"error": f"Error uploading file: {e}"}


def download_file(
    name: str,
    remote_path: str,
    local_path: str,
    user_permission: bool = False,
) -> dict:
    """
    Download a file from the printer's SD card to the local filesystem.

    Requires user_permission=True. remote_path is the full path on the printer;
    local_path is the destination path on the host.
    """
    log.debug("download_file: called for name=%s remote_path=%s local_path=%s user_permission=%s", name, remote_path, local_path, user_permission)
    if not user_permission:
        log.debug("download_file: permission denied for %s", name)
        return {"error": _permission_denied()}
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("download_file: printer not connected: %s", name)
        return _no_printer(name)
    try:
        log.debug("download_file: calling printer.download_sdcard_file for %s", name)
        printer.download_sdcard_file(remote_path, local_path)
        log.debug("download_file: success for %s remote_path=%s", name, remote_path)
        log.debug("download_file: → remote_path=%s local_path=%s", remote_path, local_path)
        return {"success": True, "remote_path": remote_path, "local_path": local_path}
    except Exception as e:
        log.error("download_file: error for %s: %s", name, e, exc_info=True)
        return {"error": f"Error downloading file: {e}"}


def delete_file(
    name: str,
    remote_path: str,
    user_permission: bool = False,
) -> dict:
    """
    Delete a file from the printer's SD card.

    Requires user_permission=True. Returns the updated SD card listing after deletion.
    Paths ending with '/' delete folders (calls delete_sdcard_folder); all other paths
    delete files (calls delete_sdcard_file).
    """
    log.debug("delete_file: called for name=%s remote_path=%s user_permission=%s", name, remote_path, user_permission)
    if not user_permission:
        log.debug("delete_file: permission denied for %s", name)
        return {"error": _permission_denied()}
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("delete_file: printer not connected: %s", name)
        return _no_printer(name)
    try:
        if remote_path.endswith("/"):
            log.debug("delete_file: calling printer.delete_sdcard_folder for %s", name)
            result = printer.delete_sdcard_folder(remote_path)
        else:
            log.debug("delete_file: calling printer.delete_sdcard_file for %s", name)
            result = printer.delete_sdcard_file(remote_path)
        log.debug("delete_file: success for %s remote_path=%s", name, remote_path)
        log.debug("delete_file: → remote_path=%s", remote_path)
        return {"success": True, "remote_path": remote_path, "contents": result}
    except Exception as e:
        log.error("delete_file: error for %s: %s", name, e, exc_info=True)
        return {"error": f"Error deleting file: {e}"}


def create_folder(
    name: str,
    path: str,
    user_permission: bool = False,
) -> dict:
    """
    Create a directory on the printer's SD card.

    Requires user_permission=True. Returns the updated SD card listing after creation.
    Calls printer.make_sdcard_directory(path) via FTPS mkdir.
    """
    log.debug("create_folder: called for name=%s path=%s user_permission=%s", name, path, user_permission)
    if not user_permission:
        log.debug("create_folder: permission denied for %s", name)
        return {"error": _permission_denied()}
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("create_folder: printer not connected: %s", name)
        return _no_printer(name)
    try:
        log.debug("create_folder: calling printer.make_sdcard_directory for %s", name)
        result = printer.make_sdcard_directory(path)
        log.debug("create_folder: success for %s path=%s", name, path)
        log.debug("create_folder: → path=%s", path)
        return {"success": True, "path": path, "contents": result}
    except Exception as e:
        log.error("create_folder: error for %s: %s", name, e, exc_info=True)
        return {"error": f"Error creating folder: {e}"}


# ── AMS mapping from live spools ────────────────────────────────────────────
# bpm's get_project_info() never yields real tray ids: the 3mf's
# `filament_maps` is the slicer's per-filament EXTRUDER assignment, and bpm
# replaces it with a filament-id placeholder ("1", "2", ...). The only source
# of a real mapping is what the printer last reported loaded. This block mirrors
# bambu-printer-app's Print3mfFileDialog (reconstructAmsMapping /
# getProtocolTrayId / toWireMapping / getMatchQuality) scoring. Two deliberate
# differences, because the dialog has a human at a dropdown and this tool does
# not: colour names are parsed with the full CSS3 table (bpm emits CSS3 names;
# BPA's 10-name table mis-defines `green`), and assignment runs exact-first,
# then best-unused, then reuse, instead of BPA's single greedy pass.

_TYPE_MISMATCH_PENALTY = 10000.0
# A type-mismatched spool is still accepted when its colour is this close
# (BPA COLOR_ONLY_MATCH_DISTANCE).
_COLOR_ONLY_MATCH_DISTANCE = 60.0
_TYPE_MATCH_EXCELLENT_DISTANCE = 50.0
_TYPE_MATCH_GOOD_DISTANCE = 150.0


def _match_quality(type_match: bool, dist: float) -> tuple[str, str]:
    """BPA getMatchQuality: (quality key, human label) for one filament/spool pair."""
    if type_match and dist < _TYPE_MATCH_EXCELLENT_DISTANCE:
        return "excellent", "Excellent Match"
    if type_match and dist < _TYPE_MATCH_GOOD_DISTANCE:
        return "good", "Good Match"
    if type_match:
        return "fair", "Type Match"
    if dist < _COLOR_ONLY_MATCH_DISTANCE:
        return "poor", "Color Match Only"
    return "bad", "Poor Match"


def _color_to_rgb(color) -> tuple[int, int, int] | None:
    """Parse '#RRGGBB', '#RRGGBBAA', bare 'RRGGBB', or a CSS3 colour name.

    bpm stores a spool colour as the CSS3 name when webcolors knows the hex
    exactly, else as '#RRGGBBAA'; slicer metadata is '#RRGGBB'. Returns None
    for anything unparseable, which the caller treats as "cannot compare".
    """
    if not isinstance(color, str) or not color.strip():
        return None
    c = color.strip()
    if not c.startswith("#"):
        try:
            import webcolors
            c = webcolors.name_to_hex(c.lower())
        except ValueError:
            c = "#" + c
    h = c.lstrip("#")
    if len(h) == 8:
        h = h[:6]
    if len(h) != 6:
        return None
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return None


def _color_distance(a, b) -> float:
    """Euclidean RGB distance; inf when either colour cannot be parsed."""
    import math
    ra, rb = _color_to_rgb(a), _color_to_rgb(b)
    if ra is None or rb is None:
        return math.inf
    return math.dist(ra, rb)


def _protocol_tray_id(spool) -> int | None:
    """Wire tray id for an AMS-backed spool (BPA getProtocolTrayId).

    4-slot units (ams_id 0..127): ams_id * 4 + slot_id → 0..103.
    AMS HT / N3S (ams_id 128..253): ams_id + slot_id → 128..135.
    External spool / no AMS: None — not a use_ams candidate.
    """
    try:
        ams_id, slot_id = int(spool.ams_id), int(spool.slot_id)
    except (TypeError, ValueError, AttributeError):
        return None
    if slot_id < 0:
        return None
    if 0 <= ams_id < 128:
        return ams_id * 4 + slot_id
    if 128 <= ams_id < 254:
        return ams_id + slot_id
    return None


def _usable_spools(spools: list) -> list[tuple[int, object]]:
    """(tray_id, spool) for every AMS-backed spool that can be printed from.

    BPA isValidSpool: AMS-backed (external excluded), has a type, and
    state != 0 (an empty slot reports state 0).
    """
    out: list[tuple[int, object]] = []
    for s in spools or []:
        tray = _protocol_tray_id(s)
        if tray is None or not getattr(s, "type", "") or getattr(s, "state", -1) == 0:
            continue
        out.append((tray, s))
    return out


def _resolve_ams_mapping_from_spools(
    filaments: list, spools: list
) -> tuple[list[int], list[dict], list[dict]]:
    """Build the filament-id-indexed wire ams_mapping from the loaded spools.

    Scoring per BPA: exact type match scores 0, mismatch 10000, plus RGB
    colour distance; a pair is acceptable when the type matches or the colour
    alone is within _COLOR_ONLY_MATCH_DISTANCE. Assignment runs three passes
    over the filaments still unmapped:
      1. exact — type matches and the colour distance is 0, unused spool;
      2. best unused — lowest acceptable score among unused spools;
      3. reuse — lowest acceptable score among ALL usable spools, so two
         project filaments of one loaded material share a tray rather than
         refusing the print.
    A filament with no usable id, type, or colour is unmatched. The mapping is
    indexed by 1-based filament id and gap-filled with -1, because the gcode
    inside the 3mf references filaments by id, not list position.

    Returns (wire_mapping, matches, unmatched): one match dict per mapped
    filament (id, type, color, tray_id, spool_type, spool_color, distance,
    quality, label, pass, reused) and the unmatched filament dicts.
    """
    import math
    candidates = _usable_spools(spools)

    def parse_id(f) -> int:
        try:
            return int(f.get("id", 0))
        except (TypeError, ValueError):
            return 0

    entries = [(f, parse_id(f)) for f in (filaments or [])]
    wire = [-1] * max((fid for _, fid in entries), default=0)
    used: set[int] = set()
    matches: list[dict] = []
    unmatched: list[dict] = []
    pending: list[tuple[dict, int]] = []
    for f, fid in entries:
        if fid <= 0 or not str(f.get("type") or "") or not (f.get("color") or ""):
            unmatched.append(f)
        else:
            pending.append((f, fid))

    def scored(f, s) -> tuple[float, bool, float] | None:
        type_match = str(s.type).upper() == str(f.get("type")).upper()
        dist = _color_distance(f.get("color"), getattr(s, "color", ""))
        if dist == math.inf:
            return None
        return (0.0 if type_match else _TYPE_MISMATCH_PENALTY) + dist, type_match, dist

    def acceptable(score: float) -> bool:
        return score < _TYPE_MISMATCH_PENALTY or (
            score - _TYPE_MISMATCH_PENALTY < _COLOR_ONLY_MATCH_DISTANCE
        )

    def assign(f, fid, tray, s, type_match, dist, pass_name, reused):
        quality, label = _match_quality(type_match, dist)
        wire[fid - 1] = tray
        used.add(tray)
        matches.append({
            "id": fid, "type": f.get("type"), "color": f.get("color"),
            "tray_id": tray, "spool_type": s.type, "spool_color": getattr(s, "color", ""),
            "distance": round(dist, 1), "quality": quality, "label": label,
            "pass": pass_name, "reused": reused,
        })

    # Pass 1 — exact type and colour, unused spools only.
    for f, fid in list(pending):
        for tray, s in candidates:
            if tray in used:
                continue
            sc = scored(f, s)
            if sc and sc[1] and sc[2] == 0:
                assign(f, fid, tray, s, True, 0.0, "exact", False)
                pending.remove((f, fid))
                break

    # Pass 2 — best acceptable unused spool; pass 3 — best acceptable spool, reuse allowed.
    for pass_name, allow_reuse in (("best-unused", False), ("reuse", True)):
        for f, fid in list(pending):
            best = None
            for tray, s in candidates:
                if tray in used and not allow_reuse:
                    continue
                sc = scored(f, s)
                if sc and (best is None or sc[0] < best[0]):
                    best = (sc[0], tray, s, sc[1], sc[2])
            if best and acceptable(best[0]):
                _, tray, s, type_match, dist = best
                assign(f, fid, tray, s, type_match, dist, pass_name, tray in used)
                pending.remove((f, fid))

    unmatched.extend(f for f, _ in pending)
    return wire, matches, unmatched


def _resolve_print_mapping(name: str, printer, file_path: str, plate_num: int) -> dict:
    """Resolve the ams_mapping a print of file_path/plate_num would use, from the spools
    the printer last reported. Never prints. Returns the resolution payload; when the
    print could not proceed on it the payload also carries an "error" message.
    """
    import json
    try:
        from bpm.bambuproject import get_project_info as _get_project_info
        info = _get_project_info(file_path, printer, plate_num=plate_num)
    except Exception as e:
        log.warning("resolve_print_mapping: could not read project metadata for %s: %s", file_path, e)
        return {
            "error": (
                f"Could not read project metadata for {file_path}: {e}. "
                "Pass ams_mapping explicitly or use use_ams=False."
            )
        }
    metadata = getattr(info, "metadata", None) or {}
    filaments = list(metadata.get("filament") or [])
    if info is None or not filaments:
        return {
            "error": (
                f"{file_path} plate {plate_num} carries no filament metadata to map from "
                "(plate missing or unparsed). Pass ams_mapping explicitly or use use_ams=False."
            )
        }
    state = session_manager.get_state(name)
    spools = list(getattr(state, "spools", None) or [])
    wire, matches, unmatched = _resolve_ams_mapping_from_spools(filaments, spools)
    result = {
        "file_path": file_path,
        "plate_num": plate_num,
        "filaments": filaments,
        "resolved_ams_mapping": wire,
        "ams_mapping_json": json.dumps(wire),
        "matches": matches,
        "unmatched": unmatched,
        "loaded_spools": [
            {"tray_id": tray, "type": s.type, "color": getattr(s, "color", "")}
            for tray, s in _usable_spools(spools)
        ],
        "external_spools": [
            {"slot_id": getattr(s, "slot_id", -1), "type": s.type, "color": getattr(s, "color", "")}
            for s in spools if _protocol_tray_id(s) is None and getattr(s, "slot_id", -1) >= 254
        ],
    }
    log.info(
        "resolve_print_mapping: %s plate %s → %s (unmatched=%d)",
        file_path, plate_num, wire, len(unmatched),
    )
    if unmatched or not wire:
        result["error"] = (
            "No loaded AMS spool matches these project filaments: "
            + ", ".join(f"id {f.get('id')} ({f.get('type')} {f.get('color')})" for f in unmatched)
            + ". Load a matching spool, pass ams_mapping explicitly, or use use_ams=False."
        )
    return result


def preview_ams_mapping(name: str, file_path: str, plate_num: int = 1) -> dict:
    """
    Resolve — WITHOUT printing — the ams_mapping that print_file would send for a
    .3mf plate, from the spools the printer last reported loaded.

    Read-only. Call it in STEP 1 of the print_file confirmation gate and show the
    result in the summary: each filament → tray_id with the spool it matched, its
    colour distance, and a BPA match label (Excellent Match / Good Match / Type Match /
    Color Match Only / Poor Match). "Type Match" means the material matches but the
    colour is off — print_file WILL print on it, so surface it to the user.

    Returns resolved_ams_mapping (list, indexed by 1-based filament id, -1 for an
    unused id), ams_mapping_json (the exact string print_file sends), matches,
    unmatched, loaded_spools (the AMS spools that could be printed from), and
    external_spools. When "error" is present print_file would refuse with the same
    message; pass ams_mapping explicitly or use use_ams=False.
    """
    log.debug("preview_ams_mapping: called for name=%s file_path=%s plate_num=%s", name, file_path, plate_num)
    printer = session_manager.get_printer(name)
    if printer is None:
        return _no_printer(name)
    return _resolve_print_mapping(name, printer, file_path, plate_num)


def print_file(
    name: str,
    file_path: str,
    plate_num: int = 1,
    bed_type: str = "auto",
    use_ams: bool = True,
    ams_mapping: list | str | None = None,
    timelapse: bool = False,
    bed_leveling: bool = True,
    flow_calibration: bool = False,
    user_permission: bool = False,
) -> dict:
    """
    Start printing a .3mf file already stored on the printer's SD card.

    Requires user_permission=True. bed_type must be one of: auto, cool_plate,
    eng_plate, hot_plate, textured_plate (case-insensitive). When use_ams=True
    and no ams_mapping is given, the mapping is resolved from the spools the
    printer last reported loaded: each project filament is matched to an AMS
    spool by exact type then closest colour (bambu-printer-app's print-dialog
    scoring; exact pairs are assigned first, then best unused, then reuse).
    A same-material spool of the WRONG colour is accepted ("Type Match") — call
    preview_ams_mapping() first and show the user every match label.
    The 3mf carries no usable tray ids — get_project_info()'s ams_mapping is a
    filament-id placeholder, never a slot assignment. If any filament finds no
    loaded match, or the plate carries no filament metadata, the print is
    REFUSED with the unmatched filaments and the loaded spools listed; pass
    ams_mapping explicitly (or use_ams=False) to proceed.
    Calls printer.print_3mf_file() with the given parameters.
    bed_type values: 'cool_plate' = smooth cold plate (PLA, TPU at low temp).
    'eng_plate' = smooth engineering plate (PETG, PA, ABS). 'hot_plate' = smooth
    high-temp plate (ASA, PC). 'textured_plate' = textured PEI surface (good
    general-purpose adhesion). 'auto' = let the printer decide based on the sliced
    settings in the file.
    use_ams=True = load filament from AMS slots, resolved as described above.
    use_ams=False = print using only the external spool holder (for single-color
    prints without AMS).
    ams_mapping overrides the live-spool resolution above. Provide a JSON array
    string or a list of integers indexed by 1-based filament id (index 0 = filament
    1), each element an absolute tray_id, -1 for a filament id the plate does not use.

    tray_id encoding — ALWAYS derive from live telemetry (the spool's ams_id is the
    hardware chip_id from get_ams_units() / get_spool_info()), NEVER hardcode:
      4-slot AMS (ams_id 0..127):  tray_id = ams_id * 4 + slot_id   → 0..103
      AMS HT / N3S (ams_id ≥ 128): tray_id = ams_id + slot_id       → 128..
      External spool holder = 254. Unused filament id = -1.
    NEVER use the 0-based unit_index in place of ams_id, and NEVER apply the
    4-slot formula to an AMS HT (ams_id 128 → 512 is wrong; 128 is right).

    Correct workflow when overriding:
      1. Call preview_ams_mapping() to see what the tool would resolve, then
         get_spool_info() for each spool's ams_id and slot_id.
      2. Encode each chosen spool with the formula above.
      3. Build the array indexed by 1-based filament id from get_project_info().

    Example: if AMS 2 Pro has ams_id=0, slot 1 → tray_id=1.
             if AMS HT has ams_id=128, slot 0 → tray_id=128.
    When ams_mapping is provided, use_ams is automatically set to True.
    Always call get_project_info() first to see which filaments the .3mf plate uses.

    ⚠️ CONFIRMATION REQUIRED — DO NOT CALL THIS TOOL until all steps below are done
    IN A SINGLE TURN. This tool starts an irreversible physical print.

    STEP 0 — Active-print guard:
      This tool is ⛔ BLOCKED when gcode_state is RUNNING or PREPARE (defense-in-depth;
      firmware also rejects with "printer busy"). Check get_print_progress() first if unsure.

    STEP 1 — Gather everything first (no user interaction yet):
      Call get_project_info(), preview_ams_mapping(), get_ams_units(), get_spool_info()
      to collect all data needed to build the complete summary before asking the user
      anything. preview_ams_mapping() is the mapping print_file will actually send.
      To show plate visuals to the user, call open_plate_viewer(name, file_path) — do NOT
      call get_plate_thumbnail() or get_plate_topview() and embed the data_uri in the
      response. Humans cannot see raw base64 in a terminal or chat context.
      Also look up stored preferences for each sticky field using user_prefs:
        from user_prefs import get_pref
        bed_leveling     = get_pref(f"{name}:bed_leveling",     True)
        flow_calibration = get_pref(f"{name}:flow_calibration", False)
        timelapse        = get_pref(f"{name}:timelapse",        False)
      Factory defaults: bed_leveling=True, flow_calibration=False, timelapse=False.
      Label each field "(your preference)" if the stored value differs from the factory
      default, or "(default)" if it matches the factory default.

    STEP 2 — Present ONE complete summary containing ALL of the following:
      - Part name(s) and filament(s) from the project metadata
      - bed_type (from metadata) — ask: is this correct for the plate physically on the bed?
      - ams_mapping — from preview_ams_mapping(): each filament → tray with its match
        label; call out any "Type Match" (right material, wrong colour); ask: correct?
      - flow_calibration — show stored value with label; ask: run flow calibration before printing?
      - timelapse — show stored value with label; ask: record a timelapse?
      - bed_leveling — show stored value with label; ask: run bed leveling, or skip for speed?

    STEP 3 — Wait for explicit go-ahead AFTER the complete summary.
      Do NOT call print_file after confirming individual parameters across separate turns.
      Confirming flow_calibration, timelapse, or bed_leveling mid-conversation does NOT
      satisfy this gate. The go-ahead must come in the turn immediately after the full
      summary is shown with all six items visible.
      After print_file is called successfully, update stored preferences:
        from user_prefs import set_pref
        set_pref(f"{name}:bed_leveling",     bed_leveling)
        set_pref(f"{name}:flow_calibration", flow_calibration)
        set_pref(f"{name}:timelapse",        timelapse)
    """
    log.debug("print_file: called for name=%s file_path=%s plate_num=%s bed_type=%s user_permission=%s", name, file_path, plate_num, bed_type, user_permission)
    if not user_permission:
        log.debug("print_file: permission denied for %s", name)
        return {"error": _permission_denied()}
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("print_file: printer not connected: %s", name)
        return _no_printer(name)
    from tools._guards import check_active_print_guard
    blocked = check_active_print_guard(printer, name, "print_file")
    if blocked:
        return blocked
    try:
        from bpm.bambutools import PlateType
        bed_enum = (
            PlateType[bed_type.upper()]
            if bed_type and bed_type.upper() in PlateType.__members__
            else PlateType.AUTO
        )
        matches: list[dict] = []
        # "" and [] are the empty forms clients send for "no mapping" (BPA's own URL
        # sends ams_mapping=); treat them as absent rather than as an empty mapping.
        if ams_mapping not in (None, "", []):
            # Coerce list → JSON string so print_3mf_file's json.loads() works
            if isinstance(ams_mapping, list):
                ams_mapping = __import__("json").dumps(ams_mapping)
            resolved_ams_mapping = ams_mapping
            use_ams = True
            log.debug("print_file: using caller-provided ams_mapping: %s", ams_mapping)
        else:
            resolved_ams_mapping = ""
            if use_ams:
                res = _resolve_print_mapping(name, printer, file_path, plate_num)
                if "error" in res:
                    log.warning("print_file: refused for %s: %s", name, res["error"])
                    return res
                resolved_ams_mapping = res["ams_mapping_json"]
                matches = res["matches"]
        log.debug("print_file: calling printer.print_3mf_file for %s", name)
        printer.print_3mf_file(
            name=file_path,
            plate=plate_num,
            bed=bed_enum,
            use_ams=use_ams,
            ams_mapping=resolved_ams_mapping,
            bedlevel=bed_leveling,
            flow=flow_calibration,
            timelapse=timelapse,
        )
        log.debug("print_file: print started for %s", name)
        log.debug("print_file: → file=%s plate=%s bed_type=%s use_ams=%s ams_mapping=%s", file_path, plate_num, bed_enum.name, use_ams, resolved_ams_mapping)
        return {
            "success": True,
            "file_path": file_path,
            "plate_num": plate_num,
            "bed_type": bed_enum.name,
            "use_ams": use_ams,
            "ams_mapping": resolved_ams_mapping,
            "matches": matches,
        }
    except Exception as e:
        log.error("print_file: error for %s: %s", name, e, exc_info=True)
        return {"error": f"Error starting print: {e}"}


def _build_layout_uri(topimg_uri: str, objs: list, model_key) -> str:
    """Render bounding-box overlay on topimg and return a PNG data URI, or '' on failure."""
    log.debug("_build_layout_uri: called with topimg length=%d, obj count=%d", len(topimg_uri), len(objs))
    try:
        import base64
        import io
        from PIL import Image, ImageDraw, ImageFont

        img = Image.open(
            io.BytesIO(base64.b64decode(topimg_uri.split(",", 1)[1]))
        ).convert("RGBA")
        W, H = img.size

        model_str = model_key.value if hasattr(model_key, "value") else str(model_key).lower()
        BED_W, BED_H = _BED_DIMENSIONS.get(model_str, (256.0, 256.0))
        scale = min(W / BED_W, H / BED_H)
        x_off = (W - BED_W * scale) / 2
        y_off = (H - BED_H * scale) / 2

        def mm_to_px(x_mm, y_mm):
            return (x_off + x_mm * scale, H - y_off - y_mm * scale)

        unique_names = list(dict.fromkeys(o["name"] for o in objs))
        color_map = {n: _BBOX_PALETTE[i % len(_BBOX_PALETTE)] for i, n in enumerate(unique_names)}

        # Draw largest bboxes first so smaller parts aren't obscured
        objs_sorted = sorted(
            objs,
            key=lambda o: (o["bbox"][2] - o["bbox"][0]) * (o["bbox"][3] - o["bbox"][1]),
            reverse=True,
        )
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        for o in objs_sorted:
            bx0, by0, bx1, by1 = o["bbox"]
            px0, py1 = mm_to_px(bx0, by0)
            px1, py0 = mm_to_px(bx1, by1)
            c = color_map[o["name"]]
            draw.rectangle([px0, py0, px1, py1], outline=(*c, 230), width=2, fill=(*c, 40))

        out = Image.new("RGBA", img.size, (0, 0, 0, 0))
        out.paste(img, (0, 0))
        out.paste(overlay, (0, 0), overlay)

        leg_row = 28
        legend = Image.new("RGBA", (W, H + leg_row * len(unique_names) + 20), (30, 30, 30, 255))
        legend.paste(out, (0, 0))
        ld = ImageDraw.Draw(legend)
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 14)
        except Exception:
            font = ImageFont.load_default()
        y_pos = H + 10
        for n in unique_names:
            c = color_map[n]
            ld.rectangle([10, y_pos, 26, y_pos + 16], fill=(*c, 220))
            ld.text((34, y_pos), n.replace(".stl", ""), fill=(220, 220, 220, 255), font=font)
            y_pos += leg_row

        buf = io.BytesIO()
        legend.convert("RGB").save(buf, format="PNG")
        result = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        log.debug("_build_layout_uri: → png data uri, %d bytes raw", len(buf.getvalue()))
        return result
    except Exception:
        log.debug("_build_layout_uri: → empty (exception)")
        return ""


def open_plate_viewer(name: str, file_path: str, target_plate: int = None) -> dict:
    """
    Build and open an HTML viewer showing both the isometric thumbnail and
    top-down image for all plates in a 3MF project file on the printer's SD card.

    Opens a browser window showing all plates in the project as thumbnail images
    (isometric view + top-down view side by side). Use this to visually confirm
    which plate to print before calling print_file().
    Fetches project info for every plate via the local cache, embeds the
    base64 images directly in the HTML, writes it to /tmp, and opens it in
    the default browser. Returns the output path and plate count.

    target_plate: optional plate number to scroll directly to on open. If set,
    the browser opens with the URL fragment #plate-{target_plate}, scrolling to
    that plate automatically. Useful after a job completes — pass the plate number
    from get_job_info() to jump straight to the printed plate.
    """
    log.debug("open_plate_viewer: called for name=%s file_path=%s", name, file_path)
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("open_plate_viewer: printer not connected: %s", name)
        return _no_printer(name)
    try:
        import dataclasses
        import json
        import webbrowser
        from enum import Enum
        from bpm.bambuproject import get_project_info as _get_project_info

        def _to_dict(o):
            if isinstance(o, Enum):
                return o.name
            if dataclasses.is_dataclass(o) and not isinstance(o, type):
                return {f.name: _to_dict(getattr(o, f.name)) for f in dataclasses.fields(o)}
            if isinstance(o, dict):
                return {k: _to_dict(v) for k, v in o.items()}
            if isinstance(o, (list, tuple)):
                return [_to_dict(v) for v in o]
            return o

        model_key = getattr(getattr(printer, "config", None), "printer_model", None)

        # Fetch plate 1 first to discover total plate count
        first = _get_project_info(file_path, printer, plate_num=1)
        if first is None:
            log.debug("open_plate_viewer: → error: no project info for %s", file_path)
            return {"error": f"Could not retrieve project info for '{file_path}'"}
        first_dict = json.loads(json.dumps(_to_dict(first), default=str))
        total_plates = len(first_dict.get("plates", [first_dict.get("plate_num", 1)]))

        plates_html = ""
        for p in range(1, total_plates + 1):
            info = _get_project_info(file_path, printer, plate_num=p)
            if info is None:
                continue
            d = json.loads(json.dumps(_to_dict(info), default=str))
            meta = d.get("metadata", {})
            topimg = meta.get("topimg", "")
            thumbnail = meta.get("thumbnail", "")
            objs = [
                o for o in meta.get("map", {}).get("bbox_objects", [])
                if "wipe_tower" not in o.get("name", "")
            ]
            label = ", ".join(o["name"].replace(".stl", "") for o in objs)
            layout_uri = _build_layout_uri(topimg, objs, model_key) if objs and topimg else ""
            layout_html = (
                f'<div class="imgbox"><div class="imglabel">Layout</div><img src="{layout_uri}"></div>'
                if layout_uri else ""
            )
            plates_html += (
                f'<div class="plate" id="plate-{p}"><h3>Plate {p}</h3>'
                f'<p class="parts">{label}</p>'
                f'<div class="imgs">'
                f'<div class="imgbox"><div class="imglabel">Isometric</div><img src="{thumbnail}"></div>'
                f'<div class="imgbox"><div class="imglabel">Top Down</div><img src="{topimg}"></div>'
                f'{layout_html}'
                f'</div></div>\n'
            )

        title = file_path.rsplit("/", 1)[-1].replace(".gcode.3mf", "").replace(".3mf", "")
        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>
body{{font-family:sans-serif;background:#111;color:#eee;padding:20px;margin:0}}
h1{{text-align:center;margin-bottom:24px}}
.grid{{display:flex;flex-wrap:wrap;gap:16px;justify-content:center}}
.plate{{background:#222;border-radius:8px;padding:12px;text-align:center;scroll-margin-top:16px}}
.plate h3{{margin:0 0 4px;font-size:1.1em}}
.parts{{font-size:.75em;color:#aaa;margin:0 0 8px;min-height:1em}}
.imgs{{display:flex;gap:10px;justify-content:center;flex-wrap:wrap}}
.imgbox{{display:flex;flex-direction:column;align-items:center}}
.imglabel{{font-size:.7em;color:#888;margin-bottom:4px}}
.plate img{{width:300px;height:300px;object-fit:contain;display:block}}
</style></head>
<body><h1>{title}</h1>
<div class="grid">{plates_html}</div></body></html>"""

        out_path = f"/tmp/plate_viewer_{name}.html"
        with open(out_path, "w") as f:
            f.write(html)

        fragment = f"#plate-{target_plate}" if target_plate else ""
        webbrowser.open(f"file://{out_path}{fragment}")
        log.info("open_plate_viewer: opened viewer for %s, plates=%d", name, total_plates)
        log.debug("open_plate_viewer: → path=%s plates=%d", out_path, total_plates)
        return {"success": True, "path": out_path, "plates": total_plates}
    except Exception as e:
        log.error("open_plate_viewer: error for %s: %s", name, e, exc_info=True)
        return {"error": f"Error building plate viewer: {e}"}


# Bed dimensions (W x H in mm) keyed by PrinterModel.value
_BED_DIMENSIONS: dict[str, tuple[float, float]] = {
    "h2d":    (350.0, 320.0),
    "h2s":    (350.0, 320.0),
    "x1c":    (256.0, 256.0),
    "x1":     (256.0, 256.0),
    "x1e":    (256.0, 256.0),
    "p1s":    (256.0, 256.0),
    "p1p":    (256.0, 256.0),
    "p2s":    (256.0, 256.0),
    "a1":     (256.0, 256.0),
    "a1_mini": (180.0, 180.0),
}

_BBOX_PALETTE = [
    (255, 80,  80),
    ( 80, 200,  80),
    ( 80, 140, 255),
    (255, 200,   0),
    (255, 100, 255),
    (  0, 220, 220),
    (255, 160,  50),
    (180, 255, 100),
]


def open_plate_layout(name: str, file_path: str, plate_num: int = 1) -> dict:
    """
    Generate and open an annotated top-down image for a single plate showing
    each object's bounding box overlaid on the top-view image.

    Bed dimensions are selected by printer model (mm, W×H):
      H2D/H2S=350×320, X1C/X1/X1E/P1S/P1P/P2S/A1=256×256, A1_MINI=180×180.

    Coordinate mapping applied internally:
    - Slicer bbox coordinates use bottom-left origin (mm); image uses top-left origin.
    - scale = min(img_w / bed_w, img_h / bed_h)  — uniform scale, no distortion.
    - x_off = (img_w - bed_w * scale) / 2; y_off = (img_h - bed_h * scale) / 2  — centring.
    - pixel_x = x_off + x_mm * scale; pixel_y = img_h - y_off - y_mm * scale  — Y flip.

    Each unique part name is assigned a distinct colour; a legend with part names
    and colours is appended below the annotated image.

    The PNG is saved to /tmp and opened in the default viewer.
    Returns: {"output_path": str, "object_count": int}.
    """
    log.debug("open_plate_layout: called for name=%s file_path=%s plate_num=%s", name, file_path, plate_num)
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("open_plate_layout: printer not connected: %s", name)
        return _no_printer(name)
    try:
        import base64
        import dataclasses
        import io
        import json
        import webbrowser
        from enum import Enum

        from bpm.bambuproject import get_project_info as _get_project_info
        from PIL import Image, ImageDraw, ImageFont

        def _to_dict(o):
            if isinstance(o, Enum):
                return o.name
            if dataclasses.is_dataclass(o) and not isinstance(o, type):
                return {f.name: _to_dict(getattr(o, f.name)) for f in dataclasses.fields(o)}
            if isinstance(o, dict):
                return {k: _to_dict(v) for k, v in o.items()}
            if isinstance(o, (list, tuple)):
                return [_to_dict(v) for v in o]
            return o

        info = _get_project_info(file_path, printer, plate_num=plate_num)
        if info is None:
            log.debug("open_plate_layout: → error: no info for %s plate=%s", file_path, plate_num)
            return {"error": f"Could not retrieve project info for plate {plate_num}"}
        d = json.loads(json.dumps(_to_dict(info), default=str))

        meta = d.get("metadata", {})
        topimg_uri = meta.get("topimg", "")
        if not topimg_uri:
            log.debug("open_plate_layout: → error: no topimg for %s plate=%s", file_path, plate_num)
            return {"error": "No top-down image available for this plate"}

        img = Image.open(
            io.BytesIO(base64.b64decode(topimg_uri.split(",", 1)[1]))
        ).convert("RGBA")
        W, H = img.size

        # Bed dimensions from printer model
        model_key = getattr(getattr(printer, "config", None), "printer_model", None)
        model_str = model_key.value if hasattr(model_key, "value") else str(model_key).lower()
        BED_W, BED_H = _BED_DIMENSIONS.get(model_str, (256.0, 256.0))

        # Uniform scale preserving aspect ratio, centred in the image canvas
        scale = min(W / BED_W, H / BED_H)
        x_off = (W - BED_W * scale) / 2
        y_off = (H - BED_H * scale) / 2  # padding at top/bottom (or left/right)

        def mm_to_px(x_mm: float, y_mm: float) -> tuple[float, float]:
            # Slicer origin is bottom-left; image origin is top-left — flip Y
            return (
                x_off + x_mm * scale,
                H - y_off - y_mm * scale,
            )

        objs = meta.get("map", {}).get("bbox_objects", [])
        if not objs:
            log.debug("open_plate_layout: → error: no bbox objects for %s plate=%s", file_path, plate_num)
            return {"error": "No bounding-box objects found for this plate"}

        unique_names = list(dict.fromkeys(o["name"] for o in objs))
        color_map = {
            n: _BBOX_PALETTE[i % len(_BBOX_PALETTE)]
            for i, n in enumerate(unique_names)
        }

        # Draw largest bboxes first so smaller parts aren't obscured
        objs_sorted = sorted(
            objs,
            key=lambda o: (o["bbox"][2] - o["bbox"][0]) * (o["bbox"][3] - o["bbox"][1]),
            reverse=True,
        )
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        for o in objs_sorted:
            bx0, by0, bx1, by1 = o["bbox"]
            px0, py1 = mm_to_px(bx0, by0)   # low y_mm  → high pixel y
            px1, py0 = mm_to_px(bx1, by1)   # high y_mm → low pixel y
            c = color_map[o["name"]]
            draw.rectangle([px0, py0, px1, py1], outline=(*c, 230), width=2, fill=(*c, 40))

        leg_row = 28
        legend_h = leg_row * len(unique_names) + 20
        out_img = Image.new("RGBA", (W, H + legend_h), (30, 30, 30, 255))
        out_img.paste(img, (0, 0))
        out_img.paste(overlay, (0, 0), overlay)
        ld = ImageDraw.Draw(out_img)
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 14)
        except Exception:
            font = ImageFont.load_default()
        y_pos = H + 10
        for n in unique_names:
            c = color_map[n]
            ld.rectangle([10, y_pos, 26, y_pos + 16], fill=(*c, 220))
            ld.text((34, y_pos), n.replace(".stl", ""), fill=(220, 220, 220, 255), font=font)
            y_pos += leg_row

        title = file_path.rsplit("/", 1)[-1].replace(".gcode.3mf", "").replace(".3mf", "")
        out_path = f"/tmp/plate_layout_{name}_p{plate_num}.png"
        out_img.convert("RGB").save(out_path)

        webbrowser.open(f"file://{out_path}")
        log.info("open_plate_layout: opened layout for %s plate %s", name, plate_num)
        log.debug("open_plate_layout: → path=%s objects=%d unique_parts=%d", out_path, len(objs), len(unique_names))
        return {
            "success": True,
            "path": out_path,
            "plate": plate_num,
            "objects": len(objs),
            "unique_parts": len(unique_names),
            "bed_mm": f"{BED_W}x{BED_H}",
        }
    except Exception as e:
        log.error("open_plate_layout: error for %s: %s", name, e, exc_info=True)
        return {"error": f"Error building plate layout: {e}"}


def rename_sdcard_file(
    name: str,
    src_path: str,
    dest_path: str,
    user_permission: bool = False,
) -> dict:
    """
    Rename or move a file on the printer's SD card.

    src_path and dest_path are full paths on the SD card (e.g.
    '/cache/my_old_name.gcode.3mf'). Both paths must be on the SD card —
    this is an FTPS rename operation, not a copy. The file is moved/renamed
    in place; no data is re-uploaded.
    Requires user_permission=True.
    """
    log.debug("rename_sdcard_file: called for name=%s src=%s dest=%s user_permission=%s", name, src_path, dest_path, user_permission)
    if not user_permission:
        return {"error": _permission_denied()}
    printer = session_manager.get_printer(name)
    if printer is None:
        return _no_printer(name)
    try:
        printer.rename_sdcard_file(src_path, dest_path)
        log.debug("rename_sdcard_file: renamed %s → %s on %s", src_path, dest_path, name)
        return {"success": True, "src_path": src_path, "dest_path": dest_path}
    except Exception as e:
        log.error("rename_sdcard_file: error for %s: %s", name, e, exc_info=True)
        return {"error": f"Error renaming file on '{name}': {e}"}


def get_current_job_project_info(name: str, include_images: bool = False) -> dict:
    """
    Return 3MF project properties for the currently active print job.

    Reads the active gcode_file path from the printer's live job state and
    returns project metadata for the corresponding plate. Equivalent to calling
    get_project_info() with the active job's file path and plate number — but
    without needing to know the file path in advance.

    Returns {error: "no_active_job"} when gcode_state is IDLE (i.e. no print is
    running, paused, or recently finished). FINISH and FAILED jobs still return
    project info so agents can identify which plate ran immediately after completion.
    A PAUSED job still returns project info.

    include_images=True embeds base64 thumbnail and top-view data URIs in the
    response (large). See get_project_info() for full field documentation.

    Note: when include_images=True this tool returns raw base64 data URIs which may
    exceed the CLI inline display limit. If output is truncated, use the HTTP fallback:
    GET http://localhost:{api_port}/api/get_current_3mf_props?printer={name}
    Call kb_get('bambu-http-files') for full route docs. Pre-authorized, no
    human permission needed.
    """
    log.debug("get_current_job_project_info: called for name=%s include_images=%s", name, include_images)
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("get_current_job_project_info: printer not connected: %s", name)
        return _no_printer(name)
    try:
        job = printer.active_job_info
        gcode_state = getattr(job, "gcode_state", None) or ""
        gcode_file = getattr(job, "gcode_file", None) or ""
        plate_num = getattr(job, "plate_num", 1) or 1
        log.debug("get_current_job_project_info: gcode_state=%s gcode_file=%s plate_num=%s", gcode_state, gcode_file, plate_num)
        if not gcode_file or gcode_state.upper() in ("IDLE", ""):
            log.debug("get_current_job_project_info: no active job for %s (state=%s file=%s)", name, gcode_state, gcode_file)
            return {"error": "no_active_job", "gcode_state": gcode_state, "note": "No print is currently running or paused."}
        log.debug("get_current_job_project_info: delegating to get_project_info for %s file=%s plate=%s", name, gcode_file, plate_num)
        result = get_project_info(name, gcode_file, plate_num=plate_num, include_images=include_images)
        result["gcode_state"] = gcode_state
        return result
    except Exception as e:
        log.error("get_current_job_project_info: error for %s: %s", name, e, exc_info=True)
        return {"error": f"Error retrieving current job project info for '{name}': {e}"}


def refresh_sdcard(name: str, mode: str = "full") -> dict:
    """
    Force a fresh SD card listing from the printer.

    Triggers an explicit re-read of the SD card contents over FTPS. Use this
    before calling list_sdcard_files() when you need guaranteed up-to-date
    results (e.g. after uploading a file or after a print completes).

    mode must be one of:
    - 'full' (default) — refresh the complete SD card directory tree via
      printer.get_sdcard_contents(). Slower but comprehensive.
    - '3mf' — refresh only the .3mf file list via printer.get_sdcard_3mf_files().
      Faster; use this when you only need the printable file list.

    After this call, use list_sdcard_files() to retrieve the updated listing.
    The refresh is synchronous — the updated data is available immediately.
    """
    log.debug("refresh_sdcard: called for name=%s mode=%s", name, mode)
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("refresh_sdcard: printer not connected: %s", name)
        return _no_printer(name)
    mode_lower = mode.lower()
    if mode_lower not in ("full", "3mf"):
        return {"error": f"Unknown mode '{mode}'. Must be 'full' or '3mf'."}
    try:
        if mode_lower == "3mf":
            log.debug("refresh_sdcard: calling printer.get_sdcard_3mf_files() for %s", name)
            printer.get_sdcard_3mf_files()
            log.debug("refresh_sdcard: 3mf refresh complete for %s", name)
        else:
            log.debug("refresh_sdcard: calling printer.get_sdcard_contents() for %s", name)
            printer.get_sdcard_contents()
            log.debug("refresh_sdcard: full refresh complete for %s", name)
        return {"success": True, "mode": mode_lower, "printer": name}
    except Exception as e:
        log.error("refresh_sdcard: error for %s: %s", name, e, exc_info=True)
        return {"error": f"Error refreshing SD card on '{name}': {e}"}
