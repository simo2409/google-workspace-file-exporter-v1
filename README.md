# Google Workspace File Exporter

Downloads files from Google Drive URLs into a dated local folder, with smart change detection to avoid unnecessary re-downloads.

## What it does

- Accepts a **location name** and one or more Google Drive URLs as arguments
- Saves files to `<location_path>/<YYYY-MM-DD>/` (folder named with today's date)
- Skips re-downloading files whose content hasn't changed:
  - **Binary files** (PDF, images, etc.): compared via MD5 checksum provided by the Drive API
  - **Google-native files** (Docs, Sheets, Slides, Drawings): compared via `modifiedTime`
  - If no checksum is available and the file already exists locally, it re-downloads to be safe
- Exports Google-native formats to standard Office formats:
  - Google Docs → `.docx`
  - Google Sheets → `.xlsx`
  - Google Slides → `.pptx`
  - Google Drawings → `.svg`
- Persists metadata in `<location_path>/<date>/_metadata.json` for future comparisons

## Setup

### 1. Install dependencies

```bash
uv sync
```

### 2. Configure download locations

Edit `config.json` to define named locations (arbitrary names pointing to local paths):

```json
{
  "locations": {
    "work": "~/Downloads/drive-exports/work",
    "personal": "~/Downloads/drive-exports/personal"
  }
}
```

Add as many locations as needed. Paths support `~` expansion.

### 3. Configure Google OAuth credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project (or select an existing one)
3. Enable the **Google Drive API** under *APIs & Services → Library*
4. Go to *APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID*
5. Choose **Desktop app** as the application type
6. Download the JSON file and save it as `credentials.json` in this directory

On the first run, a browser window will open asking you to authorize access to your Drive. The resulting token is cached in `token.json` and reused automatically in subsequent runs.

> `credentials.json` and `token.json` are listed in `.gitignore` and must never be committed.

## Usage

```bash
python main.py <location> <url1> [url2] ...
```

`<location>` must match a key defined in `config.json`. The script exits with an error if the name is not found.

**Examples:**

```bash
# Download a file to the "work" location
python main.py work "https://drive.google.com/file/d/FILE_ID/view"

# Download multiple files to the "personal" location
python main.py personal \
  "https://drive.google.com/file/d/ABC123/view" \
  "https://docs.google.com/spreadsheets/d/XYZ456/edit"
```

**Error if location is unknown:**

```
Unknown location 'finance'. Available locations in config.json: "personal", "work"
```

## Supported URL formats

- `https://drive.google.com/file/d/<ID>/view`
- `https://docs.google.com/document/d/<ID>/edit`
- `https://docs.google.com/spreadsheets/d/<ID>/edit`
- `https://docs.google.com/presentation/d/<ID>/edit`
- `https://drive.google.com/open?id=<ID>`

## Output structure

```
~/Downloads/drive-exports/
├── work/
│   └── 2026-05-01/
│       ├── report.pdf
│       ├── budget.xlsx
│       └── _metadata.json    ← internal, used for change detection
└── personal/
    └── 2026-05-01/
        ├── presentation.pptx
        └── _metadata.json
```
