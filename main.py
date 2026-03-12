import sys
import os
from meeting_agent import MeetBot, process_pending_notes

def main():
    print("\n" + "="*60)
    print("  GOOGLE MEET AI ATTENDANCE AGENT  ")
    print("="*60 + "\n")

    # 1. Process any pending notes from crashes first
    process_pending_notes()

    # 2. Get the meeting URL
    if len(sys.argv) > 1:
        url = sys.argv[1]
    else:
        print("No URL provided via command line.")
        url = input("Please paste the Google Meet URL to join: ").strip()

    if not url or "meet.google.com" not in url:
        print("Error: Invalid Google Meet URL. Exiting.")
        return

    # 3. Launch the bot
    print(f"\n[Main] Launching AI Agent for: {url}")
    try:
        bot = MeetBot(url)
        bot.run()
    except KeyboardInterrupt:
        print("\n[Main] Shutdown signal received.")
    except Exception as e:
        print(f"\n[Main] FATAL ERROR: {e}")

if __name__ == "__main__":
    main()
