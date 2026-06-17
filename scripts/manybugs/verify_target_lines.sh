#!/usr/bin/env bash
# Verify and print actual function line bounds from a checked-out ManyBugs workdir.
# Usage: verify_target_lines.sh <workdir> <source_file> <function_name>
# Example:
#   verify_target_lines.sh /tmp/libtiff-2005-12-14 libtiff/tif_read.c TIFFFillStrip
#
# Requires: ctags (universal-ctags preferred)

set -euo pipefail

WORKDIR="${1:?Usage: $0 <workdir> <source_file> <function_name>}"
SOURCE_FILE="${2:?}"
FUNCTION="${3:?}"

FULL_PATH="$WORKDIR/$SOURCE_FILE"

if [[ ! -f "$FULL_PATH" ]]; then
    echo "ERROR: file not found: $FULL_PATH" >&2
    exit 1
fi

echo "=== Searching for '$FUNCTION' in $FULL_PATH ==="

# Use ctags to find the function start line
ctags -x --c-kinds=f "$FULL_PATH" 2>/dev/null | grep -E "^${FUNCTION}\s" | while read -r name kind line file rest; do
    echo "ctags: function '$name' starts at line $line"
done

# grep approach as fallback
echo ""
echo "=== grep matches ==="
grep -n "${FUNCTION}" "$FULL_PATH" | head -20
