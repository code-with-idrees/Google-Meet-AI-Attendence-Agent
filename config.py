import os

# --- EMAIL SETTINGS ---
# The specific email address that will send the invitations
TARGET_SENDER_EMAIL = "i230814@isb.nu.edu.pk"
# How often to check inbox for new invitations (in seconds)
POLL_INTERVAL_SECONDS = 60

# --- CHAT INTERACTION SETTINGS ---
# The word the agent monitors for in the Google Meet chat
TARGET_WORD = "Attendance"
# The predefined response to send once the target word is spotted
RESPONSE_TEXT = "Present"

# --- BROWSER AUTOMATION SETTINGS ---
# Set to True when deploying on a server without GUI
HEADLESS_BROWSER = False

# Scopes required for Gmail API
SCOPES = ['https://www.googleapis.com/auth/gmail.modify']

# Output files for auth
CREDENTIALS_FILE = 'credentials.json'
TOKEN_FILE = 'token.json'
