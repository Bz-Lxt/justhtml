from __future__ import annotations

from typing import Literal

# Script-data escape states, matching the state tracked by find_script_end_tag.
ScriptState = Literal["data", "escaped", "double-escaped"]

SCRIPT_STATE_DATA: ScriptState = "data"
SCRIPT_STATE_ESCAPED: ScriptState = "escaped"
SCRIPT_STATE_DOUBLE_ESCAPED: ScriptState = "double-escaped"

SPACE = " \t\n\f\r"
TAG_NAME_STOP = "\t\n\f />"
# In the HTML attribute-name state, quotes, apostrophes, "<", and NUL are
# parse errors but remain part of the attribute name (NUL is replaced later).
ATTR_NAME_STOP = "\t\n\f />="
ATTR_VALUE_STOP = SPACE + ">"
TAG_END_NAME_STOP = SPACE + "/>"


def ascii_startswith(html: str, needle: str, start: int, end: int) -> bool:
    """Return whether an ASCII needle matches case-insensitively at start."""
    stop = start + len(needle)
    if stop > end:
        return False
    candidate = html[start:stop]
    return candidate == needle or (candidate.isascii() and candidate.lower() == needle.lower())


def ascii_find(html: str, needle: str, start: int, end: int) -> int:
    """Find an ASCII needle case-insensitively without folding all of html."""
    first = needle[0]
    needle_lower = needle.lower()
    needle_len = len(needle)
    if "a" <= first <= "z":
        alternate_first = first.upper()
    elif "A" <= first <= "Z":
        alternate_first = first.lower()
    else:
        alternate_first = first
    while True:
        found = html.find(first, start, end)
        if alternate_first != first:
            alternate_found = html.find(alternate_first, start, end)
            if found == -1 or (alternate_found != -1 and alternate_found < found):
                found = alternate_found
        if found == -1:
            return -1
        match_end = found + needle_len
        if match_end > end:
            return -1
        candidate = html[found:match_end]
        if candidate == needle or (candidate.isascii() and candidate.lower() == needle_lower):
            return found
        start = found + 1


def ascii_rfind(html: str, needle: str, start: int, end: int) -> int:
    """Find the last ASCII needle case-insensitively without a folded copy."""
    first = needle[0]
    if "a" <= first <= "z":
        alternate_first = first.upper()
    elif "A" <= first <= "Z":
        alternate_first = first.lower()
    else:
        alternate_first = first
    search_end = end
    while True:
        found = html.rfind(first, start, search_end)
        if alternate_first != first:
            alternate_found = html.rfind(alternate_first, start, search_end)
            if alternate_found > found:
                found = alternate_found
        if found == -1:
            return -1
        if ascii_startswith(html, needle, found, end):
            return found
        search_end = found


def find_tag_end(html: str, pos: int, end: int) -> int:
    quote: str | None = None
    while pos < end:
        ch = html[pos]
        if quote is not None:
            if ch == quote:
                quote = None
        elif ch == '"' or ch == "'":
            quote = ch
        elif ch == ">":
            return pos
        pos += 1
    return -1


def find_rawtext_end_tag(html: str, name: str, pos: int, end: int) -> tuple[int | None, int]:
    needle = f"</{name}"
    needle_len = len(needle)
    search = pos
    while True:
        close = ascii_find(html, needle, search, end)
        if close == -1:
            return None, end
        after_name = close + needle_len
        if after_name < end and html[after_name] not in TAG_END_NAME_STOP:
            search = after_name
            continue
        tag_end = find_tag_end(html, after_name, end)
        if tag_end == -1:
            if after_name < end:
                # A space or "/" terminated the end-tag name but no ">" follows.
                # At EOF the end tag is still emitted (its attributes dropped), so
                # the raw text ends here. Only a bare "</name" at EOF, with no
                # terminator, stays raw text.
                return close, end
            search = after_name
            continue
        return close, tag_end + 1


