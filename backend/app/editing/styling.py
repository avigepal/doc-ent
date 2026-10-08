"""Changing how a document looks, not what it says: "make the headings dark
blue", "use Arial 11 for the body", "centre the title", "margins of 1 inch".

The model does the understanding and code does the changing. The model turns
the request into a short list of operations (below); this module checks them
-- a model can name a colour that doesn't exist or a font size of 900 -- and
docx_style.py applies the valid ones to the Word file.

An operation is {"target": ..., "set": {...}}.
  targets  title, headings (optionally "level": 1-6), body, lists, tables,
           header_footer, all (every paragraph), page
  set keys font, size (pt), size_scale (1.2 = 20% bigger), color, bold, italic,
           underline, caps, highlight, align, line_spacing, space_before,
           space_after, indent_first_line, shading,
           margins, orientation (page), table_borders, table_header_fill (tables)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.editing.layout import ParagraphLLM, chat_json

# ---------- is this message a styling request? ----------

_STYLE_WORDS = re.compile(
    r"\b(?:fonts?|typeface|font[\s-]?size|text[\s-]?size|point\s+size|\d+\s*(?:pt|point|points)\b|colou?rs?|colou?red|"
    r"bold|italics?|underlin\w*|highlight\w*|align\w*|cent(?:er|re)d?|justif\w*|(?:line|paragraph)\s+spacing|"
    r"spacing|margins?|borders?|shad(?:e|ed|ing)|background|landscape|portrait|style|styles|styling|styled|"
    r"theme|indent\w*|bigger|larger|smaller|bolder|all[\s-]?caps|uppercase|capitali[sz]e|"
    r"arial|calibri|times\s+new\s+roman|helvetica|georgia|verdana|cambria|courier|tahoma|garamond|comic\s+sans|"
    r"segoe|roboto|open\s+sans|consolas|trebuchet|palatino|century|book\s+antiqua)\b",
    re.IGNORECASE,
)

# A colour name only means styling when it is about a part of the document
# ("make the headings blue"), not in "the red team report".
_COLOR_NAME = re.compile(r"\b(?:blue|red|green|orange|purple|yellow|black|white|gr[ae]y|navy|teal|pink|brown|gold)\b", re.IGNORECASE)
_PART_OF_DOCUMENT = re.compile(
    r"\b(?:headings?|titles?|headers?|footers?|text|body|paragraphs?|tables?|lists?|lines?|words?|letters?)\b", re.IGNORECASE
)

# "translate it" and "shorten it" are about the words, even if a style word is in them
_ABOUT_WORDS = re.compile(r"\b(?:translate|rewrite|rephrase|reword|proofread|summari[sz]e|shorten|paraphrase)\b", re.IGNORECASE)

# a question or a lookup, not a request to change anything
_ASKING = re.compile(
    r"^\W*(?:what|which|who|whom|whose|how|why|when|where|is|are|was|were|does|do|did|tell me|show me|explain|"
    r"describe|list|find|search|give me)\b",
    re.IGNORECASE,
)


def looks_like_style(message: str) -> bool:
    """True when the message asks for a different look rather than different words."""
    text = message.strip()
    if _ASKING.search(text) or _ABOUT_WORDS.search(text):
        return False
    if _STYLE_WORDS.search(text):
        return True
    return bool(_COLOR_NAME.search(text) and _PART_OF_DOCUMENT.search(text))


# ---------- operations ----------

TARGETS = ("title", "headings", "body", "lists", "tables", "header_footer", "all", "page")
ALIGNMENTS = {"left", "center", "right", "justify"}
HIGHLIGHTS = {"yellow", "green", "cyan", "magenta", "blue", "red", "gray", "none"}

_NAMED_COLORS = {
    "black": "000000", "white": "FFFFFF", "red": "C00000", "dark red": "7F0000", "blue": "1F4E9E", "dark blue": "1F3864",
    "light blue": "9DC3E6", "navy": "1F3864", "green": "2E7D32", "dark green": "1B5E20", "light green": "A9D18E",
    "orange": "ED7D31", "yellow": "FFC000", "purple": "7030A0", "pink": "E75480", "brown": "7F4F24", "gray": "7F7F7F",
    "grey": "7F7F7F", "dark gray": "404040", "dark grey": "404040", "light gray": "D9D9D9", "light grey": "D9D9D9",
    "teal": "008080", "maroon": "800000", "gold": "BF9000",
}
_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")

_FONT_SIZE_RANGE = (4.0, 96.0)
_SCALE_RANGE = (0.3, 4.0)
_SPACING_RANGE = (0.0, 10.0)
_POINTS_RANGE = (0.0, 200.0)
_MARGIN_RANGE = (0.0, 4.0)  # inches
_FONT_NAME = re.compile(r"^[A-Za-z0-9 .,&'()\-]{2,60}$")


def normalize_color(value) -> str | None:
    """'RRGGBB' for a hex colour or a common colour name, else None."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    match = _HEX.match(text)
    if match:
        return match.group(1).upper()
    return _NAMED_COLORS.get(re.sub(r"\s+", " ", text.lower()))


