import pytest
import textwrap
from contextforge.commit.patch import patch_text

# Baseline original code and patch for all tests
BASE_CODE = textwrap.dedent("""
    import os

    def process_data(data, config=None):
        \"\"\"
        Processes the given data.
        \"\"\"
        if config is None:
            config = {}

        # Initial validation
        if not data:
            print("Warning: No data provided.")
            return None

        # Core processing logic
        processed_data = []
        for item in data:
            value = item * 2
            processed_data.append(value)

        print("Processing complete.")
        return processed_data

    def main():
        items = [1, 2, 3, 4]
        result = process_data(items)
        print(f"Result: {result}")

    if __name__ == "__main__":
        main()
""")

PATCH = textwrap.dedent("""
    @@
        # Core processing logic
        processed_data = []
        for item in data:
-           value = item * 2
-           processed_data.append(value)
+           if item > 1:
+               value = item * config.get('multiplier', 2)
+               processed_data.append(value)

        print("Processing complete.")
        return processed_data
""")

# This is the state we expect the patched section to be in,
# which we can then insert into the modified original code for each test.
EXPECTED_PATCHED_LOGIC = textwrap.dedent("""
        # Core processing logic
        processed_data = []
        for item in data:
           if item > 1:
               value = item * config.get('multiplier', 2)
               processed_data.append(value)

        print("Processing complete.")
""")

def get_target_block(code):
    """Helper to extract the block of code the patch targets."""
    return textwrap.dedent("""
        # Core processing logic
        processed_data = []
        for item in data:
            value = item * 2
            processed_data.append(value)

        print("Processing complete.")
    """)

def test_clean_apply():
    """Test 1: The patch applies cleanly to the original code."""
    result = patch_text(BASE_CODE, PATCH, log=True)
    
    # Reconstruct the fully expected result for clarity
    expected_result = BASE_CODE.replace(get_target_block(BASE_CODE), EXPECTED_PATCHED_LOGIC)

    assert result.strip() == expected_result.strip()

def test_apply_with_whitespace_changes():
    """Test 2: The patch applies despite meaningless whitespace changes."""
    modified_code = BASE_CODE.replace('    for item in data:', '    for item in data:    ')
    modified_code = modified_code.replace('        return None', '\n        return None')

    result = patch_text(modified_code, PATCH, log=True)

    # The expected result needs to have the same whitespace changes outside the patch area
    expected = modified_code.replace(get_target_block(modified_code), EXPECTED_PATCHED_LOGIC)
    
    assert result.strip() == expected.strip()

def test_apply_with_comment_changes():
    """Test 3: The patch applies despite new comments being added inside context."""
    modified_code = BASE_CODE.replace(
        '# Core processing logic',
        '# Core processing logic\\n        # This is where the magic happens!'
    )
    result = patch_text(modified_code, PATCH, log=True)

    # The expected result should contain the new comment AND the patched logic.
    target_block = get_target_block(modified_code).replace(
        '# Core processing logic',
        '# Core processing logic\\n        # This is where the magic happens!'
    )
    expected_logic = EXPECTED_PATCHED_LOGIC.replace(
        '# Core processing logic',
        '# Core processing logic\\n        # This is where the magic happens!'
    )
    expected = modified_code.replace(target_block, expected_logic)

    assert result.strip() == expected.strip()

def test_apply_with_unrelated_code_changes():
    """Test 4: The patch applies even if unrelated code (the main function) is changed."""
    modified_code = BASE_CODE.replace(
        'items = [1, 2, 3, 4]',
        'items = [10, 20, 30, 40] # Changed data'
    )
    result = patch_text(modified_code, PATCH, log=True)

    expected = modified_code.replace(get_target_block(modified_code), EXPECTED_PATCHED_LOGIC)
    assert result.strip() == expected.strip()

def test_apply_with_context_variable_renamed():
    """
    Test 5: The patch applies even if a variable in the context is renamed.
    This tests the robustness of surgical patching, which should preserve the
    file's version of context lines while applying the + and - changes.
    """
    # Renaming 'processed_data' to 'results_list'
    modified_code = BASE_CODE.replace('processed_data', 'results_list')
    result = patch_text(modified_code, PATCH, log=True)

    # The surgical application should keep `results_list = []` from the modified file,
    # drop the old lines, and add the new lines from the patch, which hardcode
    # `processed_data`. The resulting code will have a NameError, but the test's
    # goal is to confirm the patch *applies* correctly, which it does.
    expected_logic = textwrap.dedent("""
        # Core processing logic
        results_list = []
        for item in data:
           if item > 1:
               value = item * config.get('multiplier', 2)
               processed_data.append(value)

        print("Processing complete.")
    """)
    
    target_block = get_target_block(modified_code).replace('processed_data', 'results_list')
    expected_result = modified_code.replace(target_block, expected_logic)

    assert result.strip() == expected_result.strip()

def test_apply_with_target_code_moved():
    """Test 6: The patch applies even if the entire function is moved."""
    
    # Find the function block
    func_start = BASE_CODE.find("def process_data")
    func_end = BASE_CODE.find("def main()")
    func_block = BASE_CODE[func_start:func_end]
    
    # Create code without the function
    code_without_func = BASE_CODE[:func_start] + BASE_CODE[func_end:]
    
    # Move function to the end
    modified_code = code_without_func.strip() + "\\n\\n" + func_block.strip()
    
    result = patch_text(modified_code, PATCH, log=True)

    # Recreate expected result with moved function
    expected_result_base = BASE_CODE.replace(get_target_block(BASE_CODE), EXPECTED_PATCHED_LOGIC)
    expected_func_start = expected_result_base.find("def process_data")
    expected_func_end = expected_result_base.find("def main()")
    expected_func_block = expected_result_base[expected_func_start:expected_func_end]
    expected_without_func = expected_result_base[:expected_func_start] + expected_result_base[expected_func_end:]

    expected_moved = expected_without_func.strip() + "\\n\\n" + expected_func_block.strip()

    assert result.strip() == expected_moved.strip()

def test_apply_with_significant_drift_in_context():
    """Test 7: The patch applies despite significant changes to context lines."""
    modified_code = BASE_CODE.replace(
        'for item in data:',
        'for item in data:  # Loop over all items\\n        if not isinstance(item, (int, float)):\\n            continue'
    )
    modified_code = modified_code.replace(
        'print("Processing complete.")',
        'print(f"Processed {len(processed_data)} items.")'
    )

    result = patch_text(modified_code, PATCH, log=True)

    target_block = get_target_block(modified_code).replace(
        'for item in data:',
        'for item in data:  # Loop over all items\\n        if not isinstance(item, (int, float)):\\n            continue'
    ).replace(
        'print("Processing complete.")',
        'print(f"Processed {len(processed_data)} items.")'
    )

    expected_logic = EXPECTED_PATCHED_LOGIC.replace(
        'for item in data:',
        'for item in data:  # Loop over all items\\n        if not isinstance(item, (int, float)):\\n            continue'
    ).replace(
        'print("Processing complete.")',
        'print(f"Processed {len(processed_data)} items.")'
    )
    
    expected = modified_code.replace(target_block, expected_logic)
    
    assert result.strip() == expected.strip()