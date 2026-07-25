"""Tool implementations for Kimi Chat."""
from .search import web_search
from .sandbox import run_code
from .vision import analyze_image
from .image_gen import generate_image

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
            "description": "Execute code in a secure sandbox. Supports Python, Bash, and HTML. Use this to run/test code, verify output, or debug scripts the user provides or you generate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "language": {"type": "string", "enum": ["python", "bash", "html"]},
                    "code": {"type": "string", "description": "The code to execute."},
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

__all__ = ["web_search", "run_code", "analyze_image", "generate_image", "TOOL_DEFINITIONS"]
