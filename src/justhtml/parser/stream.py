from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TYPE_CHECKING, Literal, TypeAlias

from justhtml.core.constants import (
    FOREIGN_BREAKOUT_ELEMENTS,
    HTML_INTEGRATION_POINT_SET,
    MATHML_TEXT_INTEGRATION_POINT_SET,
    SVG_TAG_NAME_ADJUSTMENTS,
)
from justhtml.core.entities import (
    _MAX_NAMED_ENTITY_LENGTH,
    decode_entities_in_text,
)

from . import scanner as _scanner
from .encoding import (
    decode_html,
    make_incremental_decoder,
    sniff_html_encoding_prefix,
)

if TYPE_CHECKING:
    from codecs import IncrementalDecoder
    from collections.abc import Generator

StartEvent: TypeAlias = tuple[Literal["start"], tuple[str, dict[str, str | None]]]
EndEvent: TypeAlias = tuple[Literal["end"], str]
TextEvent: TypeAlias = tuple[Literal["text"], str]
CommentEvent: TypeAlias = tuple[Literal["comment"], str]
DoctypeEvent: TypeAlias = tuple[Literal["doctype"], tuple[str | None, str | None, str | None]]
StreamEvent: TypeAlias = StartEvent | EndEvent | TextEvent | CommentEvent | DoctypeEvent

#: A single chunk accepted by the streaming API.
StreamInputChunk: TypeAlias = str | bytes | bytearray | memoryview
#: A whole document or an iterable of chunks.
StreamInput: TypeAlias = str | bytes | bytearray | memoryview | Iterable[StreamInputChunk]

_DOCTYPE_RE = re.compile(
    r"""\s*([^\s>]+)(?:\s*(PUBLIC|SYSTEM)\s*(?:(?:"([^"]*)"|'([^']*)')\s*(?:"([^"]*)"|'([^']*)')?)?)?""",
    re.IGNORECASE,
)
_RAWTEXT_TAGS = frozenset({"script", "style", "iframe", "noembed", "noframes", "noscript", "xmp"})
_RCDATA_TAGS = frozenset({"textarea", "title"})
_PLAINTEXT_TAGS = frozenset({"plaintext"})
_SPACE = _scanner.SPACE
_TAG_NAME_STOP = _scanner.TAG_NAME_STOP
_ATTR_NAME_STOP = _scanner.ATTR_NAME_STOP
_ATTR_VALUE_STOP = _scanner.ATTR_VALUE_STOP
_TAG_END_NAME_STOP = _scanner.TAG_END_NAME_STOP
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")

# A trailing '&' plus a possible entity name/numeric value is retained until
# the next chunk. The bound includes the '&' and covers every entity table
# name length and every non-overlong numeric reference.
_ENTITY_HOLD_LIMIT = _MAX_NAMED_ENTITY_LENGTH + 1

_MODE_DATA = "data"
_MODE_RAWTEXT = "rawtext"
_MODE_RCDATA = "rcdata"
_MODE_PLAINTEXT = "plaintext"


class _StreamNode:
    __slots__ = ("attrs", "html_integration", "name", "namespace")

    attrs: dict[str, str | None]
    name: str
    namespace: str

    def __init__(self, name: str, namespace: str, attrs: dict[str, str | None] | None = None) -> None:
        self.attrs = attrs or {}
        self.name = name
        self.namespace = namespace
        if namespace == "math" and name == "annotation-xml":
            encoding = self._attribute_value("encoding")
            self.html_integration = encoding is not None and encoding.lower() in {
                "application/xhtml+xml",
                "text/html",
            }
        else:
            self.html_integration = (namespace, name) in HTML_INTEGRATION_POINT_SET

    def _attribute_value(self, name: str) -> str | None:
        for attr_name, attr_value in self.attrs.items():
            if attr_name.lower() == name:
                return attr_value or ""
        return None


