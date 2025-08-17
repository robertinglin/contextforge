# contextforge/apply/patch.py
from __future__ import annotations

import difflib
import logging
import re
import textwrap
from typing import Dict, List, Optional, Tuple

from ..errors.patch import PatchFailedError

__all__ = ["patch_text", "fuzzy_patch_partial"]

logger = logging.getLogger(__name__)


# ---------- core helpers ----------

def _compose_from_to(hunk_lines: List[str]) -> Tuple[List[str], List[str]]:
    """Build 'from' (everything except '+') and 'to' (everything except '-') blocks."""
    from_lines: List[str] = []
    to_lines: List[str] = []
    for ln in hunk_lines:
        # Treat raw empty lines as context (they exist in some hand-crafted diffs)
        if ln == "":
            from_lines.append("")
            to_lines.append("")
            continue
        tag = ln[0]
        body = ln[1:] if tag in " +-+" else ln  # tag handled below
        if tag != '+':
            from_lines.append(body)
        if tag != '-':
            to_lines.append(body)
    return from_lines, to_lines


def _find_block_end_by_braces(lines: List[str], start: int) -> int:
    """
    Given a start index that points at a line with an opening '{' (or soon after),
    scan forward and return the exclusive end index where braces balance back to zero.
    If we can’t find a clear boundary, return -1.
    """
    depth = 0
    seen_open = False
    for i in range(start, len(lines)):
        ln = lines[i]
        for ch in ln:
            if ch == '{':
                depth += 1
                seen_open = True
            elif ch == '}':
                depth -= 1
        if seen_open and depth <= 0:
            return i + 1
    return -1


def _eq(a: str, b: str) -> bool:
    """Strict line equality (default)."""
    return a == b


def _eq_loose(a: str, b: str) -> bool:
    """Whitespace-insensitive equality for fuzzy/context matching."""
    return a == b or a.strip() == b.strip()


def _indent(s: str) -> int:
    """Count leading spaces/tabs as indentation depth (tabs count as 4)."""
    spaces = 0
    for ch in s:
        if ch == " ":
            spaces += 1
        elif ch == "\t":
            spaces += 4
        else:
            break
    return spaces


def _brace_depth(lines: List[str], upto: int) -> int:
    """Very light brace-depth heuristic for C/JS-like code."""
    depth = 0
    for ln in lines[:max(0, min(upto, len(lines)))]:
        for ch in ln:
            if ch in "{[(":
                depth += 1
            elif ch in "}])":
                depth -= 1
    return depth


def _flatten_ws_outside_quotes(text: str) -> str:
    """
    Remove comments and non-essential whitespace (spaces/tabs/newlines) from a
    code block, crucially preserving content inside string literals.
    Handles both // and # style comments.
    """
    comment_markers = ("#", "//")
    out: List[str] = []

    for line in text.splitlines():
        # --- Pass 1: Find end of code, respecting quotes ---
        q: Optional[str] = None
        code_end_idx = len(line)
        i = 0
        while i < len(line):
            char = line[i]
            if q:  # Inside a string
                if char == "\\" and i + 1 < len(line):
                    i += 2
                    continue
                if char == q:
                    q = None
            else:  # Outside a string
                if char in ("'", '"'):
                    q = char
                if any(line[i:].startswith(m) for m in comment_markers):
                    code_end_idx = i
                    break
            i += 1

        code_part = line[:code_end_idx]

        # --- Pass 2: Flatten whitespace on the code part ---
        q = None
        i, n = 0, len(code_part)
        while i < n:
            ch = code_part[i]
            if q:
                out.append(ch)
                if ch == "\\" and i + 1 < n:
                    out.append(code_part[i + 1])
                    i += 2
                    continue
                if ch == q:
                    q = None
            elif ch in ("'", '"'):
                q = ch
                out.append(ch)
            elif ch not in (" ", "\t", "\r", "\n"):
                out.append(ch)
            i += 1

    return "".join(out)


def _structure_penalty(target: List[str], pos: int, new_content: List[str], lead_ctx: List[str]) -> int:
    """Lower is better. Penalize positions whose indentation resembles context poorly."""
    want_indent = _indent(lead_ctx[-1]) if lead_ctx else _indent(new_content[0] if new_content else "")
    have_indent = _indent(target[pos - 1]) if pos > 0 else 0
    indent_pen = abs(want_indent - have_indent)
    return min(indent_pen, 8)


