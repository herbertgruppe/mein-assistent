"""
Shared sanitizer for untrusted tool output before it reaches prompts or UI.

Defense-in-depth layers:
1. HTML strip (drop tags, decode entities once)
2. Whitespace normalization (lines and runs)
3. Unicode NFKC + zero-width/format strip (anti-confusable / anti-ZWSP bypass)
4. Prompt-injection marker redaction in both whitespace-preserving and
   whitespace-collapsed forms (catches "Ig nore previous instructions" etc.)
5. Length clip (saturation bound)
6. Delimiter wrapping in `<untrusted_tool_output source="...">…</untrusted_tool_output>`
   so the system prompt can instruct the model to never treat the wrapped
   content as a directive (Allowlist > Denylist; markers stay as belt-and-
   suspenders).
"""

from __future__ import annotations

import html
import re
import unicodedata
from html.parser import HTMLParser

MAX_SANITIZED_CHARS = 4096
_WHITESPACE_RE = re.compile(r"\s+")

# Zero-width, soft-hyphen, bidi-format and other invisible/format code points.
# Stripped before NFKC-normalized matching so "Ignore​ previous" still
# trips the markers.
_ZERO_WIDTH_FORMAT_RE = re.compile(
    "["
    "­"            # SOFT HYPHEN
    "​-‏"     # ZWSP, ZWNJ, ZWJ, LRM, RLM
    "‪-‮"     # LRE, RLE, PDF, LRO, RLO
    "⁠-⁯"     # WORD JOINER and adjacent format chars
    "﻿"            # ZWNBSP / BOM
    "]"
)

