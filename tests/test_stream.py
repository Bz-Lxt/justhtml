import unittest

from justhtml import HTMLStream, stream
from justhtml.core.text import ascii_lower
from justhtml.parser import scanner
from justhtml.parser.encoding import ENCODING_PRESCAN_HARD_LIMIT
from justhtml.parser.scanner import (
    SCRIPT_STATE_DATA,
    SCRIPT_STATE_DOUBLE_ESCAPED,
    SCRIPT_STATE_ESCAPED,
    ascii_find,
    ascii_prefix_match,
    ascii_rfind,
    ascii_startswith,
    find_rawtext_end_tag,
    find_script_end_tag,
    scan_rawtext_data,
    scan_script_data,
)
from justhtml.parser.stream import _StreamScanner
from tests.harness.scaling import assert_scales_linearly


def _coalesce(events):
    """Merge adjacent text events; chunked output may split them."""
    merged = []
    for kind, data in events:
        if kind == "text" and merged and merged[-1][0] == "text":
            merged[-1] = ("text", merged[-1][1] + data)
        else:
            merged.append((kind, data))
    return merged


# Documents exercising every construct that can straddle a chunk boundary.
_INCREMENTAL_CORPUS = [
    "",
    "a",
    "<",
    "<!",
    "<!--",
    "<!--x",
    "<!-->x",
    "<!--->x",
    "<!--x--!>",
    "<!--x-->",
    "<a>text</a>",
    "<a>te&amp;xt</a>",
    "&amp;&lt;&gt;&quot;&nbsp;&#60;&#x3C;",
    "&notit&notin;",
    "&#" + "0" * 40,
    "&#" + "0" * 40 + ";x",
    "&#" + "1" * 40,
    "&#" + "1" * 40 + ";x",
    "&#x" + "a" * 40,
    "&#x" + "f" * 40 + ";x",
    "&#" + "0" * 25 + "1114112;y",
    "&#1114112;z",
    "a&" + "x" * 40,
    "&not" + "i" * 40,
    "&" + "x" * 40 + ";",
    "a&b\nc&d;e&#123;&#x7B;",
    "x\r\ny",
    "x\ry",
    "x\ry\nz\r",
    "<a>x\r\ny",
    "<script>var a = 1;</script><p>after</p>",
    "<script><!-- if (a<b && c>d) { x() } --></script><p>z</p>",
    "<script><!-- a <script> b --></script c><p>z</p>",
    '<script><!--<script X </script foo="</script>">tail</script><p>q</p>',
    "<script>a</script b>tail",
    "<script>a</script",
    "<script><!--",
    "<script><!-->x</script>",
    "<script>var s = '<!-- <script> --></script>';</script><p>q</p>",
    "<script>a\r\nb</script><p>q</p>",
    "<script>a\r</script>",
    "<SCRIPT TYPE=x>A</SCRIPT><p>q</p>",
    "<style>.x{color:red}</style><p>y</p>",
    "<style>a</style b>x</style>",
    "<style>a</style",
    "<textarea>a</textarea b>x",
    "<textarea>&amp;&#60;</textarea>",
    "<title>a&amp;b</title>",
    "<plaintext>a<b>still text\r&c",
    "<plaintext>a\r\nb",
    "<svg><![CDATA[a <b> &amp; ]]></svg><p>z</p>",
    "<svg><![CDATA[x",
    "<![CDATA[x]]>",
    "<math></math>",
    "<svg><script>x</script></svg>",
    "<svg><altglyph/></svg>",
    "<svg></br><script><b></script>",
    "<svg><!-- c --><g/></svg>",
    "<a href=\"x\" target='y'>z</a>",
    '<a title="unclosed',
    "<!DOCTYPE html><html><head></head><body><p>hi</p></body></html>",
    '<!DOCTYPE html SYSTEM "x">hi',
    "<!DOCTYPE",
    "<?pi data ?>x",
    "<?pi",
    "</div>x",
    "</>x",
    "x</",
    "</!x>y",
    '</e2/T2"ab;e>x',
    "<a><b><c>deep</c></b></a>",
    "<svg><foreignObject><div>x</div></foreignObject></svg>",
    "head<script>x</script>tail<style>y</style>end",
    "<iframe></iframe><p>",
    "<xmp></xmp b>t",
    "<noscript></noscript>",
    "<noframes></noframes>",
    "<noembed></noembed>",
    "a<3b",
    "\0a\0",
]


