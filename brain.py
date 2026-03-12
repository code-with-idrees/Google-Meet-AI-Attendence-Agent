"""
brain.py — The AI Brain: Whisper (local STT) + Ollama (local LLM).

Handles transcription, fuzzy keyword detection, and intelligent response generation
for the Google Meet Classroom Proxy.
"""

import os
import re
import time
import wave
import struct
import difflib
import config

# Lazy imports to avoid slow startup
_whisper_model = None

# ===================================================================
# Known Whisper hallucination phrases — harvested from real sessions.
# If >50% of the transcription matches one of these, discard it.
# ===================================================================
_WHISPER_GARBAGE_PHRASES = [
    # English hallucinations during silence
    "i'm going to go to the bathroom",
    "i'm going to go to",
    "thank you for watching",
    "thanks for watching",
    "i'll be right there",
    "i'll be right back",
    "please subscribe",
    "please like and subscribe",
    "see you in the next video",
    "see you next time",
    "thank you very much",
    "goodbye",
    "the end",
    "music",
    # Whisper identity loops
    "the student's name is",
    "the student is",
    "student named",
    # Random filler Whisper invents
    "hello ladies",
    "hello ladies how are you",
    "i'm just gonna toast",
    "1.5% deurice",
    "1kg of entrepreneurship",
    # Korean/Chinese/random script hallucinations (Whisper sometimes outputs these)
    "점油", "짜줄까봐", "睡", "나한테", "여 reporter",
]

# Minimum RMS energy for audio to be considered non-silent
_AUDIO_ENERGY_THRESHOLD = 50  # 16-bit PCM scale (0-32768); 50 is very quiet

# ===================================================================
# All possible ways Whisper might transcribe "Idrees" / "Muhammad Idrees"
# These are real examples observed from live testing with Whisper base
# ===================================================================
NAME_PATTERNS = [
    # Exact
    "idrees", "idris", "idriss", "edrees", "edris",
    # Whisper common mis-transcriptions (observed from live testing)
    "in three is", "in threes", "id r e e s", "i d r e e s",
    "idres", "adris", "adrees", "aidrees", "idhriss",
    "deurice", "deuris", "idr tavalli", "idrice", "idreece",
    "idris idr", "idrees idr", "idreez",
    # With Muhammad prefix
    "mohammad idris", "muhammad idris", "mohammed idris",
    "mohammad idrees", "muhammad idrees", "mohammed idrees",
    "mohamed idris", "mohamed idrees", "mohamed idrees idr",
    # Urdu/Arabic script
    "ادریس", "ادريس",
    # Hindi Devanagari
    "इदरीस", "इद्रीस",
]

# Core name variants for fuzzy matching (shorter list, used with SequenceMatcher)
_FUZZY_TARGETS = ["idrees", "idris", "edrees", "adrees", "idriss", "edris"]
_FUZZY_THRESHOLD = 0.92  # High threshold to prevent false positives like 'i agree'
_MIN_CANDIDATE_LENGTH = 5  # Do not check short common words like 'are', 'the', 'is'

# Words that falsely trigger fuzzy match and sound similar
_IGNORE_LIST = [
    "i agree", "address", "degrees", "a tree", "agrees", "entries",
    "increase", "interests", "actress", "mattress", "digress",
    "egress", "redress", "ingress", "tigress", "distress",
]


# (Removed legacy whisper and gemini imports)


def load_whisper_model(model_name=None):
    """Load the Faster-Whisper model. Call once at startup."""
    global _whisper_model
    if model_name is None:
        model_name = config.FASTER_WHISPER_MODEL

    from faster_whisper import WhisperModel
    print(f"[Brain] Loading Faster-Whisper model '{model_name}' on CPU... (this may take a moment)")
    _whisper_model = WhisperModel(model_name, device="cpu", compute_type="int8")
    print(f"[Brain] Faster-Whisper model '{model_name}' loaded successfully!")
    return _whisper_model