def _parse_patch_hunks(patch_str: str) -> List[Dict]:
    """
    Parse patch string into hunks. Keep header fields and include valid hunk lines.
    Also accept raw empty lines inside hunks (treat as context).
    """
    hunks: List[Dict] = []
    hunk_header_re = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
    cur = None
    for raw in patch_str.splitlines():
        line = raw.rstrip("\r\n")
        m = hunk_header_re.match(line.strip())
        if m:
            if cur:
                hunks.append(cur)
            old_start = int(m.group(1))
            old_len = int(m.group(2) or "1")
            new_start = int(m.group(3))
            new_len = int(m.group(4) or "1")
            cur = {
                "old_start": old_start,
                "old_len": old_len,
                "new_start": new_start,
                "new_len": new_len,
                "lines": [],
            }
            continue
        if cur is not None:
            if line == "" or line[:1] in (" ", "+", "-"):
                cur["lines"].append(line)
    if cur:
        hunks.append(cur)
    if not hunks:
        raise PatchFailedError("Patch string contains no valid hunks.")
    return hunks

def _parse_simplified_patch_hunks(patch_str: str) -> List[Dict]:
    """
    Parse patch string using '@@' as a simple hunk separator, ignoring line numbers.
    """
    hunks: List[Dict] = []
    current_hunk_lines: List[str] = []

    lines = patch_str.strip().splitlines()

    # Skip file headers (---, +++, diff --git, Index:, etc.)
    start_idx = 0
    for i, line in enumerate(lines):
        # Stop at first @@ or first actual diff line
        if line.strip() == '@@':
            start_idx = i + 1  # Start after the @@
            break
        if line and line[0] in ('+', '-', ' '):
            # Check if it's a file header or actual diff content
            if not (line.startswith('--- ') or line.startswith('+++ ')):
                start_idx = i
                break

    # Process the lines after headers
    lines = lines[start_idx:]

    for line in lines:
        if line.strip() == '@@':
            if current_hunk_lines:
                hunks.append({"lines": current_hunk_lines})
                current_hunk_lines = []
        else:
            if line == "" or line[:1] in (" ", "+", "-"):
                current_hunk_lines.append(line)

    if current_hunk_lines:
        hunks.append({"lines": current_hunk_lines})

    return hunks

def _find_block_matches(target: List[str], block: List[str], loose: bool = False) -> List[int]:
    """Find all start indices where block appears in target."""
    matches: List[int] = []
    m = len(block)
    if m == 0:
        return matches
    n = len(target)
    for i in range(n - m + 1):
        ok = True
        for j in range(m):
            if loose:
                if not _eq_loose(target[i + j], block[j]):
                    ok = False
                    break
        else:
                if target[i + j] != block[j]:
                    ok = False
                    break
        if ok:
            matches.append(i)
    return matches


def _split_hunk_components(hunk_lines: List[str]) -> Tuple[List[str], List[str], List[str]]:
    """Split hunk into old content, new content, and pure context (signs removed)."""
    old_content: List[str] = []
    new_content: List[str] = []
    context_only: List[str] = []

    for line in hunk_lines:
        if line == "":
            # treat as context blank line
            old_content.append("")
            new_content.append("")
            context_only.append("")
            continue
        tag = line[0]
        if tag == " ":
            content = line[1:]
            old_content.append(content)
            new_content.append(content)
            context_only.append(content)
        elif tag == "-":
            old_content.append(line[1:])
        elif tag == "+":
            new_content.append(line[1:])
        else:
            # ignore unknown tags
            pass

    return old_content, new_content, context_only


def _split_lead_tail_context(hunk_lines: List[str]) -> Tuple[List[str], List[str]]:
    """Extract leading and trailing context (signs removed)."""
    lead: List[str] = []
    tail: List[str] = []
    n = len(hunk_lines)
    i = 0
    while i < n and (hunk_lines[i] == "" or hunk_lines[i].startswith(" ")):
        lead.append(hunk_lines[i][1:] if hunk_lines[i] != "" else "")
        i += 1
    j = n
    while j > i and (hunk_lines[j - 1] == "" or hunk_lines[j - 1].startswith(" ")):
        tail.append(hunk_lines[j - 1][1:] if hunk_lines[j - 1] != "" else "")
        j -= 1
    tail.reverse()
    return lead, tail


