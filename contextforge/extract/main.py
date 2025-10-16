# contextforge/extract/main.py
import pprint
import re
from typing import Any, Dict, List, Optional

from .diffs import _looks_like_diff, _extract_custom_patch_blocks, _split_multi_file_diff
from .extract import extract_all_blocks_from_text
from .metadata import (detect_deletion_from_diff, detect_rename_from_diff,
                       extract_file_info_from_context_and_code)
from ..utils.parsing import _try_parse_comment_header



def extract_blocks_from_text(markdown_content: str) -> List[Dict[str, Any]]:
    """
    Unified extractor returning a list of dictionaries ordered by their
    occurrence in the input (v0.1.0 schema).

    Common keys (both types):
      - type: "diff" | "file"
      - start: int  (start offset in source text)
      - end:   int  (end offset in source text)
      - code:  str  (the extracted body without the fences)

    Diff blocks (type == "diff") add:
      - language: "diff"
      - file_path: Optional[str]  (best-effort from headers)
      - context:   Optional[str]  (lines before opener, if available)

    File blocks (type == "file") add:
      - language: str  (e.g., "python", "md", "plain")
      - file_path: Optional[str]  (best-effort from nearby text)

    Notes:
      - Only explicit ```diff fences are treated as diffs (for stable ordering).
      - Adjacent and nested fences are handled by the underlying extractors.
    """
    # Step 1: Extract all fenced code blocks
    all_blocks = extract_all_blocks_from_text(markdown_content)
        
    # Step 2: Extract custom patch blocks (*** Begin Patch / *** End Patch)
    custom_patch_blocks = _extract_custom_patch_blocks(markdown_content)
    
    # Step 3: Process and classify each block
    results: List[Dict[str, Any]] = []
    
    # Process custom patch blocks first
    for blk in custom_patch_blocks:
        results.append({
            "type": "diff",
            "language": "diff",
            "start": blk.get("start", 0),
            "end": blk.get("end", 0),
            "code": blk.get("code", ""),
            "file_path": blk.get("file_path") or None,
            "context": blk.get("context"),
        })
    
    # Process regular fenced blocks
    for blk in all_blocks:
        language = blk.get("language", "plain")
        code = blk.get("code", "")
        context = blk.get("context", "")
        file_path = blk.get("file_path")

        # High-priority checks based on diff content for rename/delete
        rename_info = detect_rename_from_diff(code)
        if rename_info:
            results.append(
                {
                    "type": "rename",
                    "from_path": rename_info["from_path"],
                    "to_path": rename_info["to_path"],
                    "start": blk.get("start", 0),
                    "end": blk.get("end", 0),
                    "code": code,  # Keep the diff for logging/inspection
                }
            )
            continue

        deleted_path = detect_deletion_from_diff(code)
        if deleted_path:
            results.append(
                {"type": "delete", "file_path": deleted_path, "start": blk.get("start", 0), "end": blk.get("end", 0)}
            )
            continue

        # Generic classification: is it a diff or a file?
        is_diff = False
        diff_file_path = None
        
        # Explicit diff/patch language tag
        if language in ("diff", "patch"):
            is_diff = True
            # Try to extract file path from diff headers if not already hinted
            if not file_path:
                path_match = re.search(r"^\+\+\+ b/(\S+)", code, re.MULTILINE)
                if path_match:
                    diff_file_path = path_match.group(1).strip().split("\t")[0].replace("\\", "/")
        # Check if content looks like a diff even without explicit tag
        elif _looks_like_diff(code):
            # Use extract_file_info_from_context_and_code to determine if it's a diff
            info = extract_file_info_from_context_and_code(context, code, language)
            if info and info.get("change_type") == "diff":
                is_diff = True
                diff_file_path = info.get("file_path")
        
        if is_diff:
            # Split multi-file diffs if needed
            file_chunks = _split_multi_file_diff(code)
            if not file_chunks:
                file_chunks = [(diff_file_path or file_path or "", code)]
            
            for chunk_path, chunk_text in file_chunks:
                if not chunk_text.strip():
                    continue
                results.append({
                    "type": "diff",
                    "language": "diff",
                    "start": blk.get("start", 0),
                    "end": blk.get("end", 0),
                    "code": chunk_text.strip("\n"),
                    "file_path": chunk_path or diff_file_path or file_path or None,
                    "context": context,
                })
        else:
            # It's a regular file block. Check for commented path.
            final_code = code
            final_file_path = file_path

            comment_header = _try_parse_comment_header(code)
            if comment_header:
                final_file_path = comment_header["file_path"]
                final_code = comment_header["code"]

            results.append({
                "type": "file",
                "language": language,
                "start": blk.get("start", 0),
                "end": blk.get("end", 0),
                "code": final_code,
                "file_path": final_file_path or None,
            })
    
    # If we didn't find any fenced blocks but the whole text looks like a diff,
    # emit a single diff block (raw diff fallback).
    if not results and _looks_like_diff(markdown_content):
        results.append({
            "type": "diff",
            "language": "diff",
            "start": 0,
            "end": len(markdown_content),
            "code": markdown_content,
            "file_path": None,
            "context": None,
        })
    
    # If the same file path is provided multiple times, use the last one based on start position.
    # This handles cases where a model refines its answer in a single response.
    latest_blocks_by_path = {}
    other_blocks = []
    for block in results:
        file_path = block.get("file_path")
        if file_path:
            if file_path not in latest_blocks_by_path or block["start"] > latest_blocks_by_path[file_path]["start"]:
                latest_blocks_by_path[file_path] = block
        else:
            other_blocks.append(block)

    # Combine the deduplicated blocks with the others and sort to restore order.
    deduped = other_blocks + list(latest_blocks_by_path.values())
    
    # Sort by start position for stable ordering
    return sorted(deduped, key=lambda b: b["start"])


if __name__ == "__main__":
    test = """
Of course. It's a common issue for single-page applications where `index.html` gets cached by the browser, preventing new frontend builds from being loaded.

To solve this, we can instruct browsers to never cache the `index.html` file by adding a `Cache-Control` header to the HTTP response when it's served. I'll modify your FastAPI backend to include this header for `index.html`.

Here are the changes for `backend/app/main.py`:

```diff
--- a/backend/app/main.py
+++ b/backend/app/main.py
@@ -201,7 +201,7 @@
     if not os.path.exists(index_path):
         raise HTTPException(status_code=404, detail="Frontend application not found. Please run 'npm run build'.")
     
-    return FileResponse(index_path)
+    return FileResponse(index_path, headers={"Cache-Control": "no-store, no-cache, must-revalidate"})
 
 @app.get("/{full_path:path}", tags=["Frontend"])
 async def serve_frontend_catch_all(request: Request, full_path: str):
@@ -218,4 +218,4 @@
     if not os.path.exists(index_path):
         raise HTTPException(status_code=404, detail="Frontend application not found. Please run 'npm run build'.")
     
-    return FileResponse(index_path)
+    return FileResponse(index_path, headers={"Cache-Control": "no-store, no-cache, must-revalidate"})

```

I've updated the two routes that serve your `index.html` to include headers that prevent caching. This ensures that your users will always get the latest version of the frontend after you deploy a new build. Other static assets (like hashed JS and CSS files) will still be cached by the browser, as they should be.

    """
    
    result = extract_blocks_from_text(test)
    
    pprint.pprint(result)