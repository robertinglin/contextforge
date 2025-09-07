from contextforge.extract.metadata import detect_new_files, extract_file_info_from_context_and_code


def test_extract_file_info_patterns():
    # Backticked filename
    info = extract_file_info_from_context_and_code("`main.py`", "print('hi')", "python")
    assert info["file_path"] == "main.py"
    assert info["change_type"] == "full_replacement"

    # "File: path" header
    info = extract_file_info_from_context_and_code("File: src/app.js", "...", "javascript")
    assert info["file_path"] == "src/app.js"

    # Diff indicators
    diff_code = (
        "--- a/src/style.css\n+++ b/src/style.css\n@@ -1,1 +1,1 @@\n-body {}\n+body { color: red; }"
    )
    info = extract_file_info_from_context_and_code("Changes for css", diff_code, "css")
    assert info["file_path"] == "src/style.css"
    assert info["change_type"] == "diff"

    # Truncation marker
    code_with_truncation = "line 1\n# ...\nline 100"
    info = extract_file_info_from_context_and_code("`file.txt`", code_with_truncation, "plaintext")
    assert info["change_type"] == "full_replacement"


def test_detect_new_files():
    # Git new file mode
    md_content_1 = """
    ```diff
    diff --git a/new_file.txt b/new_file.txt
    new file mode 100644
    index 0000000..e69de29
    --- /dev/null
    +++ b/new_file.txt
    ```
    """
    assert detect_new_files(md_content_1) == ["new_file.txt"]

    # /dev/null diff
    md_content_2 = """
    ```diff
    --- /dev/null
    +++ b/another_new.go
    @@ -0,0 +1,5 @@
    +package main
    ```
    """
    assert detect_new_files(md_content_2) == ["another_new.go"]

    # Multiple files, dedup and sorting
    md_content_3 = """
    ```diff
    --- /dev/null
    +++ b/z.txt
    ```
    ```diff
    diff --git a/a.txt b/a.txt
    new file mode 100644
    --- /dev/null
    +++ b/a.txt
    ```
    ```diff
    --- /dev/null
    +++ b/z.txt
    ```
    """
    assert detect_new_files(md_content_3) == ["a.txt", "z.txt"]