def _check_audio_energy(audio_path, threshold=None):
    """
    Check if a WAV file contains actual audio (not silence).
    Returns True if the RMS energy is above threshold.
    """
    if threshold is None:
        threshold = _AUDIO_ENERGY_THRESHOLD
    try:
        with wave.open(audio_path, 'rb') as wf:
            n_frames = wf.getnframes()
            if n_frames == 0:
                return False
            # Read all frames as raw bytes
            raw = wf.readframes(n_frames)
            # Convert to 16-bit signed integers
            n_samples = len(raw) // 2
            if n_samples == 0:
                return False
            samples = struct.unpack(f'<{n_samples}h', raw)
            # Compute RMS energy
            rms = (sum(s * s for s in samples) / n_samples) ** 0.5
            if rms < threshold:
                print(f"[Brain] Audio too quiet (RMS={rms:.1f} < {threshold}), skipping transcription.")
                return False
            return True
    except Exception as e:
        print(f"[Brain] Energy check error: {e}")
        return True  # On error, proceed with transcription


def _deduplicate_transcription(text):
    """
    Remove repeated sentences from Whisper output.
    Catches both consecutive AND non-consecutive duplicates.
    """
    if not text:
        return text
    # Split on sentence boundaries
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    if len(sentences) <= 1:
        return text

    # Count occurrences of each sentence (case-insensitive)
    seen = {}
    for s in sentences:
        key = s.strip().lower()
        seen[key] = seen.get(key, 0) + 1

    # If any single sentence appears 3+ times, it's a hallucination loop
    for key, count in seen.items():
        if count >= 3:
            print(f"[Brain] Filtered Whisper hallucination loop: '{key[:50]}' repeated {count}x")
            return ""

    # Remove ALL duplicates (keep only first occurrence of each)
    unique = []
    seen_set = set()
    for s in sentences:
        key = s.strip().lower()
        if key not in seen_set:
            unique.append(s)
            seen_set.add(key)

    return " ".join(unique)


def _is_whisper_garbage(text):
    """
    Central garbage detector. Returns True if the transcription should be discarded.
    Multi-layer check:
      1. Known garbage phrases
      2. Too much non-Latin/Arabic/Devanagari script (Korean, Chinese, etc.)
      3. Extremely short or meaningless
    """
    if not text or len(text.strip()) < 3:
        return True

    text_lower = text.lower().strip()

    # 1. Check known garbage phrases
    for phrase in _WHISPER_GARBAGE_PHRASES:
        if phrase in text_lower:
            # If the garbage phrase is >50% of the total text, discard
            if len(phrase) > len(text_lower) * 0.4:
                print(f"[Brain] Garbage detected (known phrase): '{text[:60]}'")
                return True

    # 2. Check for high ratio of non-useful script (Korean, Chinese, Japanese)
    # These appear when Whisper hallucinates on Urdu audio
    cjk_pattern = re.compile(r'[\u3000-\u9fff\uac00-\ud7af\u1100-\u11ff\u3130-\u318f]')
    cjk_chars = len(cjk_pattern.findall(text))
    total_chars = len(text.replace(' ', ''))
    if total_chars > 0 and cjk_chars / total_chars > 0.15:
        print(f"[Brain] Garbage detected (CJK script ratio {cjk_chars}/{total_chars}): '{text[:60]}'")
        return True

    # 3. Check for excessive repeated short words ("ok ok ok ok ok")
    words = text_lower.split()
    if len(words) >= 5:
        word_counts = {}
        for w in words:
            word_counts[w] = word_counts.get(w, 0) + 1
        # If any word is >50% of total words and total > 6 words
        for w, c in word_counts.items():
            if c > len(words) * 0.5 and len(words) > 6 and len(w) <= 4:
                print(f"[Brain] Garbage detected (repeated word '{w}' {c}x): '{text[:60]}'")
                return True

    return False


