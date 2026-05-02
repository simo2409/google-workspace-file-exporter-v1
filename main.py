"""
Download files from Google Drive Workspace URLs.

Usage:
    python main.py <location> <url1> [url2] ...

    <location> must match a key defined in config.json.

Auth setup:
    1. Create a Google Cloud project and enable the Drive API.
    2. Download OAuth 2.0 credentials and save as:
       ~/.config/llmwiki/obs-llmwiki-simone-personal-v1/credentials.json
    3. On first run, a browser window will open for authorization.
       The resulting token is cached in token-drive.json in the same directory.
"""

import argparse
import hashlib
import io
import json
import re
import sys
from datetime import date
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

_SCRIPT_DIR = Path(__file__).parent
_LLMWIKI_CONFIG_DIR = Path.home() / ".config" / "llmwiki" / "obs-llmwiki-simone-personal-v1"

CREDENTIALS_FILE = _LLMWIKI_CONFIG_DIR / "credentials.json"
TOKEN_FILE = _LLMWIKI_CONFIG_DIR / "token-drive.json"
CONFIG_FILE = _SCRIPT_DIR / "config.json"
METADATA_FILE = "_metadata.json"

# Google-native MIME types → (export MIME, extension)
GOOGLE_EXPORT_MAP: dict[str, tuple[str, str]] = {
    "application/vnd.google-apps.document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".docx",
    ),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
    ),
    "application/vnd.google-apps.presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pptx",
    ),
    "application/vnd.google-apps.drawing": ("image/svg+xml", ".svg"),
}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def get_credentials() -> Credentials:
    creds: Credentials | None = None

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                sys.exit(
                    f"credentials.json not found at {CREDENTIALS_FILE}\n"
                    "Download it from the Google Cloud Console (OAuth 2.0 client) "
                    "and place it there (shared across all llmwiki utils)."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())

    return creds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def extract_file_id(url: str) -> str | None:
    patterns = [
        r"/file/d/([a-zA-Z0-9_-]+)",
        r"/d/([a-zA-Z0-9_-]+)",
        r"[?&]id=([a-zA-Z0-9_-]+)",
    ]
    for pattern in patterns:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


def md5_of_file(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        sys.exit(
            "config.json not found. "
            "Create it with a 'locations' key mapping names to paths."
        )
    try:
        config = json.loads(CONFIG_FILE.read_text())
    except json.JSONDecodeError as e:
        sys.exit(f"config.json is not valid JSON: {e}")
    if "locations" not in config or not isinstance(config["locations"], dict):
        sys.exit("config.json must contain a 'locations' object.")
    return config


def resolve_location(config: dict, name: str) -> Path:
    locations: dict = config["locations"]
    if name not in locations:
        known = ", ".join(f'"{k}"' for k in sorted(locations))
        sys.exit(
            f"Unknown location '{name}'. "
            f"Available locations in config.json: {known}"
        )
    return Path(locations[name]).expanduser()


def load_metadata(dest_dir: Path) -> dict:
    meta_path = dest_dir / METADATA_FILE
    if meta_path.exists():
        return json.loads(meta_path.read_text())
    return {}


def save_metadata(dest_dir: Path, metadata: dict) -> None:
    meta_path = dest_dir / METADATA_FILE
    meta_path.write_text(json.dumps(metadata, indent=2))


# ---------------------------------------------------------------------------
# Download logic
# ---------------------------------------------------------------------------


def download_file(service, file_id: str, dest_dir: Path, metadata: dict) -> None:
    try:
        drive_meta = (
            service.files()
            .get(fileId=file_id, fields="id,name,mimeType,md5Checksum,modifiedTime")
            .execute()
        )
    except HttpError as e:
        print(f"  [ERROR] API error fetching metadata: {e}")
        return

    mime_type: str = drive_meta["mimeType"]
    name: str = drive_meta["name"]
    drive_md5: str | None = drive_meta.get("md5Checksum")
    modified_time: str = drive_meta.get("modifiedTime", "")
    is_native = mime_type.startswith("application/vnd.google-apps.")

    # Determine filename and request type
    if is_native:
        export_info = GOOGLE_EXPORT_MAP.get(mime_type)
        if not export_info:
            print(f"  [SKIP] Unsupported Google type '{mime_type}' for '{name}'")
            return
        export_mime, ext = export_info
        filename = name + ext
    else:
        filename = name

    local_path = dest_dir / filename
    cached = metadata.get(file_id, {})

    # Check metadata first — works across days without requiring the file in today's dir
    if is_native:
        if cached.get("modifiedTime") == modified_time:
            print(f"  [SKIP] '{filename}' unchanged (modifiedTime match)")
            return
        if cached:
            print(f"  [UPDATE] '{filename}' changed on Drive, re-downloading...")
    else:
        if drive_md5 and cached.get("md5") == drive_md5:
            print(f"  [SKIP] '{filename}' unchanged (MD5 match)")
            return
        if cached:
            if drive_md5:
                print(f"  [UPDATE] '{filename}' MD5 mismatch, re-downloading...")
            else:
                print(f"  [UPDATE] '{filename}' Drive provides no checksum, re-downloading...")

    if local_path.exists():
        local_path.unlink()

    if is_native:
        request = service.files().export_media(fileId=file_id, mimeType=export_mime)
    else:
        request = service.files().get_media(fileId=file_id)

    # Stream download
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
        pct = int(status.progress() * 100) if status else 0
        print(f"  Downloading '{filename}'... {pct}%", end="\r")

    local_path.write_bytes(buf.getvalue())
    print(f"  [OK] '{filename}' saved.          ")

    # Update cached metadata
    metadata[file_id] = {
        "filename": filename,
        "modifiedTime": modified_time,
        "md5": drive_md5,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download files from Google Drive Workspace URLs."
    )
    parser.add_argument("location", help="Destination location name (defined in config.json)")
    parser.add_argument("urls", nargs="+", help="Google Drive file URLs")
    args = parser.parse_args()

    config = load_config()
    base_dir = resolve_location(config, args.location)

    today = date.today().isoformat()
    dest_dir = base_dir / today
    dest_dir.mkdir(parents=True, exist_ok=True)

    creds = get_credentials()
    service = build("drive", "v3", credentials=creds)

    metadata = load_metadata(base_dir)

    for url in args.urls:
        file_id = extract_file_id(url)
        if not file_id:
            print(f"[ERROR] Cannot extract file ID from: {url}")
            continue
        print(f"Processing {url}")
        try:
            download_file(service, file_id, dest_dir, metadata)
        except Exception as e:
            print(f"  [ERROR] {e}")
        finally:
            save_metadata(base_dir, metadata)

    print(f"\nDone. Files saved to: {dest_dir}/")


if __name__ == "__main__":
    main()
