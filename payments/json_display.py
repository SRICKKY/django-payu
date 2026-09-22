import json
import re

from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe

_BOOL_RE = re.compile(r"\b(true|false)\b")
_NULL_RE = re.compile(r"\bnull\b")
_NUMBER_RE = re.compile(r"(?<![\w-])(-?\d+(?:\.\d+)?)(?![\w.])")
_STRING_RE = re.compile(r"&quot;.*?&quot;")
_KEY_LINE_RE = re.compile(r"^(\s*)(&quot;.*?&quot;)(\s*:)(.*)$")


def pretty_json_html(value) -> str:
    if value in (None, "", {}, []):
        return mark_safe('<span class="json-empty">No payload</span>')

    dumped = json.dumps(value, indent=2, ensure_ascii=False, default=str)
    highlighted_lines = []
    for line in dumped.splitlines():
        escaped = escape(line)
        match = _KEY_LINE_RE.match(escaped)
        if match:
            indent, key, colon, rest = match.groups()
            highlighted_lines.append(
                f'{indent}<span class="json-key">{key}</span>{colon}{_highlight_value(rest)}'
            )
        else:
            highlighted_lines.append(_highlight_value(escaped))

    return format_html(
        '<div class="json-pretty-wrap">'
        '<button type="button" class="button json-copy">Copy JSON</button>'
        '<pre class="json-pretty">{}</pre>'
        '<textarea class="json-copy-source" readonly hidden>{}</textarea>'
        "</div>",
        mark_safe("\n".join(highlighted_lines)),
        dumped,
    )


def _highlight_value(text: str) -> str:
    placeholders: list[str] = []

    def stash_string(match: re.Match[str]) -> str:
        token = f"@@JSONSTR{len(placeholders)}@@"
        placeholders.append(f'<span class="json-string">{match.group(0)}</span>')
        return token

    text = _STRING_RE.sub(stash_string, text)
    text = _BOOL_RE.sub(r'<span class="json-bool">\1</span>', text)
    text = _NULL_RE.sub(r'<span class="json-null">null</span>', text)
    text = _NUMBER_RE.sub(r'<span class="json-number">\1</span>', text)
    for index, html in enumerate(placeholders):
        text = text.replace(f"@@JSONSTR{index}@@", html)
    return text