def clean_transcript_for_notes(text):
    """
    Clean up a transcript before saving to notes file.
    Removes Whisper filler, garbage, and non-useful content.
    """
    if not text:
        return ""

    # Remove CJK/Korean script hallucinations
    text = re.sub(r'[\u3000-\u9fff\uac00-\ud7af\u1100-\u11ff\u3130-\u318f]+', '', text)
    # Collapse multiple spaces
    text = re.sub(r'\s+', ' ', text).strip()

    # Remove common Whisper filler at sentence starts (can be multiple)
    # E.g. "Okay, so..." -> remove both
    filler_word = r"(?:okay|ok|so|um|uh|well|right|alright)"
    pattern = rf"^(?:{filler_word}[,.\s]*)+"
    
    lines = text.split('.')
    cleaned = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Check if line is just a filler word
        if re.fullmatch(rf"{filler_word}[,.\s]*", line, flags=re.IGNORECASE):
            continue
            
        line = re.sub(pattern, '', line, flags=re.IGNORECASE).strip()
        
        # Capitalize first letter if needed
        if line and len(line) > 3:
            if line[0].islower():
                line = line[0].upper() + line[1:]
            cleaned.append(line)

    result = '. '.join(cleaned).strip()
    if result and not result.endswith('.'):
        result += '.'
    return result if len(result) > 5 else ""


def transcribe_audio(audio_path, model=None):
    """
    Transcribe a WAV audio file using Faster-Whisper.
    Includes energy check, language hints, and multi-layer garbage filtering.
    """
    if model is None:
        model = _whisper_model
    if model is None:
        model = load_whisper_model()

    # Energy check: skip silent audio entirely
    if not _check_audio_energy(audio_path):
        return ""

    try:
        segments, info = model.transcribe(
            audio_path,
            beam_size=8,            # Higher beam = better accuracy for mixed language
            language="ur",          # Urdu hint — Whisper handles English terms within Urdu
            vad_filter=True,        # Ignore silence to prevent Whisper hallucinations
            vad_parameters=dict(
                min_silence_duration_ms=500,
                speech_pad_ms=200,
            ),
            initial_prompt=(
                "Yeh ek university lecture hai. Teacher Urdu aur English mix mein baat karte hain. "
                "Student ka naam Idrees hai. Topics: AI agents, environment types, "
                "software engineering, programming. Observable environment, reflex agent, "
                "goal-based agent, utility-based agent."
            ),
            condition_on_previous_text=False  # Prevents hallucination loops in short chunks
        )
        text = " ".join([segment.text for segment in segments]).strip()

        # Layer 1: Deduplicate repeated sentences
        text = _deduplicate_transcription(text)
        if not text:
            return ""

        # Layer 2: Check for known garbage patterns
        if _is_whisper_garbage(text):
            return ""

        if text:
            print(f"[Brain] Transcription: \"{text[:100]}{'...' if len(text) > 100 else ''}\"")
        return text
    except Exception as e:
        print(f"[Brain] Transcription error: {e}")
        return ""


def _fuzzy_name_match(text):
    """
    Use difflib.SequenceMatcher to check if any word or n-gram in the text
    is similar enough to a known name variant.
    Returns the matched word if found, else None.
    """
    words = text.lower().split()
    # Check individual words and 2-word combinations
    candidates = list(words)
    for i in range(len(words) - 1):
        candidates.append(words[i] + words[i + 1])  # joined bigram
        candidates.append(words[i] + " " + words[i + 1])  # spaced bigram

    for candidate in candidates:
        if len(candidate) < _MIN_CANDIDATE_LENGTH:
            continue
        if candidate in _IGNORE_LIST:
            continue
        for target in _FUZZY_TARGETS:
            ratio = difflib.SequenceMatcher(None, candidate, target).ratio()
            if ratio >= _FUZZY_THRESHOLD:
                return candidate
    return None


