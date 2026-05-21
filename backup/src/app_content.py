from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

APP_TITLE = "Structured Preschool Interview Tool"
APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parent
CONFIG_DIR = REPO_ROOT / "config"

DEFAULT_RUBRIC_PATH = CONFIG_DIR / "rubric.json"
DEFAULT_SIGNALS_PATH = CONFIG_DIR / "disqualifier_signals.json"

# Stores GUI edits (trait order, trait question overrides, custom questions, and mixed flow)
QUESTIONS_OVERRIDE_PATH = CONFIG_DIR / "question_overrides.json"
INTERVIEW_HISTORY_PATH = REPO_ROOT / "interview_history.json"
SCHOOL_OFFER_SETTINGS_PATH = REPO_ROOT / "school_offer_settings.json"
SCHOOL_EMAIL_TEMPLATE_SETTINGS_PATH = REPO_ROOT / "school_email_template_settings.json"
INTERVIEW_APP_SETTINGS_PATH = REPO_ROOT / "interview_app_settings.json"

DEFAULT_BASE_DIR = REPO_ROOT / "interviews"

DEFAULT_FONT_SIZE = 10
MIN_FONT_SIZE = 8
MAX_FONT_SIZE = 18

INTRO_BODY_FONT_SPEC = ("Segoe UI", 10, "normal")
INTRO_HEADING_FONT_SPEC = ("Segoe UI", 11, "bold")

DEFAULT_SCHOOL_OPTIONS = ["Hawthorne", "Palmdale", "North Long Beach"]


# =========================
# Candidate Info Screen: Intro Script + School Info
# =========================

INTRO_SCRIPT_TEMPLATE = (
    "Let me go ahead and share a little bit of info about our company, program and benefits.\n\n"
    "Company Statement\n"
    "At Launch Pad Learning, we empower every child, starting as young as six weeks, to explore, play, and grow "
    "through a creative curriculum that builds confidence, curiosity, and early social and academic skills in a safe, "
    "inclusive community.\n\n"
    "Program Structure\n"
    "Our preschool runs year-round on a state-subsidized model, so we don\u2019t have summer or winter breaks, and\n"
    "{hours_line}\n\n"
    "We have a really tight-knit team teachers and support staff,and we also keep our classes small, so three infants per "
    "teacher, four toddlers per teacher, and eight preschool-age children per teacher.\n\n"
    "Benefits\n"
    "On the benefits side, we offer a 401(k) with employer matching contributions, medical, dental, and vision insurance, "
    "provide life & AD&D insurance, and give you ten paid holidays plus one week of PTO, which increases to two weeks "
    "after three years. We also have an on-site chef who whips up homemade meals not just for the kids, but for you too, "
    "so meals become another chance to sit down together and build community.\n\n"
    "Let me go ahead and start the recording, and you should receive a notification."
)

SCHOOL_HOURS_LINE: dict[str, str] = {
    "Hawthorne": "\u2022 Hawthorne is open weekdays from 6 AM to 8 PM, closed on the weekends, and we are caring for about 100 children.",
    "North Long Beach": "\u2022 North Long Beach is open weekdays from 6 AM to 6 PM, closed on the weekends, and we are caring for about 100 children.",
    "Palmdale": "\u2022 Palmdale is open weekdays from 5:30 AM to 7 PM, closed on the weekends, and we are licensed for about 140.",
}

def compose_intro_script(school: str) -> str:
    school_clean = (school or "").strip()
    hours_line = SCHOOL_HOURS_LINE.get(school_clean)
    if not hours_line:
        hours_line = "\u2022 (Select a school above to show hours here)"
    return INTRO_SCRIPT_TEMPLATE.format(hours_line=hours_line)


SCHOOL_INFO: dict[str, str] = {
    "Hawthorne": "Open weekdays: 6:00 AM to 8:00 PM",
    "North Long Beach": "Open weekdays: 6:00 AM to 6:00 PM",
    "Palmdale": "Open weekdays: 5:30 AM to 7:00 PM",
}


# =========================
# Interview edge case addendum
# "That's never happened to me" handling
# =========================

# Phrases that commonly indicate missing behavioral evidence.
NO_EXAMPLE_PHRASES = (
    "that's never happened to me",
    "thats never happened to me",
    "i can't think of an example",
    "i cant think of an example",
    "i've never experienced that",
    "ive never experienced that",
    "that's never come up for me",
    "thats never come up for me",
    "that doesn't really happen",
    "that doesnt really happen",
    "i've never really gotten feedback like that",
    "ive never really gotten feedback like that",
    "i've never really had challenging behavior",
    "ive never really had challenging behavior",
    "i've never had a conflict like that",
    "ive never had a conflict like that",
)


NEVER_HAPPENED_GLOBAL_SCRIPT = (
    "That makes sense. Let's zoom out a bit. Even if it wasn't a big incident, "
    "can you tell me about the closest situation you can remember?"
)


