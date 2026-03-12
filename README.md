# Google Meet AI Agent — Full Classroom Proxy 🤖🎓

Building a "Full Classroom Proxy" for free using a clever mix of open-source tools and **Free Tier** APIs. It can **hear**, **see**, and **speak** (via chat) using the "Free-Forever" approach.

---

## 🛠️ The "Free" Tech Stack

| Feature | Tool | Why? |
| --- | --- | --- |
| **Browser Control** | **Playwright** | Free; automates Chrome to join and interact with Google Meet. |
| **Audio Capture** | **PulseAudio (Linux)** | Free system-level audio routing via virtual loopback. |
| **Speech-to-Text** | **OpenAI Whisper (Local)** | Completely free; runs on your CPU/GPU. |
| **Brain (Notes/Chat)** | **Gemini 1.5 Flash API** | Massive free tier (15 RPM) and 1M context window. |
| **OCR (Slides)** | **Tesseract OCR** | Open-source; converts slide images to text for free. |

---

## 📋 How It Works (The Procedure)

### 1. Route the "Teacher's Voice" to Python
Normally, your code can't "hear" what's coming out of the speakers. The agent creates a **Virtual Loopback** using `pactl` to create a virtual source. This allows Python to record the browser audio as if it were a microphone. Audio is recorded in **10-second chunks** continuously using `SoundFile` + `parec`.

### 2. Transcribe Audio for Free (Whisper)
Instead of paying for Google/AWS Speech-to-Text, we run **Whisper** locally.
- The script sends each 10-second audio chunk to Whisper.
- **Result:** You get a live text transcript of everything the teacher says.

### 3. The "Keyword Trigger" (Attendance & Questions)
The script scans the transcript for **"Idrees"** or your **Roll Number**.
- **If detected:** Immediately sends the last 30 seconds of transcript to the **Gemini API**.
- **Prompt:** *"The teacher just called my name. Here is the transcript: [Transcript]. If it's attendance, tell me to say 'Present, mic kharab hai'. If it's a question, give me a 1-sentence answer."*

### 4. Automated Chat Response
Once Gemini gives the answer:
- Uses Playwright to click the "Chat" icon.
- Types the response: `"Present, sir. Mic kharab hai isliye chat me reply kar raha hu."`
- Presses Enter to send.

### 5. Screen Notes (OCR)
To capture slides:
- Every 60 seconds, Playwright takes a screenshot: `page.screenshot(path="slides.png")`.
- Uses `pytesseract` to extract text from the slide.
- Saves this text into a daily `notes/` file along with the timestamp and the Whisper transcript.

---

## 📂 Project Structure

```text
.
├── main.py             # Orchestrator — Gmail polling & scheduling
├── meeting_agent.py    # The Bot — Playwright browser control & UI loop
├── audio_handler.py    # Captures system audio via PulseAudio loopback
├── brain.py            # Gemini API calls & Whisper transcription logic
├── config.py           # All configuration & feature toggles
├── email_watcher.py    # Gmail API — fetches meeting invitations
├── notes/              # Folder for daily class notes (auto-created)
├── requirements.txt    # Python dependencies
└── playwright_profile/ # Persistent browser profile (saves login)
```

---

## 🚀 Setup & Installation

### Step 1: Install System Dependencies (One-Time)
```bash
sudo apt update && sudo apt install -y tesseract-ocr portaudio19-dev
```

### Step 2: Install Python Dependencies
```bash
pip install -r requirements.txt
```

### Step 3: Set Up Your Gemini API Key
Get a **free** API key from [Google AI Studio](https://aistudio.google.com/).

Then either set it in `config.py`:
```python
GEMINI_API_KEY = "your_key_here"
```
Or export it as an environment variable:
```bash
export GEMINI_API_KEY="your_key_here"
```

### Step 4: Configure Your Identity
Edit `config.py` to set your name and roll number:
```python
STUDENT_NAME = "Idrees"
ROLL_NUMBER = "21-CS-42"  # Leave empty if not needed
```

### Step 5: Set Up Gmail Credentials (For Auto-Join)
Place your `credentials.json` from Google Cloud Console in the project root. On first run, it will open a browser for Gmail authentication.

---

## 🏃 How to Run

### Option 1: Automatic Mode (Recommended)
Polls your email for Google Meet invitation links and joins automatically at the scheduled time:
```bash
python3 main.py
```

### Option 2: Direct Mode
Join a specific meeting immediately:
```bash
python3 meeting_agent.py "https://meet.google.com/abc-defg-hij"
```

### What Happens When the Bot Joins:
1. ✅ Mutes mic & camera automatically
2. ✅ Clicks "Join" / "Ask to join"
3. ✅ Opens the chat pane
4. 🔊 **Thread 1 (Audio):** Records audio → Whisper transcribes → Detects your name → Gemini generates response → Sends in chat
5. 📸 **Thread 2 (OCR):** Takes screenshots every 60s → Extracts text → Saves to `notes/`
6. 💬 **Thread 3 (Chat):** Monitors chat for attendance keywords → Auto-replies "Present"

---

## ⚠️ The "Free" Constraint Warning

1. **Hardware:** Running Whisper (Speech-to-Text) locally uses a lot of RAM/CPU. If your laptop slows down, change `WHISPER_MODEL = "tiny"` in `config.py`.
2. **Gemini API Key:** It's free as long as you don't exceed ~15 requests per minute.
3. **First Run:** Whisper will download the model file (~150MB for `base`) on the first run.

---

---

## 🛠️ Troubleshooting

### "You can't join this video call" Error
If you see this error:
1. **Manual Login:** Run with `HEADLESS_BROWSER = False` in `config.py`. When the browser opens, log in to your Google Account manually if prompted. The session will be saved in `playwright_profile/`.
2. **Account Match:** Ensure the account you log into is the same one that receives the meeting invitations.
3. **Link Validity:** Check if the meeting link is still active.

### Whisper is Slow / Laptop Lagging
- Change `WHISPER_MODEL = "tiny"` in `config.py`. This uses significantly less RAM.

### Audio Not Capturing
- Ensure your system uses **PulseAudio** (default on Ubuntu).
- Run `pactl info` in terminal; it should show a running server.

---

## ⚖️ Disclaimer
This project is for **educational purposes only**. Use responsibly and ensure you are complying with your institution's policies regarding online classes.
