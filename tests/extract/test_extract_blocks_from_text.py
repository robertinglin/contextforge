from contextforge.extract import extract_blocks_from_text as cf_extract_blocks

# =============================
# Tests (concise)
# =============================

def _assert(cond: bool, msg: str = "assertion failed") -> None:
    if not cond:
        raise AssertionError(msg)


def test_readme_block_extraction() -> None:
    s = ("""Of course. Here is an updated `README.md` that better reflects the project's features and structure.\n"""
"""\n"""
"""Here is a replacement for `README.md`:\n"""
"""File: `README.md`\n"""
"""```md\n"""
"""# Interactive Drawing and Learning Platform\n"""
"""\n"""
"""This is a feature-rich web-based drawing application designed to help users learn and practice drawing skills. It combines a powerful drawing canvas with structured lessons, an advanced brush engine, and a unique branching history system for a non-destructive workflow.\n"""
"""\n"""
"""## Features\n"""
"""\n"""
"""-   **Interactive Drawing Canvas**: A responsive and intuitive canvas for drawing with mouse or stylus, supporting pressure sensitivity.\n"""
"""-   **Structured Learning System**:\n"""
"""    -   Organized lessons for **Beginner**, **Intermediate**, and **Advanced** skill levels.\n"""
"""    -   Lessons cover topics from basic shapes and line control to advanced anatomy and lighting.\n"""
"""    -   Side-by-side view of lesson content and drawing area.\n"""
"""-   **Advanced Brush Engine**:\n"""
"""    -   Multiple brush types: Standard, Stamp, Airbrush, Texture, and Computed brushes.\n"""
"""    -   Import custom Photoshop (`.abr`) brushes by simply dragging and dropping them onto the brush selector.\n"""
"""    -   **Brush Editor**: Fine-tune brush properties like size, opacity, blend mode, roundness, angle, and hardness.\n"""
"""-   **Powerful Layer System**:\n"""
"""    -   Work non-destructively with multiple layers.\n"""
"""    -   Support for various **blend modes**.\n"""
"""    -   **Branching History**: An innovative undo/redo system that visualizes your creative process as a branching tree. You can revert to any point in history and create new branches without losing previous work.\n"""
"""    -   **Bubble Graph**: A visual representation of your drawing's branching history.\n"""
"""-   **Modern UI/UX**:\n"""
"""    -   **Draggable and Resizable Panels**: Customize your workspace by moving and resizing tool panels (Layers, Brush Selector, Color Picker).\n"""
"""    -   Built with **shadcn/ui** and **Radix UI** for accessible and high-quality components.\n"""
"""    -   **Styled Sliders** for a better user experience when adjusting brush properties.\n"""
"""-   **Color Tools**:\n"""
"""    -   Advanced HSL color selector.\n"""
"""    -   Fill tool with tolerance and contiguous options.\n"""
"""    -   Color picker (eyedropper) to sample colors from the canvas.\n"""
"""    -   Automatically saved color history for quick access.\n"""
"""-   **Deployment**:\n"""
"""    -   Continuous deployment to GitHub Pages using GitHub Actions.\n""" 
"""\n"""
"""## Tech Stack\n"""
"""\n"""
"""-   **Framework**: [React](https://react.dev/) + [Vite](https://vitejs.dev/)\n"""
"""-   **Styling**: [Tailwind CSS](https://tailwindcss.com/)\n"""
"""-   **UI Components**: [shadcn/ui](https://ui.shadcn.com/)\n"""
"""-   **Drawing Core**:\n"""
"""    -   [perfect-freehand](https://github.com/steveruizok/perfect-freehand) for smooth, pressure-sensitive strokes.\n"""
"""-   **Brush System**:\n"""
"""    -   [abr-js](https://github.com/gomonuk/abr-js) for parsing Photoshop `.abr` files.\n"""
"""-   **Local Storage**:\n"""
"""    -   [idb](https://github.com/jakearchibald/idb) for storing custom brushes in IndexedDB.\n"""
"""-   **Linting**: [ESLint](https://eslint.org/)\n"""
"""\n"""
"""## Getting Started\n"""
"""\n"""
"""Follow these instructions to get a copy of the project up and running on your local machine for development and testing purposes.\n"""
"""\n"""
"""### Prerequisites\n"""
"""\n"""
"""-   Node.js (v18 or later recommended)\n"""
"""-   npm, pnpm, or yarn\n"""
"""\n"""
"""### Installation\n"""
"""\n"""
"""1.  Clone the repository:\n"""
"""    ```sh\n"""
"""    git clone https://github.com/your-username/draw-practice-3.git\n"""
"""    cd draw-practice-3\n"""
"""    ```\n""" 
"""\n"""
"""2.  Install NPM packages:\n"""
"""    ```sh\n"""
"""    npm install\n"""
"""    ```\n""" 
"""\n"""
"""### Running the Application\n"""
"""\n"""
"""To start the Vite development server, run:\n"""
"""```sh\n"""
"""npm run dev\n"""
"""```\n""" 
"""Open [http://localhost:5173](http://localhost:5173) (or the port shown in your terminal) to view it in the browser.\n"""
"""\n"""
"""## Available Scripts\n"""
"""\n"""
"""In the project directory, you can run:\n"""
"""\n"""
"""-   `npm run dev`: Runs the app in development mode with HMR.\n"""
"""-   `npm run build`: Builds the app for production to the `dist` folder.\n"""
"""-   `npm run lint`: Lints the project files using ESLint.\n"""
"""-   `npm run preview`: Serves the production build from the `dist` folder for previewing.\n"""
"""\n"""
"""## Deployment\n"""
"""\n"""
"""This project is configured for automatic deployment to GitHub Pages. The `.github/workflows/deploy.yml` workflow triggers on every push to the `main` branch. It builds the project and deploys the contents of the `dist` directory.\n"""
"""\n"""
"""The `vite.config.js` is configured with `base: '/draw-practice/'` to ensure assets are loaded correctly on GitHub Pages.\n"""
"""```""")
    blocks = cf_extract_blocks(s)
    files = [b for b in blocks if b.get("type") == "file"]
    _assert(len(files) == 1, f"expected 1 file block, got {len(files)}")
    f0 = files[0]
    _assert(f0["language"] in ("md", "markdown"), f"unexpected language: {f0['language']}")
    _assert(f0["file_path"].lower() == "readme.md", f"path hint failed: {f0['file_path']}")
    assert any(cmd in f0["code"] for cmd in ("git clone", "npm install", "npm run dev")), \
        "inner fenced content missing"


