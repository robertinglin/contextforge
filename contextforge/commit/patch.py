# contextforge/commit/patch.py
from __future__ import annotations

from typing import List
import itertools
import difflib
import logging
import re
import unicodedata


from .._logging import resolve_logger
from ..errors.patch import PatchFailedError

__all__ = ["patch_text", "fuzzy_patch_partial"]


# ---------- core helpers ----------



def _compose_from_to(hunk_lines: list[str]) -> tuple[list[str], list[str]]:
    """Build 'from' (everything except '+') and 'to' (everything except '-') blocks."""
    from_lines: list[str] = []
    to_lines: list[str] = []
    for ln in hunk_lines:
        # Treat raw empty lines as context (they exist in some hand-crafted diffs)
        if ln == "":
            from_lines.append("")
            to_lines.append("")
            continue
        tag = ln[0]
        body = ln[1:] if tag in " +-+" else ln  # tag handled below (keep raw body incl. leading space)
        if tag != "+":
            from_lines.append(body)
        if tag != "-":
            to_lines.append(body)
    return from_lines, to_lines

def _find_block_end_by_braces(lines: list[str], start: int) -> int:
    """
    Given a start index that points at a line with an opening '{' (or soon after),
    scan forward and return the exclusive end index where braces balance back to zero.
    If we can't find a clear boundary, return -1.
    """
    depth = 0
    seen_open = False
    for i in range(start, len(lines)):
        ln = lines[i]
        for ch in ln:
            if ch == "{":
                depth += 1
                seen_open = True
            elif ch == "}":
                depth -= 1
        if seen_open and depth <= 0:
            return i + 1
    return -1

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

def _flatten_ws_outside_quotes(text: str) -> str:
    """
    Remove comments and *all* whitespace (spaces/tabs/newlines) from a code block,
    including inside string literals. Supports single (' / ") and triple (''' / \"\"\")
    quotes and strips '#' and '//' comments when not inside a string.
    Escapes inside strings are preserved.
    """
    out: list[str] = []
    i, n = 0, len(text)
    q: str | None = None  # None | "'" | '"' | "'''" | '"""'

    def starts_with(s: str) -> bool:
        return text.startswith(s, i)

    while i < n:
        if q is None:
            # Handle comments (only when not inside a string)
            if starts_with("//"):
                # Skip to end of line
                while i < n and text[i] != "\n":
                    i += 1
                # We drop the newline too because we flatten all whitespace anyway
                i += 1 if i < n else 0
                continue
            if text[i] == "#":
                while i < n and text[i] != "\n":
                    i += 1
                i += 1 if i < n else 0
                continue

            # Enter string mode (triple quotes first)
            if starts_with("'''"):
                q = "'''"
                out.extend(["'", "'", "'"])
                i += 3
                continue
            if starts_with('"""'):
                q = '"""'
                out.extend(['"', '"', '"'])
                i += 3
                continue
            if text[i] in ("'", '"'):
                q = text[i]
                out.append(text[i])
                i += 1
                continue

            # Outside any string: drop whitespace, keep non-whitespace
            ch = text[i]
            if ch not in (" ", "\t", "\r", "\n"):
                out.append(ch)
            i += 1
        else:
            # Inside a string: preserve escapes and quotes, drop whitespace
            if q in ("'''", '"""'):
                if starts_with(q):
                    out.extend(list(q))
                    i += 3
                    q = None
                    continue
                ch = text[i]
                if ch == "\\" and i + 1 < n:
                    out.append("\\")
                    out.append(text[i + 1])
                    i += 2
                    continue
                if ch not in (" ", "\t", "\r", "\n"):
                    out.append(ch)
                i += 1
            else:
                ch = text[i]
                if ch == "\\" and i + 1 < n:
                    out.append("\\")
                    out.append(text[i + 1])
                    i += 2
                    continue
                if ch == q:
                    out.append(ch)
                    q = None
                    i += 1
                    continue
                if ch not in (" ", "\t", "\r", "\n"):
                    out.append(ch)
                i += 1

    return "".join(out)

_LEADING_WS_RE = re.compile(r"^[\t ]*")

def _leading_ws(s: str) -> str:
    """Return the exact leading whitespace (tabs/spaces)."""
    m = _LEADING_WS_RE.match(s)
    return m.group(0) if m else ""

def _normalize_quotes(s: str) -> str:
    """
    Normalize a few common Unicode quotes to ASCII to reduce spurious mismatches.
    """
    tbl = {
        "\u2018": "'", "\u2019": "'", "\u201B": "'",
        "\u201C": '"', "\u201D": '"',
    }
    return "".join(tbl.get(ch, ch) for ch in s)

def _similarity(a_lines: list[str], b_lines: list[str]) -> float:
    """
    Line-wise similarity using SequenceMatcher on trimmed, quote-normalized lines.
    """
    a = [_normalize_quotes(x.strip()) for x in a_lines]
    b = [_normalize_quotes(x.strip()) for x in b_lines]
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()

_NUMBAR_RE = re.compile(r"^\s*\d+\s*\|\s?")
def _strip_line_numbers_block(lines: list[str]) -> list[str]:
    """
    Remove leading 'NN | ' prefixes that sometimes appear in AI-provided diffs.
    """
    changed = False
    out: list[str] = []
    for ln in lines:
        new = _NUMBAR_RE.sub("", ln)
        changed = changed or (new != ln)
        out.append(new)
    # Only return stripped version if anything actually changed—helps avoid loops.
    return out if changed else lines

def _reindent_relative(new_lines: list[str], search_first: str, matched_first: str) -> list[str]:
    """
    Adjust indentation of replacement lines so that the indentation of *search_first*
    is replaced by the indentation found at *matched_first*.

    This version attempts to translate indentation style (e.g., spaces to tabs)
    by replacing the patch's base indentation unit with the target's.
    """
    if not new_lines:
        return new_lines
    ref_in = _leading_ws(search_first)
    ref_out = _leading_ws(matched_first)
    if ref_in == ref_out:
        return new_lines

    # If the patch has no base indentation, we can't perform a reliable replacement.
    # Prepending the target indent is a reasonable behavior for new, top-level code.
    if not ref_in:
        return [ref_out + ln for ln in new_lines]

    adjusted: list[str] = []
    for ln in new_lines:
        ws = _leading_ws(ln)
        body = ln[len(ws):]
        # Replace all occurrences of the input reference indent with the output reference indent.
        # This correctly handles multiple levels of indentation if they are consistent
        # (e.g., converting 8 spaces to 2 tabs if ref_in='    ' and ref_out='\t').
        new_ws = ws.replace(ref_in, ref_out)
        adjusted.append(new_ws + body)
    return adjusted