class TestStream(unittest.TestCase):
    def test_basic_stream(self):
        html = '<div class="container">Hello <b>World</b></div>'
        events = list(stream(html))

        expected = [
            ("start", ("div", {"class": "container"})),
            ("text", "Hello "),
            ("start", ("b", {})),
            ("text", "World"),
            ("end", "b"),
            ("end", "div"),
        ]
        assert events == expected

    def test_comments(self):
        html = "<!-- comment -->"
        events = list(stream(html))
        expected = [("comment", " comment ")]
        assert events == expected

    def test_doctype(self):
        html = "<!DOCTYPE html>"
        events = list(stream(html))
        # Doctype token structure: (name, public_id, system_id)
        expected = [("doctype", ("html", None, None))]
        assert events == expected

    def test_void_elements(self):
        html = "<br><hr>"
        events = list(stream(html))
        expected = [
            ("start", ("br", {})),
            # The event stream does not synthesize end tags for void elements.
            ("start", ("hr", {})),
        ]
        assert events == expected

    def test_text_coalescing(self):
        # The scanner may find adjacent text spans. Stream should coalesce.
        html = "abc"
        events = list(stream(html))
        expected = [("text", "abc")]
        assert events == expected

    def test_script_rawtext(self):
        html = "<script>console.log('<');</script>"
        events = list(stream(html))
        expected = [
            ("start", ("script", {})),
            ("text", "console.log('<');"),
            ("end", "script"),
        ]
        assert events == expected

    def test_ascii_lower_preserves_full_document_search_offsets_and_ascii_markers(self):
        # The parser searches this folded full-document copy for ASCII markup
        # markers, then reuses the match offsets against the original string.
        # It must only fold ASCII A-Z, not perform Unicode case folding.
        assert ascii_lower("café</SCRIPT>") == "café</script>"
        assert ascii_lower("İ</SCRIPT>") == "İ</script>"

        # U+212A lowercases to ASCII "k" under Python's Unicode lowercasing,
        # but HTML marker matching is only ASCII case-insensitive. Keep it as
        # text so future searches for ASCII markers containing "k" cannot
        # accidentally match non-ASCII content.
        assert ascii_lower("\u212a</SCRIPT>") == "\u212a</script>"

    def test_svg_cdata_streams_as_text(self):
        html = "<svg><![CDATA[x<y]]></svg>"
        events = list(stream(html))
        expected = [
            ("start", ("svg", {})),
            ("text", "x<y"),
            ("end", "svg"),
        ]
        assert events == expected

    def test_math_cdata_streams_as_text(self):
        html = "<math><![CDATA[x<y]]></math>"
        events = list(stream(html))
        expected = [
            ("start", ("math", {})),
            ("text", "x<y"),
            ("end", "math"),
        ]
        assert events == expected

    def test_svg_script_does_not_switch_to_rawtext(self):
        html = "<svg><script><b></script></svg>"
        events = list(stream(html))
        expected = [
            ("start", ("svg", {})),
            ("start", ("script", {})),
            ("start", ("b", {})),
            ("end", "script"),
            ("end", "svg"),
        ]
        assert events == expected

    def test_foreign_font_breakout_depends_on_attrs(self):
        html_breakout = "<svg><font color=x></font><script><b></script>"
        events_breakout = list(stream(html_breakout))
        assert events_breakout == [
            ("start", ("svg", {})),
            ("start", ("font", {"color": "x"})),
            ("end", "font"),
            ("start", ("script", {})),
            ("text", "<b>"),
            ("end", "script"),
        ]

        html_late_breakout_attr = "<svg><font data-x=1 size=2></font><script><b></script>"
        events_late_breakout_attr = list(stream(html_late_breakout_attr))
        assert events_late_breakout_attr == [
            ("start", ("svg", {})),
            ("start", ("font", {"data-x": "1", "size": "2"})),
            ("end", "font"),
            ("start", ("script", {})),
            ("text", "<b>"),
            ("end", "script"),
        ]

        html_foreign = "<svg><font></font><script><b></script>"
        events_foreign = list(stream(html_foreign))
        assert events_foreign == [
            ("start", ("svg", {})),
            ("start", ("font", {})),
            ("end", "font"),
            ("start", ("script", {})),
            ("start", ("b", {})),
            ("end", "script"),
        ]

    def test_self_closing_foreign_tags_do_not_leave_stream_in_foreign_context(self):
        events_svg = list(stream("<svg/><script><b></script>"))
        assert events_svg == [
            ("start", ("svg", {})),
            ("start", ("script", {})),
            ("text", "<b>"),
            ("end", "script"),
        ]

        events_math = list(stream("<math/><script><b></script>"))
        assert events_math == [
            ("start", ("math", {})),
            ("start", ("script", {})),
            ("text", "<b>"),
            ("end", "script"),
        ]

    def test_foreign_end_tags_pop_matching_stream_context(self):
        html = "<svg><g></svg><script><b></script>"
        events = list(stream(html))
        assert events == [
            ("start", ("svg", {})),
            ("start", ("g", {})),
            ("end", "svg"),
            ("start", ("script", {})),
            ("text", "<b>"),
            ("end", "script"),
        ]

    def test_stream_integration_points_use_html_text_parsing(self):
        html = "<svg><foreignObject><textarea><b></textarea></foreignObject></svg>"
        events = list(stream(html))
        assert events == [
            ("start", ("svg", {})),
            ("start", ("foreignobject", {})),
            ("start", ("textarea", {})),
            ("text", "<b>"),
            ("end", "textarea"),
            ("end", "foreignobject"),
            ("end", "svg"),
        ]

        html_math = "<math><mi><style><b></style></mi></math>"
        events_math = list(stream(html_math))
        assert events_math == [
            ("start", ("math", {})),
            ("start", ("mi", {})),
            ("start", ("style", {})),
            ("text", "<b>"),
            ("end", "style"),
            ("end", "mi"),
            ("end", "math"),
        ]

        html_annotation = '<math><annotation-xml encoding="text/html"><script><b></script></annotation-xml></math>'
        events_annotation = list(stream(html_annotation))
        assert events_annotation == [
            ("start", ("math", {})),
            ("start", ("annotation-xml", {"encoding": "text/html"})),
            ("start", ("script", {})),
            ("text", "<b>"),
            ("end", "script"),
            ("end", "annotation-xml"),
            ("end", "math"),
        ]

        html_annotation_late_encoding = (
            '<math><annotation-xml data-x=1 encoding="application/xhtml+xml">'
            "<script><b></script></annotation-xml></math>"
        )
        events_annotation_late_encoding = list(stream(html_annotation_late_encoding))
        assert events_annotation_late_encoding == [
            ("start", ("math", {})),
            ("start", ("annotation-xml", {"data-x": "1", "encoding": "application/xhtml+xml"})),
            ("start", ("script", {})),
            ("text", "<b>"),
            ("end", "script"),
            ("end", "annotation-xml"),
            ("end", "math"),
        ]

    def test_stream_annotation_xml_without_html_encoding_keeps_foreign_context(self):
        html = "<math><annotation-xml><script><b></script></annotation-xml></math>"
        events = list(stream(html))
        assert events == [
            ("start", ("math", {})),
            ("start", ("annotation-xml", {})),
            ("start", ("script", {})),
            ("start", ("b", {})),
            ("end", "script"),
            ("end", "annotation-xml"),
            ("end", "math"),
        ]

    def test_stream_annotation_xml_allows_svg_without_html_encoding(self):
        html = "<math><annotation-xml><svg><![CDATA[x<y]]></svg></annotation-xml></math>"
        events = list(stream(html))
        assert events == [
            ("start", ("math", {})),
            ("start", ("annotation-xml", {})),
            ("start", ("svg", {})),
            ("text", "x<y"),
            ("end", "svg"),
            ("end", "annotation-xml"),
            ("end", "math"),
        ]

    def test_stream_foreign_p_end_tag_breaks_out_of_foreign_context(self):
        html = "<svg><g></p><script><b></script>"
        events = list(stream(html))
        assert events == [
            ("start", ("svg", {})),
            ("start", ("g", {})),
            ("end", "p"),
            ("start", ("script", {})),
            ("text", "<b>"),
            ("end", "script"),
        ]

    def test_stream_unmatched_foreign_end_tag_pops_one_context(self):
        html = "<svg><g></foo><script><b></script>"
        events = list(stream(html))
        assert events == [
            ("start", ("svg", {})),
            ("start", ("g", {})),
            ("end", "foo"),
            ("start", ("script", {})),
            ("start", ("b", {})),
            ("end", "script"),
        ]

    def test_stream_unmatched_html_end_tag_with_stack_pops_one_context(self):
        html = "<div></span><script><b></script>"
        events = list(stream(html))
        assert events == [
            ("start", ("div", {})),
            ("end", "span"),
            ("start", ("script", {})),
            ("text", "<b>"),
            ("end", "script"),
        ]

    def test_unmatched_end_tag(self):
        html = "</div>"
        events = list(stream(html))
        expected = [("end", "div")]
        assert events == expected

    def test_unmatched_end_tag_stops_at_nearer_html_element(self):
        # </foo> targets the outer <foo>, but the nearer <bar> (also HTML
        # namespace) isn't a match, so only the innermost open element is
        # popped and <foo>/<bar> remain unmatched.
        html = "<foo><bar><svg></svg></foo>"
        events = list(stream(html))
        assert events == [
            ("start", ("foo", {})),
            ("start", ("bar", {})),
            ("start", ("svg", {})),
            ("end", "svg"),
            ("end", "foo"),
        ]

    def test_trailing_less_than_and_non_tag_openers_are_text(self):
        assert list(stream("abc<")) == [("text", "abc<")]
        assert list(stream("<1x")) == [("text", "<1x")]

    def test_bogus_end_tags_and_processing_instructions_are_comments(self):
        assert list(stream("</>")) == []
        assert list(stream("</!x>")) == [("comment", "!x")]
        assert list(stream("</!x")) == [("comment", "!x")]
        assert list(stream("<?x>")) == [("comment", "?x")]
        assert list(stream("<?x")) == [("comment", "?x")]

    def test_unclosed_tags_are_ignored(self):
        assert list(stream("<div")) == []
        assert list(stream("</div")) == []
        assert list(stream("<x q='unterminated")) == []

    def test_plaintext_and_unclosed_rawtext(self):
        assert list(stream("<plaintext>a<b>")) == [
            ("start", ("plaintext", {})),
            ("text", "a<b>"),
        ]
        assert list(stream("<style>x")) == [
            ("start", ("style", {})),
            ("text", "x"),
        ]
        assert list(stream("<style>x</style")) == [
            ("start", ("style", {})),
            ("text", "x</style"),
        ]

    def test_comment_edge_cases(self):
        assert list(stream("<!-->")) == [("comment", "")]
        assert list(stream("<!--->")) == [("comment", "")]
        assert list(stream("<!--x--!>")) == [("comment", "x")]
        assert list(stream("<!--x--")) == [("comment", "x")]
        assert list(stream("<!--x\0-->")) == [("comment", "x\ufffd")]

    def test_doctype_edge_cases(self):
        assert list(stream("<!DOCTYPE")) == [("doctype", (None, None, None))]
        assert list(stream("<!DOCTYPE >")) == [("doctype", (None, None, None))]
        assert list(stream("<!DoCtYpE HTML>")) == [("doctype", ("html", None, None))]

    def test_doctype_identifiers_and_malformed_quotes(self):
        assert list(stream('<!DOCTYPE html SYSTEM "x">Hello')) == [
            ("doctype", ("html", None, "x")),
            ("text", "Hello"),
        ]
        assert list(stream('<!DOCTYPE html PUBLIC "x" "y">Hello')) == [
            ("doctype", ("html", "x", "y")),
            ("text", "Hello"),
        ]
        assert list(stream('<!DOCTYPE potato taco "ddd>Hello')) == [
            ("doctype", ("potato", None, None)),
            ("text", "Hello"),
        ]
        assert list(stream("<!DOCTYPE potato PUBLIC 'go'of'>Hello")) == [
            ("doctype", ("potato", "go", None)),
            ("text", "Hello"),
        ]
        assert list(stream("<!DOCTYPE h\0 PUBLIC 'p\0' 's\0'>")) == [
            ("doctype", ("h\ufffd", "p\ufffd", "s\ufffd")),
        ]

    def test_null_handling_matches_stream_compatibility(self):
        assert list(stream("a\0b")) == [("text", "a\0b")]
        assert list(stream("<svg><![CDATA[a\0b]]></svg>")) == [
            ("start", ("svg", {})),
            ("text", "a\0b"),
            ("end", "svg"),
        ]
        assert list(stream("<style>a\0b</style>")) == [
            ("start", ("style", {})),
            ("text", "a\ufffdb"),
            ("end", "style"),
        ]
        assert list(stream("<!--a\0b-->")) == [("comment", "a\ufffdb")]

    def test_unclosed_cdata_and_bogus_declarations(self):
        assert list(stream("<svg><![CDATA[x")) == [
            ("start", ("svg", {})),
            ("text", "x"),
        ]
        assert list(stream("<!foo")) == [("comment", "foo")]
        assert list(stream("<!foo>")) == [("comment", "foo")]

    def test_attribute_edge_cases(self):
        assert list(stream("<DIV A\0B=x\0&amp; A=dup>")) == [
            ("start", ("div", {"a\ufffdb": "x\ufffd&", "a": "dup"})),
        ]
        assert list(stream('<x a = unquoted b="v&amp;">')) == [
            ("start", ("x", {"a": "unquoted", "b": "v&"})),
        ]
        assert list(stream("<x / >")) == [("start", ("x", {}))]
        assert list(stream("<x/>")) == [("start", ("x", {}))]
        assert list(stream("<x a=first a=second>")) == [
            ("start", ("x", {"a": "first"})),
        ]

    def test_bytes_input(self):
        assert list(stream(b"<p>x</p>")) == [
            ("start", ("p", {})),
            ("text", "x"),
            ("end", "p"),
        ]

    def test_scanner_defensive_helpers(self):
        empty = _StreamScanner("")
        assert empty._parse_start_tag(0, 0) is None
        assert empty._parse_end_tag(0, 0) is None
        assert empty._in_foreign_context() is False

        spaces = _StreamScanner(" ")
        assert spaces._parse_attrs(0, 1) == ({}, False, 1, False)
        bare_attribute = _StreamScanner("a")
        assert bare_attribute._parse_attrs(0, 1) == ({"a": ""}, False, 1, False)

        text_buffer: list[str] = []
        empty._append_text(text_buffer, "")
        empty._append_text(text_buffer, "\r\n\0&amp;")
        assert text_buffer == ["\n\0&"]
        assert list(empty._flush_text([""])) == []

    def test_ascii_marker_searches_fold_only_ascii_candidates(self):
        html = "<sty\u212ae><STYLE>x</style>"

        assert ascii_startswith(html, "<style", 7, len(html)) is True
        assert ascii_startswith(html, "<style", 0, len(html)) is False
        assert ascii_startswith("DOCTYPE", "doctype", 0, 3) is False
        assert ascii_find(html, "<style", 0, len(html)) == 7
        assert ascii_find(html, "<script", 0, len(html)) == -1
        assert ascii_rfind(html, "</style", 0, len(html)) == 15
        assert ascii_rfind(html, "</script", 0, len(html)) == -1
        assert ascii_startswith("doctype", "DOCTYPE", 0, 7) is True
        assert ascii_find("xDOCTYPEdoctype", "doctype", 0, 15) == 1
        assert ascii_find("DOCTYPE", "doctype", 0, 7) == 0
        assert ascii_find("doctypeDOCTYPE", "doctype", 0, 14) == 0
        assert ascii_find("doctype", "DOCTYPE", 0, 7) == 0
        assert ascii_find("xDOCTYPE", "doctype", 0, 7) == -1
        assert ascii_rfind("DOCTYPEdoctype", "DOCTYPE", 0, 14) == 7
        assert ascii_rfind("DOCTYPE", "doctype", 0, 7) == 0
        assert ascii_rfind("DOCTYPE", "DOCTYPE", 0, 7) == 0

    def test_script_scanner_rejects_nested_invalid_end_markers(self):
        invalid_inner = "<!--<script X </scriptx </script>"
        assert find_script_end_tag(invalid_inner, 0, len(invalid_inner)) == (
            None,
            len(invalid_inner),
        )

        invalid_quoted = '<!--<script X </script foo="</scriptx">tail</script>'
        assert find_script_end_tag(invalid_quoted, 0, len(invalid_quoted)) == (43, 52)

        # A terminated inner end marker inside the outer end tag's quoted
        # attributes leaves the double-escaped state before that inner marker.
        valid_quoted = '<!--<script X </script foo="</script>">tail</script>'
        assert find_script_end_tag(valid_quoted, 0, len(valid_quoted)) == (28, 37)

        # A "<script" immediately followed by "</script>" is not a
        # script-data-double-escape-start (its following character "<" is not a
        # terminator), so the trailing "</script>" must still close the element.
        unterminated_marker = "<!--<script</script>"
        assert find_script_end_tag(unterminated_marker, 0, len(unterminated_marker)) == (
            11,
            20,
        )

        # "<!-->" closes the escaped comment immediately (the "<!--" dashes count
        # toward the "-->"), so the following "<script>" is plain script data and
        # does not start a double escape; the "</script>" still closes it.
        short_comment = "<!--><script></script>"
        assert find_script_end_tag(short_comment, 0, len(short_comment)) == (13, 22)

    def test_rawtext_end_tag_slash_terminated_without_gt(self):
        # "</style/x" terminates the end-tag name with "/", so at EOF the end tag
        # is emitted (attributes dropped) and the raw text ends, even though no
        # ">" ever appears.
        slashed = "</style/x"
        assert find_rawtext_end_tag(slashed, "style", 0, len(slashed)) == (0, len(slashed))
        # A bare "</style" at EOF with no terminator stays raw text.
        bare = "</style"
        assert find_rawtext_end_tag(bare, "style", 0, len(bare)) == (None, len(bare))

    def test_rawtext_end_tag_after_length_changing_case_char(self):
        # "İ" (U+0130) lowers to two characters, so a str.lower() copy of the
        # input drifts out of index alignment with the original. The rawtext
        # scan must keep finding the real end tag after such a character.
        assert list(stream("<div>İ</div><script>y</script>0")) == [
            ("start", ("div", {})),
            ("text", "İ"),
            ("end", "div"),
            ("start", ("script", {})),
            ("text", "y"),
            ("end", "script"),
            ("text", "0"),
        ]
        assert list(stream('<script>var x = "İ";</script>0')) == [
            ("start", ("script", {})),
            ("text", 'var x = "İ";'),
            ("end", "script"),
            ("text", "0"),
        ]

    def test_doctype_after_length_changing_case_char(self):
        assert list(stream("İ<!DOCTYPE html>")) == [
            ("text", "İ"),
            ("doctype", ("html", None, None)),
        ]

    def test_foreign_cdata_after_length_changing_case_char(self):
        assert list(stream("<svg>İ<![CDATA[x]]></svg>")) == [
            ("start", ("svg", {})),
            ("text", "İ"),
            ("text", "x"),
            ("end", "svg"),
        ]


