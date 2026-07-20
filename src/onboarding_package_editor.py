from __future__ import annotations

from pathlib import Path


class DocumentPackageDraftEditor:
    """Ordered, validated source-PDF selection for one immutable package draft."""

    def __init__(self) -> None:
        self._paths: list[Path] = []

    @property
    def paths(self) -> tuple[Path, ...]:
        return tuple(self._paths)

    def add(self, path: Path) -> None:
        self._paths.append(self._validated_pdf(path))

    def replace(self, index: int, path: Path) -> Path:
        current = self._paths[self._index(index)]
        self._paths[index] = self._validated_pdf(path)
        return current

    def remove(self, index: int) -> Path:
        return self._paths.pop(self._index(index))

    def move(self, index: int, destination: int) -> None:
        source_index = self._index(index)
        destination_index = self._index(destination)
        self._paths.insert(destination_index, self._paths.pop(source_index))

    def clear(self) -> None:
        self._paths.clear()

    def _index(self, value: int) -> int:
        index = int(value)
        if index < 0 or index >= len(self._paths):
            raise ValueError("Package document selection is outside the draft.")
        return index

    @staticmethod
    def _validated_pdf(path: Path) -> Path:
        candidate = Path(path)
        if not candidate.is_file() or candidate.is_symlink():
            raise ValueError("Package document must be a regular PDF file.")
        source = candidate.resolve(strict=True)
        if source.suffix.casefold() != ".pdf":
            raise ValueError("Package document must be a regular PDF file.")
        if source.stat().st_size > 25 * 1024 * 1024:
            raise ValueError("Package document exceeds the 25 MB limit.")
        with source.open("rb") as file:
            if file.read(5) != b"%PDF-":
                raise ValueError("Package document signature is not PDF.")
        return source
