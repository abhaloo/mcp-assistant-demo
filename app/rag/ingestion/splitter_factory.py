"""Splitter factory: routes `(kind, params)` to a langchain text splitter.

Kinds:
- `recursive`                  RecursiveCharacterTextSplitter (current production)
- `markdown_header_recursive`  MarkdownHeaderTextSplitter -> Recursive (two-stage)
- `semantic`                   langchain_experimental.SemanticChunker

For `recursive`, `length_function` is `"chars"` (default) or `"tokens"` (tiktoken
cl100k_base). For `semantic`, pass `embeddings=` to use a cost-tracked instance;
otherwise the factory pulls the default from `app.providers.get_embeddings`.
"""

from __future__ import annotations

from typing import Any


def _tiktoken_len(text: str) -> int:
    """Character-equivalent length under the cl100k_base tokenizer."""
    import tiktoken

    enc = tiktoken.get_encoding("cl100k_base")
    return len(enc.encode(text))


class _MarkdownHeaderRecursive:
    """Two-stage: split on markdown headers first, then recursively cap to chunk_size.

    Standard LangChain idiom for heading-aware structured corpora. Preserves
    heading boundaries (each chunk's metadata carries the header path) while
    keeping a hard max-size guarantee on the second pass.
    """

    def __init__(self, header_splitter, recursive_splitter):
        self.header_splitter = header_splitter
        self.recursive_splitter = recursive_splitter

    def split_documents(self, documents):
        out = []
        for doc in documents:
            header_chunks = self.header_splitter.split_text(doc.page_content)
            # Preserve source metadata; layer header metadata on top
            for hc in header_chunks:
                hc.metadata = {**doc.metadata, **hc.metadata}
            out.extend(self.recursive_splitter.split_documents(header_chunks))
        return out

    def split_text(self, text):
        header_chunks = self.header_splitter.split_text(text)
        out = []
        for hc in header_chunks:
            out.extend(self.recursive_splitter.split_text(hc.page_content))
        return out


def get_splitter(kind: str, params: dict[str, Any], *, embeddings=None):
    """Build a splitter for the given kind + params.

    `embeddings` (optional) is forwarded to the `semantic` kind so the caller
    can route sentence-level embed calls through a cost-tracked client.
    """
    if kind == "recursive":
        return _build_recursive(params)
    if kind == "markdown_header_recursive":
        return _build_markdown_header_recursive(params)
    if kind == "semantic":
        return _build_semantic(params, embeddings=embeddings)
    raise ValueError(f"Unknown splitter kind: {kind!r}")


def _build_recursive(params: dict[str, Any]):
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    length_function = _tiktoken_len if params.get("length_function") == "tokens" else len
    return RecursiveCharacterTextSplitter(
        chunk_size=params["chunk_size"],
        chunk_overlap=params["chunk_overlap"],
        length_function=length_function,
        separators=params.get("separators", ["\n\n", "\n", ". ", " ", ""]),
    )


def _build_markdown_header_recursive(params: dict[str, Any]):
    """MarkdownHeaderTextSplitter -> RecursiveCharacterTextSplitter.

    Heading-aware split with a max-size guarantee. Directly tests the MDPI
    Nov 2025 finding that section-based splitting dominates fixed-size on
    structured corpora.
    """
    from langchain_text_splitters import (
        MarkdownHeaderTextSplitter,
        RecursiveCharacterTextSplitter,
    )

    headers_to_split_on = params.get(
        "headers_to_split_on",
        [("#", "h1"), ("##", "h2"), ("###", "h3")],
    )
    header_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=headers_to_split_on,
        strip_headers=False,  # keep heading text inside chunk content
    )
    length_function = _tiktoken_len if params.get("length_function") == "tokens" else len
    recursive = RecursiveCharacterTextSplitter(
        chunk_size=params["chunk_size"],
        chunk_overlap=params["chunk_overlap"],
        length_function=length_function,
    )
    return _MarkdownHeaderRecursive(header_splitter, recursive)


def _build_semantic(params: dict[str, Any], *, embeddings=None):
    from langchain_experimental.text_splitter import SemanticChunker

    if embeddings is None:
        from app.providers import get_embeddings

        embeddings = get_embeddings()

    return SemanticChunker(
        embeddings=embeddings,
        breakpoint_threshold_type=params.get("breakpoint_threshold_type", "percentile"),
        breakpoint_threshold_amount=params.get("breakpoint_threshold_amount", 95),
    )
