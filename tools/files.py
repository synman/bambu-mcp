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


def list_sdcard_files(name: str, path: str = "/") -> dict:
    """
    Return the full SD card directory listing for the named printer.

    Calls get_sdcard_contents() which connects via FTPS and returns the complete
    file tree. The path parameter is informational — the full tree is always returned.
    """
    log.debug("list_sdcard_files: called for name=%s path=%s", name, path)
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("list_sdcard_files: printer not connected: %s", name)
        return _no_printer(name)
    try:
        log.debug("list_sdcard_files: calling printer.get_sdcard_contents() for %s", name)
        contents = printer.get_sdcard_contents()
        if contents is None:
            log.debug("list_sdcard_files: → error: no contents for %s", name)
            return {"error": "Failed to retrieve SD card contents"}
        log.debug("list_sdcard_files: success for %s", name)
        log.debug("list_sdcard_files: → path=%s", path)
        return {"path": path, "contents": contents}
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


def get_project_info(name: str, file_path: str, plate_num: int = 1) -> dict:
    """
    Return 3MF metadata and thumbnail info for a project file on the SD card.

    Parses the .3mf file for the requested plate and returns filament info,
    AMS mapping, thumbnail (data URI), top-view image, and bounding box objects.
    Uses a local cache to avoid repeated FTPS downloads.
    The .3mf file is created by BambuStudio or OrcaSlicer — the slicing applications
    used to prepare 3D model files for Bambu Lab printers. They convert .STL/.3MF model
    files into printable G-code and package everything into a .3mf project file.

    Key fields in the returned dict:
    - metadata.topimg:  Complete base64 data URI (data:image/png;base64,...).
                        Use DIRECTLY as the src attribute of an HTML <img> tag.
                        Do NOT re-fetch, decode, or write to disk — it is self-contained.
    - metadata.thumbnail: Complete base64 data URI (data:image/png;base64,...).
                        Use DIRECTLY as the src attribute of an HTML <img> tag.
                        Isometric perspective thumbnail (vs. top-down for topimg).
    - metadata.map.bbox_objects: List of {name, ...} dicts for objects on this plate.
                        Filter out entries whose name contains 'wipe_tower' to get
                        the human-readable part list.
    - plates:           List of all plate numbers in the file (e.g. [1,2,...,14]).
                        Iterate over this list and call get_project_info once per
                        plate to retrieve all plates.

    Coordinate system for bbox fields:
    - bbox values are [x_min, y_min, x_max, y_max] in millimetres, absolute bed position.
    - Origin (0,0) is BOTTOM-LEFT of the build plate (slicer convention).
    - To map to image pixel coords (origin top-left): flip Y → pixel_y = img_height - (y_mm / bed_h * img_height)
    - Apply uniform scale: scale = min(img_w / bed_w, img_h / bed_h); add centring offsets.
    - Bed dimensions by model (mm, W×H): H2D/H2S=350×320, X1C/X1/X1E/P1S/P1P/P2S/A1=256×256, A1_MINI=180×180
    - Use printer.config.printer_model.value to get the model string for dimension lookup.

    Cross-tool link: bbox_objects[].id values are the identify_id integers required by skip_objects().
    Filter bbox_objects to exclude entries whose name contains 'wipe_tower' to get human-readable part names.
    """
    log.debug("get_project_info: called for name=%s file_path=%s plate_num=%s", name, file_path, plate_num)
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
        import dataclasses
        import json
        from enum import Enum

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

        if dataclasses.is_dataclass(info):
            result = json.loads(
                json.dumps(_to_dict(info), default=str)
            )
            log.debug("get_project_info: → dataclass result for %s plate=%s", file_path, plate_num)
            return result
        log.debug("get_project_info: → dict/str result for %s plate=%s", file_path, plate_num)
        return info if isinstance(info, dict) else {"info": str(info)}
    except Exception as e:
        log.error("get_project_info: error for %s: %s", name, e, exc_info=True)
        return {"error": f"Error getting project info: {e}"}


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


def print_file(
    name: str,
    file_path: str,
    plate_num: int = 1,
    bed_type: str = "auto",
    use_ams: bool = True,
    timelapse: bool = False,
    bed_leveling: bool = True,
    flow_calibration: bool = False,
    user_permission: bool = False,
) -> dict:
    """
    Start printing a .3mf file already stored on the printer's SD card.

    Requires user_permission=True. bed_type must be one of: auto, cool_plate,
    eng_plate, hot_plate, textured_plate (case-insensitive). AMS mapping is
    read automatically from project metadata when use_ams=True.
    Calls printer.print_3mf_file() with the given parameters.
    bed_type values: 'cool_plate' = smooth cold plate (PLA, TPU at low temp).
    'eng_plate' = smooth engineering plate (PETG, PA, ABS). 'hot_plate' = smooth
    high-temp plate (ASA, PC). 'textured_plate' = textured PEI surface (good
    general-purpose adhesion). 'auto' = let the printer decide based on the sliced
    settings in the file.
    use_ams=True = load filament from AMS slots as mapped in the .3mf file.
    use_ams=False = print using only the external spool holder (for single-color
    prints without AMS).
    """
    log.debug("print_file: called for name=%s file_path=%s plate_num=%s bed_type=%s user_permission=%s", name, file_path, plate_num, bed_type, user_permission)
    if not user_permission:
        log.debug("print_file: permission denied for %s", name)
        return {"error": _permission_denied()}
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("print_file: printer not connected: %s", name)
        return _no_printer(name)
    try:
        from bpm.bambutools import PlateType
        bed_enum = (
            PlateType[bed_type.upper()]
            if bed_type and bed_type.upper() in PlateType.__members__
            else PlateType.AUTO
        )
        ams_mapping = ""
        if use_ams:
            try:
                from bpm.bambuproject import get_project_info as _get_project_info
                info = _get_project_info(file_path, printer, plate_num=plate_num)
                if info and hasattr(info, "metadata") and info.metadata:
                    raw = info.metadata.get("ams_mapping", "")
                    ams_mapping = __import__("json").dumps(raw) if isinstance(raw, list) else raw
            except Exception:
                pass
        log.debug("print_file: calling printer.print_3mf_file for %s", name)
        printer.print_3mf_file(
            name=file_path,
            plate=plate_num,
            bed=bed_enum,
            use_ams=use_ams,
            ams_mapping=ams_mapping,
            bedlevel=bed_leveling,
            flow=flow_calibration,
            timelapse=timelapse,
        )
        log.debug("print_file: print started for %s", name)
        log.debug("print_file: → file=%s plate=%s bed_type=%s use_ams=%s", file_path, plate_num, bed_enum.name, use_ams)
        return {
            "success": True,
            "file_path": file_path,
            "plate_num": plate_num,
            "bed_type": bed_enum.name,
            "use_ams": use_ams,
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


def open_plate_viewer(name: str, file_path: str) -> dict:
    """
    Build and open an HTML viewer showing both the isometric thumbnail and
    top-down image for all plates in a 3MF project file on the printer's SD card.

    Opens a browser window showing all plates in the project as thumbnail images
    (isometric view + top-down view side by side). Use this to visually confirm
    which plate to print before calling print_file().
    Fetches project info for every plate via the local cache, embeds the
    base64 images directly in the HTML, writes it to /tmp, and opens it in
    the default browser. Returns the output path and plate count.
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
                f'<div class="plate"><h3>Plate {p}</h3>'
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
.plate{{background:#222;border-radius:8px;padding:12px;text-align:center}}
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

        webbrowser.open(f"file://{out_path}")
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
