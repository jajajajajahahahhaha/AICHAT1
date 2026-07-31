"""Base Agent class — all agents inherit from this."""
from __future__ import annotations
import json
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

log = logging.getLogger("agents")


class BaseAgent:
    """
    Base class for all specialised agents.

    Each agent has:
      - name / display_name / emoji / description
      - system_prompt   : injected as the first system message
      - tools_allowed   : subset of global TOOL_DEFINITIONS the agent may use
      - max_iterations  : max tool-call rounds before forcing a final answer
      - temperature / max_tokens

    The agent does NOT call the model itself — it returns a config dict that
    the server merges into the regular chat_stream pipeline, so all streaming,
    SSE, and tool infrastructure stays untouched.
    """

    name: str = "base"
    display_name: str = "Base Agent"
    emoji: str = "🤖"
    description: str = "Base agent."

    # ---- inference config ----
    temperature: float = 0.7
    max_tokens: int = 8192
    max_iterations: int = 6

    # ---- which tools this agent may call (None = all) ----
    tools_allowed: Optional[List[str]] = None   # None = all tools

    # ---- override in subclass ----
    system_prompt: str = "You are a helpful AI assistant."

    # ---- optional few-shot examples injected after system prompt ----
    few_shot_examples: List[Dict[str, str]] = []

    # -------------------------------------------------------------------

    def get_system_message(self) -> Dict[str, Any]:
        return {"role": "system", "content": self.system_prompt}

    def get_few_shot_messages(self) -> List[Dict[str, Any]]:
        return self.few_shot_examples

    def build_config(self) -> Dict[str, Any]:
        """
        Return a dict that server.py merges into the ChatRequest pipeline.
        Keys:
          system_message   – dict
          few_shot          – list of dicts
          temperature
          max_tokens
          max_iterations
          tools_allowed     – list[str] | None
          agent_name
          agent_emoji
        """
        return {
            "system_message": self.get_system_message(),
            "few_shot": self.get_few_shot_messages(),
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "max_iterations": self.max_iterations,
            "tools_allowed": self.tools_allowed,
            "agent_name": self.display_name,
            "agent_emoji": self.emoji,
        }