class _StreamScanner:
    """Single-tokenizer incremental HTML scanner.

    The scanner owns only a bounded pending suffix (an unfinished construct at
    the chunk boundary) plus the open-element stack and the current raw-text
    insertion mode. Every definitive byte is released as events immediately;
    only text/CR/entity/markup prefixes that a later chunk could change are
    retained.

    Adjacent ("text", ...) events may be split across chunk boundaries. They
    carry the same concatenated text a one-shot parse would produce; all other
    events match a one-shot parse one-for-one.
    """

    __slots__ = (
        "_dangle_hex",
        "_html",
        "_html_positions",
        "_mode",
        "_name_positions",
        "_open_elements",
        "_raw_name",
        "_script_state",
    )

    _html: str
    _open_elements: list[_StreamNode]
    _name_positions: dict[str, list[int]]
    _html_positions: list[int]
    _mode: str
    _raw_name: str
    _script_state: _scanner.ScriptState
    # Not None while an over-long numeric reference run is swallowing digits
    # (and one optional ';') from the front of the next chunk(s). True means
    # hexadecimal digits, False decimal.
    _dangle_hex: bool | None

    def __init__(self, html: str = "") -> None:
        self._html = html
        self._open_elements = []
        self._name_positions = {}
        self._html_positions = []
        self._mode = _MODE_DATA
        self._raw_name = ""
        self._script_state = _scanner.SCRIPT_STATE_DATA
        self._dangle_hex = None

    def _push_open_element(self, node: _StreamNode) -> None:
        index = len(self._open_elements)
        self._open_elements.append(node)
        bucket = self._name_positions.get(node.name.lower())
        if bucket is None:
            self._name_positions[node.name.lower()] = [index]
        else:
            bucket.append(index)
        if node.namespace in {None, "html"}:
            self._html_positions.append(index)

    def _forget_open_element(self, node: _StreamNode) -> None:
        key = node.name.lower()
        bucket = self._name_positions[key]
        bucket.pop()
        if not bucket:
            del self._name_positions[key]
        if node.namespace in {None, "html"}:
            self._html_positions.pop()

    def _pop_open_element(self) -> _StreamNode:
        node = self._open_elements.pop()
        self._forget_open_element(node)
        return node

    def _truncate_open_elements(self, index: int) -> None:
        while len(self._open_elements) > index:
            self._forget_open_element(self._open_elements.pop())

    # ------------------------------------------------------------------
    # Public incremental API
    # ------------------------------------------------------------------

    def feed_text(self, chunk: str) -> Generator[StreamEvent, None, None]:
        """Consume one text chunk and emit every now-definitive event."""
        if self._dangle_hex is not None and chunk:
            chunk = self._consume_dangling_reference(chunk)
            if not chunk and self._dangle_hex is not None:
                # The whole chunk continued the over-long numeric reference.
                return
        if chunk:
            self._html += chunk
        yield from self._run(final=False)

    def finish_text(self) -> Generator[StreamEvent, None, None]:
        """Flush end-of-input behavior exactly like a one-shot parse."""
        if self._dangle_hex is not None:
            # Any continuation digits at true EOF belong to the replacement
            # character that was already emitted for the over-long reference.
            self._dangle_hex = None
        yield from self._run(final=True)

    def _consume_dangling_reference(self, chunk: str) -> str:
        is_hex = self._dangle_hex
        index = 0
        length = len(chunk)
        if is_hex:
            while index < length and chunk[index] in _HEX_DIGITS:
                index += 1
        else:
            while index < length and chunk[index].isdigit():
                index += 1
        if index < length:
            # Any non-digit terminates the reference; consume one trailing ';'.
            if chunk[index] == ";":
                index += 1
            self._dangle_hex = None
        return chunk[index:]

    def scan(self) -> Generator[StreamEvent, None, None]:
        """One-shot scan of the buffer supplied at construction time."""
        yield from self._run(final=True)

    # ------------------------------------------------------------------
    # Unified scan loop (final=True reproduces the one-shot parse)
    # ------------------------------------------------------------------

    def _run(self, *, final: bool) -> Generator[StreamEvent, None, None]:
        html = self._html
        end = len(html)
        pos = 0
        text_buffer: list[str] = []

        while pos < end:
            if self._mode == _MODE_PLAINTEXT:
                stop = end
                if not final and stop > pos and html[stop - 1] == "\r":
                    stop -= 1
                self._append_text(text_buffer, html[pos:stop], raw=True, replace_null=True)
                yield from self._flush_text(text_buffer)
                self._html = html[stop:end]
                return

            if self._mode in {_MODE_RAWTEXT, _MODE_RCDATA}:
                is_script = self._mode == _MODE_RAWTEXT and self._raw_name == "script"
                if is_script:
                    result = _scanner.scan_script_data(html, pos, end, self._script_state)
                else:
                    result = _scanner.scan_rawtext_data(html, self._raw_name, pos, end)

                if result[0] == "close":
                    close_pos = result[1]
                    after_close = result[2]
                    self._append_text(
                        text_buffer,
                        html[pos:close_pos],
                        raw=self._mode == _MODE_RAWTEXT,
                        replace_null=True,
                    )
                    yield from self._flush_text(text_buffer)
                    self._mode = _MODE_DATA
                    if is_script:
                        self._script_state = _scanner.SCRIPT_STATE_DATA
                    yield ("end", self._raw_name)
                    self._pop_for_end_tag(self._raw_name)
                    pos = after_close
                    continue

                safe_end = result[1]
                pseudo_close = result[2]
                state_id = result[3]
                drop_tail = pseudo_close != -1

                if final:
                    emit_end = pseudo_close if drop_tail else end
                    self._append_text(
                        text_buffer,
                        html[pos:emit_end],
                        raw=self._mode == _MODE_RAWTEXT,
                        replace_null=True,
                    )
                    yield from self._flush_text(text_buffer)
                    self._html = ""
                    return

                stop = safe_end
                if stop > pos and html[stop - 1] == "\r":
                    stop -= 1
                # Text between stop and end is a held CR and/or a pending close
                # prefix that must survive regardless of entity compression.
                held_tail = html[stop:end]
                if self._mode == _MODE_RCDATA:
                    hold_stop, retained = self._hold_text_tail(html, pos, stop)
                else:
                    hold_stop, retained = stop, None
                self._append_text(
                    text_buffer,
                    html[pos:hold_stop],
                    raw=self._mode == _MODE_RAWTEXT,
                    replace_null=True,
                )
                yield from self._flush_text(text_buffer)
                if is_script:
                    if hold_stop != safe_end:
                        # Retained plain text precedes the escape-state point;
                        # rescanning it from the data state re-enters every
                        # escape state deterministically.
                        self._script_state = _scanner.SCRIPT_STATE_DATA
                    elif state_id == 1:
                        self._script_state = _scanner.SCRIPT_STATE_ESCAPED
                    elif state_id == 2:
                        self._script_state = _scanner.SCRIPT_STATE_DOUBLE_ESCAPED
                    else:
                        self._script_state = _scanner.SCRIPT_STATE_DATA
                core = retained if retained is not None else html[hold_stop:stop]
                self._html = core + held_tail
                return

            lt = html.find("<", pos, end)
            if lt == -1:
                if not final:
                    stop = end
                    held_tail = ""
                    if stop > pos and html[stop - 1] == "\r":
                        held_tail = "\r"
                        stop -= 1
                    hold_stop, retained = self._hold_text_tail(html, pos, stop)
                    self._append_text(text_buffer, html[pos:hold_stop])
                    yield from self._flush_text(text_buffer)
                    core = retained if retained is not None else html[hold_stop:stop]
                    self._html = core + held_tail
                    return
                self._append_text(text_buffer, html[pos:end])
                break
            if lt > pos:
                self._append_text(text_buffer, html[pos:lt])

            pos = lt + 1
            if pos >= end:
                if not final:
                    yield from self._flush_text(text_buffer)
                    self._html = html[lt:end]
                    return
                self._append_text(text_buffer, "<")
                break

            ch = html[pos]
            if ch == "!":
                if not final and not self._declaration_terminated(html, pos + 1, end):
                    yield from self._flush_text(text_buffer)
                    self._html = html[lt:end]
                    return
                handled, event, new_pos, text = self._parse_markup_declaration(pos + 1, end)
                if handled:
                    if text is not None:
                        yield from self._flush_text(text_buffer)
                        self._append_text(text_buffer, text, raw=True)
                        yield from self._flush_text(text_buffer)
                    elif event is not None:  # pragma: no branch - declarations always produce event or text
                        yield from self._flush_text(text_buffer)
                        yield event
                    pos = new_pos
                    continue
                # All markup declarations, including bogus declarations, are
                # consumed by _parse_markup_declaration.
                raise AssertionError("unhandled markup declaration")  # pragma: no cover

            if ch == "/":
                next_pos = pos + 1
                if next_pos >= end:
                    if not final:
                        yield from self._flush_text(text_buffer)
                        self._html = html[lt:end]
                        return
                    self._append_text(text_buffer, "</")
                    break
                if not _is_ascii_alpha(html[next_pos]):
                    if html[next_pos] == ">":
                        pos = next_pos + 1
                        continue
                    comment_end = html.find(">", next_pos, end)
                    if not final and comment_end == -1:
                        yield from self._flush_text(text_buffer)
                        self._html = html[lt:end]
                        return
                    data_end = end if comment_end == -1 else comment_end
                    yield from self._flush_text(text_buffer)
                    yield ("comment", html[next_pos:data_end].replace("\0", "\ufffd"))
                    pos = end if comment_end == -1 else comment_end + 1
                    continue
                parsed = self._parse_end_tag(pos + 1, end)
                if parsed is None:  # pragma: no cover - guarded by ASCII-alpha check
                    # The caller only reaches this point for an ASCII letter.
                    self._append_text(text_buffer, "</")
                    pos += 1
                    continue
                name, new_pos, tag_terminated = parsed
                # Quote-aware check while streaming: a '>' trapped in an open
                # attribute quote does not close the tag and more bytes may
                # still arrive after a closing quote.
                if not final and not tag_terminated:
                    yield from self._flush_text(text_buffer)
                    self._html = html[lt:end]
                    return
                if new_pos == end and html[-1] != ">":
                    pos = end
                    continue
                yield from self._flush_text(text_buffer)
                yield ("end", name)
                self._pop_for_end_tag(name)
                pos = new_pos
                continue

            if ch == "?":
                comment_end = html.find(">", pos + 1, end)
                if not final and comment_end == -1:
                    yield from self._flush_text(text_buffer)
                    self._html = html[lt:end]
                    return
                data_end = end if comment_end == -1 else comment_end
                yield from self._flush_text(text_buffer)
                yield ("comment", html[pos:data_end].replace("\0", "\ufffd"))
                pos = end if comment_end == -1 else comment_end + 1
                continue

            if not _is_ascii_alpha(ch):
                yield from self._flush_text(text_buffer)
                self._append_text(text_buffer, "<")
                continue

            parsed_start = self._parse_start_tag(pos, end)
            if parsed_start is None:
                if not final:
                    yield from self._flush_text(text_buffer)
                    self._html = html[lt:end]
                    return
                pos = end
                break

            name, attrs, self_closing, new_pos = parsed_start
            yield from self._flush_text(text_buffer)
            yield ("start", (name, attrs.copy()))

            namespace = self._namespace_for_start_tag(name, attrs)
            if not (self_closing and namespace not in {None, "html"}):
                adjusted_name = self._adjusted_name_for_namespace(name, namespace)
                self._push_open_element(_StreamNode(adjusted_name, namespace, attrs.copy()))

            if namespace in {None, "html"}:
                if name in _PLAINTEXT_TAGS:
                    self._mode = _MODE_PLAINTEXT
                    self._raw_name = name
                    pos = new_pos
                    continue
                if name in _RAWTEXT_TAGS:
                    self._mode = _MODE_RAWTEXT
                    self._raw_name = name
                    if name == "script":
                        self._script_state = _scanner.SCRIPT_STATE_DATA
                    pos = new_pos
                    continue
                if name in _RCDATA_TAGS:
                    self._mode = _MODE_RCDATA
                    self._raw_name = name
                    pos = new_pos
                    continue

            pos = new_pos

        self._html = html[pos:end]
        yield from self._flush_text(text_buffer)

    # ------------------------------------------------------------------
    # Boundary holdback helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _tail_might_be_reference(tail: str) -> bool:
        """Whether a suffix starting with '&' could still become an entity."""
        length = len(tail)
        if length == 1:
            return True
        if tail[1] == "#":
            if length == 2:
                return True
            if tail[2] in "xX":
                return all(char in _HEX_DIGITS for char in tail[3:])
            return all(char.isdigit() for char in tail[2:])
        return all(char.isalpha() or char.isdigit() for char in tail[1:])

    def _hold_text_tail(self, html: str, pos: int, stop: int) -> tuple[int, str | None]:
        """Bound text that is safe to decode and emit at a chunk boundary.

        A trailing CR or a partial entity reference is retained for the next
        chunk. The second result item, when not None, replaces the retained
        suffix wholesale (used to compress an unbounded run of leading zeros).

        References already guaranteed to resolve deterministically are emitted
        now: a numeric run whose value exceeds U+10FFFF (or whose significant
        part exceeds 16 digits) yields U+FFFD whether it terminates or extends,
        so a dangling swallow is armed for its continuation; an over-long named
        run can only match a fixed-length legacy prefix. Every other numeric
        run is retained; leading zeros carry no value, so they are compressed
        away (one zero is kept for an all-zero run so an immediate ';' still
        resolves to the NUL replacement).

        ``stop`` must already exclude a trailing CR; callers retain it.
        """
        amp = html.rfind("&", pos, stop)
        if amp == -1:
            return stop, None
        tail = html[amp:stop]
        if not self._tail_might_be_reference(tail):
            return stop, None
        if len(tail) <= _ENTITY_HOLD_LIMIT:
            return amp, None
        if len(tail) > 2 and tail[1] == "#":
            # _tail_might_be_reference already guarantees an all-digit run.
            is_hex = tail[2] in "xX"
            digits = tail[3 if is_hex else 2 :]
            significant = digits.lstrip("0")
            resolved = False
            if significant:
                if len(significant) > 16:
                    resolved = True
                else:
                    resolved = int(significant, 16 if is_hex else 10) > 0x10FFFF
            if resolved:
                self._dangle_hex = is_hex
                return stop, None
            # An unresolved tail this long must consist mostly of leading
            # zeros; retain a bounded prefix with the same numeric value.
            kept = significant if significant else "0"
            marker = "&#x" if is_hex else "&#"
            return amp, marker + kept
        return stop, None

    def _declaration_terminated(self, html: str, pos: int, end: int) -> bool:
        """Whether the markup declaration starting after '<!' has its closer.

        `pos` is the index immediately following '<!'. A False result means the
        chunk ends inside the declaration and it must be retained wholesale.
        """
        if html.startswith("--", pos):
            comment_start = pos + 2
            if comment_start < end and html[comment_start] == ">":
                return True
            if html.startswith("->", comment_start):
                return True
            return html.find("-->", comment_start, end) != -1 or html.find("--!>", comment_start, end) != -1
        if self._in_foreign_context() and _scanner.ascii_prefix_match(html, "[cdata[", pos, end):
            return html.find("]]>", pos, end) != -1
        return html.find(">", pos, end) != -1

    def _parse_markup_declaration(
        self, pos: int, end: int
    ) -> tuple[bool, CommentEvent | DoctypeEvent | None, int, str | None]:
        html = self._html

        if html.startswith("--", pos):
            comment_start = pos + 2
            if comment_start < end and html[comment_start] == ">":
                return True, ("comment", ""), comment_start + 1, None
            if html.startswith("->", comment_start):
                return True, ("comment", ""), comment_start + 2, None
            normal_end = html.find("-->", comment_start, end)
            bang_end = html.find("--!>", comment_start, end)
            candidates = [candidate for candidate in (normal_end, bang_end) if candidate != -1]
            if candidates:
                comment_end = min(candidates)
                closing_len = 4 if comment_end == bang_end else 3
                data = html[comment_start:comment_end].replace("\0", "\ufffd")
                return True, ("comment", data), comment_end + closing_len, None
            data = html[comment_start:end]
            data = data.removesuffix("--")
            return True, ("comment", data.replace("\0", "\ufffd")), end, None

        if _scanner.ascii_startswith(html, "doctype", pos, end):
            # A ">" terminates a DOCTYPE even inside a quoted identifier.
            # Quote-aware tag scanning can otherwise swallow following text
            # when the declaration is malformed.
            tag_end = html.find(">", pos + 7, end)
            if tag_end == -1:
                tag_end = end
                next_pos = end
            else:
                next_pos = tag_end + 1
            data = html[pos + 7 : tag_end]
            match = _DOCTYPE_RE.match(data)
            if not match:
                return True, ("doctype", (None, None, None)), next_pos, None
            name = match.group(1)
            if name and not name.islower():
                name = name.lower()
            kind = match.group(2)
            first_id = match.group(3) if match.group(3) is not None else match.group(4)
            second_id = match.group(5) if match.group(5) is not None else match.group(6)
            if kind is not None and kind.lower() == "public":
                public_id = first_id
                system_id = second_id
            elif kind is not None and kind.lower() == "system":
                public_id = None
                system_id = first_id
            else:
                public_id = None
                system_id = None
            if "\0" in name:
                name = name.replace("\0", "\ufffd")
            if public_id is not None and "\0" in public_id:
                public_id = public_id.replace("\0", "\ufffd")
            if system_id is not None and "\0" in system_id:
                system_id = system_id.replace("\0", "\ufffd")
            return True, ("doctype", (name, public_id, system_id)), next_pos, None

        if _scanner.ascii_startswith(html, "[cdata[", pos, end) and self._in_foreign_context():
            cdata_start = pos + 7
            cdata_end = html.find("]]>", cdata_start, end)
            if cdata_end == -1:
                return True, None, end, html[cdata_start:end]
            return True, None, cdata_end + 3, html[cdata_start:cdata_end]

        comment_end = html.find(">", pos, end)
        if comment_end == -1:
            return True, ("comment", html[pos:end].replace("\0", "\ufffd")), end, None
        return True, ("comment", html[pos:comment_end].replace("\0", "\ufffd")), comment_end + 1, None

    def _parse_start_tag(self, pos: int, end: int) -> tuple[str, dict[str, str | None], bool, int] | None:
        html = self._html
        name_start = pos
        while pos < end and html[pos] not in _TAG_NAME_STOP:
            pos += 1
        if pos == name_start:
            return None
        name = html[name_start:pos]
        if not name.islower():
            name = name.lower()
        attrs, self_closing, pos, tag_closed = self._parse_attrs(pos, end)
        if not tag_closed:
            return None
        return name, attrs, self_closing, pos

    def _parse_end_tag(self, pos: int, end: int) -> tuple[str, int, bool] | None:
        html = self._html
        name_start = pos
        if pos >= end or not _is_ascii_alpha(html[pos]):
            return None
        while pos < end and html[pos] not in _TAG_END_NAME_STOP:
            pos += 1
        if pos == name_start:
            return None  # pragma: no cover - the ASCII-alpha guard advances pos
        name = html[name_start:pos]
        if not name.islower():
            name = name.lower()
        tag_end = self._find_tag_end(pos, end)
        return name, end if tag_end == -1 else tag_end + 1, tag_end != -1

    def _parse_attrs(self, pos: int, end: int) -> tuple[dict[str, str | None], bool, int, bool]:
        html = self._html
        attrs: dict[str, str | None] = {}

        while pos < end:
            while pos < end and html[pos] in _SPACE:
                pos += 1
            if pos >= end:
                return attrs, False, pos, False
            ch = html[pos]
            if ch == ">":
                return attrs, False, pos + 1, True
            if ch == "/" and pos + 1 < end and html[pos + 1] == ">":
                return attrs, True, pos + 2, True

            name_start = pos
            while pos < end and html[pos] not in _ATTR_NAME_STOP:
                pos += 1
            if pos == name_start:
                pos += 1
                continue
            raw_key = html[name_start:pos]
            key = raw_key if raw_key.islower() else raw_key.lower()
            if "\0" in key:
                key = key.replace("\0", "\ufffd")

            while pos < end and html[pos] in _SPACE:
                pos += 1

            value: str | None = ""
            if pos < end and html[pos] == "=":
                pos += 1
                while pos < end and html[pos] in _SPACE:
                    pos += 1
                if pos < end and html[pos] in "\"'":
                    quote = html[pos]
                    pos += 1
                    value_start = pos
                    close = html.find(quote, pos, end)
                    if close == -1:
                        return attrs, False, end, False
                    value = html[value_start:close]
                    pos = close + 1
                else:
                    value_start = pos
                    while pos < end and html[pos] not in _ATTR_VALUE_STOP:
                        pos += 1
                    value = html[value_start:pos]

                if value and "&" in value:
                    value = decode_entities_in_text(value, in_attribute=True)
                if value and "\0" in value:
                    value = value.replace("\0", "\ufffd")

            if key not in attrs:
                attrs[key] = value

        return attrs, False, pos, False

    def _find_tag_end(self, pos: int, end: int) -> int:
        return _scanner.find_tag_end(self._html, pos, end)

    def _append_text(
        self,
        text_buffer: list[str],
        data: str,
        *,
        raw: bool = False,
        replace_null: bool = False,
    ) -> None:
        if not data:
            return
        if "\r" in data:
            data = data.replace("\r\n", "\n").replace("\r", "\n")
        if replace_null and "\0" in data:
            data = data.replace("\0", "\ufffd")
        if not raw and "&" in data:
            data = decode_entities_in_text(data, in_attribute=False)
        if data:  # pragma: no branch - decoding cannot empty non-empty input
            text_buffer.append(data)

    def _flush_text(self, text_buffer: list[str]) -> Generator[TextEvent, None, None]:
        if not text_buffer:
            return
        text = "".join(text_buffer)
        text_buffer.clear()
        if text:  # pragma: no branch - joining a non-empty buffer is non-empty
            yield ("text", text)

    def _font_breaks_out_of_foreign_content(self, attrs: dict[str, str | None]) -> bool:
        for name in attrs:
            if name.lower() in {"color", "face", "size"}:
                return True
        return False

    def _is_html_integration_point(self, node: _StreamNode) -> bool:
        return node.html_integration

    def _is_mathml_text_integration_point(self, node: _StreamNode) -> bool:
        return (node.namespace, node.name) in MATHML_TEXT_INTEGRATION_POINT_SET

    def _adjusted_name_for_namespace(self, name: str, namespace: str) -> str:
        if namespace == "svg":
            return SVG_TAG_NAME_ADJUSTMENTS.get(name, name)
        return name

    def _namespace_from_html_context(self, name: str) -> str:
        if name == "svg":
            return "svg"
        if name == "math":
            return "math"
        return "html"

    def _namespace_for_start_tag(self, name: str, attrs: dict[str, str | None]) -> str:
        parent = self._open_elements[-1] if self._open_elements else None
        parent_namespace = parent.namespace if parent is not None else "html"

        if parent is not None:
            if self._is_html_integration_point(parent):
                return self._namespace_from_html_context(name)
            if self._is_mathml_text_integration_point(parent) and name not in {"mglyph", "malignmark"}:
                return self._namespace_from_html_context(name)
            if parent_namespace == "math" and parent.name == "annotation-xml" and name == "svg":
                return "svg"

        if parent_namespace not in {None, "html"}:
            breaks_out = name in FOREIGN_BREAKOUT_ELEMENTS or (
                name == "font" and self._font_breaks_out_of_foreign_content(attrs)
            )
            if breaks_out:
                while self._open_elements and self._open_elements[-1].namespace not in {None, "html"}:
                    self._pop_open_element()
            else:
                return parent_namespace

        return self._namespace_from_html_context(name)

    def _pop_foreign_context(self) -> None:
        while self._open_elements and self._open_elements[-1].namespace not in {None, "html"}:
            self._pop_open_element()

    def _pop_for_end_tag(self, name: str) -> None:
        if not self._open_elements:
            return

        name_lower = name.lower()
        current = self._open_elements[-1]
        if current.namespace not in {None, "html"} and name_lower in {"br", "p"}:
            self._pop_foreign_context()
            return

        bucket = self._name_positions.get(name_lower)
        if bucket is not None:
            target = bucket[-1]
            html_positions = self._html_positions
            if target >= (html_positions[-1] if html_positions else -1):
                self._truncate_open_elements(target)
                return

        self._pop_open_element()

    def _in_foreign_context(self) -> bool:
        if not self._open_elements:
            return False
        node = self._open_elements[-1]
        return node.namespace not in {None, "html"}


