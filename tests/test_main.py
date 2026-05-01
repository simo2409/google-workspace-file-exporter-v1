import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import main as m


# ---------------------------------------------------------------------------
# extract_file_id
# ---------------------------------------------------------------------------


class TestExtractFileId:
    def test_file_d_pattern(self):
        url = "https://drive.google.com/file/d/1abc123XYZ/view"
        assert m.extract_file_id(url) == "1abc123XYZ"

    def test_doc_d_pattern(self):
        url = "https://docs.google.com/document/d/1abc-_XYZ/edit"
        assert m.extract_file_id(url) == "1abc-_XYZ"

    def test_id_query_param(self):
        url = "https://drive.google.com/open?id=1abc123"
        assert m.extract_file_id(url) == "1abc123"

    def test_id_query_param_after_other_param(self):
        url = "https://drive.google.com/open?foo=bar&id=1abc123"
        assert m.extract_file_id(url) == "1abc123"

    def test_unrecognised_url_returns_none(self):
        assert m.extract_file_id("https://example.com/no-id") is None

    def test_empty_string_returns_none(self):
        assert m.extract_file_id("") is None

    def test_id_with_hyphens_and_underscores(self):
        url = "https://drive.google.com/file/d/abc-123_XYZ/view"
        assert m.extract_file_id(url) == "abc-123_XYZ"


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_valid_config(self, tmp_path, monkeypatch):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"locations": {"work": "/tmp/work"}}))
        monkeypatch.setattr(m, "CONFIG_FILE", cfg)
        assert m.load_config()["locations"]["work"] == "/tmp/work"

    def test_missing_file_exits(self, tmp_path, monkeypatch):
        monkeypatch.setattr(m, "CONFIG_FILE", tmp_path / "nonexistent.json")
        with pytest.raises(SystemExit):
            m.load_config()

    def test_invalid_json_exits(self, tmp_path, monkeypatch):
        cfg = tmp_path / "config.json"
        cfg.write_text("not { valid } json")
        monkeypatch.setattr(m, "CONFIG_FILE", cfg)
        with pytest.raises(SystemExit):
            m.load_config()

    def test_missing_locations_key_exits(self, tmp_path, monkeypatch):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"other": "value"}))
        monkeypatch.setattr(m, "CONFIG_FILE", cfg)
        with pytest.raises(SystemExit):
            m.load_config()

    def test_locations_not_dict_exits(self, tmp_path, monkeypatch):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"locations": ["a", "b"]}))
        monkeypatch.setattr(m, "CONFIG_FILE", cfg)
        with pytest.raises(SystemExit):
            m.load_config()


# ---------------------------------------------------------------------------
# resolve_location
# ---------------------------------------------------------------------------


class TestResolveLocation:
    def test_known_location(self, tmp_path):
        config = {"locations": {"work": str(tmp_path)}}
        assert m.resolve_location(config, "work") == tmp_path

    def test_unknown_location_exits(self):
        config = {"locations": {"work": "/tmp"}}
        with pytest.raises(SystemExit):
            m.resolve_location(config, "personal")

    def test_expands_tilde(self):
        config = {"locations": {"home": "~/some/path"}}
        result = m.resolve_location(config, "home")
        assert not str(result).startswith("~")


# ---------------------------------------------------------------------------
# load_metadata / save_metadata
# ---------------------------------------------------------------------------


class TestMetadata:
    def test_load_missing_returns_empty_dict(self, tmp_path):
        assert m.load_metadata(tmp_path) == {}

    def test_roundtrip(self, tmp_path):
        data = {
            "file-id": {
                "filename": "doc.docx",
                "modifiedTime": "2026-01-01T00:00:00Z",
                "md5": None,
            }
        }
        m.save_metadata(tmp_path, data)
        assert m.load_metadata(tmp_path) == data

    def test_file_lives_in_base_dir(self, tmp_path):
        m.save_metadata(tmp_path, {"k": "v"})
        assert (tmp_path / "_metadata.json").exists()

    def test_save_overwrites_previous(self, tmp_path):
        m.save_metadata(tmp_path, {"a": 1})
        m.save_metadata(tmp_path, {"b": 2})
        assert m.load_metadata(tmp_path) == {"b": 2}


# ---------------------------------------------------------------------------
# download_file — skip logic
# ---------------------------------------------------------------------------


def _make_service(mime_type: str, modified_time: str, md5: str | None = None):
    drive_meta = {
        "id": "file123",
        "name": "TestDoc",
        "mimeType": mime_type,
        "modifiedTime": modified_time,
    }
    if md5 is not None:
        drive_meta["md5Checksum"] = md5

    svc = MagicMock()
    svc.files.return_value.get.return_value.execute.return_value = drive_meta
    return svc


