<h1 align="center">🤖 Google Meet AI Attendance Agent</h1>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.9%2B-blue?style=for-the-badge&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/Playwright-Automation-green?style=for-the-badge&logo=playwright&logoColor=white" />
  <img src="https://img.shields.io/badge/Gmail-API-red?style=for-the-badge&logo=gmail&logoColor=white" />
  <img src="https://img.shields.io/badge/Google%20Cloud-Platform-orange?style=for-the-badge&logo=googlecloud&logoColor=white" />
  <img src="https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge" />
</p>

<p align="center">
  An autonomous AI agent that monitors your Gmail inbox for Google Meet invitations from a specific sender, joins the meeting automatically at the correct time, and responds in the meeting chat when a configured trigger word is detected.
</p>

---

## ✨ Features

- 📧 **Smart Email Monitoring** — Polls Gmail via the official Gmail API for unread Meet invitations from a specified sender
- 🔗 **Automatic Meet Link Extraction** — Parses the email body to extract Google Meet URLs using robust regex patterns
- ⏰ **Intelligent Scheduling** — Schedules the bot to join the meeting at the right time rather than immediately
- 🤖 **Bot Detection Bypass** — Uses Playwright stealth args, custom user-agents, and persistent profiles to avoid detection
- 💬 **Chat Monitor & Auto-Reply** — Continuously monitors the in-meeting chat and sends a configured reply when a trigger word appears
- 🔒 **OAuth 2.0 Secure Authentication** — Uses Google's official OAuth flow — no passwords stored

---

## 🏗️ Architecture

```
┌───────────────────────────────────────────────────────────────────┐
│                         main.py (Orchestrator)                    │
│                                                                   │
│   ┌─────────────────────────┐   ┌───────────────────────────┐    │
│   │     email_watcher.py    │   │      meeting_agent.py     │    │
│   │                         │   │                           │    │
│   │  Gmail API  ──► Poll    │   │  Playwright  ──► Join     │    │
│   │  Inbox every N seconds  │──►│  Browser     ──► Monitor  │    │
│   │  Extract Meet URL       │   │  Chat DOM    ──► Reply    │    │
│   └─────────────────────────┘   └───────────────────────────┘    │
│                    ▲                           ▲                   │
│                    └───────── config.py ───────┘                  │
└───────────────────────────────────────────────────────────────────┘
```

### Component Overview

| File | Responsibility |
|---|---|
| `main.py` | Orchestrator — runs the scheduling loop and coordinates all components |
| `email_watcher.py` | Authenticates with Gmail API and polls inbox for Meet invitations |
| `meeting_agent.py` | Browser automation — joins meeting, monitors chat, sends replies |
| `config.py` | Single source of truth for all settings (sender email, trigger word, etc.) |

---

## 🚀 Getting Started

### Prerequisites

- Python **3.9** or higher
- A **Google Cloud Platform** account (free tier is fine)
- A Google account whose inbox will be monitored

---

### Step 1 — Clone the Repository

```bash
git clone https://github.com/YOUR_USERNAME/google-meet-ai-agent.git
cd google-meet-ai-agent
```

---

### Step 2 — Google Cloud Setup (Gmail API)

> **This is a one-time setup.**

1. Go to [Google Cloud Console](https://console.cloud.google.com/) and create a new project (e.g., `MeetAgent`).
2. Navigate to **APIs & Services → Library**, search for **Gmail API**, and click **Enable**.
3. Navigate to **APIs & Services → OAuth consent screen**:
   - Choose **External** as User Type.
   - Fill in an App Name (e.g., `MeetAgent`) and your email as the support email.
   - Under **Audience → Test users**, click **+ ADD USERS** and add your Gmail address.
4. Navigate to **APIs & Services → Clients**:
   - Click **Create OAuth client**.
   - Application type: **Desktop app**.
   - Click **Create**, then click the **Download JSON** icon next to the new client.
5. Rename the downloaded file to **`credentials.json`** and place it in the root of this project.

---

### Step 3 — Install Dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

---

### Step 4 — Configure the Agent

Open `config.py` and edit the following settings to match your use case:

```python
# The email address that sends the Google Meet invitations
TARGET_SENDER_EMAIL = "sender@example.com"

# The word the agent watches for in the meeting chat
TARGET_WORD = "Attendance"

# The message the agent automatically sends when TARGET_WORD is spotted
RESPONSE_TEXT = "Present"

# How often to poll Gmail inbox (in seconds)
POLL_INTERVAL_SECONDS = 60
```

---

### Step 5 — Run the Agent

```bash
python main.py
```

> **First run:** A browser window will open asking you to sign in to your Google Account and authorize the app. After you approve, a `token.json` file is created locally so future runs are fully headless.

**Expected output:**

```
Initializing Google Meet AI Agent Orchestrator...
Gmail API Authenticated successfully!
Polling inbox every 60 seconds.
Checking inbox for new Meet invitations...
Agent is now running and waiting for scheduled tasks. Press Ctrl+C to exit.
```

---

## ⚙️ Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `TARGET_SENDER_EMAIL` | — | Email address to watch for invitations from |
| `POLL_INTERVAL_SECONDS` | `60` | How often to check Gmail inbox (in seconds) |
| `TARGET_WORD` | `"Attendance"` | Trigger word to watch for in the Meet chat |
| `RESPONSE_TEXT` | `"Present"` | Automated reply to send when trigger word is found |
| `HEADLESS_BROWSER` | `False` | Set to `True` when deploying on a headless server |

---

## 🖥️ Deploying on a Headless Server

When running on a cloud server (e.g., AWS EC2, DigitalOcean) that has no GUI:

1. Set `HEADLESS_BROWSER = True` in `config.py`.
2. Copy your `credentials.json` and pre-authorized `token.json` to the server.
3. Install dependencies and run with `python main.py`.
4. Optionally, wrap the command in a `systemd` service or `screen` session to keep it alive.

---

## 📁 Project Structure

```
google-meet-ai-agent/
├── main.py               # Orchestrator and scheduling loop
├── email_watcher.py      # Gmail API polling and Meet link extraction
├── meeting_agent.py      # Playwright browser automation for Google Meet
├── config.py             # All user-configurable settings
├── requirements.txt      # Python dependencies
├── credentials.json      # ⚠️ NOT committed — your OAuth client secrets
├── token.json            # ⚠️ NOT committed — generated on first run
└── playwright_profile/   # ⚠️ NOT committed — persistent browser session
```

> ⚠️ **Security Note:** `credentials.json`, `token.json`, and the `playwright_profile/` folder are listed in `.gitignore` and must **never** be committed to a public repository.

---

## 🔒 Security & Privacy

- All Gmail access is done via **OAuth 2.0** — no passwords are ever stored.
- The `token.json` file contains your session credentials. Keep it private.
- The `playwright_profile/` folder contains your Google session cookies. Never share it.

---

## 🙏 Acknowledgements

- [Google Gmail API](https://developers.google.com/gmail/api) for secure, official inbox access
- [Playwright for Python](https://playwright.dev/python/) for powerful browser automation
- [schedule](https://schedule.readthedocs.io/) for lightweight Python job scheduling

---

## 📄 License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.