def find_script_end_tag(html: str, pos: int, end: int) -> tuple[int | None, int]:
    search = pos
    escaped = False
    double_escaped = False

    while True:
        close, next_pos = find_rawtext_end_tag(html, "script", search, end)
        if close is None:
            return None, end
        if not escaped:
            comment_start = html.find("<!--", search, close)
            if comment_start == -1 or close < comment_start:
                return close, next_pos
            escaped = True
            # After "<!--" the tokenizer is in the script-data-escaped-dash-dash
            # state, so the two trailing dashes already count toward a closing
            # "-->". Resume the comment-end search at those dashes so "<!-->"
            # leaves the escaped state instead of being treated as still open.
            search = comment_start + 2
            continue

        script_start = find_script_start_marker(html, search, close, end) if not double_escaped else -1
        comment_end = html.find("-->", search, close)
        if (
            comment_end != -1
            and comment_end < close
            and (double_escaped or script_start == -1 or comment_end < script_start)
        ):
            escaped = False
            double_escaped = False
            search = comment_end + 3
            continue
        if script_start != -1 and script_start < close:
            double_escaped = True
            search = script_start + 7
            continue
        if double_escaped:
            later_end = ascii_find(html, "</script", close + 8, next_pos)
            if later_end != -1:
                after_name = later_end + 8
                if after_name >= end or html[after_name] in TAG_END_NAME_STOP:
                    double_escaped = False
                    search = close + 8
                    continue
            # No earlier terminated "</script" can precede `close`: find_rawtext_end_tag
            # already returns the first terminated end tag, so the region between
            # `search` and `close` holds only non-terminated occurrences.
            double_escaped = False
            search = next_pos
            continue
        return close, next_pos


def find_script_start_marker(html: str, pos: int, end: int, text_end: int) -> int:
    # `end` bounds where a "<script" marker may start; `text_end` bounds the real
    # input so the script-data-double-escape-start terminator is checked against
    # the actual following character. When these differ, a marker whose name ends
    # exactly at `end` still has a following character (e.g. the "<" of a trailing
    # "</script>") that must be inspected rather than treated as end-of-input.
    search = pos
    needle = "<script"
    needle_len = len(needle)
    while True:
        start = ascii_find(html, needle, search, end)
        if start == -1:
            return -1
        after_name = start + needle_len
        if after_name >= text_end or html[after_name] in TAG_END_NAME_STOP:
            return start
        search = after_name


def ascii_prefix_match(html: str, needle: str, start: int, end: int) -> bool:
    """Whether html[start:end] is a case-insensitive ASCII prefix of needle.

    Unlike ascii_startswith, this also matches when the buffer ends in the
    middle of the needle (needed while a CDATA section opener is split across
    chunks).
    """
    if start >= end:
        return False
    available = end - start
    if available >= len(needle):
        candidate = html[start : start + len(needle)]
    else:
        candidate = html[start:end]
    return candidate.isascii() and candidate.lower() == needle[: len(candidate)].lower()


def scan_rawtext_data(html: str, name: str, pos: int, end: int) -> tuple[str, int, int, int]:
    """Resumable rawtext/RCDATA end-tag scan over html[pos:end].

    Return shapes:
      ("close", close_pos, after_close, 0)
      ("hold", safe_end, pseudo_close, 0)

    "hold" means no terminating end tag is visible yet. Everything before
    safe_end is definitive text; html[safe_end:] must be retained and rescanned
    after the next chunk. pseudo_close is the start of a pseudo end tag (name
    terminated by a STOP character without a following ">") that is dropped at
    true EOF, or -1 when no such candidate is pending.
    """
    needle = f"</{name}"
    search = pos
    while True:
        close = ascii_find(html, needle, search, end)
        if close == -1:
            last_lt = html.rfind("<", pos, end)
            return "hold", last_lt if last_lt != -1 else end, -1, 0
        after_name = close + len(needle)
        if after_name < end and html[after_name] not in TAG_END_NAME_STOP:
            search = after_name
            continue
        tag_end = find_tag_end(html, after_name, end)
        if tag_end != -1:
            return "close", close, tag_end + 1, 0
        # A STOP character terminated the name but no ">" follows (pseudo
        # candidate, dropped at EOF); or the bare "</name" ends the buffer and
        # stays text at EOF but must be retained incrementally.
        return "hold", close, close if after_name < end else -1, 0


