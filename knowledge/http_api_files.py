"""
http_api_files.py — File management routes for the bambu-mcp HTTP REST API.

Sub-topic of http_api. Access via get_knowledge_topic('http_api/files').
"""

from __future__ import annotations

HTTP_API_FILES_TEXT: str = """
# HTTP API — File Management Routes

Base URL: `http://localhost:{api_port}` — call `get_server_info()` or `GET /api/server_info`
Read routes: GET. Write routes: PATCH (partial resource updates), POST (actions/commands), DELETE (resource destruction) — all accept params as query string, form body, or JSON body.
All routes accept `?printer=<name>` (or `printer` in POST body) to select the target printer.

---

## SD Card Listing

### GET /api/get_sdcard_contents

Return the full SD card directory listing.

Returns a nested dict representing the SD card file tree. May be large for cards with many
files. Use subdirectory-scoped queries when possible.

### POST /api/refresh_sdcard_contents

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

Trigger a full SD card contents refresh from the printer.

Forces a fresh FTPS directory scan. Returns the updated listing.

### GET /api/get_sdcard_3mf_files

Return a list of .3mf files on the SD card.

Filtered listing — only `.3mf` project files are returned.

### POST /api/refresh_sdcard_3mf_files

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

Trigger a refresh of the .3mf file listing from the SD card.

Returns the updated `.3mf` file list after rescanning.

---

## 3MF Project Metadata

### GET /api/get_3mf_props_for_file

Return 3MF project properties for a file on the SD card.

Query parameters:
- `file` (required) — full SD card path to the .3mf file, e.g. `/cache/myjob.gcode.3mf`

Returns plate list, filament usage, AMS mapping, and bbox objects for the first plate.
Equivalent to the MCP `get_project_info(name, file_path, plate_num=1)` tool.

### GET /api/get_current_3mf_props

Return 3MF project properties for the currently active print job.

Uses the job filename from live telemetry. Returns the same shape as
`get_3mf_props_for_file`. Returns `{"error": "no active job"}` when idle.

---

## File Operations

### DELETE /api/delete_sdcard_file

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

Delete a file or folder from the SD card.

Query parameters:
- `file` (required) — full SD card path, e.g. `/cache/myjob.gcode.3mf`
  (trailing slash on path = delete directory)

⚠️ Irreversible. Returns `{"success": true}`.

### POST /api/make_sdcard_directory

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

Create a directory on the SD card.

Query parameters:
- `dir` (required) — full path for the new directory, e.g. `/myproject`

Returns the updated SD card listing.

### PATCH /api/rename_sdcard_file

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

Rename or move an SD card file.

Query parameters:
- `src` (required) — source path on the SD card
- `dest` (required) — destination path on the SD card

Both paths must be on the SD card — no data is re-uploaded. Returns `{"success": true}`.

### GET,POST /api/download_file_from_printer

Download a file from the printer SD card and return it.

Query parameters:
- `src` (required) — SD card path to download, e.g. `/cache/myjob.gcode.3mf`

Returns the file as a binary response with appropriate Content-Disposition header.
Can also be POST with the path in the request body.

### POST /api/upload_file_to_printer

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

Upload a local file to the printer SD card.

Query parameters:
- `src` (required) — filename in the server's local uploads directory
- `dest` (optional) — destination path on the SD card (defaults to `/cache/<filename>`)

The file must already exist in the server's uploads directory. To send a file to the
server first, use `/api/upload_file_to_host`.
Returns `{"success": true}`.

### GET,POST /api/upload_file_to_host

Upload a file to the local uploads directory on the bambu-mcp server.

POST multipart/form-data with the file in a field named `file`.
Returns `{"success": true, "filename": "<saved filename>"}`.
After uploading, use `/api/upload_file_to_printer` to push it to the printer SD card.
"""