def _surgical_reconstruct_block(
   hunk_lines: List[str],
   matched_segment: List[str],
   search_first: str,
   matched_first: str,
) -> List[str]:
   """
   Rebuild the replacement block *surgically*:
         - keep file context lines exactly as they appear in matched_segment
         - drop '-' lines
         - insert '+' lines (re-indented to match the file)
   This prevents overwriting unrelated context drift (e.g., renamed functions).
   """
   out: List[str] = []
   seg_i = 0
   for raw in hunk_lines:
           if raw == "":
                   # blank-as-context: keep file line if available
                   if seg_i < len(matched_segment):
                           out.append(matched_segment[seg_i])
                   else:
                           out.append("")
                   seg_i += 1
                   continue
           tag = raw[0]
           body = raw[1:] if tag in " +-+" else raw
           if tag == " ":
                   # context comes from the file, not the patch
                   if seg_i < len(matched_segment):
                           out.append(matched_segment[seg_i])
                   else:
                           out.append(body.lstrip(" "))
                   seg_i += 1
           elif tag == "-":
                   seg_i += 1  # drop this line from the file
           elif tag == "+":
                   out.extend(_reindent_relative([body], search_first, matched_first))
   return out

def _middle_out_best_window(
        target: list[str],
        needle: list[str],
    start_hint: int,
    lo: int,
    hi: int,
) -> tuple[int, float]:
    """
    Search for best fuzzy match by scanning outward from start_hint within [lo, hi).
    Returns (best_index, best_ratio) or (-1, -1.0) if impossible.
    """
    if not target or not needle:
        return -1, -1.0
    m = min(len(needle), max(0, hi - lo))
    if m <= 0:
        return -1, -1.0
    mid = max(lo, min(start_hint, hi - m))
    best_idx, best_ratio = -1, -1.0
    max_delta = max(mid - lo, (hi - m) - mid)
    for d in range(0, max_delta + 1):
        for pos in ([mid] if d == 0 else [mid - d, mid + d]):
            if pos < lo or pos > hi - m:
                continue
            ratio = _similarity(needle[:m], target[pos:pos + m])
            if ratio > best_ratio:
                best_idx, best_ratio = pos, ratio
    return best_idx, best_ratio

def _structure_penalty(
    target: list[str], pos: int, new_content: list[str], lead_ctx: list[str]
) -> int:
    """Lower is better. Penalize positions whose indentation resembles context poorly."""
    # Prefer the indentation of the incoming content if available; fall back to lead context.
    want_indent = _indent(new_content[0]) if new_content else (_indent(lead_ctx[-1]) if lead_ctx else 0)
    have_indent = _indent(target[pos - 1]) if pos > 0 else 0
    indent_pen = abs(want_indent - have_indent)
    return min(indent_pen, 8)


def _split_lead_tail_context(hunk_lines: list[str]) -> tuple[list[str], list[str]]:
    """Extract leading and trailing context (signs removed)."""
    lead: list[str] = []
    tail: list[str] = []
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



def _parse_patch_hunks(patch_str: str) -> list[dict]:
    """
    Parse patch string into hunks. Keep header fields and include valid hunk lines.
    Also accept raw empty lines inside hunks (treat as context).
    """
    hunks: list[dict] = []
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
        if cur is not None and (line == "" or line[:1] in (" ", "+", "-")):
            cur["lines"].append(line)
    if cur:
        hunks.append(cur)
    if not hunks:
        raise PatchFailedError("Patch string contains no valid hunks.")
    return hunks

def _parse_simplified_patch_hunks(patch_str: str) -> list[dict]:
    """
    Parse patch string using '@@' as a simple hunk separator, ignoring line numbers.
    """
    hunks: list[dict] = []
    current_hunk_lines: list[str] = []

    lines = patch_str.strip().splitlines()

    # Skip file headers (---, +++, diff --git, Index:, etc.)
    start_idx = 0
    for i, line in enumerate(lines):
        # Stop at first @@ or first actual diff line
        if line.strip() == "@@":
            start_idx = i + 1  # Start after the @@
            break
        if (
            line
            and line[0] in ("+", "-", " ")
            and not (line.startswith("--- ") or line.startswith("+++ "))
        ):
            start_idx = i
            break

    # Process the lines after headers
    lines = lines[start_idx:]

    for line in lines:
        if line.strip() == "@@":
            if current_hunk_lines:
                hunks.append({"lines": current_hunk_lines})
                current_hunk_lines = []
        else:
            if line == "" or (line[:1] in (" ", "+", "-") and not (line.startswith("--- ") or line.startswith("+++ "))):
                current_hunk_lines.append(line)

    if current_hunk_lines:
        hunks.append({"lines": current_hunk_lines})

    return hunks


def _find_block_matches(target: list[str], block: list[str], loose: bool = False) -> list[int]:
    """Find all start indices where block appears in target."""
    matches: list[int] = []
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

