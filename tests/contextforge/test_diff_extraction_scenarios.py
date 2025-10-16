import os
import pytest
from contextforge.extract import extract_diffs_from_text

# Define the directory containing the test files, relative to this test's location.
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
TEST_FILES_DIR = os.path.join(TESTS_DIR, '..', 'files')

# Discover all scenario files
test_files = []
if os.path.isdir(TEST_FILES_DIR):
    test_files = [f for f in os.listdir(TEST_FILES_DIR) if f.endswith('.test.txt')]


@pytest.mark.parametrize("filename", test_files)
def test_diff_extraction_from_scenario(filename):
    """
    Reads the 'TEST' section from a scenario file and verifies that the diff
    extraction logic correctly identifies and parses the raw diff content.
    """
    file_path = os.path.join(TEST_FILES_DIR, filename)

    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Split the content to isolate the raw diff text from the 'TEST' section
    try:
        _, remaining = content.split('\n# === TEST ===\n', 1)
        diff_raw, _ = remaining.split('\n# === RESULT ===\n', 1)
    except ValueError:
        pytest.fail(
            f"Test file '{filename}' is not in the expected format of "
            "CONTENT\\n# === TEST ===\\nDIFF\\n# === RESULT ===\\nEXPECTED"
        )

    # The ground truth diff, stripped of surrounding whitespace
    expected_diff_content = diff_raw.strip()

    # Run the extraction function on the raw diff text. The function should
    # be able to handle a raw, unfenced diff string as input.
    extracted_blocks = extract_diffs_from_text(expected_diff_content)

    # 1. Assert that exactly one diff block was found
    assert len(extracted_blocks) == 1, \
        f"Expected 1 diff block to be extracted from '{filename}', but found {len(extracted_blocks)}."

    extracted_block = extracted_blocks[0]

    # 2. Assert that the block is correctly identified as a 'diff'
    assert extracted_block['lang'] == 'diff', \
        f"Expected block language to be 'diff', but got '{extracted_block['lang']}' in '{filename}'."

    # 3. Compare the extracted code content with the original, stripped content
    extracted_content = extracted_block['code'].strip()
    assert extracted_content == expected_diff_content, \
        f"Extracted diff content does not match the source for '{filename}'."