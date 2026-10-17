"""Parser factory: routes (kind, params) to a document parser backend.

A parser turns one file into normalized ParsedBlocks so every backend looks
identical to the rest of the pipeline. Mirrors splitter_factory / retriever_factory.

Kinds:
- "pypdf"        naive page-text baseline (no layout/table awareness)
- "unstructured" Unstructured hosted Serverless API (ship-fast)
- "docling"      self-host arm — local layout+table model (optional [docling] extra)
- "excel"        header-propagated row-group chunks for .xlsx workbooks
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from pypdf import PdfReader


@dataclass
class ParsedBlock:
    """One logical element of a document.

    `content` is the text that gets chunked + embedded. `html` is set only for
    tables (the structure TEDS scores). Keeping both means a table can be
    embedded as text AND scored for structural fidelity from the same object.
    """

    kind: Literal["text", "table", "figure"]
    content: str
    html: str | None = None
    metadata: dict = field(default_factory=dict)


class Parser(Protocol):
    """Structural type every backend satisfies (like a TS interface)."""

    def parse(self, path: str | Path) -> list[ParsedBlock]: ...


class _PyPdfParser:
    """Baseline: extract page text only. No layout or table awareness.

    This is the control arm — it reproduces the current ingest behaviour so the
    eval can measure what a smarter parser actually buys.
    """

    def __init__(self, params: dict):
        # The pypdf baseline has no tuning params today; the argument is accepted
        # only for factory-call symmetry with _UnstructuredParser (reserved use).
        pass

    def parse(self, path: str | Path) -> list[ParsedBlock]:
        reader = PdfReader(str(path))
        blocks: list[ParsedBlock] = []
        for i, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if not text:
                continue  # don't emit empty blocks — they pollute retrieval
            blocks.append(
                ParsedBlock(
                    kind="text",
                    content=text,
                    metadata={"page": i, "source_file": str(path)},
                )
            )
        return blocks


# Unstructured element types we treat as tables. Everything else is text.
_TABLE_TYPES = {"Table"}
_FIGURE_TYPES = {"Image", "Figure"}


class _UnstructuredParser:
    """Unstructured hosted Serverless API backend.

    `client` is injected for tests; in production the factory builds a real
    UnstructuredClient from settings. The heavy SDK import is lazy so importing
    this module never pulls unstructured-client into the serving path.
    """

    def __init__(self, params: dict, client=None):
        self.api_key = params.get("api_key", "")
        self.api_url = params.get("api_url", "")
        self.strategy = params.get("strategy", "hi_res")
        self._client = client

    def _get_client(self):
        if self._client is not None:
            return self._client
        if not self.api_key:
            raise ValueError("unstructured_api_key is not set (parser_kind='unstructured')")
        from unstructured_client import UnstructuredClient

        self._client = UnstructuredClient(api_key_auth=self.api_key, server_url=self.api_url)
        return self._client

    def parse(self, path: str | Path) -> list[ParsedBlock]:
        # The missing-key guard lives in _get_client() (the single construction
        # point); no need to repeat it here.
        client = self._get_client()
        request = self._build_request(path)
        response = client.general.partition(request=request)
        elements = response.elements or []
        return [self._to_block(e, path) for e in elements]

    def _build_request(self, path: str | Path):
        from unstructured_client.models import operations, shared

        p = Path(path)
        with open(p, "rb") as f:
            files = shared.Files(content=f.read(), file_name=p.name)
        return operations.PartitionRequest(
            partition_parameters=shared.PartitionParameters(files=files, strategy=self.strategy)
        )

    @staticmethod
    def _to_block(element: dict, path: str | Path) -> ParsedBlock:
        etype = element.get("type", "")
        meta = element.get("metadata", {}) or {}
        block_meta = {"page": meta.get("page_number"), "source_file": str(path)}
        if etype in _TABLE_TYPES:
            return ParsedBlock(
                kind="table",
                content=element.get("text", ""),
                html=meta.get("text_as_html"),
                metadata=block_meta,
            )
        kind = "figure" if etype in _FIGURE_TYPES else "text"
        return ParsedBlock(kind=kind, content=element.get("text", ""), metadata=block_meta)


class _DoclingParser:
    """Self-host arm: local layout + table-structure model, no API key.

    Heavy deps (docling → torch + models) are imported lazily so importing this
    module never pulls them into the serving path. `converter` is injectable for
    tests, mirroring _UnstructuredParser's client.
    """

    def __init__(self, params: dict, converter=None):
        # do_ocr toggles the OCR stage. Default True (docling's own default) so
        # scanned docs work; pass {"do_ocr": False} for digital PDFs that already
        # carry a text layer — there OCR is a lossy re-derivation of text the file
        # already holds (it dropped invoice cells in testing) and it skips the
        # heavy OCR-engine init.
        self._params = params or {}
        self._converter = converter

    def _get_converter(self):
        if self._converter is None:
            try:
                from docling.datamodel.base_models import InputFormat
                from docling.datamodel.pipeline_options import EasyOcrOptions, PdfPipelineOptions
                from docling.document_converter import DocumentConverter, PdfFormatOption
            except ImportError as e:
                raise ImportError(
                    "docling is not installed (parser_kind='docling'). "
                    "Install the optional extra: uv sync --extra docling"
                ) from e
            opts = PdfPipelineOptions()
            opts.do_ocr = self._params.get("do_ocr", True)
            opts.do_table_structure = True
            if opts.do_ocr:
                # Pin the OCR engine. docling 2.101's default OcrAutoOptions
                # auto-selects and picked a broken rapidocr install here (missing
                # arch_config.yaml). EasyOCR is pip-only, needs no system binary,
                # and reuses the torch docling already pulled in.
                opts.ocr_options = EasyOcrOptions()
            self._converter = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
            )
        return self._converter

    def parse(self, path: str | Path) -> list[ParsedBlock]:
        from docling_core.types.doc import PictureItem, TableItem, TextItem

        doc = self._get_converter().convert(str(path)).document
        blocks: list[ParsedBlock] = []
        for item, _level in doc.iterate_items():  # iterate_items() yields in reading order
            meta = {"page": self._page_of(item), "source_file": str(path)}
            if isinstance(item, TableItem):
                blocks.append(
                    ParsedBlock(
                        kind="table",
                        content=item.export_to_markdown(doc=doc),  # text for embedding
                        html=item.export_to_html(doc=doc),  # structure for TEDS
                        metadata=meta,
                    )
                )
            elif isinstance(item, PictureItem):
                caption = item.caption_text(doc) or ""
                blocks.append(ParsedBlock(kind="figure", content=caption, metadata=meta))
            elif isinstance(item, TextItem):
                text = (item.text or "").strip()
                if text:  # skip empties — they pollute retrieval (same rule as pypdf)
                    blocks.append(ParsedBlock(kind="text", content=text, metadata=meta))
        return blocks

    @staticmethod
    def _page_of(item) -> int | None:
        """1-based page from the item's provenance, or None if unknown."""
        prov = getattr(item, "prov", None)
        return prov[0].page_no if prov else None


