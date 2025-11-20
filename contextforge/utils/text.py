import re

def cleanup_llm_output(content: str) -> str:
    """
    Removes common LLM artifacts like <think> blocks and markdown fences.
    Returns the cleaned content string.
    """
    if not content:
        return ""
    
    # Remove <think> blocks
    content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL | re.MULTILINE)
    
    # Remove markdown code fences if they wrap the entire content
    # Matches: ```[optional lang]\n(content)\n```
    fence_match = re.match(r'^\s*```[a-zA-Z0-9-]*[ \t]*\n(.*?)\n\s*```\s*$', content, flags=re.DOTALL | re.MULTILINE)
    if fence_match:
        content = fence_match.group(1).strip()
        
    return content