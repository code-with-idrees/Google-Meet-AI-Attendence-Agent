import os

# --- CHAT INTERACTION SETTINGS ---
# The word the agent monitors for in the Google Meet chat
TARGET_WORD = "Attendence"
# The predefined response to send once the target word is spotted
RESPONSE_TEXT = "Present, Mic Kharab Hai"
QUESTION_FALLBACK_TEXT = "Sorry sir, mera mic kharab hai aur answer abhi nahi aa raha."

# Google Meet UI artifacts that get captured alongside chat messages — ignore these
CHAT_UI_ARTIFACTS = [
    "keep", "pin message", "hover over a message",
    "unpin message", "delete message", "copy message",
    "report message", "pin", "more options",
]

# --- BROWSER AUTOMATION SETTINGS ---
# Set to True when deploying on a server without GUI
HEADLESS_BROWSER = False

# ===========================================================
# NEW: Full Classroom Proxy Settings
# ===========================================================

# --- AI BRAIN SETTINGS (FULLY LOCAL) ---
# Your chosen Ollama local model
OLLAMA_MODEL = 'llama3.2:1b'

# Faster-Whisper model: State-of-the-art fast multilingual model.
# Pointing to the locally downloaded folder 'whisper-model-turbo'
FASTER_WHISPER_MODEL = "whisper-model-turbo"

# --- KEYWORD DETECTION ---
# Your name and roll number — the bot listens for these in the teacher's speech
STUDENT_NAME = "Idrees"
ROLL_NUMBER = ""  # e.g. "21-CS-42" — leave empty if not needed

# Build the keywords list (filters out empty strings)
# Include Urdu/Hindi script variants since Whisper may transcribe in native script
KEYWORDS_EXTRA = [
    "ادریس",      # Urdu script for "Idrees"
    "ادريس",      # Arabic script variant
    "इदरीस",     # Hindi Devanagari
    "muhammad idrees",
    "Muhammad Idrees",
]
KEYWORDS = [kw for kw in [STUDENT_NAME, ROLL_NUMBER] + KEYWORDS_EXTRA if kw]

# --- AUDIO SETTINGS ---
# Duration of each audio chunk recording
AUDIO_CHUNK_SECONDS = 15
# Sample rate for Whisper (16kHz is optimal for Whisper)
AUDIO_SAMPLE_RATE = 16000

# --- OCR / NOTES SETTINGS ---
# How often to capture and OCR the screen (seconds)
OCR_INTERVAL_SECONDS = 60
# Directory to save daily class notes
NOTES_DIR = "notes"

# --- RELIABILITY SETTINGS ---
# Minimum WAV file size in bytes (below this = empty/corrupt recording)
AUDIO_MIN_FILE_SIZE = 1024
# Cooldown in seconds between AI responses (prevents double-triggering)
RESPONSE_COOLDOWN_SECONDS = 30

# --- FEATURE TOGGLES ---
# Enable/disable specific features (useful for debugging)
ENABLE_AUDIO_CAPTURE = True
ENABLE_OCR_NOTES = True
ENABLE_CHAT_MONITOR = True