class TestStreamScaling(unittest.TestCase):
    def test_annotation_xml_integration_checks_scale_linearly(self) -> None:
        def source(size: int) -> str:
            attrs = " ".join(f"a{index}" for index in range(size))
            return f"<math><annotation-xml {attrs} encoding=text/html>" + "<svg/>" * size

        assert_scales_linearly(source, lambda html: list(stream(html)))

    def test_foreign_boundary_end_tags_scale_linearly(self) -> None:
        assert_scales_linearly(
            lambda size: "<div><span><svg>" + "<g>" * size + "</div>" * size,
            lambda html: list(stream(html)),
        )


class TestIncrementalChunkEquivalence(unittest.TestCase):
    """Chunked parsing must be event-equivalent to one-shot parsing."""

    def _reference(self, doc):
        return _coalesce(list(stream(doc)))

    def _assert_via_stream(self, doc, chunks):
        assert _coalesce(list(stream(chunks))) == self._reference(doc)

    def _assert_via_push(self, doc, chunks):
        parser = HTMLStream()
        events = []
        for chunk in chunks:
            events.extend(parser.feed(chunk))
        events.extend(parser.finish())
        assert _coalesce(events) == self._reference(doc)

    def _assert_all_splits(self, doc):
        reference = self._reference(doc)
        # Two-chunk splits at every possible boundary.
        for cut in range(1, len(doc)):
            chunks = [doc[:cut], doc[cut:]]
            assert _coalesce(list(stream(chunks))) == reference, (cut, chunks)
            generator = (chunk for chunk in chunks)
            assert _coalesce(list(stream(generator))) == reference, (cut, chunks)
            self._assert_via_push(doc, chunks)
        # Fixed-size multi-chunk splits, including size 1 (every byte own chunk).
        for size in range(1, min(len(doc), 12) + 1):
            chunks = [doc[i : i + size] for i in range(0, len(doc), size)]
            assert _coalesce(list(stream(chunks))) == reference, (size, chunks)
            self._assert_via_push(doc, chunks)

    def test_every_split_of_every_corpus_document_matches_one_shot(self):
        for doc in _INCREMENTAL_CORPUS:
            with self.subTest(doc=doc):
                if not doc:
                    assert list(stream([])) == []
                    continue
                self._assert_all_splits(doc)

    def test_split_document_emits_events_before_end_of_input(self):
        # Definitive events are yielded as chunks arrive rather than buffered.
        parser = HTMLStream()
        assert list(parser.feed("<p>abc")) == [("start", ("p", {})), ("text", "abc")]
        assert list(parser.feed("def</p>")) == [("text", "def"), ("end", "p")]
        assert list(parser.finish()) == []

    def test_text_events_split_at_boundary_coalesce(self):
        parser = HTMLStream()
        assert list(parser.feed("ab")) == [("text", "ab")]
        assert list(parser.feed("cd")) == [("text", "cd")]
        assert list(parser.finish()) == []

    def test_rcdata_entity_and_pending_close_tag_share_a_boundary(self):
        # The compressed numeric reference must not swallow a held CR or a
        # pending "</textarea" prefix waiting in the same retained suffix.
        doc = "<textarea>" + "&#" + "0" * 40 + "\r</textarea>x"
        chunks = ["<textarea>", "&#" + "0" * 40, "\r</texta", "rea>x"]
        assert _coalesce(list(stream(chunks))) == _coalesce(list(stream(doc)))
        self._assert_via_push(doc, chunks)

    def test_overlong_numeric_reference_dangles_across_chunks(self):
        # The replacement character is emitted early; continuation digits and
        # one optional ';' are swallowed from later chunks.
        for marker, tail, hex_mode in (
            ("&#" + "1" * 40, "12345;rest", False),
            ("&#x" + "a" * 40, "bcdef;rest", True),
        ):
            with self.subTest(hex_mode=hex_mode):
                parser = _StreamScanner()
                events = list(parser.feed_text(marker))
                assert events == [("text", "\ufffd")]
                # A chunk made entirely of continuation digits yields nothing.
                assert list(parser.feed_text(tail[:3])) == []
                assert parser._dangle_hex is hex_mode
                remaining = list(parser.feed_text(tail[3:]))
                assert list(parser.finish_text()) == []
                assert _coalesce([*remaining]) == [("text", "rest")]
                # A non-'/' non-';' terminator ends the swallow without the
                # optional semicolon; the rest of the chunk rescans as data.
                parser = _StreamScanner()
                list(parser.feed_text(marker))
                remaining = list(parser.feed_text("2rest"))
                assert parser._dangle_hex is None
                assert _coalesce([*remaining]) == [("text", "rest")]

    def test_leading_zero_run_is_compressed_then_decoded_later(self):
        # An arbitrarily long all-zero run stays bounded in memory.
        scanner_obj = _StreamScanner()
        list(scanner_obj.feed_text("&#" + "0" * 40))
        assert scanner_obj._html == "&#0"
        events = list(scanner_obj.feed_text("00000000;x"))
        finished = list(scanner_obj.finish_text())
        # &#<48 zeros>; is NUL, which decodes to U+FFFD.
        assert _coalesce([*events, *finished]) == [("text", "\ufffdx")]

    def test_trailing_cr_is_retained_in_data_mode(self):
        parser = _StreamScanner()
        assert list(parser.feed_text("a\r")) == [("text", "a")]
        assert list(parser.feed_text("\nb")) == [("text", "\nb")]
        assert list(parser.finish_text()) == []

    def test_early_generator_close_stops_feeding(self):
        chunks = ["<p>", "a", "</p>"]
        generator = stream(chunks)
        assert next(generator) == ("start", ("p", {}))
        generator.close()

        # Closing while suspended inside finish() (first chunk emitted nothing).
        generator = stream(["<"])
        assert next(generator) == ("text", "<")
        generator.close()


