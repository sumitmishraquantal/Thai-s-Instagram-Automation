"""LLM provider layer for the research + script agents.

Providers:
  anthropic — Claude with web search for trending topics (best quality)
  groq      — free-tier Llama 3.3 70B; topic research uses YouTube + Serper when configured
  mock      — instant canned output, zero API cost, for UI/TTS testing
"""
import json
import logging
import random
import re

import httpx

from ..config import get_settings
from ..schemas import SceneBlueprint, ScriptPackage, TrendingTopic
from . import research_sources

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
        "Choose the category most likely to produce a fresh, emotionally resonant, "
        "shareable short-form podcast clip for today's audience.\n"
        "Do not repeat a category that feels generic or overused.\n"
        'Respond ONLY with valid JSON. No markdown. No text before or after: '
        '{"category": "exact category name from the list"}'
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
        "Pick the ONE topic below that will make the strongest 30-second Instagram Reel.\n"
        "Score on: emotional hook strength, shareability, clarity, and freshness. "
        "Avoid topics that feel generic, overexplained, or likely already covered widely.\n"
        f"{listing}\n"
        'Respond ONLY with valid JSON. No markdown. No text before or after: '
        '{"index": 0} where index is 0-based.'
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

    s = get_settings()
    signals = await research_sources.gather_research_signals(category)
    signals_block = research_sources.format_signals_for_prompt(signals)
    has_live_data = bool(signals)

    use_anthropic_search = provider == "anthropic" and not has_live_data
    if has_live_data:
        search_clause = (
            "Using ONLY the real research signals below (YouTube + web), distill five "
            "timely discussion topics that would work as 30-second Instagram Reels. "
            "Ground every topic directly in the signals — do not invent unrelated ideas. "
            "Each topic must be traceable to at least one signal."
        )
    elif use_anthropic_search:
        search_clause = (
            "Search the web for trending discussions, recent news, and viral conversations "
            "this week in mental wellness and recovery"
        )
    else:
        search_clause = "Suggest five timely, emotionally resonant discussion topics"

    lenses = random.sample([
        "a surprising statistic", "a common misconception", "a daily habit",
        "a relationship dynamic", "a workplace angle", "a science-backed insight",
        "a recovery milestone", "a social media behavior", "a sleep connection",
        "a family perspective", "a self-talk pattern", "a physical symptom link",
    ], 5)

    prompt = (
        f'{search_clause} about "{category}" that would resonate on Instagram Reels.\n'
    )
    if has_live_data:
        prompt += f"\nLIVE RESEARCH SIGNALS:\n{signals_block}\n\n"
    prompt += (
        f"Make the 5 topics genuinely different from each other — draw on these lenses for variety: "
        f"{', '.join(lenses)}.\n"
        "Each topic must feel specific and timely. No generic evergreen titles. "
        "No topic should overlap in angle or framing with another in the list.\n"
        "AVOID: vague titles, recycled wellness clichés, topics that could apply to any category.\n"
        "Respond ONLY with valid JSON. No markdown. No text before or after:\n"
        '{"topics": [{"topic": "short punchy topic title", "angle": "one-line emotional hook angle"}]}\n'
        "Exactly 5 items. Do NOT use double-quote characters inside any string value. "
        "Use plain apostrophes for contractions."
    )
    raw = await _call_llm_json(
        prompt, use_search=use_anthropic_search, max_tokens=1500, temperature=0.7
    )
    items = raw.get("topics", raw) if isinstance(raw, dict) else raw
    return [TrendingTopic(**t) for t in items[:5]]


def _script_prompt(category: str, seed_topic: str | None, user_draft: str | None,
                   correction: str | None = None) -> str:
    if user_draft:
        source = (
            "PRIORITY INSTRUCTION — the user provided their own topic or draft below. "
            "The script MUST be about exactly this content. Do not substitute, reframe, "
            "or blend it with the general category:\n"
            f"<<<USER INPUT START>>>\n{user_draft}\n<<<USER INPUT END>>>"
        )
    elif seed_topic:
        source = f"Topic: {seed_topic}"
    else:
        source = f"Topic category: {category}"

    base = f"""You are a script agent for podcast-style Instagram Reels about mental wellness and recovery.
{source}

Write a Q&A podcast reel script. Host asks, Guest answers. Follow every rule below exactly:

TIMING:
- Total spoken duration MUST be between {MIN_SEC:.0f} and {MAX_SEC:.0f} seconds.
- Estimate at 2.4 words per second. Count your words before finalizing.
- Each line must have a realistic seconds value that reflects its actual word count.

STRUCTURE (in this exact order):
1. Thumbnail hook — 1 to 2 seconds. One arresting statement or question. No filler.
2. Host question — maximum 7 seconds. Specific, curious, conversational. Not a monologue.
3. Guest answer — 2 to 3 lines totaling 30 to 35 seconds. One continuous flowing reply (see CONVERSATION RULE below).
4. Host acknowledgement and CTA — maximum 5 seconds. Warm, direct, no corporate language.

CONVERSATION RULE (most important):
The guest answer must read as ONE unbroken natural reply — each line continues directly from the previous one.
Use real conversational connectors: "And what that means is...", "But here is the thing...", "So what actually happens is...".
Never restart context mid-answer. Never list points robotically. Never sound like bullet points read aloud.
If it sounds like an AI wrote it, rewrite it until it sounds like a real person mid-thought.

EMOTION:
Every line carries exactly one emotion tag from: curious, calm, empathetic, hopeful, serious, warm, reassuring.
Match the tag to what the line actually feels like when spoken aloud.

CONTENT RULES:
- Conversational and research-grounded throughout.
- No medical advice. No diagnosis claims. No prescriptive health instructions.
- No filler phrases: no "absolutely", "great question", "for sure", "definitely", "of course".
- Guest never repeats the host's question back before answering.

AVOID:
- Generic opener lines that could fit any topic
- Guest answers that restart or restate context from the host question
- Any line that sounds scripted, stiff, or AI-generated
- Emotion tags that do not match the line's actual spoken feeling
- Word counts that do not match the seconds value given

Respond ONLY with valid JSON. No markdown. No text before or after the JSON:
{{"title": "...", "lines": [{{"speaker": "HOST", "text": "...", "emotion": "curious", "seconds": 6}}]}}"""

    if correction:
        base += f"\n\nIMPORTANT CORRECTION — apply this before generating: {correction}"
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

