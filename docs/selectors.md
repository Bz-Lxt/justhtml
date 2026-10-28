[← Back to docs](index.md)

# CSS Selectors

JustHTML supports a comprehensive subset of CSS selectors for querying the DOM.

## Performance Notes

- Selector parsing is cached internally (LRU, up to 512 distinct selector strings). Repeating the same selector across many calls avoids re-parsing overhead.
- Simple tag-only selectors like `"div"` use a fast path (descendant scan without running the full selector matcher).
- If you generate lots of unique selectors dynamically, expect some parse overhead once the cache churns.
- Selector evaluation uses resource limits for selector length, selector shape, parse depth, match operations, and string materialization work. These limits are intentionally conservative for untrusted input.

Default selector limits:

| Limit | Default | Applies to |
|-------|---------|------------|
| `max_length` | `8192` | Selector string length |
| `max_list_items` | `256` | Distinct comma-separated selector entries |
| `max_compound_simple_selectors` | `512` | Distinct simple selectors in one compound selector |
| `max_complex_selector_parts` | `512` | Combinator-linked parts in one complex selector |
| `max_parse_depth` | `100` | Nested functional pseudo-class parsing, such as nested `:not(...)` |
| `max_match_depth` | `100` | Nested selector matching |
| `max_match_steps` | `100_000_000` | Selector matching operations |
| `max_match_bytes` | `100_000_000` | Attribute/text string work during matching |