def _number(value, low: float, high: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if low <= value <= high else None


@dataclass
class StyleOp:
    target: str
    level: int | None = None
    props: dict = field(default_factory=dict)


def _clean_props(raw: dict, target: str, ignored: list[str]) -> dict:
    props: dict = {}

    def drop(key: str) -> None:
        ignored.append(key)

    for key, value in raw.items():
        if key == "font":
            if isinstance(value, str) and _FONT_NAME.match(value.strip()):
                props["font"] = value.strip()
            else:
                drop(key)
        elif key == "size":
            number = _number(value, *_FONT_SIZE_RANGE)
            props["size"] = number if number is not None else drop(key)
        elif key == "size_scale":
            number = _number(value, *_SCALE_RANGE)
            props["size_scale"] = number if number is not None else drop(key)
        elif key in ("color", "shading", "table_header_fill"):
            color = normalize_color(value)
            props[key] = color if color else drop(key)
        elif key in ("bold", "italic", "underline", "caps", "table_borders"):
            props[key] = value if isinstance(value, bool) else drop(key)
        elif key == "highlight":
            props[key] = value.lower() if isinstance(value, str) and value.lower() in HIGHLIGHTS else drop(key)
        elif key == "align":
            name = {"centre": "center", "centered": "center", "centred": "center"}.get(str(value).lower(), str(value).lower())
            props[key] = name if name in ALIGNMENTS else drop(key)
        elif key == "line_spacing":
            number = _number(value, 0.5, 4.0)
            props[key] = number if number is not None else drop(key)
        elif key in ("space_before", "space_after", "indent_first_line"):
            number = _number(value, *_POINTS_RANGE)
            props[key] = number if number is not None else drop(key)
        elif key == "orientation":
            props[key] = value.lower() if isinstance(value, str) and value.lower() in ("landscape", "portrait") else drop(key)
        elif key == "margins":
            margins = {}
            if isinstance(value, dict):
                for side in ("top", "bottom", "left", "right"):
                    number = _number(value.get(side), *_MARGIN_RANGE) if side in value else None
                    if number is not None:
                        margins[side] = number
            if margins:
                props["margins"] = margins
            else:
                drop(key)
        else:
            drop(key)

    # keep each key only where it makes sense, and drop what failed validation
    props = {k: v for k, v in props.items() if v is not None}
    if target == "page":
        allowed = {"margins", "orientation"}
    elif target == "tables":
        allowed = set(props) - {"margins", "orientation"}
    else:
        allowed = set(props) - {"margins", "orientation", "table_borders", "table_header_fill"}
    for key in set(props) - allowed:
        ignored.append(key)
    return {k: v for k, v in props.items() if k in allowed}


def sanitize(plan: dict) -> tuple[list[StyleOp], list[str]]:
    """The valid operations in a model's plan, and the names of what was dropped."""
    ops: list[StyleOp] = []
    ignored: list[str] = []
    for raw in plan.get("operations") or []:
        if not isinstance(raw, dict):
            continue
        target = str(raw.get("target", "")).lower().replace(" ", "_")
        target = {"heading": "headings", "list": "lists", "table": "tables", "text": "body", "paragraphs": "all"}.get(target, target)
        settings = raw.get("set")
        if target not in TARGETS or not isinstance(settings, dict):
            ignored.append(str(raw.get("target")))
            continue
        level = raw.get("level")
        level = level if isinstance(level, int) and not isinstance(level, bool) and 1 <= level <= 9 else None
        props = _clean_props(settings, target, ignored)
        if props:
            ops.append(StyleOp(target, level if target == "headings" else None, props))
    return ops, ignored


def describe(ops: list[StyleOp]) -> str:
    """The changes in plain words, for the reply."""
    label = {"title": "the title", "headings": "headings", "body": "body text", "lists": "lists", "tables": "tables",
             "header_footer": "headers and footers", "all": "all text", "page": "the page"}
    parts = []
    for op in ops:
        name = label[op.target] + (f" (level {op.level})" if op.level else "")
        bits = []
        p = op.props
        if "font" in p:
            bits.append(f"font {p['font']}")
        if "size" in p:
            bits.append(f"size {p['size']:g} pt")
        if "size_scale" in p:
            bits.append(f"size ×{p['size_scale']:g}")
        if "color" in p:
            bits.append(f"colour #{p['color']}")
        for flag in ("bold", "italic", "underline", "caps"):
            if flag in p:
                bits.append(flag if p[flag] else f"not {flag}")
        if "highlight" in p:
            bits.append(f"highlight {p['highlight']}")
        if "align" in p:
            bits.append(f"{p['align']}-aligned")
        if "line_spacing" in p:
            bits.append(f"line spacing {p['line_spacing']:g}")
        if "space_before" in p:
            bits.append(f"{p['space_before']:g} pt before")
        if "space_after" in p:
            bits.append(f"{p['space_after']:g} pt after")
        if "indent_first_line" in p:
            bits.append(f"first-line indent {p['indent_first_line']:g} pt")
        if "shading" in p:
            bits.append(f"background #{p['shading']}")
        if "margins" in p:
            bits.append("margins " + ", ".join(f"{side} {v:g} in" for side, v in p["margins"].items()))
        if "orientation" in p:
            bits.append(p["orientation"])
        if "table_borders" in p:
            bits.append("borders on" if p["table_borders"] else "borders off")
        if "table_header_fill" in p:
            bits.append(f"header row #{p['table_header_fill']}")
        parts.append(f"{name}: {', '.join(bits)}")
    return "; ".join(parts)


# ---------- asking the model ----------

STYLE_SYSTEM_PROMPT = (
    "You change how a Word document looks. You get a request and facts about the document, and reply with "
    "JSON only: {\"operations\": [ {\"target\": ..., \"set\": {...}} ]}.\n"
    "targets: title, headings (add \"level\": 1-6 for one level), body, lists, tables, header_footer, all "
    "(every paragraph), page.\n"
    "set keys, only the ones the request needs:\n"
    "  font (name), size (points), size_scale (1.2 = 20% bigger), color (#RRGGBB), bold, italic, underline, caps "
    "(true/false), highlight (yellow, green, cyan, magenta, blue, red, gray, none), align (left, center, right, "
    "justify), line_spacing (1.0 = single, 1.5, 2.0), space_before, space_after, indent_first_line (points), "
    "shading (#RRGGBB paragraph background);\n"
    "  page only: margins {top, bottom, left, right} in inches, orientation (landscape or portrait);\n"
    "  tables only: table_borders (true/false), table_header_fill (#RRGGBB).\n"
    "Rules:\n"
    "- Do only what the request says; leave everything else alone. Do not change any wording.\n"
    "- \"Headings\" means every heading level unless the request names one. \"Make the text bigger\" with no "
    "number means size_scale 1.15.\n"
    "- Prefer exact values: a colour name becomes a good #RRGGBB (dark blue = #1F3864).\n"
    "- If the request is not about the look of the document, reply {\"operations\": []}.\n"
    "Example: request \"make the headings dark blue and bold, body in Arial 11, margins 1 inch\" -> "
    "{\"operations\": [{\"target\": \"headings\", \"set\": {\"color\": \"#1F3864\", \"bold\": true}}, "
    "{\"target\": \"body\", \"set\": {\"font\": \"Arial\", \"size\": 11}}, "
    "{\"target\": \"page\", \"set\": {\"margins\": {\"top\": 1, \"bottom\": 1, \"left\": 1, \"right\": 1}}}]}"
)


def plan_style_changes(llm: ParagraphLLM, instruction: str, facts: str) -> tuple[list[StyleOp], list[str]]:
    """Ask the model what to change, then keep only what is valid."""
    user = f"Request:\n{instruction}\n\nDocument facts:\n{facts}"
    return sanitize(chat_json(llm, STYLE_SYSTEM_PROMPT, user, temperature=0.1))