SETUP:
Host and Guest sit in matching armchairs angled toward each other. No desk between them. In-person conversation. One person on camera at a time — always.

Title: {title}
Duration: ~{total:.0f}s
Script:
{script_text}
{structure_clause}

ABSOLUTE RULES:
- One person per frame. No two-shots. No shared frame. Ever.
- Screen direction is permanently locked. HOST always looks toward the same off-camera side in every scene. GUEST always looks toward the opposite side. Never flip eye direction mid-episode. This must stay consistent across all scenes or the edit breaks.
- Eyes toward the other person off-camera. Never a sustained lens stare. Only a brief involuntary glance toward camera is allowed, never held.
- Every field must come from the actual line being spoken. No generic defaults.
- Posture is locked for the full episode per speaker. Baseline: front half of chair, 5–8 degree forward lean, shoulders relaxed down, feet flat, lower body still. Variations are micro-level only — small head tilts, brief gesture lifts. Never describe a scene where the speaker's posture meaningfully differs from this baseline.
- Gesture range: hands stay within torso width and below shoulder height. No movement above the chin. No wide sweeping arm motion. Small and motivated only.
- Camera: locked medium for all scenes. No shake. No handheld. No dramatic moves. No mid-episode crop changes — tighter framing is a post-production decision, not a camera instruction.
- Reaction cuts only on speaker turns longer than 8 seconds. Always 1–3 seconds maximum. Listener mouth always fully closed. Never cut to reaction mid-sentence.
- background_environment: always exactly "same fixed studio" — never describe or vary it.
- Face and clothing stay identical across all scenes for the same speaker.

FIELDS — each derived from the actual spoken line:

character_action: one specific gesture or posture shift this line physically calls for. Hands stay within torso width, below shoulder height. Never theatrical. Never repeated verbatim across consecutive scenes.

facial_expression: describe only as muscle movement. What physically moves — brow ridge, jaw, corners of mouth, eyes narrowing or softening. Never write emotion labels like "looks happy" or "seems engaged."

body_language: small hand, shoulder, or head movement matching this line's rhythm and meaning. Hands move when the line calls for it and return to rest. Shoulders never rise on confident lines. Head movement is 5–10 degrees maximum.

eye_contact: where eyes go during this line. Default: toward the other person on their locked off-camera side. Variations only when the line genuinely calls for it — brief downward glance on a hard admission, middle-distance on a searching pause. Never switch the off-camera side.

emotional_tone: one to two words only. Precise. Not generic. Examples: quietly urgent, dry and grounded, warmly matter-of-fact, measured and still.

humor: empty string for any serious, sensitive, emotional, or vulnerable line. Only include if the line is genuinely light and the topic allows it. Never forced.

reaction_cue: one specific physical beat the off-camera listener performs — only relevant if this speaker turn exceeds 8 seconds. Examples: single slow nod with brow slightly furrowed, lips pressed together with chin dropping on an exhale, jaw shifting slightly as a point lands. Never write "nodding and listening" or any generic phrase.

camera_movement: locked medium only. No other option. Never suggest a crop change, push-in, or any movement.

background_environment: always exactly "same fixed studio"

AVOID IN ALL FIELDS:
- Two people in frame or described together
- Eye direction switching sides between scenes for the same speaker
- Eyes looking into the lens unless the script line explicitly requires direct address
- Gestures above the chin or outside torso width
- Theatrical or exaggerated movement
- Repeated identical character actions across consecutive scenes
- Emotion labels instead of muscle-level physical description
- Any background detail or studio description
- Handheld shake, whip pans, zooms, crane moves, push-ins, crop changes
- Reaction cues on short lines under 8 seconds
- Reaction cues with open mouth or mid-sentence timing
- Generic reaction descriptions like "nodding attentively" or "listening intently"
- Humor on grief, trauma, vulnerability, crisis, or serious disclosure

Respond with valid JSON only. No markdown. No text before or after the JSON:
{{"title": "{title}", "scenes": [{{"scene_number": 1, "start_second": 0, "end_second": 10, "speaker_on_camera": "HOST", "character_action": "...", "facial_expression": "...", "body_language": "...", "eye_contact": "...", "emotional_tone": "...", "humor": "...", "reaction_cue": "...", "camera_movement": "...", "background_environment": "same fixed studio"}}]}}

No double-quote characters inside any string value."""
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