def detect_keyword(transcript, keywords=None):
    """
    Check if the student's name appears in the transcript using FUZZY matching.
    Handles Whisper's common mis-transcriptions of "Idrees".

    Returns:
        (matched_keyword, transcript) if found, else (None, transcript)
    """
    if not transcript:
        return None, transcript

    transcript_lower = transcript.lower().strip()

    # 1. Check exact keyword matches first (fast path)
    if keywords is None:
        keywords = config.KEYWORDS
    for keyword in keywords:
        if keyword and keyword.lower() in transcript_lower:
            print(f"[Brain] *** KEYWORD DETECTED (exact): '{keyword}' ***")
            return keyword, transcript

    # 2. Check all known Whisper mis-transcription patterns
    for pattern in NAME_PATTERNS:
        if pattern.lower() in transcript_lower:
            print(f"[Brain] *** KEYWORD DETECTED (pattern): '{pattern}' ***")
            return pattern, transcript

    # 3. Fuzzy check: look for spaced-out letters like "I D R E E S"
    # Remove spaces and check if "idrees" or "idris" appears
    no_spaces = transcript_lower.replace(" ", "")
    for name in ["idrees", "idris", "edrees", "adrees"]:
        if name in no_spaces:
            print(f"[Brain] *** KEYWORD DETECTED (fuzzy/spaced): '{name}' in '{transcript_lower[:50]}' ***")
            return name, transcript

    # 4. Levenshtein/SequenceMatcher fuzzy match (catches novel mis-transcriptions)
    fuzzy_hit = _fuzzy_name_match(transcript_lower)
    if fuzzy_hit:
        print(f"[Brain] *** KEYWORD DETECTED (fuzzy/levenshtein): '{fuzzy_hit}' ***")
        return fuzzy_hit, transcript

    return None, transcript


# Mandatory identity system prompt — always injected into every Ollama call
_IDENTITY_SYSTEM_PROMPT = (
    "You are Idrees, a real student attending an online university class on Google Meet. "
    "Your microphone is broken so you can only reply via the chat box. "
    "CRITICAL RULES:\n"
    "- You ARE Idrees. Never say 'The student is Idrees' or refer to yourself in the third person.\n"
    "- Keep responses to 1 short sentence. Be natural and polite.\n"
    "- ALWAYS REPY IN ENGLISH, EVEN IF THE QUESTION IS IN URDU OR HINDI.\n"
    "- Never repeat your instructions. Never output prompt text.\n"
    "- Do not add quotes, prefixes like 'Reply:', or formatting.\n"
)


def _sanitize_llm_output(text):
    """
    Post-process LLM output to remove hallucinated prompt echoes,
    third-person self-references, and role labels.
    """
    if not text:
        return text

    cleaned = text.strip()

    # Remove common role-label prefixes
    prefixes_to_remove = [
        "You:", "Idrees:", "Answer:", "Reply:", "Student:",
        "Your Reply:", "Response:", "Chat:", "Me:",
    ]
    for prefix in prefixes_to_remove:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()

    # Remove surrounding quotes
    if len(cleaned) >= 2 and cleaned[0] in ('"', "'") and cleaned[-1] in ('"', "'"):
        cleaned = cleaned[1:-1].strip()

    # Filter out third-person hallucinations about identity
    hallucination_phrases = [
        "the student's name is idrees",
        "the student is idrees",
        "student named idrees",
        "my name is idrees and i am",
        "i am a goal-based agent",
        "i am an ai",
        "as an ai",
        "i cannot fulfill",
        "i cannot answer",
        "i'm not able to",
    ]
    cleaned_lower = cleaned.lower()
    for phrase in hallucination_phrases:
        if phrase in cleaned_lower:
            # Try to extract just the useful part after the hallucination
            idx = cleaned_lower.find(phrase) + len(phrase)
            remainder = cleaned[idx:].strip().lstrip('.,;:!? ')
            if remainder and len(remainder) > 5:
                cleaned = remainder
            else:
                # Entire response is a hallucination — return None to trigger fallback
                print(f"[Brain] Filtered hallucinated LLM output: '{text[:60]}'")
                return None

    return cleaned.strip() if cleaned.strip() else None