def test_nested_fences_bottom_up() -> None:
    s = (
        "This is a new file: `src/file.md`\n"
        "```md\n"
        "Here is code:\n"
        "```ts\n"
        "export const x = 1;\n"
        "```\n"
        "More text.\n"
        "```\n"
    )
    blocks = cf_extract_blocks(s)
    # Should yield exactly one file block (the outer md), and include the inner ts as raw body text.
    files = [b for b in blocks if b.get("type") == "file"]
    _assert(len(files) == 1, f"expected 1 file block, got {len(files)}")
    assert files[0]["file_path"].endswith("src/file.md")
    print(files[0])
    body = s[files[0]["start"]:files[0]["end"]]
    _assert("export const x = 1;" in body, "inner ts content should be preserved in body")


def test_multiple_new_files_stay_separate() -> None:
    s = (
        "This is a new file: `src/pages/A.tsx`\n"
        "```tsx\nexport default function A(){return <h1>A</h1>}\n```\n\n"
        "This is a new file: `src/pages/B.tsx`\n"
        "```tsx\nexport default function B(){return <h1>B</h1>}\n```"
    )
    blocks = cf_extract_blocks(s)
    files = [b for b in blocks if b.get("type") == "file"]
    _assert(len(files) == 2, f"expected 2 file blocks, got {len(files)}")
    paths = [f["file_path"] for f in files]
    _assert(paths[0].endswith("A.tsx") and paths[1].endswith("B.tsx"), f"unexpected paths: {paths}")
    _assert(abs(files[0]["start"] - files[0]["end"]) > len("export default function A()..."),
            "block span includes the outer fence")