class TestHTMLStreamPushAPI(unittest.TestCase):
    def test_empty_document(self):
        parser = HTMLStream()
        assert list(parser.finish()) == []
        # finish() is idempotent.
        assert list(parser.finish()) == []

    def test_empty_str_and_bytes_chunks_before_finish(self):
        parser = HTMLStream()
        assert list(parser.feed("")) == []
        assert list(parser.finish()) == []

        parser = HTMLStream()
        assert list(parser.feed(b"")) == []
        assert parser.encoding is None
        assert list(parser.finish()) == []
        assert parser.encoding == "windows-1252"

    def test_feed_after_finish_raises(self):
        parser = HTMLStream()
        list(parser.feed("a"))
        list(parser.finish())
        with self.assertRaises(RuntimeError):
            list(parser.feed("b"))

    def test_reject_unsupported_chunk_type(self):
        parser = HTMLStream()
        with self.assertRaises(TypeError):
            list(parser.feed(123))

    def test_reject_mixed_chunk_types(self):
        parser = HTMLStream()
        list(parser.feed("a"))
        with self.assertRaises(TypeError):
            list(parser.feed(b"b"))

        parser = HTMLStream()
        list(parser.feed(b"a"))
        with self.assertRaises(TypeError):
            list(parser.feed("b"))

    def test_stream_rejects_non_iterable_non_document(self):
        with self.assertRaises(TypeError):
            list(stream(123))

    def test_bytes_like_chunk_types(self):
        # No BOM/meta/transport: the one-shot-compatible fallback is
        # windows-1252, so 0xC3 0xA9 decode as two Latin-1 characters.
        expected = [
            ("start", ("p", {})),
            ("text", "caf\u00c3\u00a9"),
            ("end", "p"),
        ]
        for container in (bytes, bytearray, memoryview):
            with self.subTest(container=container):
                chunks = [container(b"<p>caf"), container(b"\xc3\xa9</p>")]
                assert _coalesce(list(stream(chunks))) == expected

    def test_encoding_property_is_unknown_until_prefix_resolves(self):
        parser = HTMLStream()
        assert parser.encoding is None
        assert list(parser.feed(b"\xef")) == []
        assert parser.encoding is None
        assert list(parser.feed(b"\xbb")) == []
        assert parser.encoding is None
        assert list(parser.feed(b"\xbf<p>")) == [("start", ("p", {}))]
        assert parser.encoding == "utf-8"
        assert list(parser.finish()) == []

    def test_bom_split_at_every_byte_position(self):
        documents = [
            (b"\xef\xbb\xbf<p>\xc3\xa9</p>", None),
            (b"\xff\xfe<\x00p\x00>\x00h\x00i\x00<\x00/\x00p\x00>\x00", None),
            (b"\xfe\xff\x00<\x00p\x00>\x00h\x00i\x00<\x00/\x00p\x00>", None),
        ]
        for raw, transport in documents:
            reference = _coalesce(list(stream(raw, encoding=transport)))
            for cut in range(1, len(raw)):
                chunks = [raw[:cut], raw[cut:]]
                assert _coalesce(list(stream(chunks, encoding=transport))) == reference, cut

    def test_meta_charset_split_across_chunks(self):
        raw = b"<meta charset='utf-8'><p>caf\xc3\xa9 \xe4\xb8\xad</p>"
        reference = _coalesce(list(stream(raw)))
        for cut in range(1, len(raw)):
            parser = HTMLStream()
            events = list(parser.feed(raw[:cut]))
            events.extend(parser.feed(raw[cut:]))
            events.extend(parser.finish())
            assert _coalesce(events) == reference, cut
        # The encoding locks as soon as the meta tag is complete.
        parser = HTMLStream()
        list(parser.feed(b"<meta charset='utf-8'"))
        assert parser.encoding is None
        list(parser.feed(b">"))
        assert parser.encoding == "utf-8"

    def test_transport_encoding_applies_immediately(self):
        parser = HTMLStream(encoding="iso-8859-2")
        # 0xe9 is LATIN SMALL LETTER E WITH ACUTE in ISO-8859-2 too.
        assert list(parser.feed(b"<p>\xe9</p>")) == [
            ("start", ("p", {})),
            ("text", "\u00e9"),
            ("end", "p"),
        ]
        assert parser.encoding == "iso-8859-2"

    def test_windows_1252_transport_decodes_incrementally(self):
        raw = b"<p>ok\x81</p>"
        parser = HTMLStream(encoding="windows-1252")
        events = list(parser.feed(raw[:4]))
        events.extend(parser.feed(raw[4:]))
        events.extend(parser.finish())
        assert _coalesce(events) == [
            ("start", ("p", {})),
            ("text", "ok\ufffd"),
            ("end", "p"),
        ]

    def test_unresolved_prefix_falls_back_to_one_shot_decode_at_finish(self):
        # No BOM/transport/meta within a tiny document: the complete prefix is
        # decoded once at finish(), matching one-shot windows-1252 semantics.
        raw = b"<p>caf\xe9</p>"
        parser = HTMLStream()
        events = list(parser.feed(raw[:5]))
        events.extend(parser.feed(raw[5:]))
        assert parser.encoding is None
        events.extend(parser.finish())
        assert parser.encoding == "windows-1252"
        assert _coalesce(events) == _coalesce(list(stream(raw)))

    def test_prescan_hard_limit_locks_default_encoding(self):
        raw = b"a" * (ENCODING_PRESCAN_HARD_LIMIT + 100)
        parser = HTMLStream()
        events = list(parser.feed(raw[: ENCODING_PRESCAN_HARD_LIMIT - 10]))
        assert parser.encoding is None
        events.extend(parser.feed(raw[ENCODING_PRESCAN_HARD_LIMIT - 10 :]))
        assert parser.encoding == "windows-1252"
        events.extend(parser.finish())
        assert _coalesce(events) == [("text", "a" * len(raw))]

    def test_incomplete_multibyte_sequence_at_end_flushes_replacement(self):
        raw = b"\xef\xbb\xbf<p>\xc3"
        parser = HTMLStream()
        events = list(parser.feed(raw))
        events.extend(parser.finish())
        assert _coalesce(events) == _coalesce(list(stream(raw)))
        assert events[-2:] == [("text", "\ufffd"), ("end", "p")] or (("text", "\ufffd") in events)

    def test_one_shot_input_types_remain_supported(self):
        assert _coalesce(list(stream(bytearray(b"<p>x</p>")))) == [
            ("start", ("p", {})),
            ("text", "x"),
            ("end", "p"),
        ]
        assert _coalesce(list(stream(memoryview(b"<p>x</p>")))) == [
            ("start", ("p", {})),
            ("text", "x"),
            ("end", "p"),
        ]