# Pairs of (whitespace-preserving pattern, whitespace-collapsed pattern).
# The collapsed pattern is run against a version of the text with all
# whitespace removed so wordsplit bypasses ("Ig nore previous instructions")
# are caught.  Patterns are kept in sync by listing both forms explicitly.
_PROMPT_INJECTION_PATTERN_SOURCES: tuple[tuple[str, str], ...] = (
    # ---- English ----
    (
        r"ignore\s+(?:all\s+|the\s+)?previous\s+instructions?",
        r"ignore(?:all|the)?previousinstructions?",
    ),
    (
        r"disregard\s+(?:all\s+|the\s+)?(?:previous|prior)\s+(?:instructions?|directives?|rules?)",
        r"disregard(?:all|the)?(?:previous|prior)(?:instructions?|directives?|rules?)",
    ),
    (
        r"forget\s+(?:your\s+|all\s+|the\s+)?(?:previous|prior)\s+(?:instructions?|directives?|rules?)",
        r"forget(?:your|all|the)?(?:previous|prior)(?:instructions?|directives?|rules?)",
    ),
    (
        r"system\s*[-_]?\s*prompt",
        r"system[-_]?prompt",
    ),
    (
        r"developer\s+(?:message|prompt|instructions?)",
        r"developer(?:message|prompt|instructions?)",
    ),
    (
        r"you\s+are\s+now",
        r"youarenow",
    ),
    (
        r"(?:override|bypass)\s+(?:your\s+|the\s+|all\s+)?(?:instructions?|directives?|rules?)",
        r"(?:override|bypass)(?:your|the|all)?(?:instructions?|directives?|rules?)",
    ),
    (
        r"reveal\s+(?:the\s+)?(?:system|developer)\s*[-_]?\s*prompt",
        r"reveal(?:the)?(?:system|developer)[-_]?prompt",
    ),
    # ---- German ----
    (
        r"ignor(?:e|ier(?:e|en|st|t|te|ten|end|enden)?)\s+(?:alle\s+|deine\s+|s[äa]mtliche\s+|die\s+)?(?:vorherigen?|bisherigen?|vorigen|vorhergehenden?|obigen?)\s+(?:anweisung(?:en)?|instruktionen?|regeln|vorgaben|direktiven)",
        r"ignor(?:e|ier(?:e|en|st|t|te|ten|end|enden)?)(?:alle|deine|s[äa]mtliche|die)?(?:vorherigen?|bisherigen?|vorigen|vorhergehenden?|obigen?)(?:anweisung(?:en)?|instruktionen?|regeln|vorgaben|direktiven)",
    ),
    (
        r"vergiss(?:t|e|en)?\s+(?:alles?\b|bisher\b|zuvor\b|alle\s+|deine\s+|s[äa]mtliche\s+|die\s+(?:vorherigen?|bisherigen?))",
        r"vergiss(?:t|e|en)?(?:alles?|bisher|zuvor|alle|deine|s[äa]mtliche|die(?:vorherigen?|bisherigen?))",
    ),
    (
        r"verges(?:se|sen|st|sene)\s+(?:alles?|alle\s+|deine\s+|s[äa]mtliche\s+|die\s+)?(?:vorherigen?|bisherigen?)?\s*(?:anweisung(?:en)?|instruktionen?|regeln|vorgaben)",
        r"verges(?:se|sen|st|sene)(?:alles?|alle|deine|s[äa]mtliche|die)?(?:vorherigen?|bisherigen?)?(?:anweisung(?:en)?|instruktionen?|regeln|vorgaben)",
    ),
    (
        r"missachte\s+(?:alle\s+|deine\s+|s[äa]mtliche\s+|die\s+)?(?:anweisung(?:en)?|instruktionen?|regeln|vorgaben)",
        r"missachte(?:alle|deine|s[äa]mtliche|die)?(?:anweisung(?:en)?|instruktionen?|regeln|vorgaben)",
    ),
    (
        r"entwickler\s*[-_]?\s*(?:nachricht|prompt|anweisung)",
        r"entwickler[-_]?(?:nachricht|prompt|anweisung)",
    ),
    (
        r"du\s+bist\s+(?:jetzt|nun|ab\s+jetzt|von\s+nun\s+an)",
        r"dubist(?:jetzt|nun|abjetzt|vonnunan)",
    ),
    (
        r"(?:überschreibe|umgehe|ignoriere)\s+(?:deine\s+|alle\s+|s[äa]mtliche\s+|die\s+)?(?:anweisung(?:en)?|instruktionen?|regeln|vorgaben|direktiven)",
        r"(?:überschreibe|umgehe|ignoriere)(?:deine|alle|s[äa]mtliche|die)?(?:anweisung(?:en)?|instruktionen?|regeln|vorgaben|direktiven)",
    ),
    (
        r"verrate?\s+(?:mir\s+|uns\s+|das\s+|den\s+)?(?:system|entwickler)\s*[-_]?\s*(?:prompt|nachricht|anweisung)",
        r"verrate?(?:mir|uns|das|den)?(?:system|entwickler)[-_]?(?:prompt|nachricht|anweisung)",
    ),
    (
        r"(?:agiere|handle|verhalte\s+dich)\s+(?:jetzt|nun|ab\s+jetzt|von\s+nun\s+an)?\s*als\s+\w",
        r"(?:agiere|handle|verhaltedich)(?:jetzt|nun|abjetzt|vonnunan)?als\w",
    ),
    (
        r"neue\s+(?:anweisung(?:en)?|instruktionen?|regeln|vorgaben)\s*[:.\-—]",
        r"neue(?:anweisung(?:en)?|instruktionen?|regeln|vorgaben)[:.\-—]",
    ),
)

_PROMPT_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(src, re.IGNORECASE) for src, _ in _PROMPT_INJECTION_PATTERN_SOURCES
)
_COMPACT_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(src, re.IGNORECASE) for _, src in _PROMPT_INJECTION_PATTERN_SOURCES
)


class _HTMLStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"br", "p", "div", "li", "ul", "ol"}:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"p", "div", "li"}:
            self._parts.append("\n")

    def get_data(self) -> str:
        return "".join(self._parts)


def _strip_html(value: str) -> str:
    stripper = _HTMLStripper()
    stripper.feed(value)
    stripper.close()
    return html.unescape(stripper.get_data())


def _normalize_whitespace(value: str) -> str:
    lines = []
    for line in value.splitlines():
        normalized = _WHITESPACE_RE.sub(" ", line).strip()
        if normalized:
            lines.append(normalized)
    return "\n".join(lines).strip()


def _normalize_unicode(value: str) -> str:
    """NFKC normalize + strip zero-width / bidi-format / soft-hyphen code points.

    Removes the common bypass vehicles for marker matching:
    - U+200B ZWSP, U+200C ZWNJ, U+200D ZWJ, U+FEFF BOM
    - Bidi controls U+202A..U+202E and U+2066..U+2069
    - U+00AD SOFT HYPHEN
    NFKC additionally collapses Cyrillic/Greek confusable look-alikes that
    have canonical Latin equivalents and decomposes compatibility forms.
    """
    normalized = unicodedata.normalize("NFKC", value)
    return _ZERO_WIDTH_FORMAT_RE.sub("", normalized)


