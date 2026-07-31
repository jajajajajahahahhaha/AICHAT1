"""
Agent Router v1.0
=================
Analyses the user's last message and selects the best agent.

Strategy: keyword scoring + intent matching (no extra API call needed).
Falls back to None (= normal chat mode) when no agent matches well enough.

Note: Persian text doesn't use word boundaries (\b), so we use simple
      `in` / re.search without \b for Persian patterns.
"""
from __future__ import annotations
import re
from typing import Optional, Dict, Any, List

# ── Agent metadata exposed to the frontend ───────────────────────────────────
AGENT_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "id": "designer",
        "name": "Designer Agent",
        "emoji": "🎨",
        "description": "UI/UX طراحی، HTML/CSS/JS prototype، design system",
        "description_en": "UI/UX design, HTML/CSS/JS prototypes, design systems",
        "color": "#a78bfa",
    },
    {
        "id": "coder",
        "name": "Coder Agent",
        "emoji": "💻",
        "description": "کدنویسی، دیباگ، معماری نرم‌افزار، همه زبان‌ها",
        "description_en": "Code writing, debugging, software architecture, all languages",
        "color": "#22c55e",
    },
    {
        "id": "prompt_opt",
        "name": "Prompt Optimizer",
        "emoji": "✨",
        "description": "بهینه‌سازی پرامپت، prompt engineering، system prompt",
        "description_en": "Prompt optimization, prompt engineering, system prompts",
        "color": "#f59e0b",
    },
    {
        "id": "image_specialist",
        "name": "Image Specialist",
        "emoji": "🖼️",
        "description": "ساخت تصویر، AI art، عکاسی مصنوعی",
        "description_en": "Image generation, AI art, visual creation",
        "color": "#ec4899",
    },
]

