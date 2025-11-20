from contextforge.utils.text import cleanup_llm_output

def test_cleanup_llm_output_removes_think_blocks():
    content = "<think>Should I use recursion? No.</think>def factorial(n):..."
    assert cleanup_llm_output(content) == "def factorial(n):..."

def test_cleanup_llm_output_multiline_think():
    content = "<think>\nStep 1\nStep 2\n</think>\nResult"
    assert cleanup_llm_output(content) == "\nResult"

def test_cleanup_llm_output_removes_markdown_fences():
    content = "```python\nprint('hello')\n```"
    assert cleanup_llm_output(content) == "print('hello')"

def test_cleanup_llm_output_removes_fences_with_spaces():
    content = " ``` \ncode\n ``` "
    assert cleanup_llm_output(content) == "code"

def test_cleanup_llm_output_handles_combined_artifacts():
    content = "<think>...</think>\n```javascript\nconst x = 1;\n```"
    assert cleanup_llm_output(content) == "const x = 1;"

def test_cleanup_llm_output_noop_on_clean_text():
    content = "Just normal text."
    assert cleanup_llm_output(content) == "Just normal text."

def test_cleanup_llm_output_none_safe():
    assert cleanup_llm_output(None) == ""