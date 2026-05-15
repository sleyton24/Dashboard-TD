"""Storage helpers para adjuntos de jobs.

Los archivos se guardan en disco (no en DB) bajo ATTACHMENTS_DIR/{job_id}/.
La tabla `job_attachments` solo guarda metadata (nombre original, path,
mime, tamaño).

Soportamos hoy:
    .xlsx / .xls   - parseo con openpyxl, devuelve sheets como texto.
    .pdf           - parseo con pypdf, devuelve texto.
    .csv / .tsv    - lectura cruda.
    .txt / .md     - lectura cruda.
    .json          - lectura + pretty-print.

Otros tipos se rechazan en el upload.
"""
from __future__ import annotations

import os
import re
from io import BytesIO
from pathlib import Path
from typing import Any

import structlog

from jarvis_orch.settings import get_settings

logger = structlog.get_logger(__name__)


_ALLOWED_EXTS = frozenset({
    ".xlsx", ".xls",
    ".pdf",
    ".csv", ".tsv",
    ".txt", ".md",
    ".json",
})

# Texto representativo máximo devuelto por archivo (para no inflar el contexto del LLM)
_MAX_TEXT_CHARS = 40_000
_MAX_ROWS_PER_SHEET = 200

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(name: str) -> str:
    """Sanitiza el nombre original para no romper el sistema de archivos.

    Mantiene letras, números, punto, guion bajo y guion. Reemplaza todo lo
    demás por `_`. Trunca a 200 chars (límite conservador en ext4).
    """
    base = os.path.basename(name)  # quita rutas
    cleaned = _SAFE_NAME.sub("_", base).strip("._") or "file"
    return cleaned[:200]


def ext_of(name: str) -> str:
    return Path(name).suffix.lower()


def is_allowed(name: str) -> bool:
    return ext_of(name) in _ALLOWED_EXTS


def allowed_extensions() -> list[str]:
    return sorted(_ALLOWED_EXTS)


def job_dir(job_id: str) -> Path:
    """Devuelve el directorio de un job (no lo crea)."""
    return Path(get_settings().ATTACHMENTS_DIR) / job_id


def ensure_job_dir(job_id: str) -> Path:
    p = job_dir(job_id)
    p.mkdir(parents=True, exist_ok=True)
    # 700 — solo el user de servicio
    try:
        os.chmod(p, 0o700)
    except PermissionError:
        pass
    return p


def write_file(job_id: str, original_name: str, data: bytes) -> Path:
    """Guarda el archivo bajo `<ATTACHMENTS_DIR>/<job_id>/<safe-name>` y
    devuelve el path absoluto. Si ya existe un archivo con ese nombre,
    le agrega sufijo numérico."""
    folder = ensure_job_dir(job_id)
    safe = safe_filename(original_name)
    target = folder / safe
    counter = 1
    while target.exists():
        stem, suffix = os.path.splitext(safe)
        target = folder / f"{stem}__{counter}{suffix}"
        counter += 1
    target.write_bytes(data)
    return target


def remove_job_files(job_id: str) -> None:
    """Borra todo el directorio de un job. Best-effort."""
    folder = job_dir(job_id)
    if not folder.exists():
        return
    for child in folder.iterdir():
        try:
            child.unlink()
        except OSError:
            pass
    try:
        folder.rmdir()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Lectura / parseo
