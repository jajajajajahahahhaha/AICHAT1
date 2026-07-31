"""Tool implementations for Kimi Chat."""
from .search import web_search
from .sandbox import run_code, SUPPORTED_LANGUAGES
from .vision import analyze_image
from .image_gen import generate_image

# The sandbox now speaks 17 languages. Keep the string short so it fits in the
# tool definition without bloating every request; the full list is at
# SUPPORTED_LANGUAGES.
_SANDBOX_LANG_DESCRIPTION = (
    "Execute code in a secure multi-language sandbox. Supported: "
    + ", ".join(SUPPORTED_LANGUAGES)
    + ", plus 'auto' to detect from a shebang. "
    "Compiled languages (c, cpp, go, rust, java, kotlin) are built then run. "
    "SQL runs against a local SQLite DB (sandbox.db). HTML is rendered client-side. "
    "Any files the code creates (plots, CSVs, binaries) come back in `files` with "
    "download URLs; small images are additionally embedded as data_url so they can be "
    "shown inline. matplotlib.pyplot.show() auto-saves to plot.png. Persistent "
    "workspaces let you keep files across calls in the same chat."
)


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web using DuckDuckGo. Use this when the user asks about current events, recent information, or topics that require up-to-date facts from the internet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query."},
                    "max_results": {"type": "integer", "description": "Number of results (default 5).", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_code",
            "description": _SANDBOX_LANG_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "language": {
                        "type": "string",
                        "description": (
                            "Language identifier: python, bash, html, javascript, "
                            "typescript, c, cpp, go, rust, java, kotlin, ruby, php, "
                            "lua, r, sql, perl — or 'auto' to detect from a shebang."
                        ),
                    },
                    "code": {"type": "string", "description": "The source code to execute."},
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default 90, max 300).",
                    },
                    "stdin": {
                        "type": "string",
                        "description": "Optional text piped to the program's stdin.",
                    },
                    "packages": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional list of packages to install BEFORE running: "
                            "pip pkgs for python, npm pkgs for javascript/typescript, "
                            "gems for ruby. Example: ['requests', 'beautifulsoup4']."
                        ),
                    },
                    "workspace_id": {
                        "type": "string",
                        "description": (
                            "Optional persistent workspace id — reuse across calls in the "
                            "same chat to keep files (data, outputs, package caches) between runs."
                        ),
                    },
                    "env": {
                        "type": "object",
                        "description": "Optional extra environment variables (UPPER_SNAKE_CASE names only).",
                        "additionalProperties": {"type": "string"},
                    },
                    "files": {
                        "type": "object",
                        "description": (
                            "Optional additional source files to drop into the workspace "
                            "before running, keyed by relative path. Useful for multi-file "
                            "projects, config files (package.json, go.mod), or seed data."
                        ),
                        "additionalProperties": {"type": "string"},
                    },
                    "memory_mb": {
                        "type": "integer",
                        "description": "Address-space limit in MB (default 2048).",
                    },
                },
                "required": ["language", "code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_image",
            "description": "Analyze an image the user uploaded. Use to describe, extract text (OCR), or answer questions about visual content. When the user attached an image, ALWAYS use this tool to see what's in it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_id": {"type": "string", "description": "The ID of the uploaded image (starts with img_)."},
                    "question": {"type": "string", "description": "Question or instruction about the image."},
                },
                "required": ["image_id", "question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": "Generate an image from a text description. Use this when the user asks you to create/draw/make an image, picture, or illustration of something. Convert the user's request into a detailed, vivid English prompt describing subject, style, lighting, and composition.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Detailed English prompt describing the image to create."},
                    "width": {"type": "integer", "description": "Image width in pixels (default 1024).", "default": 1024},
                    "height": {"type": "integer", "description": "Image height in pixels (default 1024).", "default": 1024},
                },
                "required": ["prompt"],
            },
        },
    },
]

__all__ = [
    "web_search",
    "run_code",
    "analyze_image",
    "generate_image",
    "TOOL_DEFINITIONS",
    "SUPPORTED_LANGUAGES",
]
