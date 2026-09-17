"""Structural markdown analysis for answers rendered in the web panel.

These are assertions about the markdown grammar the panel has to render — a
table whose rows disagree on cell count, an unclosed code fence, an unclosed
bold run. They are deliberately not assertions about wording: nothing here
looks for a phrase a model happens to produce.

The parser is intentionally small. It recognizes the block kinds the output
format contract names (tables, lists, code fences, headings) and treats
everything else as prose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\S")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+\S")
_DELIMITER_CELL_RE = re.compile(r"^:?-{1,}:?$")
_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][a-zA-Z0-9-]*(?:\s[^<>]*)?/?>")
_URL_RE = re.compile(r"https?://", re.IGNORECASE)
_UNESCAPED_PIPE_RE = re.compile(r"(?<!\\)\|")


@dataclass(frozen=True)
class MarkdownTable:
    header: tuple[str, ...]
    body_rows: tuple[tuple[str, ...], ...]
    start_line: int

    @property
    def column_count(self) -> int:
        return len(self.header)


@dataclass(frozen=True)
class MarkdownList:
    items: tuple[str, ...]
    start_line: int


@dataclass(frozen=True)
class MarkdownDocument:
    """Blocks found outside fenced code, plus the defects found while parsing."""

    tables: tuple[MarkdownTable, ...] = ()
    lists: tuple[MarkdownList, ...] = ()
    headings: tuple[str, ...] = ()
    defects: tuple[str, ...] = field(default=())
    prose_lines: tuple[str, ...] = ()

    @property
    def well_formed(self) -> bool:
        return not self.defects


def _split_row(line: str) -> tuple[str, ...]:
    """Cells of one table row, outer pipes dropped, each cell stripped."""
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|") and not stripped.endswith("\\|"):
        stripped = stripped[:-1]
    return tuple(cell.strip() for cell in _UNESCAPED_PIPE_RE.split(stripped))


def _is_delimiter_row(line: str) -> bool:
    cells = _split_row(line)
    return bool(cells) and all(_DELIMITER_CELL_RE.fullmatch(cell) for cell in cells)


def _looks_like_row(line: str) -> bool:
    return "|" in line and bool(line.strip())


def parse_markdown(answer: str) -> MarkdownDocument:
    """Parse the answer body into blocks and record every structural defect."""
    lines = answer.splitlines()
    tables: list[MarkdownTable] = []
    lists: list[MarkdownList] = []
    headings: list[str] = []
    prose: list[str] = []
    defects: list[str] = []

    in_fence = False
    fence_marker = ""
    index = 0
    while index < len(lines):
        line = lines[index]
        fence = _FENCE_RE.match(line)
        if fence is not None:
            if not in_fence:
                in_fence, fence_marker = True, fence.group(1)
            elif fence.group(1) == fence_marker:
                in_fence, fence_marker = False, ""
            index += 1
            continue
        if in_fence:
            index += 1
            continue

        if _HEADING_RE.match(line):
            headings.append(line.strip())
            index += 1
            continue

        if _looks_like_row(line) and index + 1 < len(lines) and _is_delimiter_row(lines[index + 1]):
            table, index, table_defects = _consume_table(lines, index)
            tables.append(table)
            defects.extend(table_defects)
            continue

        if _looks_like_row(line) and not _is_delimiter_row(line):
            # A pipe row with no delimiter beneath it renders as literal text,
            # not as a table. That is the defect users report as "the table
            # came out as one long line".
            if index + 1 >= len(lines) or not _looks_like_row(lines[index + 1]):
                prose.append(line)
                index += 1
                continue
            defects.append(f"line {index + 1}: pipe row block has no delimiter row")
            while index < len(lines) and _looks_like_row(lines[index]):
                index += 1
            continue

        if _LIST_ITEM_RE.match(line):
            block, index = _consume_list(lines, index)
            lists.append(block)
            continue

        if line.strip():
            prose.append(line)
        index += 1

    if in_fence:
        defects.append("unclosed code fence")

    defects.extend(_inline_defects(answer))
    return MarkdownDocument(
        tables=tuple(tables),
        lists=tuple(lists),
        headings=tuple(headings),
        defects=tuple(defects),
        prose_lines=tuple(prose),
    )


def _consume_table(lines: list[str], start: int) -> tuple[MarkdownTable, int, list[str]]:
    header = _split_row(lines[start])
    delimiter = _split_row(lines[start + 1])
    defects: list[str] = []
    if len(delimiter) != len(header):
        defects.append(
            f"line {start + 2}: delimiter row has {len(delimiter)} cells, header has {len(header)}"
        )
    index = start + 2
    body: list[tuple[str, ...]] = []
    while index < len(lines) and _looks_like_row(lines[index]):
        cells = _split_row(lines[index])
        if len(cells) != len(header):
            defects.append(
                f"line {index + 1}: row has {len(cells)} cells, header has {len(header)}"
            )
        body.append(cells)
        index += 1
    if not body:
        defects.append(f"line {start + 1}: table has a header but no body rows")
    return MarkdownTable(header=header, body_rows=tuple(body), start_line=start + 1), index, defects


def _consume_list(lines: list[str], start: int) -> tuple[MarkdownList, int]:
    items: list[str] = []
    index = start
    while index < len(lines):
        line = lines[index]
        if _LIST_ITEM_RE.match(line):
            items.append(line.strip())
            index += 1
            continue
        if line.strip() and line.startswith((" ", "\t")):
            # Continuation of the previous item, not a new one.
            index += 1
            continue
        break
    return MarkdownList(items=tuple(items), start_line=start + 1), index


def _inline_defects(answer: str) -> list[str]:
    """Inline defects that survive block parsing, ignoring fenced code."""
    defects: list[str] = []
    in_fence = False
    fence_marker = ""
    for number, line in enumerate(answer.splitlines(), start=1):
        fence = _FENCE_RE.match(line)
        if fence is not None:
            if not in_fence:
                in_fence, fence_marker = True, fence.group(1)
            elif fence.group(1) == fence_marker:
                in_fence, fence_marker = False, ""
            continue
        if in_fence:
            continue
        if line.count("**") % 2:
            defects.append(f"line {number}: unclosed bold run")
        if _HTML_TAG_RE.search(line):
            defects.append(f"line {number}: raw HTML tag")
        if _URL_RE.search(line):
            defects.append(f"line {number}: answer body contains a URL")
    return defects


def total_list_items(document: MarkdownDocument) -> int:
    return sum(len(block.items) for block in document.lists)


def widest_table(document: MarkdownDocument) -> MarkdownTable | None:
    """The table with the most body rows, which is the one a case scores."""
    if not document.tables:
        return None
    return max(document.tables, key=lambda table: (len(table.body_rows), table.column_count))
