"""LLM provider layer for the research + script agents.

Providers:
  anthropic — Claude with web search for trending topics (best quality)
  groq      — free-tier Llama 3.3 70B (no web search; topics are model-generated)
  mock      — instant canned output, zero API cost, for UI/TTS testing
"""
import json
import logging
import random
import re

import httpx

from ..config import get_settings
from ..schemas import SceneBlueprint, ScriptPackage, TrendingTopic

logger = logging.getLogger(__name__)

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

MIN_SEC, MAX_SEC = 30.0, 35.0

CATEGORIES = [
    "Mental Health",
    "Mental Wellness",
    "Wellbeing",
    "Anxiety",
    "Peace & Mindfulness",
    "Addiction",
    "Trauma Recovery",
    "Recovery Centers",
]


# ── JSON helpers ─────────────────────────────────────────
def _repair_json(text: str) -> str:
    """Fix the malformed escaping Groq's JSON mode sometimes emits, e.g.
    over-escaped quotes (\\\\") and invalid apostrophe escapes (\\').
    Returns a best-effort repaired string (still may need json.loads to confirm)."""
    if not text:
        return text
    t = text
    # collapse runs of backslashes before a quote down to a single escape
    t = re.sub(r'\\{2,}"', r'\\"', t)
    # invalid \' -> just ' (JSON has no \' escape)
    t = t.replace("\\'", "'")
    # stray lone backslashes not part of a valid escape sequence
    t = re.sub(r'\\(?!["\\/bfnrtu])', "", t)
    return t


def _parse_json(text: str):
    clean = re.sub(r"```(?:json)?", "", text).strip()
    candidates = [i for i in (clean.find("["), clean.find("{")) if i != -1]
    if not candidates:
        raise ValueError("No JSON found in model response")
    start = min(candidates)
    end = max(clean.rfind("]"), clean.rfind("}")) + 1
    snippet = clean[start:end]
    try:
        return json.loads(snippet)
    except json.JSONDecodeError:
        # second chance: repair common escaping breakage and retry
        return json.loads(_repair_json(snippet))


async def _call_llm_json(prompt: str, use_search: bool = False, max_tokens: int = 1800,
                          temperature: float = 0.8, retries: int = 1):
    """Call the LLM and parse JSON; on a parse failure, retry once at lower
    temperature with an explicit valid-JSON reminder."""
    last_err = None
    for attempt in range(retries + 1):
        text = await _call_llm(prompt, use_search=use_search, max_tokens=max_tokens,
                               temperature=max(0.3, temperature - 0.4 * attempt))
        try:
            return _parse_json(text)
        except (json.JSONDecodeError, ValueError) as e:
            last_err = e
            logger.warning("JSON parse failed (attempt %d): %s", attempt + 1, e)
            prompt += "\n\nREMINDER: output strictly valid JSON. Escape any double quotes inside string values."
    raise last_err


