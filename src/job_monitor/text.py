from __future__ import annotations

from html.parser import HTMLParser
import re


class _StructuredHTMLParser(HTMLParser):
    """Extract blocks and headings without interpreting JD meaning.

    Ashby commonly renders a section title as a paragraph whose entire contents
    are wrapped in <strong>, rather than as an HTML heading element. That is an
    explicit presentation structure, so it is safe to preserve as a heading.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, str]] = []
        self._active_tag: str | None = None
        self._active_kind: str | None = None
        self._parts: list[str] = []
        self._strong_parts: list[str] = []
        self._strong_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._active_tag is None:
            if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                self._start_block(tag, "heading")
            elif tag == "p":
                self._start_block(tag, "paragraph")
            elif tag == "li":
                self._start_block(tag, "list_item")
        if tag in {"strong", "b"} and self._active_tag is not None:
            self._strong_depth += 1
        elif tag == "br" and self._active_tag is not None:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"strong", "b"} and self._strong_depth:
            self._strong_depth -= 1
        if tag == self._active_tag:
            self._finish_block()

    def handle_data(self, data: str) -> None:
        if not data:
            return
        if self._active_tag is None:
            body = clean_text(data)
            if body:
                self.blocks.append(("paragraph", body))
            return
        self._parts.append(data)
        if self._strong_depth:
            self._strong_parts.append(data)

    def close(self) -> None:
        super().close()
        if self._active_tag is not None:
            self._finish_block()

    def _start_block(self, tag: str, kind: str) -> None:
        self._active_tag = tag
        self._active_kind = kind
        self._parts = []
        self._strong_parts = []

    def _finish_block(self) -> None:
        body = clean_text("".join(self._parts))
        strong = clean_text("".join(self._strong_parts))
        if body:
            kind = self._active_kind or "paragraph"
            if kind == "paragraph" and strong and _heading_equivalent(body, strong):
                kind = "heading"
            self.blocks.append((kind, body))
        self._active_tag = None
        self._active_kind = None
        self._parts = []
        self._strong_parts = []
        self._strong_depth = 0


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
    value = "\n".join(line for line in lines if line)
    return value or None


def html_to_text_and_sections(html: str | None) -> tuple[str | None, dict[str, str]]:
    if not html:
        return None, {}
    parser = _StructuredHTMLParser()
    parser.feed(html)
    parser.close()
    text = clean_text("\n".join(body for _, body in parser.blocks))
    sections: dict[str, str] = {}
    current_heading: str | None = None
    current_parts: list[str] = []
    for kind, body in parser.blocks:
        if kind == "heading":
            if current_heading and current_parts:
                sections[current_heading] = clean_text("\n".join(current_parts)) or ""
            current_heading = body.strip().lower()
            current_parts = []
        elif current_heading:
            current_parts.append(body)
    if current_heading and current_parts:
        sections[current_heading] = clean_text("\n".join(current_parts)) or ""
    return text, {heading: body for heading, body in sections.items() if body}


def first_section(sections: dict[str, str], headings: set[str]) -> str | None:
    for heading, body in sections.items():
        normalized = re.sub(r"[^a-z0-9]+", " ", heading).strip()
        if normalized in headings:
            return body
    return None


def _heading_equivalent(body: str, strong: str) -> bool:
    normalize = lambda value: re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
    return normalize(body) == normalize(strong)