def _redact_injection_markers(value: str) -> tuple[str, bool]:
    """Two-pass marker detection.

    Pass 1 — apply whitespace-preserving patterns to the input and replace
    matches with a redaction placeholder.

    Pass 2 — additionally collapse all whitespace and re-test with the
    compact pattern variants.  This catches wordsplit bypasses like
    ``Ig nore previous instructions`` where the visible text has spaces
    sprinkled between letters.  When only the compact form matches we
    flag but cannot point at a precise span to redact (the original
    string was tampered with on purpose); the surrounding delimiter wrap
    is then the load-bearing defense.
    """

    flagged = False
    sanitized = value
    for pattern in _PROMPT_INJECTION_PATTERNS:
        if pattern.search(sanitized):
            flagged = True
            sanitized = pattern.sub("[prompt-injection-marker removed]", sanitized)

    if not flagged:
        compacted = _WHITESPACE_RE.sub("", sanitized)
        for pattern in _COMPACT_INJECTION_PATTERNS:
            if pattern.search(compacted):
                flagged = True
                break

    return sanitized, flagged


def _wrap_delimited(body: str, source: str, *, injection_marker_removed: bool) -> str:
    """Wrap the sanitized body in an unambiguous delimiter the LLM can be
    instructed (in the system prompt) to never treat as a directive.

    Body text is HTML-escaped so any literal ``<`` / ``>`` cannot forge the
    close tag.  Source string is attribute-escaped.
    """
    safe_source = html.escape(source, quote=True)
    safe_body = html.escape(body, quote=False)
    flag_attr = ' injection_marker_removed="true"' if injection_marker_removed else ""
    return (
        f'<untrusted_tool_output source="{safe_source}"{flag_attr}>\n'
        f"{safe_body}\n"
        f"</untrusted_tool_output>"
    )


def sanitize(text, *, source: str) -> str:
    """
    Normalize untrusted tool output before it is reused.

    The returned string is wrapped in a ``<untrusted_tool_output …>…
    </untrusted_tool_output>`` delimiter so that the system prompt can
    instruct the model to never treat the wrapped content as a directive.
    """
    if text is None:
        return ""

    sanitized = _strip_html(str(text))
    sanitized = _normalize_whitespace(sanitized)
    sanitized = _normalize_unicode(sanitized)
    sanitized, flagged = _redact_injection_markers(sanitized)

    # Reserve space for the wrapping tags so the final string stays under
    # MAX_SANITIZED_CHARS.  Build the wrapper once empty to know its size.
    wrapper_overhead = len(
        _wrap_delimited("", source, injection_marker_removed=flagged)
    )
    # html.escape can roughly double `<` / `>` / `&` density; assume the
    # worst case (every char expands ~6x for `&`/`<`/`>` -> 5 chars) but
    # cap at a safe constant so we don't over-truncate ordinary text.
    # Empirically, escaping multiplies by ~1.0–1.3 for natural-language
    # text; budget defensively at 2x for safety.
    budget = MAX_SANITIZED_CHARS - wrapper_overhead
    if budget < 0:
        budget = 0

    # Account for escape expansion in budgeting: shrink budget by the
    # escape delta if the body grows when escaped.
    escaped_len = len(html.escape(sanitized, quote=False))
    if escaped_len > len(sanitized) and escaped_len > budget:
        # Trim body until its escaped form fits the budget.
        suffix = " …[truncated]"
        target = max(budget - len(suffix), 0)
        lo, hi = 0, len(sanitized)
        # Binary search for the largest prefix whose escaped form fits.
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if len(html.escape(sanitized[:mid], quote=False)) <= target:
                lo = mid
            else:
                hi = mid - 1
        sanitized = sanitized[:lo] + suffix
    elif len(sanitized) > budget:
        suffix = " …[truncated]"
        if budget > len(suffix):
            sanitized = sanitized[: budget - len(suffix)] + suffix
        else:
            sanitized = sanitized[:budget]

    return _wrap_delimited(sanitized, source, injection_marker_removed=flagged)