def _script_hold(
    html: str,
    pos: int,
    end: int,
    search: int,
    state: ScriptState,
    pseudo_close: int | None,
) -> tuple[str, int, int, int]:
    # Hold back a possible "<" opener and, inside escape states, a trailing
    # "-" or "--" that could become the end of a comment marker.
    hold = end
    if state != SCRIPT_STATE_DATA:
        dashes = 0
        while dashes < 2 and hold - dashes > search and html[hold - 1 - dashes] == "-":
            dashes += 1
        hold -= dashes
    bound = pseudo_close if pseudo_close is not None else end
    last_lt = html.rfind("<", search, bound)
    if last_lt != -1 and last_lt < hold:
        hold = last_lt
    # The hold position must never move past a pending pseudo candidate: the
    # whole candidate has to be rescanned after more bytes arrive.
    if pseudo_close is not None and hold > pseudo_close:
        hold = pseudo_close
    return (
        "hold",
        hold,
        pseudo_close if pseudo_close is not None else -1,
        _script_state_id(state),
    )


def _script_state_id(state: ScriptState) -> int:
    if state == SCRIPT_STATE_ESCAPED:
        return 1
    if state == SCRIPT_STATE_DOUBLE_ESCAPED:
        return 2
    return 0


def scan_script_data(
    html: str, pos: int, end: int, state: ScriptState = SCRIPT_STATE_DATA
) -> tuple[str, int, int, int]:
    """Resumable counterpart of find_script_end_tag.

    Return shapes:
      ("close", close_pos, after_close, 0)
      ("hold", safe_end, pseudo_close, state_id)

    safe_end bounds definitive script text; html[safe_end:] is rescanned after
    more bytes arrive. pseudo_close is the start of a pseudo end tag (name
    terminated without a following ">") that is dropped at true EOF, or -1.
    state_id (see _script_state_id) is the escape state in effect when the
    scan resumes at html[safe_end]. Callers that retain text before safe_end
    (e.g. a dangling CR) must resume in SCRIPT_STATE_DATA because the retained
    prefix re-enters the escape states deterministically.
    """
    search = pos
    while True:
        close, next_pos = find_rawtext_end_tag(html, "script", search, end)
        terminated = close is not None and (next_pos < end or html[end - 1] == ">")
        if state == SCRIPT_STATE_DATA:
            bound = close if close is not None else end
            marker = html.find("<!--", search, bound)
            if marker != -1 and (close is None or marker < close):
                state = SCRIPT_STATE_ESCAPED
                search = marker + 2
                continue
            if terminated and close is not None:
                return "close", close, next_pos, 0
            return _script_hold(html, pos, end, search, state, close)

        bound = close if close is not None else end
        comment_end = html.find("-->", search, bound)
        script_start = find_script_start_marker(html, search, bound, end) if state == SCRIPT_STATE_ESCAPED else -1
        if comment_end != -1 and (
            state == SCRIPT_STATE_DOUBLE_ESCAPED or script_start == -1 or comment_end < script_start
        ):
            state = SCRIPT_STATE_DATA
            search = comment_end + 3
            continue
        if script_start != -1:
            state = SCRIPT_STATE_DOUBLE_ESCAPED
            search = script_start + 7
            continue
        if terminated and close is not None:
            if state == SCRIPT_STATE_ESCAPED:
                return "close", close, next_pos, 0
            later_end = ascii_find(html, "</script", close + 8, next_pos)
            if later_end != -1:
                after_name = later_end + 8
                if after_name >= end or html[after_name] in TAG_END_NAME_STOP:
                    state = SCRIPT_STATE_ESCAPED
                    search = close + 8
                    continue
            state = SCRIPT_STATE_ESCAPED
            search = next_pos
            continue
        return _script_hold(html, pos, end, search, state, close)
