"""Pygments syntax highlighting helper shared by PyQt6 and Textual frontends."""

from pygments import lex
from pygments.lexer import Lexer
from pygments.lexers import TextLexer, get_lexer_for_filename
from pygments.token import (
    Comment,
    Keyword,
    Literal,
    Name,
    Number,
    Operator,
    Punctuation,
    String,
    Token,
)
from pygments.util import ClassNotFound


def get_lexer_for_path(path: str) -> Lexer:
    """Return a Pygments lexer for `path`, falling back to `TextLexer`."""
    if not path:
        return TextLexer(stripnl=False)
    try:
        return get_lexer_for_filename(path, stripnl=False)
    except ClassNotFound:
        return TextLexer(stripnl=False)


def token_color_hex(tok_type: object, dark: bool = True) -> str | None:
    """Map a Pygments token type to a VS Code Dark+ / Light+ foreground hex color."""
    if tok_type in Comment or str(tok_type).startswith("Token.Comment"):
        return "#6a9955" if dark else "#008000"
    if tok_type in Keyword or str(tok_type).startswith("Token.Keyword"):
        return "#569cd6" if dark else "#0000ff"
    if tok_type in String or str(tok_type).startswith("Token.Literal.String"):
        return "#ce9178" if dark else "#a31515"
    if tok_type in Number or str(tok_type).startswith("Token.Literal.Number"):
        return "#b5cea8" if dark else "#098658"
    if tok_type in (Name.Class, Name.Exception):
        return "#4ec9b0" if dark else "#267f99"
    if tok_type in (Name.Function, Name.Decorator):
        return "#dcdcaa" if dark else "#795e26"
    if tok_type in Name.Builtin:
        return "#4fc1ff" if dark else "#0070c1"
    if tok_type in (Operator, Punctuation):
        return "#d4d4d4" if dark else "#000000"
    if tok_type in Literal:
        return "#b5cea8" if dark else "#098658"
    return None


def tokenize_line(
    line_text: str, lexer: Lexer, dark: bool = True
) -> list[tuple[int, int, str | None]]:
    """Return `(start_col, end_col, fg_hex)` spans for a single line of code."""
    spans: list[tuple[int, int, str | None]] = []
    pos = 0
    for tok_type, value in lex(line_text, lexer):
        clean = value.rstrip("\r\n")
        if not clean:
            continue
        end = pos + len(clean)
        color = token_color_hex(tok_type, dark=dark)
        spans.append((pos, end, color))
        pos = end
        if pos >= len(line_text):
            break
    return spans
