"""Transparent, rule-based complaint intelligence.

No machine-learning model or external AI service is used. Every suggestion
returns the reasons that produced it, so administrators can see why a
priority or category was proposed. The functions are pure and deterministic.
"""

import re

# Keyword lexicon per category (English plus common Hinglish/Kannada-English
# transliterations seen in Indian civic complaints).
CATEGORY_KEYWORDS = {
    "Roads": ["pothole", "road", "crater", "asphalt", "tar", "speed breaker", "footpath", "pavement", "divider", "manhole", "gaddha"],
    "Sanitation": ["garbage", "trash", "waste", "litter", "dump", "kachra", "sewage", "drain", "gutter", "smell", "stink", "toilet", "debris", "dead animal"],
    "Streetlights": ["streetlight", "street light", "lamp", "light not working", "dark", "pole", "bulb", "electric", "wire", "flicker"],
    "Waterlogging": ["waterlogging", "waterlogged", "flood", "flooding", "stagnant", "rainwater", "puddle", "inundated", "overflow"],
    "Water Supply": ["water supply", "no water", "pipeline", "pipe", "leak", "leakage", "tap", "drinking water", "borewell", "contaminated", "low pressure"],
}

# Signals that raise urgency, with their weight and a human-readable reason.
RISK_SIGNALS = [
    (r"\b(accident|injur|hurt|bleed|fell|fallen)\w*", 25, "risk of injury mentioned"),
    (r"\b(live wire|electrocut|exposed wire|sparking|shock)\w*", 35, "electrical hazard mentioned"),
    (r"\b(school|hospital|clinic|ambulance|children|kids|elderly)\b", 15, "near vulnerable users"),
    (r"\b(sewage|contaminat|disease|dengue|mosquito|malaria)\w*", 15, "public-health risk"),
    (r"\b(flood|overflow|inundat|collapse|cave[- ]?in|sinkhole)\w*", 20, "structural or flooding risk"),
    (r"\b(fire|smoke|gas leak)\b", 35, "fire or gas hazard"),
    (r"\b(main road|highway|junction|signal|traffic)\b", 10, "busy traffic location"),
    (r"\b(days|weeks|months|long time|since)\b", 5, "long-standing issue"),
]

CATEGORY_BASE = {"Roads": 30, "Sanitation": 25, "Streetlights": 25, "Waterlogging": 35, "Water Supply": 35, "Other": 20}
URGENCY_BONUS = {"LOW": -5, "MEDIUM": 0, "HIGH": 10}


def _normalise(text):
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def suggest_category(title, description=""):
    """Return {category, confidence, matched} or category None when no keyword matches."""
    text = _normalise(f"{title} {description}")
    scores = {}
    for category, words in CATEGORY_KEYWORDS.items():
        matched = [w for w in words if re.search(rf"\b{re.escape(w)}", text)]
        if matched:
            # Title matches count double: titles are short and specific.
            title_text = _normalise(title)
            score = sum(2 if re.search(rf"\b{re.escape(w)}", title_text) else 1 for w in matched)
            scores[category] = (score, matched)
    if not scores:
        return {"category": None, "confidence": 0.0, "matched": []}
    ranked = sorted(scores.items(), key=lambda kv: kv[1][0], reverse=True)
    best, (best_score, matched) = ranked[0]
    total = sum(v[0] for v in scores.values())
    return {"category": best, "confidence": round(best_score / total, 2), "matched": matched[:5]}


def priority_from_score(score):
    if score >= 75:
        return "CRITICAL"
    if score >= 50:
        return "HIGH"
    if score >= 25:
        return "MEDIUM"
    return "LOW"


def score_priority(category, title, description="", nearby_open=0, citizen_urgency=None):
    """Score 0-100 with explicit reasons. Higher means more urgent."""
    text = _normalise(f"{title} {description}")
    score = CATEGORY_BASE.get(category, CATEGORY_BASE["Other"])
    reasons = [f"base score {score} for {category or 'Other'}"]
    for pattern, weight, reason in RISK_SIGNALS:
        if re.search(pattern, text):
            score += weight
            reasons.append(f"+{weight}: {reason}")
    if nearby_open:
        bonus = min(20, 5 * int(nearby_open))
        score += bonus
        reasons.append(f"+{bonus}: {nearby_open} similar open complaint(s) nearby")
    if citizen_urgency in URGENCY_BONUS and URGENCY_BONUS[citizen_urgency]:
        score += URGENCY_BONUS[citizen_urgency]
        reasons.append(f"{URGENCY_BONUS[citizen_urgency]:+d}: citizen marked urgency {citizen_urgency}")
    score = max(0, min(100, score))
    return {"score": score, "priority": priority_from_score(score), "reasons": reasons}


def summarize(title, description, max_length=160):
    """Extractive summary: title plus the first sentence of the description."""
    first = re.split(r"(?<=[.!?])\s+", (description or "").strip(), maxsplit=1)[0]
    summary = re.sub(r"\s+", " ", f"{title.strip()}: {first}" if first else title.strip())
    return summary if len(summary) <= max_length else summary[: max_length - 1].rstrip() + "…"
