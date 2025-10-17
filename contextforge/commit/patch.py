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


# ---------- NEW: Two-pass location structure ----------

def _locate_hunk_position(
    target_lines: list[str],
    hunk: dict,
    threshold: float,
    start_hint: int,
    log: logging.Logger,
) -> dict:
    """
    Locate where a hunk should be applied without modifying the file.
    Returns a dict with: start_idx, end_idx, replacement_lines, match_type
    Raises PatchFailedError if no acceptable match is found.
    """
    old_content, new_content, context_only = _split_hunk_components(hunk["lines"])
    lead_ctx, tail_ctx = _split_lead_tail_context(hunk["lines"])
    ctx_probe = _adaptive_ctx_window(lead_ctx, tail_ctx)

    log.debug(f"\n=== LOCATING HUNK (start_hint={start_hint}) ===")
    log.debug(f"Old content ({len(old_content)} lines): {old_content[:3] if old_content else '(empty)'}")
    log.debug(f"New content ({len(new_content)} lines): {new_content[:3] if new_content else '(empty)'}")

    # Debug: Show what we're looking for in the file
    if old_content:
        log.debug(f"\n  SEARCHING FOR (first 3 lines of old_content):")
        for i, line in enumerate(old_content[:3]):
            log.debug(f"    [{i}] {repr(line)}")
        if len(old_content) > 3:
            log.debug(f"    ... and {len(old_content) - 3} more lines")
        log.debug(f"  start_hint={start_hint}, lead_ctx={len(lead_ctx)} lines, tail_ctx={len(tail_ctx)} lines")

    # --- Step 0: composite block replace (from_lines -> to_lines) ---
    from_lines, to_lines = _compose_from_to(hunk["lines"])
    if any(ln for ln in hunk["lines"] if ln and ln[0] in " -") and len(from_lines) > 0:
        # Try exact, nearest to hint
        hits = _find_block_matches(target_lines, from_lines, loose=False)
        log.debug(f"Exact block match for {len(from_lines)} lines: {len(hits)} hits at positions {hits[:5]}")
        if hits:
            i = min(hits, key=lambda p: abs(p - start_hint))
            surg = _surgical_reconstruct_block(
                hunk["lines"],
                target_lines[i : i + len(from_lines)],
                from_lines[0] if from_lines else "",
                target_lines[i] if target_lines else ""
            )
            log.debug(f"  ✅ Located exact block at index {i}")
            return {
                "start_idx": i,
                "end_idx": i + len(from_lines),
                "replacement_lines": surg,
                "match_type": "exact_block"
            }

        # Try loose, nearest to hint
        hits_loose = _find_block_matches(target_lines, from_lines, loose=True)
        log.debug(f"Loose block match for {len(from_lines)} lines: {len(hits_loose)} hits at positions {hits_loose[:5]}")
        if hits_loose:
            i = min(hits_loose, key=lambda p: abs(p - start_hint))
            surg = _surgical_reconstruct_block(
                hunk["lines"],
                target_lines[i : i + len(from_lines)],
                from_lines[0] if from_lines else "",
                target_lines[i] if target_lines else ""
            )
            log.debug(f"  ✅ Located loose block at index {i}")
            return {
                "start_idx": i,
                "end_idx": i + len(from_lines),
                "replacement_lines": surg,
                "match_type": "loose_block"
            }

        # JS-ish brace-aware fallback
        sig = next(
            (ln for ln in lead_ctx if ln.strip().startswith("function ") or "updateParentCheckboxState(" in ln),
            None,
        )
        if sig:
            sig_hits = [k for k, ln in enumerate(target_lines) if _eq_loose(ln, sig)]
            if sig_hits:
                start = min(sig_hits, key=lambda p: abs(p - start_hint))
                block_end = start + len(from_lines)
                if block_end > len(target_lines) or block_end <= start:
                    block_end = _find_block_end_by_braces(target_lines, start)
                if block_end != -1 and block_end > start:
                    to_lines_adj = _reindent_relative(to_lines, sig, target_lines[start])
                    return {
                        "start_idx": start,
                        "end_idx": block_end,
                        "replacement_lines": to_lines_adj,
                        "match_type": "brace_aware"
                    }

    # Pure addition (no old_content to find)
    if not old_content:
        log.debug("Pure addition detected - using context anchors")
        ins_pos = _locate_insertion_index(target_lines, lead_ctx, tail_ctx, start_hint, ctx_probe)
        return {
            "start_idx": ins_pos,
            "end_idx": ins_pos,
            "replacement_lines": new_content,
            "match_type": "pure_addition"
        }

    # 1) Exact block match(es)
    exact = _find_block_matches(target_lines, old_content, loose=False)
    if exact:
        # Debug: Show all exact matches found
        log.debug(f"\n  EXACT MATCHES FOUND: {len(exact)} locations: {exact}")
        for match_idx in exact[:5]:  # Show first 5 matches in detail
            log.debug(f"    Match at line {match_idx}:")
            for i, line in enumerate(target_lines[match_idx:match_idx + min(3, len(old_content))]):
                log.debug(f"      [{match_idx + i}] {repr(line)}")
            if match_idx > 0:
                log.debug(f"      (line before: [{match_idx-1}] {repr(target_lines[match_idx-1])})")
            if match_idx + len(old_content) < len(target_lines):
                log.debug(f"      (line after: [{match_idx + len(old_content)}] {repr(target_lines[match_idx + len(old_content)])})")

        log.debug(f"Exact content match: {len(exact)} hits at positions {exact[:5]}")
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

        i = sorted(exact, key=_score_exact)[0]
        # Debug: Show why this particular match was chosen
        all_scores = [(p, _score_exact(p)) for p in exact[:10]]  # Score first 10
        log.debug(f"\n  MATCH SELECTION:")
        log.debug(f"    All scores (pos, (dist_from_hint, -ctx_match, struct_pen, tie_breaker)):")
        for pos, score in sorted(all_scores, key=lambda x: x[1]):
            log.debug(f"      pos={pos}: {score}")
        log.debug(f"    SELECTED: position {i} with score {_score_exact(i)}")
        log.debug(f"    Context: {len(lead_ctx)} lead lines, {len(tail_ctx)} tail lines")

        surg = _surgical_reconstruct_block(
            hunk["lines"],
            target_lines[i : i + len(old_content)],
            old_content[0] if old_content else "",
            target_lines[i] if target_lines else ""
        )
        log.debug(f"  ✅ Located exact content at index {i}")
        return {
            "start_idx": i,
            "end_idx": i + len(old_content),
            "replacement_lines": surg,
            "match_type": "exact_content"
        }

    # 2) Loose block match(es)
    loose = _find_block_matches(target_lines, old_content, loose=True)
    if loose:
        log.debug(f"Loose content match: {len(loose)} hits at positions {loose[:5]}")
        def _score_loose(p: int) -> tuple[int, int]:
            return (abs(p - start_hint), _structure_penalty(target_lines, p, new_content, lead_ctx))

        i = sorted(loose, key=_score_loose)[0]
        surg = _surgical_reconstruct_block(
            hunk["lines"],
            target_lines[i : i + len(old_content)],
            old_content[0] if old_content else "",
            target_lines[i] if target_lines else ""
        )
        log.debug(f"  ✅ Located loose content at index {i}")
        return {
            "start_idx": i,
            "end_idx": i + len(old_content),
            "replacement_lines": surg,
            "match_type": "loose_content"
        }

    # 3) Fuzzy window
    log.debug("\nFuzzy window search:")
    m_full = len(old_content)
    n = len(target_lines)
    if n == 0:
        return {
            "start_idx": 0,
            "end_idx": 0,
            "replacement_lines": new_content,
            "match_type": "empty_file"
        }
    m = min(m_full, n)

    best_ratio = -1.0
    best_index = -1

    a_trim_full = [x.strip() for x in old_content]
    a_trim = a_trim_full[:m]

    for i in range(n - m + 1):
        window = target_lines[i : i + m]
        b_trim = [x.strip() for x in window]
        ratio = difflib.SequenceMatcher(None, a_trim, b_trim, autojunk=False).ratio()

        # Enforce first-line alignment for fuzzy matches
        # A fuzzy match with high ratio but wrong starting position creates bugs.
        # Only accept matches where the first line reasonably aligns.
        first_line_matches = False
        if old_content and i < len(target_lines):
            old_first = old_content[0].strip()
            file_first = target_lines[i].strip()
            # Allow loose match: either exact or high similarity
            if old_first == file_first:
                first_line_matches = True
            elif old_first and file_first:
                # Allow if first lines are very similar (>0.8)
                first_ratio = difflib.SequenceMatcher(None, old_first, file_first, autojunk=False).ratio()
                first_line_matches = first_ratio > 0.8
        else:
            first_line_matches = True  # Empty old_content edge case
        
        # Only update best match if first line aligns
        if first_line_matches:
            if ratio > best_ratio or (
                ratio == best_ratio and abs(i - start_hint) < abs(best_index - start_hint)
            ):
                best_ratio = ratio
                best_index = i
                if best_ratio == 1.0:
                    break

    log.debug(f"  Best fuzzy match: ratio={best_ratio:.3f} at position {best_index}")

    # Middle-out bounded search
    if best_ratio < threshold:
        BUF = 40
        lo = max(0, start_hint - (BUF + 1))
        hi = min(len(target_lines), start_hint + len(old_content) + BUF)
        mid_idx, mid_ratio = _middle_out_best_window(target_lines, old_content, start_hint, lo, hi)
        if mid_ratio > best_ratio:
            log.debug(f"  Middle-out improved ratio from {best_ratio:.3f} to {mid_ratio:.3f} at {mid_idx}")
            best_ratio, best_index = mid_ratio, mid_idx

    # Line-number stripping fallback
    if best_ratio < threshold and any(_NUMBAR_RE.match(x) for x in old_content if x):
        log.debug("  💡 Attempting line-number stripping...")
        stripped_old = _strip_line_numbers_block(old_content)
        BUF = 40
        lo = max(0, start_hint - (BUF + 1))
        hi = min(len(target_lines), start_hint + len(stripped_old) + BUF)
        s_idx, s_ratio = _middle_out_best_window(target_lines, stripped_old, start_hint, lo, hi)
        if s_ratio < threshold:
            s_best_ratio, s_best_index = -1.0, -1
            m = min(len(stripped_old), len(target_lines))
            a_trim = [_normalize_quotes(x.strip()) for x in stripped_old[:m]]
            for i in range(len(target_lines) - m + 1):
                b_trim = [_normalize_quotes(x.strip()) for x in target_lines[i:i+m]]
                r = difflib.SequenceMatcher(None, a_trim, b_trim, autojunk=False).ratio()
                if r > s_best_ratio:
                    s_best_ratio, s_best_index = r, i
            s_idx, s_ratio = s_best_index, s_best_ratio
        if s_ratio >= threshold and s_idx != -1:
            log.debug(f"  ✅ Stripped-number match at {s_idx} with ratio={s_ratio:.3f}")
            best_ratio, best_index = s_ratio, s_idx

    if best_ratio < threshold:
        log.debug("\n  ⚠️  MATCH FAILURE - trying fallbacks...")

        # FALLBACK 1: Anchored whitespace-insensitive
        if old_content:
            anchor_line_stripped = old_content[0].strip()
            anchor_hits = [
                i for i, line in enumerate(target_lines) if line.strip() == anchor_line_stripped
            ]
            if anchor_hits:
                sorted_anchors = sorted(anchor_hits, key=lambda i: abs(i - start_hint))
                flat_old_block = _flatten_ws_outside_quotes("\n".join(old_content))
                for anchor_index in sorted_anchors:
                    for i in range(anchor_index, len(target_lines)):
                        current_consumed_lines = target_lines[anchor_index : i + 1]
                        flat_consumed = _flatten_ws_outside_quotes("\n".join(current_consumed_lines))
                        if not flat_old_block.startswith(flat_consumed):
                            break
                        if flat_consumed == flat_old_block:
                            log.debug(f"  ✅ Fallback: anchored match at {anchor_index}")
                            return {
                                "start_idx": anchor_index,
                                "end_idx": i + 1,
                                "replacement_lines": new_content,
                                "match_type": "anchored_fallback"
                            }

        # FALLBACK 2: Unique end-anchor
        if old_content and len(old_content) > 1:
            start_anchor_strip = old_content[0].strip()
            end_anchor_strip = next(
                (line.strip() for line in reversed(old_content) if line.strip()), None
            )
            if start_anchor_strip and end_anchor_strip:
                start_hits = [i for i, k in enumerate(target_lines) if k.strip() == start_anchor_strip]
                end_hits = [i for i, k in enumerate(target_lines) if k.strip() == end_anchor_strip]
                if len(end_hits) == 1:
                    end_line_idx = end_hits[0]
                    plausible_starts = [i for i in start_hits if i <= end_line_idx]
                    if plausible_starts:
                        start_line_idx = min(plausible_starts, key=lambda i: abs(i - start_hint))
                        log.debug(f"  ✅ Fallback: unique end-anchor [{start_line_idx}-{end_line_idx}]")
                        return {
                            "start_idx": start_line_idx,
                            "end_idx": end_line_idx + 1,
                            "replacement_lines": new_content,
                            "match_type": "end_anchor_fallback"
                        }

        # FINAL FALLBACK: Fuzzy window merge conflict
        log.debug("\n  💡 Creating merge conflict...")
        conflict_threshold = 0.25
        start_line = max(0, best_index)
        window_len = min(len(old_content), len(target_lines) - start_line) if target_lines else 0
        end_line = start_line + window_len

        if len(old_content) >= 2 and best_ratio >= conflict_threshold and window_len > 0:
            log.debug(f"  ✅ Conflict created at lines [{start_line}-{end_line - 1}]")
            original_block = target_lines[start_line:end_line]
            conflict_block = []
            conflict_block.append("<<<<<<< CURRENT CHANGE")
            conflict_block.extend(original_block)
            conflict_block.append("=======")
            conflict_block.extend(new_content)
            conflict_block.append(">>>>>>> INCOMING CHANGE (from patch)")
            return {
                "start_idx": start_line,
                "end_idx": end_line,
                "replacement_lines": conflict_block,
                "match_type": "merge_conflict"
            }

        if len(old_content) >= 2:
            raise PatchFailedError(
                f"Best fuzzy ratio ({best_ratio:.2f}) below conflict threshold ({conflict_threshold})."
            )
        else:
            raise PatchFailedError(f"Best match ratio {best_ratio:.2f} below threshold {threshold:.2f}.")

    # Fuzzy match succeeded
    if best_ratio >= threshold and best_index != -1:
        i = best_index

        # Validate alignment before using surgical reconstruction
        # For fuzzy matches, the window might not align with the hunk structure.
        # Only use surgical reconstruction if the first line of old_content actually matches.
        use_surgical = False
        if i + len(old_content) <= len(target_lines) and len(old_content) > 0:
            # Check if first old_content line matches the file at position i
            first_old = old_content[0].strip() if old_content else ""
            first_file = target_lines[i].strip() if i < len(target_lines) else ""
            
            # Verify alignment by checking if multiple lines match
            alignment_checks = min(3, len(old_content))  # Check up to 3 lines
            matches = 0
            for check_idx in range(alignment_checks):
                if i + check_idx >= len(target_lines):
                    break
                old_line = old_content[check_idx].strip()
                file_line = target_lines[i + check_idx].strip()
                if old_line == file_line:
                    matches += 1
            
            # Require at least 2 out of 3 lines to match for surgical reconstruction
            use_surgical = matches >= min(2, alignment_checks)
            
            log.debug(f"\n  FUZZY ALIGNMENT CHECK:")
            log.debug(f"    Checking {alignment_checks} lines, {matches} matched")
            log.debug(f"    First old: {repr(first_old[:60])}")
            log.debug(f"    First file[{i}]: {repr(first_file[:60])}")
            log.debug(f"    Use surgical: {use_surgical}")
        
        if use_surgical:
            # Calculate the ACTUAL number of file lines this hunk modifies
            # by counting non-'+' lines in the hunk (these map to old file content).
            # This is NOT the same as len(old_content) which excludes context lines!
            actual_old_file_lines = 0
            for ln in hunk["lines"]:
                if ln == "" or (ln and ln[0] in " -"):
                    # These lines exist in the old file
                    actual_old_file_lines += 1
                # '+' lines don't exist in old file, so don't count them
            
            # Sanity check: don't go beyond file bounds
            if i + actual_old_file_lines > len(target_lines):
                actual_old_file_lines = len(target_lines) - i
            
            log.debug(f"  Surgical segment: hunk has {len(hunk['lines'])} lines, "
                     f"mapping to {actual_old_file_lines} old file lines")
            
            log.debug(f"  ✅ Using surgical reconstruction for aligned fuzzy match")
            surg = _surgical_reconstruct_block(
                hunk["lines"],
                target_lines[i : i + actual_old_file_lines],
                old_content[0],
                target_lines[i]
            )
            log.debug(f"  ✅ Located fuzzy match at index {i} with surgical reconstruction")
            return {
                "start_idx": i,
                "end_idx": i + actual_old_file_lines,
                "replacement_lines": surg,
                "match_type": "fuzzy_surgical"
            }
        
        # Fallback: simple replacement with re-indentation
        log.debug(f"  ⚠️ Fuzzy match not aligned, using simple replacement")
        m = min(len(old_content), len(target_lines) - i)
        if new_content:
            new_adj = _reindent_relative(new_content, old_content[0], target_lines[i])
        else:
            new_adj = new_content
        log.debug(f"  ✅ Located fuzzy match at index {i} with simple replacement (m={m} lines)")
        return {
            "start_idx": i,
            "end_idx": i + m,
            "replacement_lines": new_adj,
            "match_type": "fuzzy_window"
        }

    raise PatchFailedError(f"Best match ratio {best_ratio:.2f} below threshold {threshold:.2f}.")