def _adaptive_ctx_window(lead_ctx: List[str], tail_ctx: List[str]) -> int:
    """Pick a context slice size between 3 and 10 based on available context."""
    total = len(lead_ctx) + len(tail_ctx)
    if total <= 3:
        return 3
    if total >= 20:
        return 10
    return max(3, min(10, 3 + (total - 3) * (10 - 3) // (20 - 3)))


def _locate_insertion_index(
    target: List[str],
    lead_ctx: List[str],
    tail_ctx: List[str],
    start_hint: int,
    ctx_probe: int,
) -> int:
    """
    Choose a good insertion point for pure additions using both sides of context.
    """
    n = len(target)
    if n == 0:
        return 0

    lead_slice = lead_ctx[-min(ctx_probe, len(lead_ctx)):] if lead_ctx else []
    tail_slice = tail_ctx[:min(ctx_probe, len(tail_ctx))] if tail_ctx else []

    lead_hits = _find_block_matches(target, lead_slice, loose=True) if lead_slice else []
    tail_hits = _find_block_matches(target, tail_slice, loose=True) if tail_slice else []

    best = None
    best_key = None

    def score_insert(pos: int, anchor_bonus: int) -> Tuple[int, int]:
        return (abs(pos - start_hint), -anchor_bonus)

    if lead_hits and tail_hits:
        for L in lead_hits:
            L_end = L + len(lead_slice)
            for T in tail_hits:
                if L_end <= T:
                    pos = max(L_end, min(T, n))
                    key = score_insert(pos, anchor_bonus=2)
                    if best is None or key < best_key:
                        best, best_key = pos, key

    if best is None and lead_hits:
        L = min(lead_hits, key=lambda p: abs((p + len(lead_slice)) - start_hint))
        pos = min(n, L + len(lead_slice))
        best, best_key = pos, score_insert(pos, anchor_bonus=1)

    if best is None and tail_hits:
        T = min(tail_hits, key=lambda p: abs(p - start_hint))
        pos = max(0, T)
        best, best_key = pos, score_insert(pos, anchor_bonus=1)

    if best is not None:
        return best

    return max(0, min(start_hint, n))


# ---------- main application ----------

def _apply_hunk_block_style(
    target_lines: List[str],
    hunk: Dict,
    threshold: float,
    start_hint: int,
) -> Tuple[List[str], int]:
    """
    Apply hunk using block-first approach, resolving ambiguity by distance to hint
    and adaptive surrounding context. Returns (new_lines, new_cursor_pos).
    """
    old_content, new_content, context_only = _split_hunk_components(hunk["lines"])
    lead_ctx, tail_ctx = _split_lead_tail_context(hunk["lines"])
    ctx_probe = _adaptive_ctx_window(lead_ctx, tail_ctx)

    # DEBUG: Log hunk details
    logger.debug(f"\n=== APPLYING HUNK (start_hint={start_hint}) ===")
    logger.debug(f"Hunk lines ({len(hunk['lines'])} total):")
    for i, line in enumerate(hunk['lines'][:10]):  # Show first 10 lines
        logger.debug(f"  {i:3}: {repr(line)}")
    if len(hunk['lines']) > 10:
        logger.debug(f"  ... and {len(hunk['lines']) - 10} more lines")
    logger.debug(f"Old content ({len(old_content)} lines): {old_content[:3] if old_content else '(empty)'}")
    logger.debug(f"New content ({len(new_content)} lines): {new_content[:3] if new_content else '(empty)'}")
    logger.debug(f"Lead context: {lead_ctx}")
    logger.debug(f"Tail context: {tail_ctx}")

    # --- Step 0: composite block replace (from_lines -> to_lines) ---
    from_lines, to_lines = _compose_from_to(hunk["lines"])
    if any(ln for ln in hunk["lines"] if ln and ln[0] in " -") and len(from_lines) > 0:
        # Try exact, nearest to hint
        hits = _find_block_matches(target_lines, from_lines, loose=False)
        logger.debug(f"Exact block match for {len(from_lines)} lines: {len(hits)} hits at positions {hits[:5]}")
        if hits:
            i = min(hits, key=lambda p: abs(p - start_hint))
            new_lines = target_lines[:i] + to_lines + target_lines[i + len(from_lines):]
            return new_lines, i + len(to_lines)

        # Try loose, nearest to hint
        hits_loose = _find_block_matches(target_lines, from_lines, loose=True)
        logger.debug(f"Loose block match for {len(from_lines)} lines: {len(hits_loose)} hits at positions {hits_loose[:5]}")
        if hits_loose:
            i = min(hits_loose, key=lambda p: abs(p - start_hint))
            new_lines = target_lines[:i] + to_lines + target_lines[i + len(from_lines):]
            return new_lines, i + len(to_lines)

        # JS-ish brace-aware fallback: if lead has a likely function signature,
        # anchor there and replace to the balanced brace end.
        sig = next((ln for ln in lead_ctx if ln.strip().startswith("function ") or "updateParentCheckboxState(" in ln), None)
        if sig:
            sig_hits = [k for k, ln in enumerate(target_lines) if _eq_loose(ln, sig)]
            if sig_hits:
                start = min(sig_hits, key=lambda p: abs(p - start_hint))
                block_end = start + len(from_lines)
                if block_end > len(target_lines) or block_end <= start:
                    block_end = _find_block_end_by_braces(target_lines, start)
                if block_end != -1 and block_end > start:
                    new_lines = target_lines[:start] + to_lines + target_lines[block_end:]
                    return new_lines, start + len(to_lines)

    # Pure addition (no old_content to find): insert using both lead & tail anchors.
    if not old_content:
        logger.debug("Pure addition detected - using context anchors")
        ins_pos = _locate_insertion_index(target_lines, lead_ctx, tail_ctx, start_hint, ctx_probe)
        # Tiny structure bias hook (doesn't move insertion yet—kept conservative)
        new_lines = target_lines[:ins_pos] + new_content + target_lines[ins_pos:]
        return new_lines, ins_pos + len(new_content)

    # 1) Exact block match(es)
    exact = _find_block_matches(target_lines, old_content, loose=False)
    if exact:
        logger.debug(f"Exact content match: {len(exact)} hits at positions {exact[:5]}")
        def _score_exact(p: int) -> Tuple[int, int, int, int]:
            before = target_lines[max(0, p - ctx_probe):p]
            after = target_lines[p + len(old_content): p + len(old_content) + ctx_probe]
            lead_hit = 0 if not lead_ctx else int(
                difflib.SequenceMatcher(
                    None,
                    [x.strip() for x in lead_ctx[-min(ctx_probe, len(lead_ctx)):]],
                    [x.strip() for x in before[-min(ctx_probe, len(before)):]],
                    autojunk=False,
                ).ratio() * 1000
            )
            tail_hit = 0 if not tail_ctx else int(
                difflib.SequenceMatcher(
                    None,
                    [x.strip() for x in tail_ctx[:min(ctx_probe, len(tail_ctx))]],
                    [x.strip() for x in after[:min(ctx_probe, len(after))]],
                    autojunk=False,
                ).ratio() * 1000
            )
            struct_pen = _structure_penalty(target_lines, p, new_content, lead_ctx)
            return (abs(p - start_hint), -(lead_hit + tail_hit), struct_pen, p)

        i = sorted(exact, key=_score_exact)[0]
        new_lines = target_lines[:i] + new_content + target_lines[i + len(old_content):]
        return new_lines, i + len(new_content)

    # 2) Loose block match(es)
    loose = _find_block_matches(target_lines, old_content, loose=True)
    if loose:
        logger.debug(f"Loose content match: {len(loose)} hits at positions {loose[:5]}")
        def _score_loose(p: int) -> Tuple[int, int]:
            return (abs(p - start_hint), _structure_penalty(target_lines, p, new_content, lead_ctx))
        i = sorted(loose, key=_score_loose)[0]
        new_lines = target_lines[:i] + new_content + target_lines[i + len(old_content):]
        return new_lines, i + len(new_content)

    # 3) Fuzzy window (trimmed tokens). Be robust when old_content is longer than target.
    logger.debug(f"\nFuzzy window search:")
    logger.debug(f"  Looking for {len(old_content)} lines in {len(target_lines)} total lines")
    logger.debug(f"  First 3 lines to match (trimmed):")
    for i, line in enumerate(old_content[:3]):
        logger.debug(f"    {i}: {repr(line.strip())}")
    logger.debug(f"  Actual file content around hint position {start_hint}:")
    for i in range(max(0, start_hint-1), min(len(target_lines), start_hint+4)):
        logger.debug(f"    {i}: {repr(target_lines[i][:80])}")

    m_full = len(old_content)
    n = len(target_lines)
    if n == 0:
        new_lines = new_content + []
        return new_lines, len(new_content)
    m = min(m_full, n)

    best_ratio = -1.0
    best_index = -1

    a_trim_full = [x.strip() for x in old_content]
    a_trim = a_trim_full[:m]

    for i in range(n - m + 1):
        window = target_lines[i:i + m]
        b_trim = [x.strip() for x in window]
        ratio = difflib.SequenceMatcher(None, a_trim, b_trim, autojunk=False).ratio()

        # Log high-scoring matches for debugging
        if ratio > 0.3:
            logger.debug(f"    Position {i}: ratio={ratio:.3f}")
            if ratio > 0.5:
                logger.debug(f"      Window preview: {b_trim[:2]}")

        if (ratio > best_ratio or
            (ratio == best_ratio and abs(i - start_hint) < abs(best_index - start_hint))):
            best_ratio = ratio
            best_index = i
            if best_ratio == 1.0:
                logger.debug(f"  Perfect match found at position {i}")
                break

    logger.debug(f"  Best fuzzy match: ratio={best_ratio:.3f} at position {best_index}")
    if best_ratio < threshold:
        logger.debug("\n  ⚠️  MATCH FAILURE ANALYSIS:")
        logger.debug(f"  Expected to find these lines:")
        for i, line in enumerate(old_content[:min(5, len(old_content))]):
            logger.debug(f"    - {repr(line)}")
        if len(old_content) > 5:
            logger.debug(f"    ... and {len(old_content) - 5} more lines")

        # --- FALLBACK 1: ANCHORED, WHITESPACE-INSENSITIVE "SURGICAL" MATCH ---
        logger.debug("\n  💡 Attempting anchored, whitespace-insensitive fallback...")
        if old_content:
            anchor_line_stripped = old_content[0].strip()
            anchor_hits = [i for i, line in enumerate(target_lines) if line.strip() == anchor_line_stripped]
            if anchor_hits:
                sorted_anchors = sorted(anchor_hits, key=lambda i: abs(i - start_hint))
                flat_old_block = _flatten_ws_outside_quotes("\n".join(old_content))
                for anchor_index in sorted_anchors:
                    # This loop is for trying multiple anchors if the first one fails
                    # (though it usually succeeds on the first try if it's going to work)
                    for i in range(anchor_index, len(target_lines)):
                        current_consumed_lines = target_lines[anchor_index : i + 1]
                        flat_consumed = _flatten_ws_outside_quotes("\n".join(current_consumed_lines))
                        if not flat_old_block.startswith(flat_consumed): break
                        if flat_consumed == flat_old_block:
                            logger.debug(f"  ✅ Fallback success: Surgically matched {i + 1 - anchor_index} file lines from anchor {anchor_index}.")
                            start, end = anchor_index, i + 1
                            new_lines = target_lines[:start] + new_content + target_lines[end:]
                            return new_lines, start + len(new_content)

        # --- FALLBACK 2: UNIQUE START AND END ANCHOR MATCH ---
        logger.debug("\n  💡 Attempting unique end-anchor fallback...")
        if old_content and len(old_content) > 1:
            start_anchor_strip = old_content[0].strip()
            end_anchor_strip = next((line.strip() for line in reversed(old_content) if line.strip()), None)

            if start_anchor_strip and end_anchor_strip:
                start_hits = [i for i, l in enumerate(target_lines) if l.strip() == start_anchor_strip]
                end_hits = [i for i, l in enumerate(target_lines) if l.strip() == end_anchor_strip]

                if len(end_hits) == 1:
                    end_line_idx = end_hits[0]
                    logger.debug(f"  ✅ Found unique end-anchor at line {end_line_idx}.")
                    # Find the best start anchor that comes before this unique end
                    plausible_starts = [i for i in start_hits if i <= end_line_idx]
                    if plausible_starts:
                        start_line_idx = min(plausible_starts, key=lambda i: abs(i - start_hint))
                        logger.debug(f"  Paired with best start-anchor at line {start_line_idx}. Replacing block.")
                        start, end = start_line_idx, end_line_idx + 1
                        new_lines = target_lines[:start] + new_content + target_lines[end:]
                        return new_lines, start + len(new_content)

        # --- FINAL FALLBACK: FIND BEST FUZZY BLOCK AND CREATE MERGE CONFLICT ---
        logger.debug("\n  💡 All precise fallbacks failed. Attempting to find best fuzzy block for merge conflict...")
        if not old_content:
            raise PatchFailedError("All patch methods failed and cannot generate conflict for an empty block.")

        anchor_line_stripped = old_content[0].strip()
        anchor_hits = [i for i, line in enumerate(target_lines) if line.strip() == anchor_line_stripped]

        if not anchor_hits:
            raise PatchFailedError("Final fallback failed: Anchor line for merge conflict not found.")

        sorted_anchors = sorted(anchor_hits, key=lambda i: abs(i - start_hint))
        anchor_index = sorted_anchors[0]
        flat_old_block = _flatten_ws_outside_quotes("\n".join(old_content))
        best_ratio = -1.0
        best_end_line = -1

        search_end = min(len(target_lines), anchor_index + len(old_content) * 2 + 20) # Search a reasonable distance
        for i in range(anchor_index, search_end): # Expand window from anchor
            flat_current_block = _flatten_ws_outside_quotes("\n".join(target_lines[anchor_index : i + 1]))
            ratio = difflib.SequenceMatcher(None, flat_current_block, flat_old_block, autojunk=False).ratio()
            if ratio > best_ratio: best_ratio, best_end_line = ratio, i + 1

        conflict_threshold = 0.25  # Lower threshold for creating a conflict vs. a clean patch
        if best_ratio >= conflict_threshold:
            start_line, end_line = anchor_index, best_end_line
            logger.debug(f"  ✅ Found best fuzzy block match (ratio={best_ratio:.2f}) on lines [{start_line}-{end_line-1}]. Inserting merge conflict.")
            original_block = target_lines[start_line:end_line]
            conflict_block = []
            conflict_block.append("<<<<<<< CURRENT CHANGE")
            conflict_block.extend(original_block)
            conflict_block.append("=======")
            conflict_block.extend(new_content)
            conflict_block.append(">>>>>>> INCOMING CHANGE (from patch)")

            new_lines = target_lines[:start_line] + conflict_block + target_lines[end_line:]
            return new_lines, start_line + len(conflict_block)
        else:
            raise PatchFailedError(f"Final fallback failed: Best fuzzy block ratio ({best_ratio:.2f}) is below conflict threshold ({conflict_threshold}).")


    if best_ratio >= threshold and best_index != -1:
        i = best_index
        new_lines = target_lines[:i] + new_content + target_lines[i + m:]

        return new_lines, i + len(new_content)

    raise PatchFailedError(f"Best match ratio {best_ratio:.2f} is below threshold {threshold:.2f}.")


def patch_text(content: str, patch_str: str, threshold: float = 0.6) -> str:
    """
    Apply a patch string to the provided content.

    Prefers standard unified-diff hunks; falls back to a simplified '@@' hunk
    separator when headers are absent. Matching is robust to minor whitespace
    drift and uses a block-first strategy with contextual anchors and a final
    fuzzy-search fallback. End-of-line style and the presence/absence of a
    trailing newline are preserved.

    Raises:
        PatchFailedError: if no acceptable match can be found for any hunk.
    """
    if not patch_str.strip():
        return content

    def _detect_eol(s: str) -> str:
        if "\r\n" in s:
            return "\r\n"
        if "\r" in s:
            return "\r"
        return "\n"

    eol = _detect_eol(content)
    had_trailing_nl = content.endswith(("\r\n", "\n", "\r"))

    dedented_patch = textwrap.dedent(patch_str).strip()

    # DEBUG: Show patch detection
    logger.debug("\n=== PATCH PARSING ===")
    logger.debug(f"Patch first 500 chars:\n{dedented_patch[:500]}")
    logger.debug(f"\nChecking for standard diff pattern (@@ -N,N +N,N @@)...")
    # Look for standard unified diff header: @@ -start[,count] +start[,count] @@
    standard_match = re.search(r"^@@\s+-\d+(?:,\d+)?\s+\+\d+(?:,\d+)?\s+@@", dedented_patch, re.MULTILINE)
    logger.debug(f"Standard diff pattern found: {bool(standard_match)}")

    if not standard_match:
        # Check for simplified format
        has_simplified = '@@' in dedented_patch
        logger.debug(f"Simplified format detected (contains @@): {has_simplified}")

    # Decide which parser to use. If it looks like a standard diff, use the strict parser.
    # Otherwise, use the simplified '@@' separator parser.
    if re.search(r"^@@\s+-\d+(?:,\d+)?\s+\+\d+(?:,\d+)?\s+@@", dedented_patch, re.MULTILINE):
        hunks = _parse_patch_hunks(dedented_patch)
    else:
        hunks = _parse_simplified_patch_hunks(dedented_patch)

    logger.debug(f"\nParsed {len(hunks)} hunks using {'standard' if standard_match else 'simplified'} parser")
    for i, h in enumerate(hunks):
        logger.debug(f"  Hunk {i+1}: {len(h.get('lines', []))} lines")

    current_lines = content.splitlines()
    logger.debug(f"Target file has {len(current_lines)} lines")
    cursor = 0

    for i, h in enumerate(hunks):
        logger.debug(f"\n{'='*60}\nProcessing Hunk #{i+1}/{len(hunks)}")
        lines = h.get("lines", [])
        pure_add = all(ln.startswith("+") or ln == "" for ln in lines) and any(ln.startswith("+") for ln in lines)

        # Generate start_hint: use header if available, otherwise use previous cursor.
        if h.get("new_start"):
            header_hint = min(len(current_lines), max(0, int(h.get("new_start", 1)) - 1))
        else:
            # For simplified patches, the best hint is where the last hunk left off.
            header_hint = cursor

        # Use a simpler hint calculation for pure adds or when no header is present
        start_hint = header_hint if pure_add or not h.get("new_start") else max(0, min(len(current_lines), int(round(0.7 * cursor + 0.3 * header_hint))))

        try:
            current_lines, cursor = _apply_hunk_block_style(current_lines, h, threshold, start_hint)
            logger.debug(f"✓ Hunk #{i+1} applied successfully. New cursor position: {cursor}")
        except PatchFailedError as e:
            logger.debug(f"✗ Hunk #{i+1} FAILED: {e}")
            raise PatchFailedError(f"Failed to apply hunk #{i + 1}: {e}") from e

    return (eol.join(current_lines)) + (eol if had_trailing_nl else "")

def fuzzy_patch_partial(content: str, patch_str: str, threshold: float = 0.6):
    """
    Best-effort patching:
      - applies all hunks it can
      - returns (new_text, applied_indices, failed) where failed is a list of
        {index, error, lead_ctx, tail_ctx, old_content, new_content}
    """
    if not patch_str.strip():
        return content, [], []
    def _detect_eol(s: str) -> str:
        if "\r\n" in s: return "\r\n"
        if "\r" in s:   return "\r"
        return "\n"
    eol = _detect_eol(content)
    had_trailing_nl = content.endswith(("\r\n", "\n", "\r"))
    hunks = _parse_patch_hunks(textwrap.dedent(patch_str).strip())
    current_lines = content.splitlines()
    cursor = 0
    applied, failed = [], []
    for i, h in enumerate(hunks):
        header_hint = min(len(current_lines), max(0, int(h.get("new_start", 1)) - 1))
        lines = h.get("lines", [])
        pure_add = all(ln.startswith("+") or ln == "" for ln in lines) and any(ln.startswith("+") for ln in lines)
        start_hint = header_hint if pure_add else max(0, min(len(current_lines), int(round(0.7 * cursor + 0.3 * header_hint))))
        try:
            current_lines, cursor = _apply_hunk_block_style(current_lines, h, threshold, start_hint)
            applied.append(i)
        except PatchFailedError as e:
            old_content, new_content, _ctx = _split_hunk_components(h["lines"])
            lead_ctx, tail_ctx = _split_lead_tail_context(h["lines"])
            failed.append({
                "index": i,
                "error": str(e),
                "lead_ctx": lead_ctx,
                "tail_ctx": tail_ctx,
                "old_content": old_content,
                "new_content": new_content,
                "header_hint": header_hint,
            })
            # continue; do not raise
    new_text = eol.join(current_lines) + (eol if had_trailing_nl else "")
    return new_text, applied, failed