class TestResumableScannerHelpers(unittest.TestCase):
    def test_ascii_prefix_match(self):
        assert ascii_prefix_match("ab[cdata[x", "[cdata[", 2, 10) is True
        assert ascii_prefix_match("ab[CDAT", "[cdata[", 2, 7) is True
        assert ascii_prefix_match("ab[xyz", "[cdata[", 2, 6) is False
        assert ascii_prefix_match("ab", "[cdata[", 2, 2) is False

    def test_scan_rawtext_data_shapes(self):
        assert scan_rawtext_data("ab<x", "style", 0, 5) == ("hold", 2, -1, 0)
        assert scan_rawtext_data("ab</sty", "style", 0, 8) == ("hold", 2, -1, 0)
        assert scan_rawtext_data("ab</style x", "style", 0, 11) == ("hold", 2, 2, 0)
        assert scan_rawtext_data("ab</style", "style", 0, 9) == ("hold", 2, -1, 0)
        assert scan_rawtext_data("ab</style>x", "style", 0, 11) == ("close", 2, 10, 0)
        assert scan_rawtext_data("a<b", "style", 0, 3) == ("hold", 1, -1, 0)
        # A name character directly after the candidate ("</stylex") is not a
        # close tag; scanning resumes after the candidate.
        assert scan_rawtext_data("ab</stylex y", "style", 0, 13) == ("hold", 2, -1, 0)

    def test_scan_script_data_data_state(self):
        assert scan_script_data("abc", 0, 3, SCRIPT_STATE_DATA) == ("hold", 3, -1, 0)
        assert scan_script_data("a</script>x", 0, 11, SCRIPT_STATE_DATA) == ("close", 1, 10, 0)
        assert scan_script_data("a</script b", 0, 11, SCRIPT_STATE_DATA) == ("hold", 1, 1, 0)
        # A split "<!--" opener enters the escaped state and is retained.
        assert scan_script_data("a<!--", 0, 5, SCRIPT_STATE_DATA) == ("hold", 3, -1, 1)

    def test_scan_script_data_escaped_state(self):
        # Trailing dashes that could complete a "-->" are held back.
        assert scan_script_data("a--", 0, 3, SCRIPT_STATE_ESCAPED) == ("hold", 1, -1, 1)
        assert scan_script_data("a-", 0, 2, SCRIPT_STATE_ESCAPED) == ("hold", 1, -1, 1)
        assert scan_script_data("a<", 0, 2, SCRIPT_STATE_ESCAPED) == ("hold", 1, -1, 1)
        assert scan_script_data("a--></script>x", 0, 14, SCRIPT_STATE_ESCAPED) == (
            "close",
            4,
            13,
            0,
        )

    def test_scan_script_data_double_escaped_state(self):
        assert scan_script_data("a</script", 0, 9, SCRIPT_STATE_DOUBLE_ESCAPED) == (
            "hold",
            1,
            -1,
            2,
        )
        # A terminated "</script>" in double-escape merely leaves that state,
        # and an inner marker followed by a STOP char ends the outer search.
        assert scan_script_data('a</script foo="</script>">x', 0, 27, SCRIPT_STATE_DOUBLE_ESCAPED) == (
            "close",
            15,
            24,
            0,
        )
        # A pending pseudo candidate clamps the hold position.
        assert scan_script_data('a</script x"</script >tail', 0, 26, SCRIPT_STATE_DOUBLE_ESCAPED) == ("hold", 1, 1, 2)
        # An inner "</script" followed by a name character is not a close tag;
        # the state falls back to escaped and scanning resumes past it.
        assert scan_script_data('a</script foo="</scriptX">x', 0, 27, SCRIPT_STATE_DOUBLE_ESCAPED) == (
            "hold",
            27,
            -1,
            1,
        )
        # A terminated pseudo tag containing no inner candidate drops back to
        # the escaped state and resumes after the pseudo tag.
        assert scan_script_data('a</script foo="bar">xy', 0, 21, SCRIPT_STATE_DOUBLE_ESCAPED) == ("hold", 21, -1, 1)

    def test_script_state_ids(self):
        assert scanner._script_state_id(SCRIPT_STATE_DATA) == 0
        assert scanner._script_state_id(SCRIPT_STATE_ESCAPED) == 1
        assert scanner._script_state_id(SCRIPT_STATE_DOUBLE_ESCAPED) == 2