# ── Scoring rules ─────────────────────────────────────────────────────────────
# Each rule: (regex_pattern, agent_id, score, use_word_boundary)
# use_word_boundary=False for Persian patterns (no \b support)
_RULES: List[tuple] = [

    # ── Designer ─────────────────────────────────────────────────────────────
    # English (word-boundary safe)
    (r"\b(ui|ux|interface|dashboard|landing.?page|webpage|website)\b",     "designer", 3),
    (r"\b(html|css|sass|scss|tailwind|bootstrap|flexbox|grid)\b",          "designer", 3),
    (r"\b(design|prototype|mockup|wireframe|template|component)\b",        "designer", 2),
    (r"\b(navbar|sidebar|modal|card|button|form|dropdown)\b",              "designer", 2),
    (r"\b(dark.mode|light.mode|color.palette|typography|animation)\b",     "designer", 2),
    (r"\b(responsive|mobile.first|accessibility|aria)\b",                  "designer", 2),
    # Persian (no \b)
    (r"(رابط کاربری|رابط‌کاربری|داشبورد|وب.سایت|لندینگ|صفحه.ی? وب)",      "designer", 3),
    (r"(طراح[یِ]|طراحی کن|قالب|کامپوننت|فرم|دکمه|کارت)",                  "designer", 2),
    (r"(بساز|درست کن|طراحی کن).{0,25}(صفحه|سایت|رابط|داشبورد|فرم)",       "designer", 4),
    (r"(ظاهر|جذاب|زیبا|شیک).{0,15}(صفحه|سایت|اپ|برنامه)",                 "designer", 3),
    (r"(بساز|درست کن|بنویس).{0,10}(ui|UI|رابط|صفحه|سایت)",                  "designer", 4),
    (r"(ui|UI|رابط).{0,15}(بساز|درست کن|شیک|جذاب|زیبا|مدرن)",               "designer", 4),
    (r"(landing.?page|لندینگ.?پیج|لندینگ)",                                 "designer", 5),
    (r"(صفحه).{0,10}(شیک|زیبا|جذاب|مدرن|حرفه)",                            "designer", 4),

    # ── Coder ────────────────────────────────────────────────────────────────
    # English (word-boundary safe)
    (r"\b(python|javascript|typescript|rust|golang|java|kotlin|swift|bash|php|ruby|c\+\+)\b", "coder", 3),
    (r"\b(function|class|api|endpoint|database|sql|algorithm|script)\b",   "coder", 2),
    (r"\b(debug|fix.?bug|refactor|optimize|unittest|pytest|jest)\b",       "coder", 2),
    (r"\b(fastapi|django|flask|express|react|vue|next\.?js|node|spring)\b","coder", 3),
    (r"\b(docker|kubernetes|devops|microservice|redis|postgres|mongodb)\b", "coder", 3),
    # Persian (no \b)
    (r"(کد بنویس|کدنویسی|برنامه نویس|پروگرام)",                            "coder", 3),
    (r"(بنویس|پیاده.سازی کن|ایجاد کن).{0,30}(کد|برنامه|اسکریپت|تابع|api|سرور)", "coder", 4),
    (r"\b(python|javascript|typescript|rust|golang|java|bash|php)\b.{0,30}(بنویس|بساز|درست)",  "coder", 5),
    (r"(بنویس|بساز).{0,20}(با python|با js|با typescript|با rust|با go)", "coder", 5),
    (r"(باگ|ارور|خطا|مشکل).{0,20}(کد|برنامه|اسکریپت|پروژه)",              "coder", 3),
    (r"(فانکشن|کلاس|متد|لایبرری|پکیج|ماژول|API|ای‌پی‌آی)",                 "coder", 2),
    (r"(دیباگ|ریفکتور|بهینه.سازی کد|اتوماسیون|ربات|بات)",                  "coder", 2),
    (r"(پایتون|جاوااسکریپت|تایپ.اسکریپت|راست|گولنگ).{0,20}(rate.?limit|limiter|فانکشن|کلاس|ماژول|سرور|اسکریپت|کد)", "coder", 5),
    (r"(rate.?limit|rate.?limiter)",                                         "coder", 4),

    # ── Prompt Optimizer ─────────────────────────────────────────────────────
    # English (word-boundary safe)
    (r"\b(prompt|system.prompt|instruction|chain.of.thought|few.shot|zero.shot)\b", "prompt_opt", 4),
    (r"\b(prompt.engineer|react.pattern|tree.of.thought|constitutional.ai)\b",     "prompt_opt", 4),
    (r"\b(hallucination|temperature|top.p|token|llm.prompt)\b",                    "prompt_opt", 3),
    (r"\b(improve|optimize|rewrite|enhance).{0,10}(prompt|instruction)\b",         "prompt_opt", 5),
    # Persian (no \b)
    (r"(پرامپت)",                                                           "prompt_opt", 4),
    (r"(بهتر کن|بهینه.کن|اصلاح کن|بهبود بده).{0,20}(پرامپت|دستورالعمل)", "prompt_opt", 5),
    (r"(سیستم پرامپت|system prompt|پرامپت قوی|پرامپت حرفه)",               "prompt_opt", 4),
    (r"(این پرامپت|این دستور|این متن).{0,20}(بهتر|اصلاح|بهینه)",           "prompt_opt", 5),

    # ── Image Specialist ─────────────────────────────────────────────────────
    # English (word-boundary safe)
    (r"\b(generate|create|draw|paint|make|render).{0,10}(image|photo|picture|art|illustration)\b", "image_specialist", 4),
    (r"\b(photorealistic|cinematic|8k|masterpiece|midjourney|stable.diffusion|dall.e)\b", "image_specialist", 4),
    (r"\b(portrait|landscape.photo|wallpaper|concept.art|digital.art|anime|artwork)\b", "image_specialist", 3),
    (r"\b(draw|paint|sketch|illustrate).{0,20}(me|a|an|the)\b",            "image_specialist", 5),
    (r"\b(of a|of an|of the).{0,30}(cat|dog|person|man|woman|city|mountain|dragon|robot)", "image_specialist", 3),
    (r"\b(create|generate|make|produce|draw|paint|render).{0,15}(a|an).{0,20}(image|photo|picture|art|artwork|illustration|portrait|landscape|wallpaper)", "image_specialist", 5),
    (r"\b(cyberpunk|anime|fantasy|sci.?fi|realistic|photorealistic|portrait|landscape).{0,20}(image|photo|picture|art)", "image_specialist", 4),
    (r"\b(image|photo|picture|artwork).{0,15}(of|featuring|showing|with).{0,20}\w", "image_specialist", 3),
    # Persian (no \b)
    (r"(تصویر|عکس|نقاشی|پوستر|بنر|آرتورک|تصویرسازی)",                     "image_specialist", 3),
    (r"(بساز|خلق کن|رسم کن|طراحی کن|بکش).{0,20}(تصویر|عکس|نقاشی|پوستر|بنر)", "image_specialist", 5),
    (r"(یه? تصویر|یه? عکس|یه? نقاشی|یه? پوستر)",                           "image_specialist", 3),
    (r"(سبک|استایل|هنری|رئالیستیک|انیمه|سینماتیک).{0,15}(تصویر|عکس|عکاسی)", "image_specialist", 3),
]

# ── Minimum total score to activate an agent ─────────────────────────────────
_THRESHOLD = 3

# ── Exclusion patterns — never route to agent ────────────────────────────────
_EXCLUSIONS = [
    r"\b(what is|what are|who is|explain|describe|define|tell me about)\b",
    r"\b(search|find|look up).{0,15}(latest|news|recent)\b",
    r"(اخبار|خبر).{0,15}(جدید|آخرین|امروز)",
]


def route_to_agent(
    messages: list,
    user_override: Optional[str] = None,
) -> Optional[str]:
    """
    Return an agent id (str) or None (= normal chat mode).

    Parameters
    ----------
    messages       : current message list — we inspect the last user message
    user_override  : agent id explicitly chosen by the user in the UI
    """
    # Manual override always wins
    if user_override and user_override in {a["id"] for a in AGENT_DEFINITIONS}:
        return user_override

    # Extract last user message text
    last_user = ""
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, str):
                last_user = content
            elif isinstance(content, list):
                last_user = " ".join(
                    p.get("text", "") for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                )
            break

    if not last_user.strip():
        return None

    # Check exclusions
    for pat in _EXCLUSIONS:
        if re.search(pat, last_user, re.IGNORECASE):
            return None

    # Score each agent
    scores: Dict[str, int] = {}
    for rule in _RULES:
        pattern, agent_id, weight = rule[0], rule[1], rule[2]
        if re.search(pattern, last_user, re.IGNORECASE):
            scores[agent_id] = scores.get(agent_id, 0) + weight

    if not scores:
        return None

    best_agent = max(scores, key=lambda k: scores[k])
    best_score = scores[best_agent]

    return best_agent if best_score >= _THRESHOLD else None