# ---------- main application ----------


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
    Apply a patch string to the provided content using a two-pass approach.

    Pass 1: Locate all hunks in the ORIGINAL file
    Pass 2: Apply changes from bottom to top (so line numbers stay valid)

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
    log.debug(f"Patch first 500 chars:\n{dedented_patch[:500]}")

    standard_match = re.search(
        r"^@@\s+-\d+(?:,\d+)?\s+\+\d+(?:,\d+)?\s+@@", dedented_patch, re.MULTILINE
    )
    log.debug(f"Standard diff pattern found: {bool(standard_match)}")

    if standard_match:
        hunks = _parse_patch_hunks(dedented_patch)
    else:
        hunks = _parse_simplified_patch_hunks(dedented_patch)
    
    if not hunks:
        raise PatchFailedError("no valid hunks")

    log.debug(f"\nParsed {len(hunks)} hunks using {'standard' if standard_match else 'simplified'} parser")

    original_lines = content.splitlines()
    log.debug(f"Target file has {len(original_lines)} lines")

    # PASS 1: Locate all hunks in the ORIGINAL file
    log.debug("\n" + "=" * 60)
    log.debug("PASS 1: LOCATING ALL HUNKS")
    log.debug("=" * 60)
    
    match_locations = []
    cursor = 0

    for i, h in enumerate(hunks):
        log.debug(f"\n{'=' * 60}\nLocating Hunk #{i + 1}/{len(hunks)}")
        lines = h.get("lines", [])
        pure_add = all(ln.startswith("+") or ln == "" for ln in lines) and any(
            ln.startswith("+") for ln in lines
        )

        if h.get("new_start"):
            header_hint = min(len(original_lines), max(0, int(h.get("new_start", 1)) - 1))
        else:
            header_hint = cursor

        start_hint = (
            header_hint
            if pure_add or not h.get("new_start")
            else max(0, min(len(original_lines), int(round(0.7 * cursor + 0.3 * header_hint))))
        )

        try:
            location = _locate_hunk_position(original_lines, h, threshold, start_hint, log=log)
            location["hunk_index"] = i  # Track original hunk order
            match_locations.append(location)
            # Update cursor for next hunk hint (simulate where we would be after applying)
            cursor = location["end_idx"] + len(location["replacement_lines"]) - (location["end_idx"] - location["start_idx"])
            log.debug(f"✓ Hunk #{i + 1} located. Match type: {location['match_type']}")
        except PatchFailedError as e:
            log.debug(f"✗ Hunk #{i + 1} FAILED: {e}")
            raise PatchFailedError(f"Failed to locate hunk #{i + 1}: {e}") from e

    # PASS 2: Apply all changes from bottom to top
    log.debug("\n" + "=" * 60)
    log.debug("PASS 2: APPLYING CHANGES (BOTTOM TO TOP)")
    log.debug("=" * 60)
    
    # Sort by start_idx in descending order (bottom to top)
    match_locations.sort(key=lambda loc: loc["start_idx"], reverse=True)
    
    log.debug("\nApplication order (sorted by position, highest first):")
    for i, loc in enumerate(match_locations):
        log.debug(f"  {i + 1}. Original Hunk #{loc['hunk_index'] + 1} at lines [{loc['start_idx']}:{loc['end_idx']}]")
    
    current_lines = original_lines[:]
    
    for i, location in enumerate(match_locations):
        log.debug(f"\n{'=' * 60}\nAPPLYING HUNK #{location['hunk_index'] + 1} (originally) - Apply order #{i + 1}/{len(match_locations)}")
        log.debug(f"  Position: [{location['start_idx']}:{location['end_idx']}]")
        
        # Debug: Show the current state at this position
        log.debug(f"  Current file state at position (lines {max(0, location['start_idx']-2)} to {min(len(current_lines), location['end_idx']+2)}):")
        for j in range(max(0, location['start_idx']-2), min(len(current_lines), location['end_idx']+2)):
            marker = " >>> " if location['start_idx'] <= j < location['end_idx'] else "     "
            log.debug(f"    {marker}[{j}] {repr(current_lines[j][:80])}")
        log.debug(f"  Match type: {location['match_type']}")
        log.debug(f"  Replacing {location['end_idx'] - location['start_idx']} lines with {len(location['replacement_lines'])} lines")
        
        # Show what we're replacing
        old_content = current_lines[location['start_idx']:location['end_idx']]
        log.debug(f"  OLD content (lines {location['start_idx']}-{location['end_idx'] - 1}):")
        for j, line in enumerate(old_content[:5]):  # Show first 5 lines
            log.debug(f"    {location['start_idx'] + j}: {repr(line[:80])}")
        if len(old_content) > 5:
            log.debug(f"    ... and {len(old_content) - 5} more lines")
        
        log.debug(f"  NEW content ({len(location['replacement_lines'])} lines):")
        for j, line in enumerate(location['replacement_lines'][:5]):  # Show first 5 lines
            log.debug(f"    {j}: {repr(line[:80])}")
        if len(location['replacement_lines']) > 5:
            log.debug(f"    ... and {len(location['replacement_lines']) - 5} more lines")
        
        current_lines = (
            current_lines[:location["start_idx"]] +
            location["replacement_lines"] +
            current_lines[location["end_idx"]:]
        )
        
        # Debug: Show the result after this hunk
        log.debug(f"  Result after applying (lines {max(0, location['start_idx']-1)} to {min(len(current_lines), location['start_idx']+len(location['replacement_lines'])+1)}):")
        for j in range(max(0, location['start_idx']-1), min(len(current_lines), location['start_idx']+len(location['replacement_lines'])+1)):
            marker = " NEW " if location['start_idx'] <= j < location['start_idx'] + len(location['replacement_lines']) else "     "
            log.debug(f"    {marker}[{j}] {repr(current_lines[j][:80])}")
        
        log.debug(f"  ✅ Applied. File now has {len(current_lines)} lines (delta: {len(current_lines) - len(original_lines)})")
    
    log.debug("\n" + "=" * 60)
    log.debug("PATCH APPLICATION COMPLETE")
    log.debug("=" * 60)

    return (eol.join(current_lines)) + (eol if had_trailing_nl else "")


