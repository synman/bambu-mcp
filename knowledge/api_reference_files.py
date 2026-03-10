"""
api_reference_files.py — BambuPrinter FTPS file management sub-topic.

Sub-topic of api_reference. Access via get_knowledge_topic('api_reference/files').
"""

from __future__ import annotations

API_REFERENCE_FILES_TEXT: str = """
# BambuPrinter API — File Management (FTPS)

All signatures sourced from bambuprinter.py.
FTPS connects to printer SD card on port 990 (implicit SSL).
Credentials: mqtt_username ("bblp") + access_code.

---

## File Management Methods

#### ftp_connection() -> contextmanager[IoTFTPSClient]
Context manager. Opens FTPS connection (port 990, implicit SSL) to printer SD card.
Closes connection on exit. Use as `with printer.ftp_connection() as ftp:`.

#### get_sdcard_contents() -> dict | None
Returns dict of ALL files on printer SD card. Populates `_sdcard_contents` and
`_sdcard_3mf_files`. Uses FTPS via ftp_connection(). Returns None on failure.

#### get_sdcard_3mf_files() -> dict | None
Returns dict of only .3mf files. Calls get_sdcard_contents() internally.

#### delete_sdcard_file(file: str) -> dict
Deletes file at full path on SD card. Invalidates cached plate metadata for that
file. Updates `_sdcard_contents` and `_sdcard_3mf_files` in-memory. Returns
updated `_sdcard_contents`.

#### delete_sdcard_folder(path: str) -> dict
Recursively deletes folder and all contents. Invalidates all cached plate metadata
under that prefix. Returns updated `_sdcard_contents`.

#### download_sdcard_file(src: str, dest: str) -> None
Downloads file from printer (src = SD card path) to host (dest = local path).

#### upload_sdcard_file(src: str, dest: str) -> dict
Uploads local file (src) to printer (dest). If src ends with .3mf, calls
get_project_info(dest, self, local_file=src) to populate metadata cache.
Returns updated SD card contents.

#### rename_sdcard_file(src: str, dest: str) -> dict
Renames file on SD card via FTPS move. Returns updated SD card contents.

#### make_sdcard_directory(dir: str) -> dict
Creates directory on SD card. Returns updated SD card contents.

#### sdcard_file_exists(path: str) -> bool
Checks whether a specific file exists on the printer's SD card via a live FTPS query
(`ftps.fexists(path)`). Returns `True` if the file is present, `False` otherwise.

Unlike `get_sdcard_contents()` and `get_sdcard_3mf_files()` which use a cached directory
tree, `sdcard_file_exists()` opens a fresh FTPS connection and queries the printer directly —
the result is always current, not stale cache.

**When to use it:**
- Verify a file upload succeeded before starting a print job
- Check whether a .3mf file already exists before uploading to avoid overwriting
- Confirm a file was deleted after calling `delete_sdcard_file()`
- Pre-print validation: ensure the target gcode file is present before calling `print_3mf_file()`

**Path format:** full SD card path, e.g. `"/cache/my_model.gcode.3mf"` or `"/model/parts.3mf"`.
Same path convention used by all other FTPS methods.

**Cost:** opens an FTPS connection for each call — prefer the cached listing tools
(`list_sdcard_files`, `get_sdcard_contents`) for bulk enumeration. Use `sdcard_file_exists()`
only when a single authoritative yes/no answer is needed.

---

## File URL Formats

| Printer series | URL format |
|---|---|
| A1 / P1 series | `file:///sdcard{path}` |
| X1 / H2D series | `ftp://{path}` |

Used internally by print_3mf_file(). See also FTPS operations in
`get_knowledge_topic('protocol/3mf')`.
"""