Direct `query(...)` and `matches(...)` calls use the defaults. Sanitization transform pipelines can raise limits for trusted workloads with `SanitizationPolicy(selector_limits=...)`; see [Transforms](transforms.md#validation).

## Basic Selectors

| Selector | Example | Description |
|----------|---------|-------------|
| Tag | `div` | Elements by tag name |
| Class | `.intro` | Elements with class |
| ID | `#main` | Element with ID |
| Universal | `*` | All elements |

```python
doc.query("div")       # All <div> elements
doc.query_one("div")   # First matching <div>, or None

doc.query(".intro")    # All elements with class="intro"
doc.query("#main")     # Element with id="main"
doc.query("*")         # All elements
```

## Attribute Selectors

| Selector | Example | Description |
|----------|---------|-------------|
| Has attribute | `[href]` | Elements with attribute |
| Exact match | `[type="text"]` | Exact attribute value |
| Starts with | `[href^="https"]` | Attribute starts with value |
| Ends with | `[href$=".pdf"]` | Attribute ends with value |
| Contains | `[href*="example"]` | Attribute contains value |
| Word match | `[class~="active"]` | Space-separated word match |
| Prefix match | `[lang\|="en"]` | Value or value followed by `-` |

```python
doc.query("[href]")              # All elements with href attribute
doc.query('[type="submit"]')     # Exact match
doc.query('[href^="https://"]')  # Links starting with https://
doc.query('[src$=".png"]')       # Images ending with .png
doc.query('[class*="btn"]')      # Classes containing "btn"
```

## Combinators

| Combinator | Example | Description |
|------------|---------|-------------|
| Descendant | `div p` | `<p>` anywhere inside `<div>` |
| Child | `div > p` | `<p>` direct child of `<div>` |
| Adjacent | `h1 + p` | `<p>` immediately after `<h1>` |
| Sibling | `h1 ~ p` | Any `<p>` sibling after `<h1>` |

```python
doc.query("div p")          # Paragraphs anywhere in a div
doc.query("ul > li")        # Direct list items
doc.query("h1 + p")         # First paragraph after h1
doc.query("h1 ~ p")         # All paragraphs after h1
```

## Pseudo-classes

### Structural

| Selector | Description |
|----------|-------------|
| `:first-child` | First child of parent |
| `:last-child` | Last child of parent |
| `:only-child` | Only child of parent |
| `:nth-child(n)` | Nth child (1-indexed) |
| `:nth-last-child(n)` | Nth child from end |
| `:first-of-type` | First of its type in parent |
| `:last-of-type` | Last of its type in parent |
| `:only-of-type` | Only of its type in parent |
| `:nth-of-type(n)` | Nth of its type |
| `:nth-last-of-type(n)` | Nth of its type from end |

```python
doc.query("li:first-child")       # First list item
doc.query("li:last-child")        # Last list item
doc.query("li:nth-child(2)")      # Second list item
doc.query("li:nth-child(odd)")    # Odd list items (1st, 3rd, 5th...)
doc.query("li:nth-child(even)")   # Even list items (2nd, 4th, 6th...)
doc.query("li:nth-child(3n)")     # Every 3rd item
doc.query("li:nth-child(3n+1)")   # 1st, 4th, 7th... (formula: An+B)
```

### Content

| Selector | Description |
|----------|-------------|
| `:empty` | Elements with no children |
| `:root` | Document root element |
| `:contains("text")` | Non-standard (jQuery-style): elements whose descendant text contains `text` |
| `:comment` | Non-standard: comment nodes (`#comment`) |

```python
doc.query("p:empty")    # Empty paragraphs
doc.query(":root")      # The <html> element
doc.query(":comment")   # Comment nodes
```

### Functional pseudo-classes

| Selector | Description |
|----------|-------------|
| `:not(selector)` | Elements not matching the (strict) selector argument |
| `:is(selector, ...)` | Elements matching any selector in the forgiving list |
| `:where(selector, ...)` | Alias of `:is()` (specificity is not modeled) |
| `:has(relative-selector)` | Elements whose relative subtree matches the argument |

All four pseudo-classes can be nested and combined (`div:is(.a, .b):not(:has(p))`),
and their arguments are parsed and matched by the same engine that powers
`query()` and `matches()`, so selector behavior is identical in DOM queries,
`matches()` checks, and transform targeting (`SetAttrs`, `Drop`, ...).

```python
doc.query("div:not(.hidden)")            # Visible divs
doc.query(":is(h1, h2, h3)")             # Any heading
doc.query("p:is(.intro, .special)")      # p with either class
doc.query("div:has(p)")                  # Divs containing a paragraph
doc.query("li:has(+ li)")                # List items followed by another item
doc.query("div:is(.card, .panel):has(a)")  # Cards or panels containing a link
```

#### Relative selector arguments

Arguments may start with a combinator, which is resolved against the subject
element:

| Argument | Axis relative to the subject |
|----------|-------------------------------|
| `:has(> p)` | A `p` that is a direct child of the subject |
| `:has(+ p)` | The immediately following element sibling is `p` |
| `:has(~ p)` | A following element sibling is `p` |
| `:has(p)` | A `p` anywhere in the subject's subtree (implied descendant) |
| `:is(+ p)`, `:where(~ p)` | Same axes, testing the subject's siblings |

Chains after the leading combinator are evaluated forward and stay inside the
subject's axis; the argument never climbs back above the subject. The content
fragment of `<template>` elements is searched as part of the subject subtree.

```python
doc.query("div:has(> p + span)")   # Div with a p child directly followed by span
doc.query("h2:has(~ div em)")      # h2 with a later sibling div containing em
```

#### Forgiving vs. strict arguments

`:is()` and `:where()` use a forgiving selector list: entries that fail to
parse are skipped instead of invalidating the whole selector.

```python
doc.query(":is(p, :bogus, a)")  # Matches p and a; :bogus is ignored
doc.query(":is()")              # Empty list matches nothing
```

`:not()` and `:has()` use strict selector lists, so an unsupported pseudo-class
or malformed entry raises `SelectorError`. `:has()` also requires a non-empty
argument. All arguments stay within the same resource limits as top-level
selectors: nesting depth (`max_parse_depth` / `max_match_depth`), list entries
(`max_list_items`), and combinator-chain length (`max_complex_selector_parts`).
Resource-limit errors raised inside forgiving lists are never swallowed.

## Selector Groups

Combine multiple selectors with commas:

```python
doc.query("h1, h2, h3")           # All headings
doc.query(".btn, [type='submit']") # Buttons and submit inputs
```

## Compound Selectors

Chain selectors without spaces:

```python
doc.query("div.container")        # <div class="container">
doc.query("input[type='text']")   # <input type="text">
doc.query("li.active:first-child") # First li with class active
```

## Examples

```python
# Navigation links
doc.query("nav a")

# Active menu item
doc.query("nav li.active > a")

# Form inputs except hidden
doc.query("form input:not([type='hidden'])")

# Alternating table rows
doc.query("tr:nth-child(even)")

# First paragraph in each container
doc.query("div > p:first-of-type")

# External links
doc.query('a[href^="http"]:not([href*="mysite.com"])')
```
