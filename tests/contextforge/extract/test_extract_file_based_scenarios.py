import json
import os
import pytest
import difflib
from contextforge.extract import extract_blocks_from_text as cf_extract_blocks


# Define the directory containing the test files.
# The path is constructed relative to the current test file's location.
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
TEST_FILES_DIR = os.path.join(TESTS_DIR, "..", "..", "files", "extract")

# Discover test files in the directory
test_files = []
if os.path.isdir(TEST_FILES_DIR):
    test_files = [f for f in os.listdir(TEST_FILES_DIR) if f.endswith(".test.txt")]

print(test_files)


def _write_result_to_file(directory, original_filename, actual_content):
    """Writes the actual result to a file for easy comparison."""
    output_filename = f"{original_filename}.actual"
    output_path = os.path.join(directory, output_filename)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(actual_content)
    return output_path


@pytest.mark.parametrize("filename", test_files)
def test_extract_from_file_scenario(filename):
    """
    Reads a test file, splits it into a diff and an expected hunk count,
    then counts the hunks and asserts the outcome.
    If the assertion fails, it writes the actual hunk count to a file.
    """
    file_path = os.path.join(TEST_FILES_DIR, filename)

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Split the content into initial, test, and result sections
    try:
        diff_raw, expected_raw = content.split("\n# === RESULT ===\n", 1)
    except ValueError:
        pytest.fail(
            f"Test file '{filename}' is not in the expected format of "
            "CONTENT\\n# === TEST ===\\nDIFF\\n# === RESULT ===\\nEXPECTED"
        )

    # Trim whitespace from each section for robustness
    diff_text = diff_raw.strip()
    expected_result = expected_raw.strip()

    # In this test, we are counting "hunks"
    result = list(cf_extract_blocks(diff_text))
    actual_hunk_count = len(result)
    actual_result = str(actual_hunk_count)

    # If the results don't match, write the actual result to a file and generate a diff
    if actual_result != expected_result:
        result = json.dumps(result, indent=2)

        output_path = _write_result_to_file(TEST_FILES_DIR, filename, result)

        diff = difflib.unified_diff(
            expected_result.splitlines(keepends=True),
            actual_result.splitlines(keepends=True),
            fromfile=f"{filename} (Expected Hunk Count)",
            tofile=f"{filename} (Actual Hunk Count)",
        )
        diff_output = "".join(diff)
        pytest.fail(
            f"Hunk count for '{filename}' does not match the expected output.\n"
            f"Actual result has been written to: {output_path}\n\n"
            f"Visual Difference:\n{diff_output}"
        )
