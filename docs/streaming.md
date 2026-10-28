[← Back to docs](index.md)

# Streaming API

For large files or when you don't need the full DOM tree, use the streaming API.

## Overview

The streaming parser is:
- **Memory efficient** - Processes HTML without building a tree
- **Fast** - Minimal overhead per token
- **Simple** - Just iterate over events

## Basic Usage

```python
from justhtml import stream

html = "<html><body><p>Hello, world!</p></body></html>"

for event, data in stream(html):
    print(event, data)
```

<!-- justhtml: output -->
```text
start ('html', {})
start ('body', {})
start ('p', {})
text Hello, world!
end p
end body
end html
```

## Incremental Chunked Input

For documents that arrive in pieces (socket reads, file chunks, generator pipelines), pass any iterable of chunks to `stream()`. The chunks are parsed as they arrive without ever joining the full document; only the unfinished construct at a chunk edge is retained, so memory stays bounded regardless of document size.

All chunks must be the same type: either all `str` or all bytes-like (`bytes`, `bytearray`, `memoryview`).

```python
from justhtml import stream

chunks = ["<p>Hel", "lo</p>", "<!-- ok -->"]
for event, data in stream(chunks):
    print(event, data)
```

<!-- justhtml: output -->
```text
start ('p', {})
text Hel
text lo
end p
comment  ok 
```

The event stream is equivalent to parsing the concatenated input. The only difference is that a single text node may be delivered as several consecutive `"text"` events whose contents concatenate to the same string.

Constructs split across chunk boundaries are handled transparently: encoding detection (BOM, meta charset, transport encoding), tags and attributes, character references, comments, CDATA sections, raw-text elements (`script`, `style`, `textarea`, `plaintext`), script-data escape states, CRLF normalization, and SVG/MathML foreign-content boundaries.

### Push API: `HTMLStream`

When chunks arrive through callbacks rather than an iterable, use `HTMLStream` directly. `feed()` yields every event that is already definitive, and `finish()` flushes the remaining events at end of input:

```python
from justhtml import HTMLStream

parser = HTMLStream()
for chunk in read_chunks():
    for event, data in parser.feed(chunk):
        handle(event, data)
for event, data in parser.finish():
    handle(event, data)
```

With bytes chunks, encoding sniffing runs incrementally. The chosen encoding is exposed on the `encoding` attribute as soon as it is known (`None` before that), and a split multibyte sequence at the end of a chunk is buffered until the rest arrives:

```python
from justhtml import HTMLStream

parser = HTMLStream()
events = list(parser.feed(b"\xef\xbb\xbf<p>caf"))
events += list(parser.feed(b"\xc3\xa9</p>"))
events += list(parser.finish())
print(parser.encoding)
for event, data in events:
    print(event, data)
```

<!-- justhtml: output -->
```text
utf-8
start ('p', {})
text caf
text é
end p
```

Pass `encoding="..."` to skip sniffing when the transport encoding is already known. Feeding a chunk of a different type than the first chunk, or feeding after `finish()`, raises `TypeError`/`RuntimeError`.

## Byte Input and Encodings

`stream()` also accepts bytes (`bytes`, `bytearray`, `memoryview`). In that case, the input is decoded using HTML encoding sniffing (including a `windows-1252` fallback for legacy documents).

```python
from justhtml import stream
from pathlib import Path

data = Path("page.html").read_bytes()
for event, data in stream(data):
    ...
```

To override decoding when you already know the correct encoding:

```python
from justhtml import stream
from pathlib import Path

data = Path("page.html").read_bytes()
for event, data in stream(data, encoding="utf-8"):
    ...
```

See [Encoding & Byte Input](encoding.md) for details.

## Events

| Event | Data Type | Description |
|-------|-----------|-------------|
| `"start"` | `(tag_name, attrs_dict)` | Opening tag encountered |
| `"end"` | `tag_name` | Closing tag encountered |
| `"text"` | `str` | Text content |
| `"comment"` | `str` | HTML comment content |
| `"doctype"` | `(name, public_id, system_id)` | DOCTYPE declaration fields |

## Examples

### Extract All Links

```python
from justhtml import stream
from pathlib import Path

html = Path("page.html").read_text()

for event, data in stream(html):
    if event == "start":
        tag, attrs = data
        if tag == "a" and "href" in attrs:
            print(attrs["href"])
```

### Count Elements

```python
from justhtml import stream
from collections import Counter

counts = Counter()

for event, data in stream(html):
    if event == "start":
        tag, attrs = data
        counts[tag] += 1

print(counts.most_common(10))
```

### Extract Text Content

```python
from justhtml import stream

text_parts = []
for event, data in stream(html):
    if event == "text":
        text_parts.append(data)

full_text = " ".join(text_parts)
```

### Filter by Tag

```python
from justhtml import stream

in_script = False
for event, data in stream(html):
    if event == "start" and data[0] == "script":
        in_script = True
    elif event == "end" and data == "script":
        in_script = False
    elif event == "text" and not in_script:
        print(data)  # Only non-script text
```

## When to Use Streaming

Use the streaming API when:
- Processing very large HTML files
- You only need specific elements (like all links)
- Memory is constrained
- You don't need to traverse the tree

Use the DOM API (`JustHTML`) when:
- You need to query with CSS selectors
- You need to traverse up/down the tree
- You need to serialize back to HTML
- The document fits in memory

## Performance

The streaming API is faster than building a full DOM:

| API | Time (100 files) | Memory |
|-----|------------------|--------|
| `JustHTML()` | ~1.0s | Higher |
| `stream()` | ~0.7s | Lower |

For most use cases, the difference is negligible. Use whichever API fits your needs.