def ask_ollama(prompt, context="", system_prompt="", max_tokens=60, is_summary=False):
    """
    Send a prompt to the local Ollama LLM.
    Always injects the identity system prompt unless is_summary=True.
    """
    import ollama

    if is_summary:
        # Specialized prompt for generating the final high-detail lecture notes
        full_system = (
            "You are an expert academic note-taker. Your goal is to transform raw class logs "
            "(OCR and Transcripts) into a perfectly organized, highly detailed study guide. "
            "Use clear Markdown headers (# ## ###), bullet points, and bold text for key terms. "
            "Preserve ALL technical details, numbers, and definitions from the source text. "
            "Organize the content logically by topic."
        )
    else:
        # Standard identity for live chat interaction
        full_system = _IDENTITY_SYSTEM_PROMPT
    
    if system_prompt:
        full_system += "\n" + system_prompt

    messages = [{"role": "system", "content": full_system}]

    full_user_content = prompt
    if context:
        full_user_content = f"Recent lecture context:\n{context}\n\nCurrent message:\n{prompt}"

    messages.append({"role": "user", "content": full_user_content})

    try:
        response = ollama.chat(
            model=config.OLLAMA_MODEL,
            messages=messages,
            options={
                "temperature": 0.2,   # Low temperature = factual, less hallucination
                "top_p": 0.5,
                "num_predict": max_tokens,    # Higher for questions, lower for attendance
            }
        )
        raw_reply = response.get('message', {}).get('content', '').strip()
        print(f"[Brain] Ollama raw: \"{raw_reply[:100]}{'...' if len(raw_reply) > 100 else ''}\"")

        # Sanitize output
        reply = _sanitize_llm_output(raw_reply)
        if reply:
            print(f"[Brain] Ollama cleaned: \"{reply[:100]}\"")
        return reply
    except Exception as e:
        print(f"[Brain] Ollama error: {e}")
        return None

def generate_attendance_response(transcript):
    """When name/roll is called for attendance, generate a chat response."""
    prompt = (
        "The teacher just called your name for attendance. "
        "Reply in 1 short sentence confirming you are present. "
        "Mention your mic is broken so you're using chat. "
        f"Transcript: \"{transcript[:200]}\"\n"
        "Example good replies: 'Present sir, mic issue hai.', 'Ji sir, main present hoon.'"
    )
    response = ask_ollama(prompt)
    return response if response else config.RESPONSE_TEXT


def generate_question_response(transcript, context=""):
    """When the teacher asks a question or makes casual conversation, generate a real answer."""
    prompt = f"Teacher said: \"{transcript[:400]}\"\n"
    if context:
        # Reduced context from 800 chars to 400 chars for much faster LLM processing
        prompt += f"\nRecent lecture context:\n{context.strip()[-400:]}\n"
    prompt += (
        "\nThe teacher just asked you a question. "
        "Answer it correctly using ONLY the recent lecture context if possible. "
        "Reply naturally in 1 short sentence IN ENGLISH. "
        "Do NOT just say 'present', give a brief academic answer."
    )

    # Use max_tokens=60 to force concise, fast replies
    response = ask_ollama(prompt, context="", max_tokens=60)  # context already embedded in prompt
    if not response:
        return config.QUESTION_FALLBACK_TEXT
    return response