class HTMLStream:
    """Push-style incremental HTML tokenizer.

    Feed the same document as a sequence of ``str`` or bytes-like chunks and
    iterate the events after each feed. All chunks must share one type; bytes
    input is decoded with the same HTML encoding sniffing as the one-shot API
    (BOM first, then the transport ``encoding`` label, then the meta prescan,
    defaulting to windows-1252). The encoding is available on
    :attr:`encoding` as soon as the prefix determines it.

    Pending state stays bounded: only an unfinished construct at the current
    chunk edge and, before the encoding locks, the sniffing prefix (capped at
    64 KiB) are retained.
    """

    __slots__ = (
        "_bytes_mode",
        "_decoder",
        "_encoding",
        "_finished",
        "_prefix",
        "_scanner",
        "_transport",
    )

    def __init__(self, *, encoding: str | None = None) -> None:
        self._scanner = _StreamScanner()
        self._transport = encoding
        # None = undecided, True = bytes-like, False = str.
        self._bytes_mode: bool | None = None
        self._prefix: bytearray = bytearray()
        self._decoder: IncrementalDecoder | None = None
        self._encoding: str | None = None
        self._finished = False

    @property
    def encoding(self) -> str | None:
        """The sniffed encoding name, or None until the prefix decides it."""
        return self._encoding

    def feed(self, chunk: StreamInputChunk) -> Generator[StreamEvent, None, None]:
        """Feed one chunk and yield every event that is now definitive."""
        if self._finished:
            raise RuntimeError("HTMLStream.feed() called after finish()")
        yield from self._push(chunk, False)

    def finish(self) -> Generator[StreamEvent, None, None]:
        """Signal end of input and flush remaining events."""
        if self._finished:
            return
        self._finished = True
        yield from self._push(None, True)

    def _push(self, chunk: StreamInputChunk | None, final: bool) -> Generator[StreamEvent, None, None]:
        if self._bytes_mode is None:
            if isinstance(chunk, str):
                self._bytes_mode = False
            elif isinstance(chunk, (bytes, bytearray, memoryview)):
                self._bytes_mode = True
            elif chunk is None and final:
                # Empty document: nothing to decode or tokenize.
                return
            else:
                raise TypeError("HTMLStream chunks must be str or bytes-like")
        elif chunk is not None:
            expected_text = not self._bytes_mode
            if expected_text != isinstance(chunk, str) or (
                not expected_text and not isinstance(chunk, (bytes, bytearray, memoryview))
            ):
                kind = "str" if self._bytes_mode else "bytes-like"
                raise TypeError(f"all HTMLStream chunks must be {kind}")

        if not self._bytes_mode:
            if isinstance(chunk, str) and chunk:
                yield from self._scanner.feed_text(chunk)
            if final:
                yield from self._scanner.finish_text()
            return

        # The type check above rejects str chunks in bytes mode, but mypy
        # cannot correlate it with the runtime _bytes_mode attribute.
        if isinstance(chunk, str):  # pragma: no cover
            raise TypeError("all HTMLStream chunks must be bytes-like")
        data = b"" if chunk is None else bytes(chunk)
        if self._decoder is None:
            self._prefix.extend(data)
            if final:
                # One-shot decode of the complete prefix guarantees identical
                # output to stream(full_bytes), including truncation handling.
                text, self._encoding = decode_html(bytes(self._prefix), self._transport)
                self._prefix.clear()
                if text:
                    yield from self._scanner.feed_text(text)
                yield from self._scanner.finish_text()
                return
            encoding, bom_len = sniff_html_encoding_prefix(bytes(self._prefix), self._transport)
            if encoding is None:
                # The encoding may still change; retain the bounded prefix.
                return
            self._encoding = encoding
            self._decoder = make_incremental_decoder(encoding)
            text = self._decoder.decode(bytes(self._prefix[bom_len:]), final=False)
            self._prefix.clear()
            if text:
                yield from self._scanner.feed_text(text)
            return

        text = self._decoder.decode(data, final=final)
        if text:
            yield from self._scanner.feed_text(text)
        if final:
            yield from self._scanner.finish_text()


def _is_ascii_alpha(ch: str) -> bool:
    return ("a" <= ch <= "z") or ("A" <= ch <= "Z")


def stream(
    html: StreamInput,
    *,
    encoding: str | None = None,
) -> Generator[StreamEvent, None, None]:
    """
    Stream HTML events from an HTML document.

    ``html`` may be a complete ``str``/bytes-like document, or an iterable of
    same-type chunks. Chunked input is parsed incrementally without joining the
    document first; the event stream is equivalent to parsing the concatenated
    input (consecutive text events may be split across chunk boundaries and
    coalesce to the same text).

    Yields tuples of (event_type, data).
    """
    if isinstance(html, (str, bytes, bytearray, memoryview)):
        html_str: str
        if isinstance(html, str):
            html_str = html
        else:
            html_str, _ = decode_html(bytes(html), transport_encoding=encoding)
        yield from _StreamScanner(html_str).scan()
        return

    if not isinstance(html, Iterable):
        raise TypeError("stream() expects a str, bytes-like, or iterable of chunks")

    parser = HTMLStream(encoding=encoding)
    for chunk in html:
        yield from parser.feed(chunk)
    yield from parser.finish()