class _ExcelParser:
    """Header-propagated row-group chunking for .xlsx workbooks.

    Each sheet → N kind="table" blocks: a context line (file, sheet, row range,
    any leading title rows) + a markdown table whose header row repeats in every
    block, so each chunk is self-describing (contextual chunk header pattern).
    Leading rows with <2 filled cells are treated as titles, not headers —
    real corpus sheets open with a one-cell title row. Trailing styled-but-empty
    cells are trimmed (openpyxl reports 16k columns on such sheets).
    """

    def __init__(self, params: dict):
        self.rows_per_chunk = params.get("rows_per_chunk", None)

    def parse(self, path: str | Path) -> list[ParsedBlock]:
        import openpyxl  # lazy — serving path never imports it

        from app.config import settings

        rows_per_chunk = self.rows_per_chunk or settings.excel_rows_per_chunk
        wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
        try:
            blocks: list[ParsedBlock] = []
            for ws in wb.worksheets:
                blocks.extend(self._sheet_blocks(ws, Path(path), rows_per_chunk))
            return blocks
        finally:
            wb.close()  # read_only mode holds the file handle until closed

    def _sheet_blocks(self, ws, path: Path, rows_per_chunk: int) -> list[ParsedBlock]:
        rows: list[tuple[int, list[str]]] = []
        for idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
            vals = list(row)
            while vals and _is_blank(vals[-1]):
                vals.pop()
            if vals:
                rows.append((idx, [_format_cell(v) for v in vals]))
        if not rows:
            return []

        title_lines: list[str] = []
        header: list[str] | None = None
        data: list[tuple[int, list[str]]] = []
        for i, (_idx, vals) in enumerate(rows):
            if len([v for v in vals if v]) >= 2:
                header, data = vals, rows[i + 1 :]
                break
            title_lines.append(" ".join(v for v in vals if v))
        if header is None:  # degenerate sheet: single-cell rows only
            header, data = rows[0][1], rows[1:]
            title_lines = []
        if not data:
            return []

        blocks: list[ParsedBlock] = []
        for start in range(0, len(data), rows_per_chunk):
            group = data[start : start + rows_per_chunk]
            row_start, row_end = group[0][0], group[-1][0]
            context = f"{path.name} — sheet '{ws.title}' — rows {row_start}–{row_end}"
            if title_lines:
                context += " — " + " | ".join(title_lines)
            table = _markdown_table(header, [vals for _, vals in group])
            blocks.append(
                ParsedBlock(
                    kind="table",
                    content=f"{context}\n\n{table}",
                    metadata={
                        "sheet": ws.title,
                        "row_start": row_start,
                        "row_end": row_end,
                        "section": f"{ws.title} · rows {row_start}–{row_end}",
                        "source_file": str(path),
                    },
                )
            )
        return blocks


def _is_blank(v) -> bool:
    return v is None or str(v).strip() == ""


def _format_cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else f"{v:.2f}"
    return str(v).strip().replace("|", "\\|")


def _markdown_table(header: list[str], rows: list[list[str]]) -> str:
    width = max(len(header), *(len(r) for r in rows))

    def pad(r: list[str]) -> list[str]:
        return list(r) + [""] * (width - len(r))

    lines = ["| " + " | ".join(pad(header)) + " |", "|" + " --- |" * width]
    lines += ["| " + " | ".join(pad(r)) + " |" for r in rows]
    return "\n".join(lines)


def get_parser(kind: str, params: dict) -> Parser:
    """Build a parser for the given kind. Settings are read lazily so importing
    this module doesn't require the full app config (secrets) to be present.
    """
    if kind == "pypdf":
        return _PyPdfParser(params)
    if kind == "unstructured":
        from app.config import settings

        merged = {
            "api_key": params.get("api_key", settings.unstructured_api_key),
            "api_url": params.get("api_url", settings.unstructured_api_url),
            **{k: v for k, v in params.items() if k not in ("api_key", "api_url")},
        }
        return _UnstructuredParser(merged)
    if kind == "docling":
        return _DoclingParser(params)
    if kind == "excel":
        return _ExcelParser(params)
    raise ValueError(
        f"Unknown parser kind: {kind!r}. Expected 'pypdf', 'unstructured', 'docling', 'excel'."
    )