def clean_chat_text(raw_text):
    """
    Strip Google Meet UI artifacts and AI hallucinated prompt leaks from chat text.
    """
    lines = raw_text.strip().split("\n")
    ui_artifacts_lower = [a.lower() for a in config.CHAT_UI_ARTIFACTS]
    
    # Words that indicate the AI accidentally copied its prompt instructions into the chat
    hallucination_markers = [
        "student named idrees",
        "student's name is idrees",
        "the student is idrees",
        "teacher's message",
        "teacher said:",
        "teacher:",
        "you: ",
        "i am a goal-based agent",
        "lecture description",
        "i cannot fulfill your request",
        "recent lecture context",
        "your reply:",
        "as an ai",
        "i'm an ai",
        "answer the teacher",
        "critical rules",
        "system prompt",
    ]
    
    cleaned_lines = []
    for line in lines:
        line_stripped = line.strip()
        line_lower = line_stripped.lower()
        
        # Skip UI artifacts
        if line_lower in ui_artifacts_lower:
            continue
        if any(line_lower.startswith(a) for a in ui_artifacts_lower):
            continue
            
        # Skip hallucinated prompt echoes
        if any(marker in line_lower for marker in hallucination_markers):
            print(f"[Brain] Filtering hallucinated output from chat: {line_stripped[:50]}")
            continue
            
        if len(line_stripped) <= 3 and line_lower in ["pin", "ke", "..."]:
            continue
        cleaned_lines.append(line_stripped)
    return " ".join(cleaned_lines).strip()


def classify_and_respond(transcript, context=""):
    """
    Determine if the trigger is for attendance or a question,
    and generate the appropriate response.
    """
    # Quick heuristics
    attendance_words = [
        "attendance", "attendence", "present", "absent", "haziri", "haazri",
        "roll call", "roll number", "hazar", "hajir", "are you present",
        "are u present", "kya aap present", "hazir"
    ]
    question_words = [
        "bataiye", "bata do", "batao", "kya hai", "kitne types", "tell me",
        "can you explain", "what is", "how many", "explain", "samjha do",
        "question for you"
    ]

    transcript_lower = transcript.lower()
    is_attendance = any(word in transcript_lower for word in attendance_words)

    # If it contains an explicit question word, prioritize question
    if any(word in transcript_lower for word in question_words) and not is_attendance:
        print("[Brain] Classified as: EXPLICIT QUESTION — asking Ollama...")
        response = generate_question_response(transcript, context=context)
        if response:
            return response
        return config.QUESTION_FALLBACK_TEXT

    if is_attendance:
        print("[Brain] Classified as: ATTENDANCE")
        # Return static string immediately to save LLM processing time!
        return "Present sir, mic kharab hai."
    else:
        print("[Brain] Classified as: QUESTION/OTHER — asking Ollama...")
        response = generate_question_response(transcript, context=context)
        if response:
            return response
        # Fallback: DON'T say "Present" for a question — say something appropriate
        return config.QUESTION_FALLBACK_TEXT


def classify_chat_message(chat_text, context=""):
    """
    Classify a chat message directed at the student.
    Uses smarter heuristics for chat context (vs audio context).
    Returns the appropriate response string.
    """
    cleaned = clean_chat_text(chat_text)
    if not cleaned:
        return None

    cleaned_lower = cleaned.lower()

    # Pure attendance check: very short message with just name, or explicit attendance words
    attendance_words = [
        "attendance", "attendence", "present", "are you present",
        "are u present", "roll call", "haziri", "haazri", "hazir"
    ]
    question_words = [
        "bataiye", "bata do", "batao", "kya hai", "kitne types", "tell me",
        "can you explain", "what is", "how many", "explain", "samjha do",
        "question for you"
    ]

    # Check if it's JUST the name (or name + greeting) — that's attendance
    name_only_patterns = [
        r'^(hello\s+)?idrees[.!?\s]*$',
        r'^idrees\s+(are\s+u|are\s+you)\s+present',
        r'^(hello|hi|hey)\s+idrees\s+(are\s+u|are\s+you)',
    ]
    is_attendance = any(word in cleaned_lower for word in attendance_words)
    is_name_only = any(re.search(pat, cleaned_lower) for pat in name_only_patterns)

    if any(word in cleaned_lower for word in question_words) and not (is_attendance or is_name_only):
        print("[Brain] Chat classified as: EXPLICIT QUESTION — asking Ollama...")
        response = generate_question_response(cleaned, context=context)
        return response if response else config.QUESTION_FALLBACK_TEXT

    if is_name_only:
        print("[Brain] Chat classified as: ATTENDANCE (name-only pattern)")
        response = generate_attendance_response(cleaned)
        return response if response else config.RESPONSE_TEXT

    if is_attendance:
        print("[Brain] Chat classified as: ATTENDANCE")
        response = generate_attendance_response(cleaned)
        return response if response else config.RESPONSE_TEXT

    # Otherwise it's a question — ask Ollama
    print("[Brain] Chat classified as: QUESTION — asking Ollama...")
    response = generate_question_response(cleaned, context=context)
    return response if response else config.QUESTION_FALLBACK_TEXT