def _split_hunk_components(hunk_lines: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Split hunk into old content, new content, and pure context (signs removed)."""
    old_content: list[str] = []
    new_content: list[str] = []
    context_only: list[str] = []

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
            content = line[1:]
            old_content.append(content)
        elif tag == "+":
            content = line[1:]
            new_content.append(content)
        else:
            # ignore unknown tags
            pass

    return old_content, new_content, context_only


def _adaptive_ctx_window(lead_ctx: list[str], tail_ctx: list[str]) -> int:
    """Pick a context slice size between 3 and 10 based on available context."""
    total = len(lead_ctx) + len(tail_ctx)
    if total <= 3:
        return 3
    if total >= 20:
        return 10
    return max(3, min(10, 3 + (total - 3) * (10 - 3) // (20 - 3)))

def _locate_insertion_index(
    target: list[str],
    lead_ctx: list[str],
    tail_ctx: list[str],
    start_hint: int,
    ctx_probe: int,
) -> int:
    """
    Choose a good insertion point for pure additions using both sides of context.
    """
    n = len(target)
    if n == 0:
        return 0

    lead_slice = lead_ctx[-min(ctx_probe, len(lead_ctx)) :] if lead_ctx else []
    tail_slice = tail_ctx[: min(ctx_probe, len(tail_ctx))] if tail_ctx else []

    lead_hits = _find_block_matches(target, lead_slice, loose=True) if lead_slice else []
    tail_hits = _find_block_matches(target, tail_slice, loose=True) if tail_slice else []

    best = None
    best_key = None

    def score_insert(pos: int, anchor_bonus: int) -> tuple[int, int]:
        return (abs(pos - start_hint), -anchor_bonus)

    if lead_hits and tail_hits:
        # Prefer an insertion point within [L_end, T] closest to start_hint;
        # if start_hint is within the range, pick it; otherwise pick the nearer boundary
        for L in lead_hits:
            L_end = L + len(lead_slice)
            for T in tail_hits:
                if L_end <= T:
                    if L_end <= start_hint <= T:
                        pos = start_hint
                    else:
                        pos = L_end if abs(start_hint - L_end) <= abs(T - start_hint) else T
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


# ---------- Finding ALL candidates for a hunk ----------

def _find_all_hunk_candidates(
    target_lines: list[str],
    hunk: dict,
    threshold: float,
    start_hint: int,
    search_min: int,
    search_max: int,
    log: logging.Logger,
    max_candidates: int = 10,
) -> list[dict]:
    """
    Find ALL candidate locations where a hunk could be applied.
    Only searches between [search_min, search_max].
    Returns a list of dicts sorted by quality, each with:
      start_idx, end_idx, replacement_lines, match_type, confidence
    Returns empty list if no acceptable match is found.
    """
    old_content, new_content, context_only = _split_hunk_components(hunk["lines"])
    lead_ctx, tail_ctx = _split_lead_tail_context(hunk["lines"])
    ctx_probe = _adaptive_ctx_window(lead_ctx, tail_ctx)
    
    candidates = []

    log.debug(f"\n=== FINDING CANDIDATES (hint={start_hint}, range=[{search_min}, {search_max}]) ===")
    log.debug(f"Old content ({len(old_content)} lines): {old_content[:3] if old_content else '(empty)'}")
    log.debug(f"New content ({len(new_content)} lines): {new_content[:3] if new_content else '(empty)'}")
    
    # Log the full hunk for debugging
    log.debug(f"Full hunk lines ({len(hunk['lines'])} lines):")
    for i, line in enumerate(hunk["lines"]):
        log.debug(f"  [{i}] {repr(line)}")

    # Extract changed content (only the - lines, without context)
    changed_lines = []
    leading_context_count = 0
    found_first_change = False
    
    for ln in hunk["lines"]:
        if not found_first_change:
            if ln == "" or (ln and ln[0] == " "):
                leading_context_count += 1
            elif ln and ln[0] in "-+":
                found_first_change = True
        
        if ln and ln[0] == "-":
            changed_lines.append(ln[1:])
        
    log.debug(f"Changed lines (- lines only, {len(changed_lines)} lines):")
    log.debug(f"Leading context lines before first change: {leading_context_count}")

    for i, line in enumerate(changed_lines):
        log.debug(f"  [{i}] {repr(line)}")
    
    # For pure additions, extract only the + lines (not context)
    if not changed_lines:
        addition_lines = []
        for ln in hunk["lines"]:
            if ln and ln[0] == "+":
                addition_lines.append(ln[1:])
        
        log.debug(f"Addition lines (+ lines only, {len(addition_lines)} lines):")
        for i, line in enumerate(addition_lines):
            log.debug(f"  [{i}] {repr(line)}")
    
    # If we have no changed lines, this is a pure addition
    if not changed_lines:
        log.debug("Pure addition detected (no - lines)")
        if not old_content:
            # Completely new content with no context
            log.debug("No old content, treating as pure addition")
            ins_pos = _locate_insertion_index(target_lines, lead_ctx, tail_ctx, start_hint, ctx_probe)
            return [{
                "start_idx": ins_pos,
                "end_idx": ins_pos,
                "replacement_lines": addition_lines,
                "match_type": "pure_addition",
                "confidence": 0.9
            }]
    
    # --- Strategy: Find all locations with the changed content, then score by context ---
    from_lines, to_lines = _compose_from_to(hunk["lines"])
    log.debug(f"Composed from_lines ({len(from_lines)} lines):")
    for i, line in enumerate(from_lines):
        log.debug(f"  [{i}] {repr(line)}")
    
    # For pure additions with context, find where to insert based on context
    if not changed_lines and old_content:
        log.debug("Pure addition with context - locating insertion point")
        ins_pos = _locate_insertion_index(target_lines, lead_ctx, tail_ctx, start_hint, ctx_probe)
        return [{
            "start_idx": ins_pos,
            "end_idx": ins_pos,
            "replacement_lines": addition_lines,
            "match_type": "pure_addition",
            "confidence": 0.9
        }]
    
    # Step 1: Find all locations that contain the changed lines (fuzzy)
    anchor_candidates = []
    
    if changed_lines:
        log.debug(f"Searching for anchor (changed content): {len(changed_lines)} lines")
        
        # Search for changed content with fuzzy matching
        for i in range(max(0, search_min), min(len(target_lines) - len(changed_lines) + 1, search_max)):
            window = target_lines[i : i + len(changed_lines)]
            
            # Exact match on changed lines
            exact_match = all(
                target_lines[i + j] == changed_lines[j]
                for j in range(len(changed_lines))
            )
            
            if exact_match:
                anchor_candidates.append(i)
                continue
            
            # Loose match on changed lines (whitespace-insensitive)
            loose_match = all(
                _eq_loose(target_lines[i + j], changed_lines[j])
                for j in range(len(changed_lines))
            )
            
            if loose_match:
                anchor_candidates.append(i)
                continue
            
            # Fuzzy match on changed lines
            a_trim = [x.strip() for x in changed_lines]
            b_trim = [x.strip() for x in window]
            ratio = difflib.SequenceMatcher(None, a_trim, b_trim, autojunk=False).ratio()
            
            if ratio >= 0.8:  # High similarity threshold for anchor
                anchor_candidates.append(i)
        
        log.debug(f"Found {len(anchor_candidates)} anchor candidates at: {anchor_candidates}")
    
    # Step 2: Score each anchor candidate by surrounding context
    if anchor_candidates:
        scored_candidates = []
        
        # The anchor is the position of the changed line, not the hunk start
        # We need to adjust by subtracting the leading context count
        log.debug(f"Adjusting anchor positions by -{leading_context_count} (leading context)")
        
        for anchor_idx in anchor_candidates:
            # Calculate how well the context matches
            context_score = 0.0
            context_weight = 0

            # Check leading context
            if lead_ctx:
                # The context check needs to look BEFORE the anchor
                # But the anchor is at the changed line, and there's leading_context_count lines before it
                # So we check context BEFORE (anchor - leading_context_count)
                before = target_lines[max(0, anchor_idx - leading_context_count - ctx_probe) : anchor_idx - leading_context_count]
                lead_slice = lead_ctx[-min(ctx_probe, len(lead_ctx)):]
                lead_ratio = _similarity(lead_slice, before[-len(lead_slice):] if before else [])
                context_score += lead_ratio
                context_weight += 1
                log.debug(f"  Anchor {anchor_idx}: lead_context_ratio={lead_ratio:.3f}")
            
            # Check trailing context  
            if tail_ctx:
                after_start = anchor_idx + len(changed_lines)
                after = target_lines[after_start : after_start + ctx_probe]
                tail_slice = tail_ctx[:min(ctx_probe, len(tail_ctx))]
                tail_ratio = _similarity(tail_slice, after[:len(tail_slice)] if after else [])
                context_score += tail_ratio
                context_weight += 1
                log.debug(f"  Anchor {anchor_idx}: tail_context_ratio={tail_ratio:.3f}")
            
            # Overall confidence: high base score with context adjustment
            avg_context = (context_score / context_weight) if context_weight > 0 else 0.5
            confidence = 0.9 + (avg_context * 0.1)  # 0.9-1.0 range
            
            # Calculate the actual hunk start position
            hunk_start = anchor_idx - leading_context_count
            
            # Build replacement using surgical reconstruction
            # Use old_content length for the match window
            match_len = len(old_content) if old_content else len(changed_lines)
            surg = _surgical_reconstruct_block(
                hunk["lines"],
                target_lines[hunk_start : hunk_start + match_len],
                old_content[0] if old_content else "",
                target_lines[hunk_start] if hunk_start < len(target_lines) else ""
            )
            
            scored_candidates.append({
                "start_idx": hunk_start,
                "end_idx": hunk_start + match_len,
                "replacement_lines": surg,
                "match_type": "anchor_with_context",
                "confidence": confidence,
                "distance_from_hint": abs(hunk_start - start_hint)
            })
        
        # Sort by confidence desc, then by distance from hint
        scored_candidates.sort(key=lambda c: (-c["confidence"], c["distance_from_hint"]))
        
        log.debug(f"Scored candidates:")
        for idx, cand in enumerate(scored_candidates[:max_candidates]):
            log.debug(f"  [{idx}] hunk_start={cand['start_idx']}, confidence={cand['confidence']:.3f}")
        
        return scored_candidates[:max_candidates]


    # 1) Exact content match
    exact = _find_block_matches(target_lines, old_content, loose=False)
    exact = [e for e in exact if search_min <= e < search_max]
    if exact:
        log.debug(f"Exact content matches: {len(exact)} hits at {exact}")
        
        # Show what we're matching against at each hit location
        for hit_idx in exact:
            log.debug(f"  Exact content match at line {hit_idx}:")
            for i, line in enumerate(target_lines[hit_idx:hit_idx+len(old_content)]):
                log.debug(f"    [{i}] {repr(line)}")
        
        def _score_exact(p: int) -> tuple[int, int, int, int]:
            before = target_lines[max(0, p - ctx_probe) : p]
            after = target_lines[p + len(old_content) : p + len(old_content) + ctx_probe]
            lead_hit = (
                0
                if not lead_ctx
                else int(
                    difflib.SequenceMatcher(
                        None,
                        [x.strip() for x in lead_ctx[-min(ctx_probe, len(lead_ctx)) :]],
                        [x.strip() for x in before[-min(ctx_probe, len(before)) :]],
                        autojunk=False,
                    ).ratio()
                    * 1000
                )
            )
            tail_hit = (
                0
                if not tail_ctx
                else int(
                    difflib.SequenceMatcher(
                        None,
                        [x.strip() for x in tail_ctx[: min(ctx_probe, len(tail_ctx))]],
                        [x.strip() for x in after[: min(ctx_probe, len(after))]],
                        autojunk=False,
                    ).ratio()
                    * 1000
                )
            )
            struct_pen = _structure_penalty(target_lines, p, new_content, lead_ctx)
            return (abs(p - start_hint), -(lead_hit + tail_hit), struct_pen, p)

        for i in sorted(exact, key=_score_exact):
            surg = _surgical_reconstruct_block(
                hunk["lines"],
                target_lines[i : i + len(old_content)],
                old_content[0] if old_content else "",
                target_lines[i] if target_lines else ""
            )
            candidates.append({
                "start_idx": i,
                "end_idx": i + len(old_content),
                "replacement_lines": surg,
                "match_type": "exact_content",
                "confidence": 1.0
            })
        return candidates[:max_candidates]

    # 2) Loose content match
    loose = _find_block_matches(target_lines, old_content, loose=True)
    loose = [l for l in loose if search_min <= l < search_max]
    if loose:
        log.debug(f"Loose content matches: {len(loose)} hits at {loose}")
        
        # Show what we're matching against at each hit location
        for hit_idx in loose:
            log.debug(f"  Loose content match at line {hit_idx}:")
            for i, line in enumerate(target_lines[hit_idx:hit_idx+len(old_content)]):
                log.debug(f"    [{i}] {repr(line)}")
        
        if len(loose) == 1:
            # Single match = high confidence
            i = loose[0]
            surg = _surgical_reconstruct_block(
                hunk["lines"],
                target_lines[i : i + len(old_content)],
                old_content[0] if old_content else "",
                target_lines[i] if target_lines else ""
            )
            return [{
                "start_idx": i,
                "end_idx": i + len(old_content),
                "replacement_lines": surg,
                "match_type": "loose_content",
                "confidence": 0.9
            }]
        else:
            # Multiple matches
            def _score_loose(p: int) -> tuple[int, int]:
                return (abs(p - start_hint), _structure_penalty(target_lines, p, new_content, lead_ctx))
            for i in sorted(loose, key=_score_loose):
                surg = _surgical_reconstruct_block(
                    hunk["lines"],
                    target_lines[i : i + len(old_content)],
                    old_content[0] if old_content else "",
                    target_lines[i] if target_lines else ""
                )
                candidates.append({
                    "start_idx": i,
                    "end_idx": i + len(old_content),
                    "replacement_lines": surg,
                    "match_type": "loose_content_ambiguous",
                    "confidence": 0.6
                })
            return candidates[:max_candidates]

    # 3) Fuzzy window
    log.debug("Fuzzy window search:")
    n = len(target_lines)
    if n == 0:
        return [{
            "start_idx": 0,
            "end_idx": 0,
            "replacement_lines": new_content,
            "match_type": "empty_file",
            "confidence": 1.0
        }]
    
    m = min(len(old_content), n)
    fuzzy_candidates = []

    a_trim = [x.strip() for x in old_content[:m]]
    
    log.debug(f"Starting fuzzy window search with window size {m}")
    log.debug(f"Search range: [{max(0, search_min)}, {min(n - m + 1, search_max)})")

    for i in range(max(0, search_min), min(n - m + 1, search_max)):
        window = target_lines[i : i + m]
        b_trim = [x.strip() for x in window]
        ratio = difflib.SequenceMatcher(None, a_trim, b_trim, autojunk=False).ratio()

        # Enforce first-line alignment
        first_line_matches = False
        if old_content and i < len(target_lines):
            old_first = old_content[0].strip()
            file_first = target_lines[i].strip()
            if old_first == file_first:
                first_line_matches = True
            elif old_first and file_first:
                first_ratio = difflib.SequenceMatcher(None, old_first, file_first, autojunk=False).ratio()
                first_line_matches = first_ratio > 0.8
        else:
            first_line_matches = True
        
        if first_line_matches and ratio >= threshold:
            fuzzy_candidates.append((i, ratio))
            log.debug(f"  Fuzzy candidate at line {i}, ratio={ratio:.3f}")
            if ratio >= 0.8:  # Log high-confidence fuzzy matches in detail
                log.debug(f"    Window content:")
                for j, line in enumerate(window[:min(5, len(window))]):
                    log.debug(f"      [{j}] {repr(line)}")

    if fuzzy_candidates:
        # Sort by ratio desc, then by distance from hint
        fuzzy_candidates.sort(key=lambda x: (-x[1], abs(x[0] - start_hint)))
        log.debug(f"  Found {len(fuzzy_candidates)} fuzzy matches:")
        for idx, (pos, ratio) in enumerate(fuzzy_candidates[:10]):  # Show top 10
            log.debug(f"    [{idx}] line {pos}, ratio={ratio:.3f}")
    else:
        log.debug(f"  No fuzzy matches found (threshold={threshold:.2f})")
        # Show best ratios even if below threshold
        log.debug(f"  Showing all positions checked (sample):")
        sample_positions = list(range(max(0, search_min), min(n - m + 1, search_max), max(1, (min(n - m + 1, search_max) - max(0, search_min)) // 20)))
        for i in sample_positions[:10]:
            window = target_lines[i : i + m]
            b_trim = [x.strip() for x in window]
            ratio = difflib.SequenceMatcher(None, a_trim, b_trim, autojunk=False).ratio()
            log.debug(f"    line {i}: ratio={ratio:.3f}")
        
        for i, ratio in fuzzy_candidates[:max_candidates]:
            # Validate alignment for surgical reconstruction
            use_surgical = False
            if i + len(old_content) <= len(target_lines) and len(old_content) > 0:
                alignment_checks = min(3, len(old_content))
                matches = 0
                for check_idx in range(alignment_checks):
                    if i + check_idx >= len(target_lines):
                        break
                    old_line = old_content[check_idx].strip()
                    file_line = target_lines[i + check_idx].strip()
                    if old_line == file_line:
                        matches += 1
                
                use_surgical = matches >= min(2, alignment_checks)
            
            if use_surgical:
                actual_old_file_lines = 0
                for ln in hunk["lines"]:
                    if ln == "" or (ln and ln[0] in " -"):
                        actual_old_file_lines += 1
                
                if i + actual_old_file_lines > len(target_lines):
                    actual_old_file_lines = len(target_lines) - i
                
                surg = _surgical_reconstruct_block(
                    hunk["lines"],
                    target_lines[i : i + actual_old_file_lines],
                    old_content[0],
                    target_lines[i]
                )
                candidates.append({
                    "start_idx": i,
                    "end_idx": i + actual_old_file_lines,
                    "replacement_lines": surg,
                    "match_type": "fuzzy_surgical",
                    "confidence": ratio
                })
            else:
                # Simple replacement
                m_local = min(len(old_content), len(target_lines) - i)
                if new_content:
                    new_adj = _reindent_relative(new_content, old_content[0], target_lines[i])
                else:
                    new_adj = new_content
                candidates.append({
                    "start_idx": i,
                    "end_idx": i + m_local,
                    "replacement_lines": new_adj,
                    "match_type": "fuzzy_window",
                    "confidence": ratio
                })
        
        return candidates

    # No candidates found
    return []


# ---------- Global assignment with constraints ----------

def _assign_hunks_to_candidates(all_candidates: list[list[dict]], log: logging.Logger) -> list[dict | None]:
    """
    Given candidates for each hunk, assign hunks to non-overlapping candidates
    that respect sequential ordering and maximize overall confidence.
    
    Constraints:
    1. No overlaps: Two hunks can't claim overlapping regions
    2. Sequential ordering: Hunk[i] must come before hunk[i+1] in the file
    
    Returns a list of assignments (one per hunk), where each is either a candidate dict or None.
    """
    num_hunks = len(all_candidates)
    
    # Try to find a valid assignment using backtracking
    def backtrack(hunk_idx: int, assigned: list[dict | None], used_regions: list[tuple[int, int]]) -> list[dict | None] | None:
        if hunk_idx == num_hunks:
            return assigned[:]
        
        candidates = all_candidates[hunk_idx]
        if not candidates:
            # No candidates - mark as None and continue
            assigned.append(None)
            result = backtrack(hunk_idx + 1, assigned, used_regions)
            assigned.pop()
            return result
        
        # Try each candidate for this hunk
        for candidate in candidates:
            start = candidate["start_idx"]
            end = candidate["end_idx"]
            
            # Check if this overlaps with any already-assigned region
            overlaps = any(
                not (end <= used_start or start >= used_end)
                for used_start, used_end in used_regions
            )
            
            # Check sequential ordering: this hunk should come after all previous assigned hunks
            violates_order = any(
                assigned[i] is not None and assigned[i]["start_idx"] >= start
                for i in range(hunk_idx)
            )
            
            if not overlaps and not violates_order:
                # This candidate is valid
                assigned.append(candidate)
                used_regions.append((start, end))
                
                result = backtrack(hunk_idx + 1, assigned, used_regions)
                if result is not None:
                    return result
                
                # Backtrack
                assigned.pop()
                used_regions.pop()
        
        # No valid candidate found - mark as None and continue
        assigned.append(None)
        result = backtrack(hunk_idx + 1, assigned, used_regions)
        assigned.pop()
        return result
    
    result = backtrack(0, [], [])
    if result is None:
        log.debug("No valid assignment found, using greedy fallback")
        # Fallback: just take the first candidate for each hunk
        return [candidates[0] if candidates else None for candidates in all_candidates]
    
    return result


def patch_text(
    content: str,
    patch: str | list[dict[str, str]],
    threshold: float = 0.6,
    *,
    logger=None,
    log: bool = False,
    debug: bool | None = None,
) -> str:
    """
    Apply a patch using a four-phase algorithm:
    
    Phase 1: Find ALL candidate locations for each hunk
    Phase 2: Assign hunks to candidates globally (respecting order and avoiding overlaps)
    Phase 3: Refine failed hunks using perfect hunks as anchors
    Phase 4: Apply all changes bottom-to-top

    Raises:
        PatchFailedError: if no acceptable match can be found for any hunk.
    """
    log = resolve_logger(logger=logger, enabled=log, name=__name__, level=logging.DEBUG)

    if debug is not None:
        log = log or bool(debug)

    # Structured patch path: list of dicts
    if isinstance(patch, list):
        text = content
        for i, spec in enumerate(patch, 1):
            old = spec.get("old")
            new = spec.get("new", "")
            pattern = spec.get("pattern")
            if not (old or pattern):
                raise PatchFailedError("missing 'old' or 'pattern' in structured patch")
            if pattern:
                log.debug(f"[{i}] regex replace: pattern={pattern!r}")
                text, n = re.subn(pattern, new, text, count=1)
                if n == 0:
                    raise PatchFailedError(f"pattern not found: {pattern!r}")
                continue
            # Sentinel replacement
            head_len = 0
            for a, b in zip(old, new):
                if a == b:
                    head_len += 1
                else:
                    break
            tail_len = 0
            for a, b in zip(reversed(old), reversed(new)):
                if a == b:
                    tail_len += 1
                else:
                    break
            head = old[:head_len]
            tail = old[len(old) - tail_len :] if tail_len else ""
            mid_new = new[head_len : len(new) - tail_len if tail_len else None]
            if head and tail:
                start = text.find(head)
                if start != -1:
                    end = text.find(tail, start + len(head))
                    if end != -1:
                        log.debug(f"[{i}] sentinel replace between head/tail")
                        text = text[: start + len(head)] + mid_new + text[end:]
                        continue
            if old in text:
                log.debug(f"[{i}] exact replace of old block")
                text = text.replace(old, new, 1)
            else:
                raise PatchFailedError("old block not found for structured patch")
        return text

    if not patch.strip():
        return content

    def _detect_eol(s: str) -> str:
        if "\r\n" in s:
            return "\r\n"
        if "\r" in s:
            return "\r"
        return "\n"

    eol = _detect_eol(content)
    had_trailing_nl = content.endswith(("\r\n", "\n", "\r"))

    dedented_patch = patch.strip()

    log.debug("\n=== PATCH PARSING ===")

    standard_match = re.search(
        r"^@@\s+-\d+(?:,\d+)?\s+\+\d+(?:,\d+)?\s+@@", dedented_patch, re.MULTILINE
    )

    if standard_match:
        hunks = _parse_patch_hunks(dedented_patch)
    else:
        hunks = _parse_simplified_patch_hunks(dedented_patch)
    
    if not hunks:
        raise PatchFailedError("no valid hunks")

    log.debug(f"Parsed {len(hunks)} hunks")

    original_lines = content.splitlines()
    log.debug(f"Target file has {len(original_lines)} lines")

    # PHASE 1: Find ALL candidates for each hunk
    log.debug("\n" + "=" * 60)
    log.debug("PHASE 1: FIND ALL CANDIDATES FOR EACH HUNK")
    log.debug("=" * 60)
    
    all_candidates = []
    
    for i, h in enumerate(hunks):
        log.debug(f"\nHunk #{i + 1}/{len(hunks)}")
        
        if h.get("new_start"):
            start_hint = min(len(original_lines), max(0, int(h.get("new_start", 1)) - 1))
        else:
            start_hint = 0
        
        candidates = _find_all_hunk_candidates(
            original_lines, h, threshold, start_hint,
            0, len(original_lines),
            log=log
        )
        
        if candidates:
            log.debug(f"  Found {len(candidates)} candidate(s):")
            for j, cand in enumerate(candidates):
                log.debug(f"    [{j}] line {cand['start_idx']}, confidence={cand['confidence']:.2f}, type={cand['match_type']}")
        else:
            log.debug(f"  No candidates found")
        
        all_candidates.append(candidates)
    
    # PHASE 2: Assign hunks to candidates globally
    log.debug("\n" + "=" * 60)
    log.debug("PHASE 2: ASSIGN HUNKS TO CANDIDATES (GLOBAL OPTIMIZATION)")
    log.debug("=" * 60)
    
    assignments = _assign_hunks_to_candidates(all_candidates, log)
    
    locations = []
    for i, assignment in enumerate(assignments):
        if assignment is None:
            log.debug(f"Hunk #{i + 1}: no valid assignment")
            locations.append({
                "hunk_index": i,
                "hunk": hunks[i],
                "match_type": "failed",
                "confidence": 0.0,
                "start_idx": -1,
                "end_idx": -1,
                "replacement_lines": [],
                "error": "No valid assignment"
            })
        else:
            assignment["hunk_index"] = i
            assignment["hunk"] = hunks[i]
            locations.append(assignment)
            log.debug(f"Hunk #{i + 1}: assigned to line {assignment['start_idx']}, confidence={assignment['confidence']:.2f}")
    
    # PHASE 3: Refine ambiguous/failed hunks using anchors
    log.debug("\n" + "=" * 60)
    log.debug("PHASE 3: REFINE USING ANCHORS")
    log.debug("=" * 60)
    
    # Identify perfect vs ambiguous/failed
    PERFECT_THRESHOLD = 0.95
    perfect_indices = [i for i, loc in enumerate(locations) if loc["confidence"] >= PERFECT_THRESHOLD]
    
    log.debug(f"Perfect hunks: {[locations[i]['hunk_index'] + 1 for i in perfect_indices]}")
    
    for i, loc in enumerate(locations):
        if loc["confidence"] >= PERFECT_THRESHOLD:
            continue  # Already good
        
        log.debug(f"\nRefining Hunk #{loc['hunk_index'] + 1} (confidence={loc['confidence']:.2f})")
        
        # Find bounding perfect hunks
        prev_perfect = None
        next_perfect = None
        
        for pi in perfect_indices:
            if pi < i:
                prev_perfect = locations[pi]
            elif pi > i:
                next_perfect = locations[pi]
                break
        
        # Determine search bounds
        if prev_perfect and next_perfect:
            search_min = prev_perfect["end_idx"]
            search_max = next_perfect["start_idx"]
            log.debug(f"  Bounded by hunks #{prev_perfect['hunk_index']+1} and #{next_perfect['hunk_index']+1}")
            log.debug(f"  Search range: [{search_min}, {search_max}]")
        elif prev_perfect:
            search_min = prev_perfect["end_idx"]
            search_max = len(original_lines)
            log.debug(f"  Bounded below by hunk #{prev_perfect['hunk_index']+1}")
        elif next_perfect:
            search_min = 0
            search_max = next_perfect["start_idx"]
            log.debug(f"  Bounded above by hunk #{next_perfect['hunk_index']+1}")
        else:
            # No perfect hunks to anchor on
            log.debug(f"  No perfect anchors available")
            continue
        
        if search_max <= search_min:
            log.debug(f"  ⚠️ Invalid search range, skipping refinement")
            continue
        
        # Re-search with constraints
        h = loc["hunk"]
        if h.get("new_start"):
            start_hint = min(len(original_lines), max(0, int(h.get("new_start", 1)) - 1))
        else:
            start_hint = (search_min + search_max) // 2
        
        # Find new candidates in constrained region
        new_candidates = _find_all_hunk_candidates(
            original_lines, h, threshold, start_hint,
            search_min, search_max,
            log=log
        )
        
        if new_candidates:
            # Take the best candidate
            new_location = new_candidates[0]
            new_location["hunk_index"] = loc["hunk_index"]
            new_location["hunk"] = h
            locations[i] = new_location
            log.debug(f"  ✅ Refined (new confidence={new_location['confidence']:.2f})")
        else:
            # Still can't find it - create merge conflict if we have both bounds
            if prev_perfect and next_perfect:
                log.debug(f"  💡 Creating merge conflict between anchors")
                
                # Calculate proportional position
                patch_total = sum(
                    len(locations[j]["hunk"].get("lines", [])) 
                    for j in range(len(locations))
                )
                patch_before = sum(
                    len(locations[j]["hunk"].get("lines", [])) 
                    for j in range(i)
                )
                ratio = patch_before / max(patch_total, 1)
                
                file_range = search_max - search_min
                insert_pos = search_min + int(ratio * file_range)
                
                # Get what we expected vs what's there
                old_content, new_content, _ = _split_hunk_components(h["lines"])
                window_size = min(len(old_content), search_max - insert_pos) if old_content else 5
                actual_content = original_lines[insert_pos:insert_pos + window_size]
                
                conflict_lines = []
                conflict_lines.append("<<<<<<< CURRENT (file content)")
                conflict_lines.extend(actual_content)
                conflict_lines.append("=======")
                conflict_lines.extend(new_content if new_content else ["(empty)"])
                conflict_lines.append(f">>>>>>> PATCH (hunk #{loc['hunk_index'] + 1})")
                
                locations[i] = {
                    "hunk_index": loc["hunk_index"],
                    "hunk": h,
                    "start_idx": insert_pos,
                    "end_idx": insert_pos + window_size,
                    "replacement_lines": conflict_lines,
                    "match_type": "merge_conflict",
                    "confidence": 0.5
                }
                log.debug(f"  ✅ Merge conflict created at line {insert_pos}")
            else:
                log.debug(f"  ✗ Still failed, no both-side anchors for merge conflict")
    
    # PHASE 4: Apply all changes bottom-to-top
    log.debug("\n" + "=" * 60)
    log.debug("PHASE 4: APPLY CHANGES (BOTTOM TO TOP)")
    log.debug("=" * 60)
    
    # Sort by position descending (bottom to top), then by hunk_index ascending (patch order)
    locations.sort(key=lambda x: (-x["start_idx"], x["hunk_index"]))
    
    # Detect overlaps (should not happen with proper assignment)
    for i in range(len(locations) - 1):
        curr = locations[i]
        next_loc = locations[i + 1]
        if curr["start_idx"] >= 0 and next_loc["start_idx"] >= 0:
            if curr["start_idx"] < next_loc["end_idx"] and curr["end_idx"] > next_loc["start_idx"]:
                log.debug(f"\n⚠️ UNEXPECTED OVERLAP (BUG?):")
                log.debug(f"  Hunk #{curr['hunk_index'] + 1} [{curr['start_idx']}:{curr['end_idx']}]")
                log.debug(f"  Hunk #{next_loc['hunk_index'] + 1} [{next_loc['start_idx']}:{next_loc['end_idx']}]")
    
    current_lines = original_lines[:]
    
    for loc in locations:
        if loc["start_idx"] < 0:
            log.debug(f"\nSkipping failed hunk #{loc['hunk_index'] + 1}")
            continue
        
        log.debug(f"\nApplying Hunk #{loc['hunk_index'] + 1} at [{loc['start_idx']}:{loc['end_idx']}]")
        log.debug(f"  Type: {loc['match_type']}, Confidence: {loc['confidence']:.2f}")
        
        current_lines = (
            current_lines[:loc["start_idx"]] +
            loc["replacement_lines"] +
            current_lines[loc["end_idx"]:]
        )
        
        log.debug(f"  ✅ Applied. File now has {len(current_lines)} lines")
    
    log.debug("\n" + "=" * 60)
    log.debug("PATCH APPLICATION COMPLETE")
    log.debug("=" * 60)

    return (eol.join(current_lines)) + (eol if had_trailing_nl else "")


def fuzzy_patch_partial(
    content: str, patch_str: str, threshold: float = 0.6, *, logger=None, log: bool = False
):
    """
    Best-effort patching - same four-phase algorithm as patch_text.
    Returns (new_text, applied_indices, failed) where failed is a list of failed hunk details.
    """
    log = resolve_logger(logger=logger, enabled=log, name=__name__, level=logging.DEBUG)

    if not patch_str.strip():
        return content, [], []

    def _detect_eol(s: str) -> str:
        if "\r\n" in s:
            return "\r\n"
        if "\r" in s:
            return "\r"
        return "\n"

    eol = _detect_eol(content)
    had_trailing_nl = content.endswith(("\r\n", "\n", "\r"))
    hunks = _parse_patch_hunks(patch_str.strip())
    original_lines = content.splitlines()
    
    # Phase 1: Find all candidates
    all_candidates = []
    
    for i, h in enumerate(hunks):
        if h.get("new_start"):
            start_hint = min(len(original_lines), max(0, int(h.get("new_start", 1)) - 1))
        else:
            start_hint = 0
        
        candidates = _find_all_hunk_candidates(
            original_lines, h, threshold, start_hint,
            0, len(original_lines),
            log=log
        )
        all_candidates.append(candidates)
    
    # Phase 2: Assign globally
    assignments = _assign_hunks_to_candidates(all_candidates, log)
    
    locations = []
    for i, assignment in enumerate(assignments):
        if assignment is None:
            locations.append({
                "hunk_index": i,
                "hunk": hunks[i],
                "match_type": "failed",
                "confidence": 0.0,
                "start_idx": -1,
                "end_idx": -1,
                "replacement_lines": [],
                "error": "No valid assignment"
            })
        else:
            assignment["hunk_index"] = i
            assignment["hunk"] = hunks[i]
            locations.append(assignment)
    
    # Phase 3: Refine (same logic as patch_text)
    PERFECT_THRESHOLD = 0.95
    perfect_indices = [i for i, loc in enumerate(locations) if loc["confidence"] >= PERFECT_THRESHOLD]
    
    for i, loc in enumerate(locations):
        if loc["confidence"] >= PERFECT_THRESHOLD:
            continue
        
        prev_perfect = None
        next_perfect = None
        
        for pi in perfect_indices:
            if pi < i:
                prev_perfect = locations[pi]
            elif pi > i:
                next_perfect = locations[pi]
                break
        
        if prev_perfect and next_perfect:
            search_min = prev_perfect["end_idx"]
            search_max = next_perfect["start_idx"]
        elif prev_perfect:
            search_min = prev_perfect["end_idx"]
            search_max = len(original_lines)
        elif next_perfect:
            search_min = 0
            search_max = next_perfect["start_idx"]
        else:
            continue
        
        if search_max <= search_min:
            continue
        
        h = loc["hunk"]
        start_hint = (search_min + search_max) // 2
        
        new_candidates = _find_all_hunk_candidates(
            original_lines, h, threshold, start_hint,
            search_min, search_max,
            log=log
        )
        
        if new_candidates:
            new_location = new_candidates[0]
            new_location["hunk_index"] = loc["hunk_index"]
            new_location["hunk"] = h
            locations[i] = new_location
        else:
            if prev_perfect and next_perfect:
                # Create merge conflict
                patch_total = sum(len(locations[j]["hunk"].get("lines", [])) for j in range(len(locations)))
                patch_before = sum(len(locations[j]["hunk"].get("lines", [])) for j in range(i))
                ratio = patch_before / max(patch_total, 1)
                
                insert_pos = search_min + int(ratio * (search_max - search_min))
                
                old_content, new_content, _ = _split_hunk_components(h["lines"])
                window_size = min(len(old_content) if old_content else 5, search_max - insert_pos)
                actual_content = original_lines[insert_pos:insert_pos + window_size]
                
                conflict_lines = []
                conflict_lines.append("<<<<<<< CURRENT")
                conflict_lines.extend(actual_content)
                conflict_lines.append("=======")
                conflict_lines.extend(new_content if new_content else ["(empty)"])
                conflict_lines.append(f">>>>>>> PATCH (hunk #{loc['hunk_index'] + 1})")
                
                locations[i] = {
                    "hunk_index": loc["hunk_index"],
                    "hunk": h,
                    "start_idx": insert_pos,
                    "end_idx": insert_pos + window_size,
                    "replacement_lines": conflict_lines,
                    "match_type": "merge_conflict",
                    "confidence": 0.5
                }
    
    # Phase 4: Apply
    locations.sort(key=lambda x: (-x["start_idx"], x["hunk_index"]))
    
    current_lines = original_lines[:]
    applied = []
    failed = []
    
    for loc in locations:
        if loc["start_idx"] < 0:
            old_content, new_content, _ = _split_hunk_components(loc["hunk"]["lines"])
            lead_ctx, tail_ctx = _split_lead_tail_context(loc["hunk"]["lines"])
            failed.append({
                "index": loc["hunk_index"],
                "error": loc.get("error", "Unknown error"),
                "lead_ctx": lead_ctx,
                "tail_ctx": tail_ctx,
                "old_content": old_content,
                "new_content": new_content,
                "header_hint": 0,
            })
            continue
        
        current_lines = (
            current_lines[:loc["start_idx"]] +
            loc["replacement_lines"] +
            current_lines[loc["end_idx"]:]
        )
        applied.append(loc["hunk_index"])
    
    new_text = eol.join(current_lines) + (eol if had_trailing_nl else "")
    return new_text, applied, failed