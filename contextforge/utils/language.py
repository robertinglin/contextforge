# contextforge/utils/language.py
def _get_language_from_path(file_path: str) -> str:
    """Gets a Monaco language identifier from a file path extension."""
    extension = file_path.split(".")[-1].lower()
    lang_map = {
        "js": "javascript",
        "jsx": "javascript",
        "ts": "typescript",
        "tsx": "typescript",
        "py": "python",
        "css": "css",
        "html": "html",
        "json": "json",
        "md": "markdown",
        "java": "java",
        "cs": "csharp",
        "cpp": "cpp",
        "h": "cpp",
        "sh": "shell",
        "go": "go",
        "rs": "rust",
    }
    return lang_map.get(extension, "plaintext")