class TranscriptBuffer:
    """
    Rolling buffer that keeps the last N seconds of transcript text.
    Thread-safe via simple append-and-trim.
    """

    def __init__(self, max_duration_seconds=60, chunk_duration=10):
        self.chunks = []  # List of (timestamp, text) tuples
        self.max_duration = max_duration_seconds
        self.chunk_duration = chunk_duration

    def add(self, text):
        """Add a new transcript chunk with current timestamp."""
        if text and text.strip():
            self.chunks.append((time.time(), text.strip()))
            self._trim()

    def _trim(self):
        """Remove chunks older than max_duration."""
        cutoff = time.time() - self.max_duration
        self.chunks = [(t, txt) for t, txt in self.chunks if t > cutoff]

    def get_recent(self, seconds=30):
        """Get transcript text from the last N seconds."""
        cutoff = time.time() - seconds
        recent = [txt for t, txt in self.chunks if t > cutoff]
        return " ".join(recent)

    def get_all(self):
        """Get the full buffer content."""
        return " ".join(txt for _, txt in self.chunks)

    def __str__(self):
        return self.get_all()

    def __len__(self):
        return len(self.chunks)


def generate_comprehensive_notes(notes_file_path):
    """
    Reads the daily notes file and asks Ollama to generate a high-detail, 
    well-organized academic document from the whole session log.
    """
    if not os.path.exists(notes_file_path):
        return None
        
    try:
        with open(notes_file_path, "r", encoding="utf-8") as f:
            notes_content = f.read()
            
        prompt = (
            "COMMAND: Generate COMPREHENSIVE CLASS NOTES from the provided raw logs below. "
            "DO NOT SUMMARIZE. I need the WHOLE content organized well. "
            "Include every technical detail, every slide point, and every important explanation. "
            "Format with clear headers and bullet points.\n\n"
            "RAW CLASS LOGS:\n"
            f"{notes_content}\n"
        )
        
        print(f"[Brain] Generating high-detail PDF notes from {os.path.basename(notes_file_path)}...")
        return ask_ollama(prompt, max_tokens=2048, is_summary=True)
    except Exception as e:
        print(f"[Brain] Error generating comprehensive notes: {e}")
        return None


if __name__ == "__main__":
    # Quick test
    print("=== Brain Module Test ===")

    # Test 1: Keyword detection (exact)
    print("\n--- Test: Exact Keyword Detection ---")
    kw, _ = detect_keyword("Is Idrees present today?")
    print(f"Detected: {kw}")  # Should print "Idrees"

    # Test 2: Keyword detection (fuzzy — Whisper mis-transcriptions)
    print("\n--- Test: Fuzzy Keyword Detection ---")
    test_transcripts = [
        "Mohammad Idris, are you present?",
        "Muhammad in three is. Hello",
        "I D R E E S are you there?",
        "Mohamed Idris Idr Tavalli S. Are you present?",
        "There is a question for you. I D R E E S.",
        "Please open chapter 5",  # Should NOT match
    ]
    for t in test_transcripts:
        kw, _ = detect_keyword(t)
        print(f"  '{t[:50]}...' => {kw}")

    # Test 3: Ollama
    print("\n--- Test: Ollama Local LLM ---")
    resp = ask_ollama("Say 'test successful' in exactly two words.")
    print(f"Response: {resp}")

    print("\n=== Brain Module Test Complete ===")
