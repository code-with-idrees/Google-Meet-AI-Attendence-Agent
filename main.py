import time
import schedule
import config
from email_watcher import authenticate_gmail, fetch_meet_invitations
from meeting_agent import MeetBot

# Keep track of message IDs we have already processed to avoid duplicating scheduling
PROCESSED_MESSAGES = set()

def launch_bot(meeting_url):
    print(f"\n--- ATTEMPTING TO JOIN SCHEDULED MEETING at {meeting_url} ---")
    bot = MeetBot(meeting_url)
    bot.run()
    # Once .run() returns, returning schedule.CancelJob ensures it doesn't repeat this daily
    return schedule.CancelJob

def check_inbox_job(service):
    print("Checking inbox for new Meet invitations...")
    meetings = fetch_meet_invitations(service)
    
    for meeting in meetings:
        msg_id = meeting['msg_id']
        if msg_id in PROCESSED_MESSAGES:
            continue
            
        url = meeting['url']
        scheduled_time = meeting['scheduled_time'] # A datetime object
        
        # We need to format the time for the 'schedule' library.
        # Format string for HH:MM:SS (24-hour)
        time_str = scheduled_time.strftime("%H:%M:%S")
        
        # Schedule the bot to launch
        print(f"Scheduling new bot instance for {url} at {time_str}")
        schedule.every().day.at(time_str).do(launch_bot, meeting_url=url)
        
        PROCESSED_MESSAGES.add(msg_id)

def main():
    print("Initializing Google Meet AI Agent Orchestrator...")
    
    # 1. Authenticate Gmail
    try:
        gmail_service = authenticate_gmail()
        print("Gmail API Authenticated successfully!")
    except Exception as e:
        print(f"FATAL: Could not authenticate Gmail API: {e}")
        return

    # 2. Schedule the inbox polling
    print(f"Polling inbox every {config.POLL_INTERVAL_SECONDS} seconds.")
    schedule.every(config.POLL_INTERVAL_SECONDS).seconds.do(check_inbox_job, service=gmail_service)
    
    # Call once immediately on startup
    check_inbox_job(gmail_service)

    # 3. Main Loop
    print("Agent is now running and waiting for scheduled tasks. Press Ctrl+C to exit.")
    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nAgent orchestrator shutting down...")

if __name__ == "__main__":
    main()
