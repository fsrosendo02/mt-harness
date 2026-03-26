def _validate_balanced_fragment(code: str):
    """
    Lightweight structural sanity check for C/Java-like code fragments.

    Handles:
    - (), {}, []
    - strings
    - char literals
    - line comments //
    - block comments /* ... */
    """

    pairs = {
        "{": "}",
        "(": ")",
        "[": "]",
    }
    closing = {v: k for k, v in pairs.items()}

    stack = []
    i = 0
    n = len(code)

    in_string = False
    in_char = False
    in_line_comment = False
    in_block_comment = False
    escape = False

    while i < n:
        ch = code[i]
        nxt = code[i + 1] if i + 1 < n else ""

        # --- inside line comment ---
        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue

        # --- inside block comment ---
        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 2
            else:
                i += 1
            continue

        # --- inside string literal ---
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue

        # --- inside char literal ---
        if in_char:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == "'":
                in_char = False
            i += 1
            continue

        # --- entering comments ---
        if ch == "/" and nxt == "/":
            in_line_comment = True
            i += 2
            continue

        if ch == "/" and nxt == "*":
            in_block_comment = True
            i += 2
            continue

        # --- entering string / char ---
        if ch == '"':
            in_string = True
            i += 1
            continue

        if ch == "'":
            in_char = True
            i += 1
            continue

        # --- structural delimiters ---
        if ch in pairs:
            stack.append(ch)
        elif ch in closing:
            if not stack or stack[-1] != closing[ch]:
                return False, f"unmatched closing delimiter: {ch}"
            stack.pop()

        i += 1

    if in_string:
        return False, "unterminated string literal"
    if in_char:
        return False, "unterminated char literal"
    if in_block_comment:
        return False, "unterminated block comment"
    if stack:
        return False, f"unclosed delimiter(s): {''.join(stack)}"

    return True, None


def validate_syntax_fragment(code: str, language: str):
    language = (language or "").lower()

    if language in {"java", "c"}:
        return _validate_balanced_fragment(code)

    return True, None