# ---------------------------------------------------------------------------
def read_as_text(path: Path) -> dict[str, Any]:
    """Lee un archivo y devuelve un dict con su contenido textual.

    Returns:
        {
          "filename": <str>,
          "mime": <str>,
          "size_bytes": <int>,
          "ext": <str>,
          "content": <str>,         # texto plano truncado
          "truncated": <bool>,
          "warnings": [<str>],      # opcional
        }
    """
    ext = path.suffix.lower()
    if not path.exists():
        raise FileNotFoundError(f"Adjunto no existe: {path.name}")

    size = path.stat().st_size

    if ext in {".txt", ".md", ".csv", ".tsv"}:
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="replace")
        truncated = len(text) > _MAX_TEXT_CHARS
        return _result(path, size, ext, text[:_MAX_TEXT_CHARS], truncated, [])

    if ext == ".json":
        import json
        raw = path.read_bytes().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
            pretty = json.dumps(parsed, indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            pretty = raw
        truncated = len(pretty) > _MAX_TEXT_CHARS
        return _result(path, size, ext, pretty[:_MAX_TEXT_CHARS], truncated, [])

    if ext in {".xlsx", ".xls"}:
        return _read_xlsx(path, size)

    if ext == ".pdf":
        return _read_pdf(path, size)

    raise ValueError(f"Extensión no soportada: {ext}")


def _read_xlsx(path: Path, size: int) -> dict[str, Any]:
    """Parsea xlsx con openpyxl. Devuelve hasta 200 filas por hoja, todas
    las hojas concatenadas con headers. Calcular fórmulas (data_only=True)."""
    from openpyxl import load_workbook  # noqa: PLC0415 — pesado, lazy

    warnings: list[str] = []
    truncated = False
    try:
        wb = load_workbook(path, data_only=True, read_only=True)
    except Exception as exc:  # noqa: BLE001
        return _result(path, size, ".xlsx", f"[ERROR abriendo xlsx: {exc}]", False, [str(exc)])

    chunks: list[str] = []
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        chunks.append(f"\n=== Hoja: {sheet_name} ===")
        row_count = 0
        for row in ws.iter_rows(values_only=True):
            if row_count >= _MAX_ROWS_PER_SHEET:
                warnings.append(
                    f"Hoja '{sheet_name}' truncada a {_MAX_ROWS_PER_SHEET} filas."
                )
                truncated = True
                break
            cells = ["" if v is None else str(v) for v in row]
            if any(cells):
                chunks.append(" | ".join(cells))
                row_count += 1
    wb.close()

    text = "\n".join(chunks)
    if len(text) > _MAX_TEXT_CHARS:
        text = text[:_MAX_TEXT_CHARS]
        truncated = True
        warnings.append(f"Contenido truncado a {_MAX_TEXT_CHARS} caracteres.")
    return _result(path, size, ".xlsx", text, truncated, warnings)


def _read_pdf(path: Path, size: int) -> dict[str, Any]:
    """Parsea pdf con pypdf. Concatena texto de cada página."""
    from pypdf import PdfReader  # noqa: PLC0415 — lazy

    warnings: list[str] = []
    try:
        reader = PdfReader(str(path))
    except Exception as exc:  # noqa: BLE001
        return _result(path, size, ".pdf", f"[ERROR abriendo pdf: {exc}]", False, [str(exc)])

    parts: list[str] = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            t = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Página {i}: error al extraer ({exc!r}).")
            continue
        if t:
            parts.append(f"--- Página {i} ---\n{t}")

    text = "\n\n".join(parts)
    truncated = False
    if len(text) > _MAX_TEXT_CHARS:
        text = text[:_MAX_TEXT_CHARS]
        truncated = True
        warnings.append(f"Contenido truncado a {_MAX_TEXT_CHARS} caracteres.")
    return _result(path, size, ".pdf", text, truncated, warnings)


def _result(
    path: Path,
    size: int,
    ext: str,
    content: str,
    truncated: bool,
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "filename": path.name,
        "mime": _guess_mime(ext),
        "size_bytes": size,
        "ext": ext,
        "content": content,
        "truncated": truncated,
        "warnings": warnings,
    }


def _guess_mime(ext: str) -> str:
    return {
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xls": "application/vnd.ms-excel",
        ".pdf": "application/pdf",
        ".csv": "text/csv",
        ".tsv": "text/tab-separated-values",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".json": "application/json",
    }.get(ext, "application/octet-stream")


# ---------------------------------------------------------------------------
# BytesIO variants — útiles cuando el upload aún no fue persistido
# ---------------------------------------------------------------------------
def detect_mime_from_bytes(name: str, data: bytes) -> str:  # noqa: ARG001
    """Detección simple por extensión. Suficiente para los tipos soportados."""
    return _guess_mime(ext_of(name))


def bytes_to_path(_data: bytes) -> BytesIO:
    """Wrapper trivial — usado para tests."""
    return BytesIO(_data)