# ── Provider calls ───────────────────────────────────────
async def _call_anthropic(prompt: str, use_search: bool, max_tokens: int) -> str:
    s = get_settings()
    body: dict = {
        "model": s.anthropic_model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if use_search:
        body["tools"] = [{"type": "web_search_20250305", "name": "web_search"}]
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post(
            ANTHROPIC_URL,
            headers={
                "x-api-key": s.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=body,
        )
        r.raise_for_status()
        data = r.json()
    return "\n".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")


async def director_complete(system: str, user: str, max_tokens: int = 1300,
                            temperature: float = 0.8) -> str | None:
    """Plain-text completion with a SYSTEM prompt, used by the prompt-director
    skills (gpt-image-2-director / seedance-director). Unlike _call_llm_json this
    is NOT JSON-mode — the skills return prose or a raw JSON array as text.

    Returns the raw text, or None when we should fall back to the built-in
    hand-written prompts: mock provider, missing key, or any error. Callers MUST
    treat None as 'use the built-in prompt' so a flaky prompt step never breaks a
    paid render."""
    s = get_settings()
    prov = s.llm_provider.lower()
    try:
        if prov == "groq":
            if not s.groq_api_key:
                return None
            async with httpx.AsyncClient(timeout=90) as client:
                r = await client.post(
                    GROQ_URL,
                    headers={"Authorization": f"Bearer {s.groq_api_key}",
                             "content-type": "application/json"},
                    json={
                        "model": s.groq_model,
                        "max_tokens": max_tokens,
                        "temperature": temperature,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                    },
                )
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"]
        if prov == "anthropic":
            if not s.anthropic_api_key:
                return None
            async with httpx.AsyncClient(timeout=90) as client:
                r = await client.post(
                    ANTHROPIC_URL,
                    headers={"x-api-key": s.anthropic_api_key,
                             "anthropic-version": "2023-06-01",
                             "content-type": "application/json"},
                    json={
                        "model": s.anthropic_model,
                        "max_tokens": max_tokens,
                        "system": system,
                        "messages": [{"role": "user", "content": user}],
                    },
                )
                r.raise_for_status()
                data = r.json()
            return "\n".join(b.get("text", "") for b in data["content"]
                             if b.get("type") == "text")
    except Exception as e:  # noqa: BLE001 — never let a prompt-LLM hiccup break the render
        logger.warning("director_complete failed (%s); falling back to built-in prompt: %s",
                       prov, str(e)[:160])
        return None
    return None  # mock or unknown provider -> built-in prompt


async def _call_groq(prompt: str, max_tokens: int, temperature: float = 0.8) -> str:
    s = get_settings()
    # Groq's JSON mode occasionally emits malformed escaping and rejects its OWN
    # output with a 400 json_validate_failed. We retry at progressively lower
    # temperature (less likely to break), and as a last resort recover the
    # `failed_generation` text from the error body and repair it ourselves.
    temps = [temperature, 0.4, 0.2]
    last_failed_gen = None
    async with httpx.AsyncClient(timeout=90) as client:
        for temp in temps:
            r = await client.post(
                GROQ_URL,
                headers={
                    "Authorization": f"Bearer {s.groq_api_key}",
                    "content-type": "application/json",
                },
                json={
                    "model": s.groq_model,
                    "max_tokens": max_tokens,
                    "temperature": temp,
                    "response_format": {"type": "json_object"},
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            if r.status_code == 400:
                # Inspect the error; if it's json_validate_failed, keep the
                # partial generation and retry cooler.
                try:
                    err = r.json().get("error", {})
                except Exception:  # noqa: BLE001
                    err = {}
                if err.get("code") == "json_validate_failed":
                    last_failed_gen = err.get("failed_generation") or last_failed_gen
                    logger.warning("Groq json_validate_failed at temp=%s; retrying cooler", temp)
                    continue
                r.raise_for_status()  # some other 400 — surface it
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]

    # All retries failed validation. Try to salvage the last partial generation.
    if last_failed_gen:
        logger.warning("Recovering Groq failed_generation via repair")
        return last_failed_gen  # _parse_json will repair + parse it
    raise RuntimeError("Groq JSON generation failed after retries")


async def _call_llm(prompt: str, use_search: bool = False, max_tokens: int = 1800,
                    temperature: float = 0.8) -> str:
    provider = get_settings().llm_provider.lower()
    if provider == "groq":
        return await _call_groq(prompt, max_tokens, temperature)
    return await _call_anthropic(prompt, use_search, max_tokens)


# ── Mock data (zero-cost testing) ────────────────────────
MOCK_TOPICS = [
    {"topic": "The 5-4-3-2-1 grounding trick", "angle": "An anxiety reset you can do anywhere in 30 seconds"},
    {"topic": "Why rest isn't laziness", "angle": "Reframing recovery days as part of mental fitness"},
    {"topic": "Doomscrolling and your sleep", "angle": "What one hour of late-night scrolling does to your mind"},
    {"topic": "The first 30 days of recovery", "angle": "What nobody tells you about early sobriety"},
    {"topic": "Asking for help is a skill", "angle": "Why reaching out feels hard and how to make it easier"},
]

MOCK_SCRIPT = {
    "title": "Why Rest Isn't Laziness",
    "lines": [
        {"speaker": "HOST", "text": "What if I told you your rest day is doing more for your mind than your busiest one?", "emotion": "curious", "seconds": 7},
        {"speaker": "GUEST", "text": "It's true. When we rest, the brain shifts into what scientists call the default mode network. That's when it processes emotions, files away memories, and actually recovers.", "emotion": "calm", "seconds": 13},
        {"speaker": "GUEST", "text": "Most people think rest is the absence of progress. It's the opposite. Skipping it is like expecting your phone to run all week on one charge.", "emotion": "warm", "seconds": 11},
        {"speaker": "GUEST", "text": "So if you took a slow day this week and felt guilty about it, don't. That was maintenance, not weakness.", "emotion": "reassuring", "seconds": 9},
        {"speaker": "HOST", "text": "Maintenance, not weakness. I love that. Save this one for your next guilt-free rest day.", "emotion": "hopeful", "seconds": 7},
    ],
}


# ── Public interface ─────────────────────────────────────
async def pick_category() -> str:
    """Pick a content category when DEFAULT_CATEGORY is not set."""
    provider = get_settings().llm_provider.lower()
    if provider == "mock":
        return random.choice(CATEGORIES)

    cats = ", ".join(f'"{c}"' for c in CATEGORIES)
    prompt = (
        "You are choosing a content category for a mental wellness Instagram Reel.\n"
        f"Pick exactly ONE category from this list: {cats}.\n"
        "Choose the one most likely to produce a fresh, engaging short-form podcast clip today.\n"
        'Respond ONLY with valid JSON: {"category": "exact category name from the list"}'
    )
    raw = await _call_llm_json(prompt, max_tokens=200, temperature=0.6)
    chosen = raw.get("category", "")
    if chosen in CATEGORIES:
        return chosen
    for c in CATEGORIES:
        if c.lower() == str(chosen).lower():
            return c
    logger.warning("LLM returned unknown category %r; falling back to first", chosen)
    return CATEGORIES[0]


async def select_best_topic(category: str, topics: list[TrendingTopic]) -> TrendingTopic:
    """Pick the single best topic+angle for a Reel from research results."""
    if not topics:
        raise ValueError("No topics to select from")
    if len(topics) == 1:
        return topics[0]

    provider = get_settings().llm_provider.lower()
    if provider == "mock":
        return topics[0]

    listing = "\n".join(
        f'{i}. topic: {t.topic} | angle: {t.angle}' for i, t in enumerate(topics)
    )
    prompt = (
        f'Category: "{category}"\n'
        "Pick the ONE topic below that will make the strongest 30-second Instagram Reel "
        "(emotional hook, shareability, clarity).\n"
        f"{listing}\n"
        'Respond ONLY with valid JSON: {"index": 0} where index is 0-based.'
    )
    raw = await _call_llm_json(prompt, max_tokens=100, temperature=0.3)
    idx = int(raw.get("index", 0))
    if 0 <= idx < len(topics):
        return topics[idx]
    logger.warning("LLM returned invalid topic index %d; using first", idx)
    return topics[0]


async def fetch_trending_topics(category: str) -> list[TrendingTopic]:
    provider = get_settings().llm_provider.lower()
    if provider == "mock":
        return [TrendingTopic(**t) for t in MOCK_TOPICS]

    use_search = provider == "anthropic"
    search_clause = (
        "Search the web for trending discussions, recent news, and viral conversations this week"
        if use_search
        else "Suggest five timely, resonant discussion topics"
    )
    lenses = random.sample([
        "a surprising statistic", "a common misconception", "a daily habit",
        "a relationship dynamic", "a workplace angle", "a science-backed insight",
        "a recovery milestone", "a social media behavior", "a sleep connection",
        "a family perspective", "a self-talk pattern", "a physical symptom link",
    ], 5)
    prompt = (
        f'{search_clause} about "{category}" that would resonate on Instagram Reels.\n'
        f'Make the 5 topics genuinely different from each other — for variety, draw on lenses such as: {", ".join(lenses)}.\n'
        'Do not repeat generic evergreen topics; be specific and fresh.\n'
        'Respond ONLY with valid JSON (no markdown, no preamble) in exactly this shape:\n'
        '{"topics": [{"topic": "short punchy topic title", "angle": "one-line emotional hook angle"}]}\n'
        'Exactly 5 items. IMPORTANT: do NOT use double-quote characters inside any '
        'string value — phrase angles without quotation marks. Use plain apostrophes for contractions.'
    )
    raw = await _call_llm_json(prompt, use_search=use_search, max_tokens=1500, temperature=0.7)
    items = raw.get("topics", raw) if isinstance(raw, dict) else raw
    return [TrendingTopic(**t) for t in items[:5]]


def _script_prompt(category: str, seed_topic: str | None, user_draft: str | None,
                   correction: str | None = None) -> str:
    if user_draft:
        source = (
            "PRIORITY INSTRUCTION — the user provided their own topic/draft below. "
            "The script MUST be about exactly this, not the general category:\n"
            f"<<<USER INPUT START>>>\n{user_draft}\n<<<USER INPUT END>>>"
        )
    elif seed_topic:
        source = f"Topic: {seed_topic}"
    else:
        source = f"Topic category: {category}"

    base = f"""You are a script agent for podcast-style Instagram Reels about mental wellness and recovery.
{source}

Write a Q&A podcast reel script. Host asks, Guest answers. Hard rules:
- TOTAL spoken duration MUST be between {MIN_SEC:.0f} and {MAX_SEC:.0f} seconds. Estimate at ~2.4 words/second.
- Structure: thumbnail hook line (1-2s) -> host question (max 7s) -> guest answer split into 2-3 lines (~30-35s total) -> host acknowledgement + CTA (max 5s).
- Every line carries an emotion tag from: curious, calm, empathetic, hopeful, serious, warm, reassuring.
- Conversational, research-grounded, emotionally resonant. No medical advice or diagnosis claims.
- The guest's multi-line answer must read as ONE continuous, flowing reply — each line picks up naturally from the previous one (use connectors like "And here's the thing...", "But what most people miss is..."). No restarts, no repeated context, no robotic listing. It should sound like a real podcast conversation, never AI-generated.

Respond ONLY with JSON, no markdown:
{{"title": "...", "lines": [{{"speaker": "HOST", "text": "...", "emotion": "curious", "seconds": 6}}]}}"""
    if correction:
        base += f"\n\nIMPORTANT CORRECTION: {correction}"
    return base


async def generate_script(category: str, seed_topic: str | None,
                          user_draft: str | None) -> ScriptPackage:
    if get_settings().llm_provider.lower() == "mock":
        return ScriptPackage(**MOCK_SCRIPT)

    correction = None
    last_pkg: ScriptPackage | None = None
    for attempt in range(2):
        raw = await _call_llm_json(
            _script_prompt(category, seed_topic, user_draft, correction)
        )
        pkg = ScriptPackage(**raw)
        total = pkg.total_seconds
        if MIN_SEC <= total <= MAX_SEC:
            return pkg
        last_pkg = pkg
        correction = (
            f"Your previous script totaled {total:.0f}s which is outside the "
            f"{MIN_SEC:.0f}-{MAX_SEC:.0f}s budget. Rewrite so line durations sum to "
            f"~{(MIN_SEC + MAX_SEC) / 2:.0f}s."
        )
        logger.warning("Script attempt %d out of budget (%.1fs), retrying", attempt + 1, total)
    return last_pkg


# ── Scene Planning Agent (Stage 4) ────────────────────────
MOCK_BLUEPRINT = {
    "title": "Mock Blueprint",
    "scenes": [
        {"scene_number": 1, "start_second": 0, "end_second": 10, "speaker_on_camera": "HOST",
         "character_action": "Leans forward toward the mic, hands clasped",
         "facial_expression": "Curious, raised eyebrows", "camera_movement": "Slow push-in",
         "background_environment": "same fixed studio"},
        {"scene_number": 2, "start_second": 10, "end_second": 30, "speaker_on_camera": "GUEST",
         "character_action": "Gestures gently while explaining, nods at key points",
         "facial_expression": "Calm, sincere eye contact", "camera_movement": "Static medium shot, slight handheld sway",
         "background_environment": "same fixed studio"},
        {"scene_number": 3, "start_second": 30, "end_second": 47, "speaker_on_camera": "GUEST",
         "character_action": "Opens palms outward on the takeaway line, settles back",
         "facial_expression": "Warm, reassuring smile", "camera_movement": "Slow pull-back, single-person framing",
         "background_environment": "same fixed studio"},
    ],
}


async def generate_scene_plan(title: str, lines: list, segments: list | None = None) -> SceneBlueprint:
    if get_settings().llm_provider.lower() == "mock":
        return SceneBlueprint(**MOCK_BLUEPRINT)

    script_text = "\n".join(
        f"[{l.speaker} | {l.emotion} | {l.seconds}s] {l.text}" for l in lines
    )
    total = sum(l.seconds for l in lines)

    if segments:
        seg_text = "\n".join(
            f"Segment {s.index}: {s.start_second}s-{s.end_second}s | spoken text: {s.text}"
            for s in segments
        )
        structure_clause = f"""The audio has already been cut into video-generation segments at natural pauses.
Produce EXACTLY one scene per segment, using these exact start/end times:
{seg_text}

scene_number must equal the segment index, start_second/end_second must match the segment times."""
    else:
        structure_clause = "Divide the reel into 3-4 scenes aligned to the dialogue turns."

    prompt = f"""You are a scene planning agent for a podcast-style Instagram Reel (vertical 9:16).
Two people sit in their own armchairs in the same fixed studio, angled slightly toward each other (there is NO desk between them): HOST and GUEST.
This is an in-person conversation — they mostly look toward EACH OTHER (off to the side), not at the camera.
Title: {title}
Total duration: ~{total:.0f}s
Script with per-line timing and emotion:
{script_text}

{structure_clause}
For each scene give concrete, production-ready visual direction for an AI talking-head
video generator, DERIVED FROM THE ACTUAL LINE being spoken. The studio set is FIXED and
locked elsewhere — do NOT invent or describe backgrounds; keep one consistent studio.

For every scene, base the direction on what the line actually says and feels like:
- character_action: specific posture/gesture that fits THIS line (e.g. leans in on a question, opens palms on a reveal, counts on fingers when listing).
- facial_expression: the expression this exact line calls for.
- body_language: natural hand/shoulder/head movement matching the line's rhythm and meaning.
- eye_contact: where the speaker looks — usually toward the OTHER PERSON off to the side, sometimes down or toward the mic in thought, only an occasional brief glance toward camera. Never a fixed camera stare.
- emotional_tone: the felt tone of the delivery (e.g. serious, warm, reflective, light, playful).
- humor: if — and ONLY if — the topic's seriousness allows it, an optional light/off-hand beat or small aside that fits naturally (a brief smile, a wry line read). For heavy/sensitive topics, leave this empty. Never force a joke.
- reaction_cue: what the OTHER person (the listener) is doing during this line — nodding, listening intently, reacting, a small empathetic expression. Used for cut-to-listener reaction shots on longer turns.
- camera_movement: subtle and realistic (slow push-in, gentle handheld sway, locked medium). No gimmicks. Keep a SINGLE-PERSON framing — only one person is ever on camera at a time; never describe a two-shot or both people in frame.
- background_environment: ALWAYS exactly "same fixed studio" — do NOT describe or invent the set; the exact studio is fixed downstream.

Respond ONLY with JSON, no markdown:
{{"title": "{title}", "scenes": [{{"scene_number": 1, "start_second": 0, "end_second": 10,
"speaker_on_camera": "HOST", "character_action": "...", "facial_expression": "...",
"body_language": "...", "eye_contact": "...", "emotional_tone": "...", "humor": "...",
"reaction_cue": "...", "camera_movement": "...", "background_environment": "same fixed studio"}}]}}
Do NOT use double-quote characters inside any string value; phrase naturally without quotation marks."""
    raw = await _call_llm_json(prompt, max_tokens=2200, temperature=0.7)
    return SceneBlueprint(**raw)


# ── Visual Canon (locked descriptions for image/video consistency) ──
MOCK_CANON = {
    "studio": "A modern podcast studio with a matte-black bookshelf-and-cabinet back wall lit by warm interior LED accents, motivational typography posters, books, plants, a live-edge wood-slice sculpture and a small astronaut figurine; the hosts sit in mid-century grey upholstered armchairs with black dynamic microphones on boom arms; neutral patterned rug, dark wood floor, warm moody lighting.",
    "host": "RON, a 38-year-old Indian man with short black hair, trimmed beard, wearing a black crew-neck sweater, curious and professional demeanor",
    "guest": "JASON, a 45-year-old man with salt-and-pepper short hair and beard, wearing a black polo shirt, calm and warm demeanor",
}


async def build_visual_canon(blueprint) -> dict:
    """One locked description set reused verbatim in every image and video
    prompt — this is what keeps faces, wardrobe and the set identical."""
    if get_settings().llm_provider.lower() == "mock":
        return MOCK_CANON

    scene_bgs = "; ".join(s.background_environment for s in blueprint.scenes)
    prompt = f"""You are an art director locking the visual canon for an AI-generated podcast reel.
Scene background notes from the blueprint: {scene_bgs}

Produce ONE definitive, highly specific visual description set that will be reused verbatim
across all image and video generations (consistency is the entire point):
- studio: one richly detailed sentence describing the podcast set (furniture, lighting, colors, props). Synthesize the blueprint notes into a single consistent set.
- host: "RON, ..." one sentence: age, ethnicity, hair, facial hair, exact clothing and colors, headphones, demeanor.
- guest: "JASON, ..." same level of detail, visually clearly distinct from the host.

Respond ONLY with valid JSON: {{"studio": "...", "host": "...", "guest": "..."}}
Escape any double quotes inside string values."""
    raw = await _call_llm_json(prompt, max_tokens=800, temperature=0.6)
    return {k: raw[k] for k in ("studio", "host", "guest")}
