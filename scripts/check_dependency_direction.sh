#!/usr/bin/env bash
# Copyright (C) 2025-2026 Retio AI
# SPDX-License-Identifier: AGPL-3.0-only
#
# CI boundary verification: ensure core-candidate modules never import
# server-layer modules (browser_session) or Playwright at runtime.
#
# Supports:
#   Phase 0 (flat layout) — checks individual files
#   Phase 1+ (core/ dir)  — checks entire core/ directory

set -euo pipefail

SRC_DIR="src/pagemap"
EXIT_CODE=0

# ── Core candidate files (Phase 0, flat layout) ──────────────────────
CORE_FILES=(
    "$SRC_DIR/dom_converters.py"
    "$SRC_DIR/protocols.py"
    "$SRC_DIR/page_map_builder.py"
    "$SRC_DIR/interactive_detector.py"
    "$SRC_DIR/dom_change_detector.py"
)

# ── Forbidden patterns ───────────────────────────────────────────────
# Runtime imports of server-layer modules.
# TYPE_CHECKING-guarded imports are allowed (not executed at runtime).
check_file() {
    local file="$1"
    local basename
    basename=$(basename "$file")

    # Skip if file doesn't exist (Phase 1 may have moved it)
    [[ -f "$file" ]] || return 0

    # 1) Direct import of browser_session (except re-export in browser_session.py itself)
    if grep -nE '^\s*(from \.browser_session import|import .*browser_session)' "$file" \
        | grep -v 'TYPE_CHECKING' > /dev/null 2>&1; then
        echo "FAIL: $file imports browser_session at runtime"
        grep -nE '^\s*(from \.browser_session import|import .*browser_session)' "$file"
        EXIT_CODE=1
    fi

    # 2) Runtime import of playwright (not under TYPE_CHECKING)
    #    Extract lines with playwright imports, exclude those inside TYPE_CHECKING blocks.
    #    Simple heuristic: if the import line is NOT preceded by "if TYPE_CHECKING:" on
    #    a nearby line, it's a runtime import.
    local playwright_imports
    playwright_imports=$(grep -nE '^\s*from playwright' "$file" 2>/dev/null || true)
    if [[ -n "$playwright_imports" ]]; then
        while IFS= read -r line; do
            local lineno
            lineno=$(echo "$line" | cut -d: -f1)
            # Check if this import is inside a TYPE_CHECKING block
            # Look at the 3 lines before for "if TYPE_CHECKING:"
            local context_before
            context_before=$(sed -n "$((lineno > 3 ? lineno - 3 : 1)),${lineno}p" "$file")
            if ! echo "$context_before" | grep -qE 'if TYPE_CHECKING'; then
                echo "FAIL: $file has runtime Playwright import at line $lineno"
                echo "  $line"
                EXIT_CODE=1
            fi
        done <<< "$playwright_imports"
    fi
}

echo "=== Dependency direction check ==="

# Phase 1+: if core/ directory exists, check everything inside it
if [[ -d "$SRC_DIR/core" ]]; then
    echo "Phase 1+ detected: checking $SRC_DIR/core/"
    while IFS= read -r -d '' file; do
        check_file "$file"
    done < <(find "$SRC_DIR/core" -name '*.py' -print0)
else
    echo "Phase 0 (flat layout): checking core candidate files"
    for file in "${CORE_FILES[@]}"; do
        check_file "$file"
    done
fi

# Phase 4: core/ must not import from cloud/
if [[ -d "$SRC_DIR/core" && -d "$SRC_DIR/cloud" ]]; then
    echo "Phase 4: checking core/ does not import from cloud/"
    if grep -rn 'from.*\.cloud\.' "$SRC_DIR/core/" --include='*.py' | grep -v __pycache__; then
        echo "FAIL: core/ imports from cloud/"
        EXIT_CODE=1
    fi
    if grep -rn 'from pagemap\.cloud' "$SRC_DIR/core/" --include='*.py' | grep -v __pycache__; then
        echo "FAIL: core/ imports from pagemap.cloud"
        EXIT_CODE=1
    fi
fi

if [[ $EXIT_CODE -eq 0 ]]; then
    echo "PASS: No forbidden reverse dependencies found."
else
    echo ""
    echo "FAILED: Reverse dependencies detected. See above."
fi

exit $EXIT_CODE
