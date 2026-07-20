from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import subprocess
import zipfile


MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".csv", ".txt", ".png", ".jpg", ".jpeg"}
MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


@dataclass(frozen=True)
class ValidatedAttachment:
    path: Path
    name: str
    media_type: str
    content: bytes


def validate_task_attachment(path_value: Path) -> ValidatedAttachment:
    path = Path(path_value).expanduser().resolve(strict=True)
    if not path.is_file() or path.is_symlink():
        raise ValueError("Task attachment must be a regular file.")
    extension = path.suffix.casefold()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError("Task attachment file type is not allowed.")
    size = path.stat().st_size
    if size > MAX_ATTACHMENT_BYTES:
        raise ValueError("Task attachment exceeds the 25 MB limit.")
    content = path.read_bytes()
    if content.startswith(b"MZ"):
        raise ValueError("Executable task attachments are not allowed.")
    _validate_signature(extension, content, path)
    return ValidatedAttachment(path=path, name=path.name, media_type=MEDIA_TYPES[extension], content=content)


def _validate_signature(extension: str, content: bytes, path: Path) -> None:
    if extension == ".pdf" and not content.startswith(b"%PDF-"):
        raise ValueError("Task attachment signature does not match PDF.")
    if extension == ".png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Task attachment signature does not match PNG.")
    if extension in {".jpg", ".jpeg"} and not content.startswith(b"\xff\xd8\xff"):
        raise ValueError("Task attachment signature does not match JPEG.")
    if extension in {".txt", ".csv"}:
        if b"\x00" in content:
            raise ValueError("Text task attachment contains binary data.")
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Text task attachment must use UTF-8.") from exc
    if extension in {".docx", ".xlsx"}:
        _validate_office_archive(path, extension)


def _validate_office_archive(path: Path, extension: str) -> None:
    if not zipfile.is_zipfile(path):
        raise ValueError("Task attachment signature does not match Office Open XML.")
    required_prefix = "word/" if extension == ".docx" else "xl/"
    with zipfile.ZipFile(path) as archive:
        names = [item.filename.replace("\\", "/") for item in archive.infolist()]
        for name in names:
            pure = PurePosixPath(name)
            if pure.is_absolute() or ".." in pure.parts:
                raise ValueError("Office attachment contains an unsafe archive path.")
            lowered = name.casefold()
            if lowered.endswith("vbaproject.bin") or lowered.endswith(".exe") or lowered.endswith(".dll"):
                raise ValueError("Office attachment contains a macro or executable.")
        if "[Content_Types].xml" not in names or not any(name.startswith(required_prefix) for name in names):
            raise ValueError("Task attachment signature does not match Office document type.")


class WindowsDefenderAttachmentScanner:
    """Small Windows Defender boundary. Returns clean, flagged, or unavailable."""

    def __call__(self, path: Path) -> str:
        executable = self._executable()
        if executable is None:
            return "unavailable"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            result = subprocess.run(
                [str(executable), "-Scan", "-ScanType", "3", "-File", str(Path(path).resolve())],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
                creationflags=flags,
            )
        except (OSError, subprocess.SubprocessError):
            return "unavailable"
        if result.returncode == 0:
            return "clean"
        if result.returncode == 2:
            return "flagged"
        return "unavailable"

    @staticmethod
    def _executable() -> Path | None:
        roots = [
            Path(os.environ.get("ProgramFiles", "")) / "Windows Defender" / "MpCmdRun.exe",
            Path(os.environ.get("ProgramData", "")) / "Microsoft" / "Windows Defender" / "Platform",
        ]
        direct = roots[0]
        if direct.is_file():
            return direct
        platform_root = roots[1]
        if platform_root.is_dir():
            versions = sorted(platform_root.glob("*/MpCmdRun.exe"), reverse=True)
            if versions:
                return versions[0]
        return None