def fuzzy_patch_partial(
    content: str, patch_str: str, threshold: float = 0.6, *, logger=None, log: bool = False
):
    """
    Best-effort patching using two-pass approach:
      - locates all hunks it can in the original file
      - applies them from bottom to top
      - returns (new_text, applied_indices, failed) where failed is a list of
        {index, error, lead_ctx, tail_ctx, old_content, new_content}
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
    
    # PASS 1: Try to locate all hunks
    match_locations = []
    applied = []
    failed = []
    cursor = 0
    
    for i, h in enumerate(hunks):
        header_hint = min(len(original_lines), max(0, int(h.get("new_start", 1)) - 1))
        lines = h.get("lines", [])
        pure_add = all(ln.startswith("+") or ln == "" for ln in lines) and any(
            ln.startswith("+") for ln in lines
        )
        start_hint = (
            header_hint
            if pure_add
            else max(0, min(len(original_lines), int(round(0.7 * cursor + 0.3 * header_hint))))
        )
        try:
            location = _locate_hunk_position(original_lines, h, threshold, start_hint, log=log)
            location["hunk_index"] = i  # Track original hunk order
            match_locations.append((i, location))
            applied.append(i)
            cursor = location["end_idx"] + len(location["replacement_lines"]) - (location["end_idx"] - location["start_idx"])
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
    
    # PASS 2: Apply successfully located hunks from bottom to top
    match_locations.sort(key=lambda x: x[1]["start_idx"], reverse=True)
    current_lines = original_lines[:]
    
    for hunk_idx, location in match_locations:
        current_lines = (
            current_lines[:location["start_idx"]] +
            location["replacement_lines"] +
            current_lines[location["end_idx"]:]
        )
    
    new_text = eol.join(current_lines) + (eol if had_trailing_nl else "")
    return new_text, applied, failed