def test_general_run_tests() -> None:
    # README block test
    test_readme_block_extraction()

    # Nested fences handled by bottom-up matcher
    test_nested_fences_bottom_up()

    # Adjacent 'new file' sections do not merge
    test_multiple_new_files_stay_separate()


        # README block test
    test_readme_block_extraction()

    # Ordering file then diff (regression)
    s_order = (
        "file: a.py\n"
        "```python\nprint('a')\n```\n"
        "middle\n"
        "```diff\n--- a/b\n+++ b/b\n@@ -1 +1 @@\n-x\n+y\n```\n"
    )
    blocks = cf_extract_blocks(s_order)
    types = [b.get("type") for b in blocks]
    _assert(types == ["file", "diff"], f"ordering failed: {types}")

        # 1) Nested fences inside a Markdown file block (your case)
    s_nested = (
        "Here is a replacement for `README.md`:\n"
        "File: `README.md`\n"
        "```md\n"
        "# Title\n"
        "\n"
        "Some prose.\n"
        "\n"
        "```sh\n"
        "echo hello\n"
        "```\n"
        "\n"
        "```ts\n"
        "export const x = 1;\n"
        "```\n"
        "\n"
        "Final line.\n"
        "```\n"
    )
    blocks_nested = cf_extract_blocks(s_nested)
    files_nested = [b for b in blocks_nested if b.get("type") == "file"]
    _assert(len(files_nested) == 1, "should extract one outer file block despite inner fences")
    _assert(files_nested[0]["language"] in ("md", "markdown", "plain"), "language not detected for md")
    _assert("echo hello" in files_nested[0]["code"] and "export const x" in files_nested[0]["code"],
            "inner fences not preserved inside outer file block")

    # 2) Ordering with file then diff
    s7 = (
        "file: a.py\n"
        "```python\n"
        "print('a')\n"
        "```\n"
        "middle\n"
        "```diff\n"
        "--- a/b\n"
        "+++ b/b\n"
        "@@ -1 +1 @@\n"
        "-x\n"
        "+y\n"
        "```\n"
        "tail\n"
    )
    blocks7 = cf_extract_blocks(s7)
    types7 = [b.get("type") for b in blocks7]
    _assert(types7 == ["file", "diff"], "ordering/file+diff coexistence failed")

    # 3) Long fences and same-line close for file block
    s_long = (
        "create: lib/main.go\n"
        "todo\n"
        "````go\n"
        "package main\n"
        "func main() { println(\"ok\") }````\n"
    )
    blocks_long = cf_extract_blocks(s_long)
    files_long = [b for b in blocks_long if b.get("type") == "file"]
    _assert(len(files_long) == 1 and "package main" in files_long[0]["code"],
            "long fence same-line close failed")

    # 4) Tilde fences with close at end of code
    s_tilde = (
        "file: src/index.css\n"
        "~~~css\n"
        "body { font-family: system-ui; }~~~\n"
    )
    blocks_tilde = cf_extract_blocks(s_tilde)
    files_tilde = [b for b in blocks_tilde if b.get("type") == "file"]
    _assert(len(files_tilde) == 1 and files_tilde[0]["file_path"] == "src/index.css",
            "tilde fence file block not extracted or path hint failed")

    # 5) Raw (unfenced) diff should still be captured
    s_rawdiff = (
        "diff --git a/file b/file\n"
        "index 111..222 100644\n"
        "--- a/file\n"
        "+++ b/file\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )
    blocks_raw = cf_extract_blocks(s_rawdiff)
    _assert(any(b.get("type") == "diff" for b in blocks_raw), "raw diff fallback missing")


    print("All tests passed ✅")