# Trait-specific follow-ups and guidance (no rubric wording, weights, or disqualifiers are changed).
# Keys are expected rubric trait IDs (trait_1 ... trait_11).
NEVER_HAPPENED_BY_TRAIT: dict[str, dict[str, Any]] = {
    "trait_1": {
        "title": "Empathy and Respect for Children",
        "followups": [
            "Can you tell me about a time a child was upset, even in a small way?",
            "How do you usually know when a child is having an emotional need?",
            "What signs do you look for when a child is struggling emotionally?",
        ],
        "scoring": [
            "Score 5-4 if they can generalize empathy, attunement, and child-centered thinking.",
            "Score 3 if response is surface-level but appropriate.",
            "Score 2 if they minimize emotional experiences.",
            "Score 1 if they deny emotional needs or frame children as manipulative.",
        ],
        "concerns": [
            "Denial that children have meaningful emotional distress.",
            "Statements implying emotions are insignificant or exaggerated.",
        ],
    },
    "trait_2": {
        "title": "Emotional Regulation Under Stress",
        "followups": [
            "What does stress look like for you at work, even on a mild day?",
            "How do you usually notice when you're starting to feel dysregulated?",
            "What do you do to stay calm during busy or chaotic moments?",
        ],
        "scoring": [
            "Score 5-4 if they show self-awareness and proactive regulation.",
            "Score 3 if they describe basic coping without reflection.",
            "Score 2 if they minimize stress or normalize frustration.",
            "Score 1 if they deny stress entirely or imply loss of control is inevitable.",
        ],
        "concerns": [
            "Claiming they never feel stress combined with rigidity or emotional flatness.",
            "Statements suggesting emotional regulation is unnecessary.",
        ],
    },
    "trait_3": {
        "title": "Respect for Children's Rights and Safety",
        "followups": [
            "How do you think about children's boundaries in everyday routines?",
            "What does respectful handling mean to you?",
            "What helps you stay safety-focused even during routine tasks?",
        ],
        "scoring": [
            "Score 5-4 if they articulate safety and dignity proactively.",
            "Score 3 if they reference rules without deeper understanding.",
            "Score 2 if safety is framed as an inconvenience.",
            "Score 1 if they minimize safety or boundaries.",
        ],
        "concerns": [
            "Dismissing the importance of boundaries or safety rules.",
        ],
    },
    "trait_4": {
        "title": "Coachability and Openness to Feedback",
        "followups": [
            "What kind of feedback do you usually receive?",
            "How do you prefer feedback to be given?",
            "How do you know when you need to adjust something in your work?",
        ],
        "scoring": [
            "Score 5-4 if they show openness and self-correction.",
            "Score 3 if they accept feedback passively.",
            "Score 2 if they subtly resist or deflect.",
            "Score 1 if they position themselves as beyond feedback.",
        ],
        "concerns": [
            "Statements implying supervision is unnecessary or unwelcome.",
        ],
    },
    "trait_5": {
        "title": "Reliability and Accountability",
        "followups": [
            "When things do go wrong, how do you usually respond?",
            "What do you do if you make a small mistake?",
            "How do you hold yourself accountable day to day?",
        ],
        "scoring": [
            "Score 5-4 if they describe ownership and corrective action.",
            "Score 3 if accountability is vague.",
            "Score 2 if responsibility is externalized.",
            "Score 1 if accountability is rejected.",
        ],
        "concerns": [],
    },
    "trait_6": {
        "title": "Team Orientation and Collaboration",
        "followups": [
            "How do you usually communicate with coworkers?",
            "What do you do if you and another adult see things differently?",
            "How do you contribute to a team environment?",
        ],
        "scoring": [
            "Score 5-4 if collaboration is proactive and respectful.",
            "Score 3 if teamwork is neutral.",
            "Score 2 if independence is emphasized over collaboration.",
            "Score 1 if teamwork is dismissed.",
        ],
        "concerns": [],
    },
    "trait_7": {
        "title": "Curiosity and Willingness to Learn",
        "followups": [
            "What do you feel most confident about in your approach?",
            "What do you still want to get better at?",
            "How do you usually learn new things at work?",
        ],
        "scoring": [
            "Score 5-4 if curiosity and reflection emerge.",
            "Score 3 if learning is passive.",
            "Score 2 if growth is minimized.",
            "Score 1 if beliefs are rigid.",
        ],
        "concerns": [],
    },
    "trait_8": {
        "title": "Gentleness and Physical Awareness (Infant/Toddler)",
        "followups": [
            "What helps you stay gentle during repetitive or tiring routines?",
            "How do you respond when a baby resists a routine?",
            "What cues tell you to slow down?",
        ],
        "scoring": [
            "Lack of reflection here is high risk.",
            "If they cannot articulate gentleness, score conservatively.",
            "Score 1 language remains automatic no hire.",
        ],
        "concerns": [],
    },
    "trait_9": {
        "title": "Patience with Nonverbal Communication (Infant/Toddler)",
        "followups": [
            "What signals do you usually look for?",
            "What do you do if your first guess is wrong?",
            "How do you stay patient during repeated crying?",
        ],
        "scoring": [],
        "concerns": [],
    },
    "trait_10": {
        "title": "Positive Behavior Guidance (Preschool)",
        "followups": [
            "How do you define challenging behavior?",
            "What do you do when a child does not follow expectations?",
            "How do you teach behavior skills?",
        ],
        "scoring": [],
        "concerns": [
            "Denial of challenging behavior in preschool settings can signal lack of insight or overly punitive environments.",
        ],
    },
    "trait_11": {
        "title": "Structure and Flexibility (Preschool)",
        "followups": [
            "How do you usually respond when children move at a different pace?",
            "What do you do if an activity is not working?",
            "How comfortable are you changing plans mid-day?",
        ],
        "scoring": [],
        "concerns": [],
    },
}


def text_suggests_no_example(text: str) -> bool:
    """Heuristic check: does the response resemble a 'no example' statement."""
    t = (text or "").strip().lower()
    if not t:
        return False
    return any(p in t for p in NO_EXAMPLE_PHRASES)


# =========================
# Utility helpers
# =========================

def sanitize_filename(name: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|]+", "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name or "Unknown"


def is_valid_date_yyyy_mm_dd(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")