class TestDownloadFileSkipLogic:
    NATIVE_MIME = "application/vnd.google-apps.document"
    PDF_MIME = "application/pdf"
    MOD_TIME = "2026-01-01T00:00:00Z"
    FILE_ID = "file123"

    # --- native files (Google Docs / Sheets / Slides) ---

    def test_skip_native_when_modified_time_matches(self, tmp_path):
        svc = _make_service(self.NATIVE_MIME, self.MOD_TIME)
        metadata = {
            self.FILE_ID: {"filename": "TestDoc.docx", "modifiedTime": self.MOD_TIME, "md5": None}
        }
        m.download_file(svc, self.FILE_ID, tmp_path, metadata)
        svc.files.return_value.export_media.assert_not_called()

    def test_skip_native_works_even_without_local_file(self, tmp_path):
        """Cross-day skip: metadata present but file not yet in today's dir."""
        svc = _make_service(self.NATIVE_MIME, self.MOD_TIME)
        metadata = {
            self.FILE_ID: {"filename": "TestDoc.docx", "modifiedTime": self.MOD_TIME, "md5": None}
        }
        dest = tmp_path / "2026-05-02"
        dest.mkdir()
        # File does NOT exist in dest — simulates a previous-day download
        m.download_file(svc, self.FILE_ID, dest, metadata)
        svc.files.return_value.export_media.assert_not_called()

    def test_download_native_when_modified_time_changed(self, tmp_path):
        svc = _make_service(self.NATIVE_MIME, "2026-02-01T00:00:00Z")
        mock_downloader = MagicMock()
        mock_downloader.next_chunk.return_value = (MagicMock(progress=lambda: 1.0), True)
        metadata = {
            self.FILE_ID: {"filename": "TestDoc.docx", "modifiedTime": self.MOD_TIME, "md5": None}
        }
        with patch("main.MediaIoBaseDownload", return_value=mock_downloader):
            m.download_file(svc, self.FILE_ID, tmp_path, metadata)
        svc.files.return_value.export_media.assert_called_once()
        assert metadata[self.FILE_ID]["modifiedTime"] == "2026-02-01T00:00:00Z"

    def test_download_native_when_no_cached_metadata(self, tmp_path):
        svc = _make_service(self.NATIVE_MIME, self.MOD_TIME)
        mock_downloader = MagicMock()
        mock_downloader.next_chunk.return_value = (MagicMock(progress=lambda: 1.0), True)
        metadata: dict = {}
        with patch("main.MediaIoBaseDownload", return_value=mock_downloader):
            m.download_file(svc, self.FILE_ID, tmp_path, metadata)
        assert self.FILE_ID in metadata

    # --- non-native files (PDF, images, …) ---

    def test_skip_non_native_when_md5_matches(self, tmp_path):
        svc = _make_service(self.PDF_MIME, self.MOD_TIME, md5="abc123")
        metadata = {
            self.FILE_ID: {"filename": "TestDoc.pdf", "modifiedTime": self.MOD_TIME, "md5": "abc123"}
        }
        m.download_file(svc, self.FILE_ID, tmp_path, metadata)
        svc.files.return_value.get_media.assert_not_called()

    def test_skip_non_native_works_even_without_local_file(self, tmp_path):
        """Cross-day skip for binary files."""
        svc = _make_service(self.PDF_MIME, self.MOD_TIME, md5="abc123")
        metadata = {
            self.FILE_ID: {"filename": "TestDoc.pdf", "modifiedTime": self.MOD_TIME, "md5": "abc123"}
        }
        dest = tmp_path / "2026-05-02"
        dest.mkdir()
        m.download_file(svc, self.FILE_ID, dest, metadata)
        svc.files.return_value.get_media.assert_not_called()

    def test_download_non_native_when_md5_changed(self, tmp_path):
        svc = _make_service(self.PDF_MIME, self.MOD_TIME, md5="newmd5")
        mock_downloader = MagicMock()
        mock_downloader.next_chunk.return_value = (MagicMock(progress=lambda: 1.0), True)
        metadata = {
            self.FILE_ID: {"filename": "TestDoc.pdf", "modifiedTime": self.MOD_TIME, "md5": "oldmd5"}
        }
        with patch("main.MediaIoBaseDownload", return_value=mock_downloader):
            m.download_file(svc, self.FILE_ID, tmp_path, metadata)
        svc.files.return_value.get_media.assert_called_once()
        assert metadata[self.FILE_ID]["md5"] == "newmd5"

    def test_download_non_native_when_drive_has_no_md5(self, tmp_path):
        """Drive provides no checksum → always re-download."""
        svc = _make_service(self.PDF_MIME, self.MOD_TIME, md5=None)
        mock_downloader = MagicMock()
        mock_downloader.next_chunk.return_value = (MagicMock(progress=lambda: 1.0), True)
        metadata = {
            self.FILE_ID: {"filename": "TestDoc.pdf", "modifiedTime": self.MOD_TIME, "md5": None}
        }
        with patch("main.MediaIoBaseDownload", return_value=mock_downloader):
            m.download_file(svc, self.FILE_ID, tmp_path, metadata)
        svc.files.return_value.get_media.assert_called_once()

    # --- unsupported native type ---

    def test_skip_unsupported_native_type(self, tmp_path):
        svc = _make_service("application/vnd.google-apps.unknown", self.MOD_TIME)
        metadata: dict = {}
        m.download_file(svc, self.FILE_ID, tmp_path, metadata)
        assert self.FILE_ID not in metadata
