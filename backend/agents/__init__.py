"""Multi-Agent System for Kimi Chat v3.0

Agents:
  1. designer   — UI/UX design + HTML/CSS generation
  2. coder      — Code writing, debugging, architecture
  3. prompt_opt — Prompt engineering & optimization
  4. image_gen  — Dedicated image generation specialist

Router: keyword + intent-based routing from user message.
"""
from .router import route_to_agent, AGENT_DEFINITIONS
from .designer import DesignerAgent
from .coder import CoderAgent
from .prompt_opt import PromptOptimizerAgent
from .image_specialist import ImageSpecialistAgent

__all__ = [
    "route_to_agent",
    "AGENT_DEFINITIONS",
    "DesignerAgent",
    "CoderAgent",
    "PromptOptimizerAgent",
    "ImageSpecialistAgent",
]
