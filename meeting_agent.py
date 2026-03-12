from playwright.sync_api import sync_playwright
import time
import config

class MeetBot:
    def __init__(self, meeting_url: str):
        self.meeting_url = meeting_url
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.chat_history_set = set() # To store processed chat messages

    def setup(self):
        self.playwright = sync_playwright().start()
        
        # Bypassing Bot Detection
        # We disable AutomationControlled features and use a standard user-agent
        context_args = {
            "headless": config.HEADLESS_BROWSER,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--use-fake-ui-for-media-stream",  # Auto-grant camera/mic permissions internally
                "--disable-infobars"
            ],
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "permissions": ['microphone', 'camera']
        }
        
        # Launching as a Persistent Context ensures it isn't an "Incognito" window.
        # This saves cookies and local storage so the bot can stay logged into Google Meet.
        self.context = self.playwright.chromium.launch_persistent_context(
            user_data_dir="./playwright_profile",
            **context_args
        )
        # Persistent context comes with a default page
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()

    def teardown(self):
        if self.page: self.page.close()
        if self.context: self.context.close()
        if self.browser: self.browser.close()
        if self.playwright: self.playwright.stop()

    def join_meeting(self):
        print(f"Navigating to {self.meeting_url}...")
        self.page.goto(self.meeting_url)

        # 0. Check for Google Login Prompt (since we are not in incognito anymore)
        # If it redirects to accounts.google.com, we must wait for the user to log in manually.
        if "accounts.google.com" in self.page.url:
            print("\n*** GOOGLE LOGIN REQUIRED ***")
            print("Please log into your Google Account in the opened Playwright browser.")
            print("The bot will pause and wait until you successfully land on the Google Meet page.\n")
            
            # Wait until the URL changes back to meet.google.com (meaning login succeeded)
            # Timeout is very long (5 mins) to give the user time to type passwords and 2FA
            try:
                self.page.wait_for_url("**/meet.google.com/**", timeout=300000)
                print("Login successful! Continuing to Google Meet...")
                self.page.wait_for_timeout(2000) # Give meet time to load after redirect
            except Exception as e:
                print("Timed out waiting for manual Google Login.")
                return False

        # 1. Pre-meeting Lobby Setup (Mute Mic & Cam)
        # Using Google Meet standard keyboard shortcuts is extremely robust against dynamic DOM changes
        print("Muting Microphone and Camera...")
        self.page.wait_for_timeout(3000) # Give page time to load media devices
        self.page.keyboard.press('Control+d') # Mute Microphone
        self.page.wait_for_timeout(1000)
        self.page.keyboard.press('Control+e') # Turn off Camera

        # 2. Join the Meeting
        print("Attempting to join meeting...")
        # Since button text might be "Ask to join" or "Join now", we check for both
        join_selectors = [
            'button:has-text("Ask to join")',
            'button:has-text("Join now")',
            '[aria-label="Ask to join"]',
            '[aria-label="Join now"]'
        ]
        
        clicked = False
        for selector in join_selectors:
            try:
                if self.page.locator(selector).is_visible():
                    self.page.locator(selector).click()
                    clicked = True
                    print(f"Clicked join button using: {selector}")
                    break
            except Exception:
                continue

        if not clicked:
            print("Could not find a join button. Continuing anyway...")

        # 3. Wait to be admitted (Wait until the chat button is visible)
        print("Waiting to be admitted into the room... (Timeout: 5 minutes)")
        chat_btn_selector = 'button[aria-label="Chat with everyone"]'
        try:
            self.page.wait_for_selector(chat_btn_selector, timeout=300000)
            print("Successfully entered the meeting!")
            
            # Open the chat pane
            self.page.click(chat_btn_selector)
            self.page.wait_for_timeout(2000)
            print("Chat pane opened.")
        except Exception as e:
            print(f"Timed out or failed to enter meeting room: {e}")
            self.teardown()
            return False
            
        return True

    def monitor_chat_and_reply(self):
        print(f"Monitoring chat for target word: '{config.TARGET_WORD}'...")
        
        # Robust selectors for reading messages and typing
        # Meet chat messages are usually inside elements with data-sender-id or specific structural classes
        # We can look for all text elements inside the chat panel. 
        # A simple approach is grabbing all divs that might be messages
        
        chat_input_selector = 'textarea[aria-label="Chat text input"]' # Fallbacks usually required, but this is standard
        
        # We loop continuously
        start_time = time.time()
        while time.time() - start_time < 3600: # Stay for max 1 hour (customize as needed)
            try:
                # To be resilient, we extract all text blocks in the chat region
                # Usually messages are grouped in div[data-message-id]
                messages = self.page.locator('div[data-message-id]').all_inner_texts()
                
                for msg_text in messages:
                    # Prevent loops: If we've seen this exact message block, skip
                    if msg_text in self.chat_history_set:
                        continue
                    
                    self.chat_history_set.add(msg_text)
                    
                    # Prevent agent from triggering itself
                    if config.RESPONSE_TEXT in msg_text:
                        continue
                    
                    # Check for our target word
                    if config.TARGET_WORD.lower() in msg_text.lower():
                        print(f"Spotting TARGET_WORD in message: '{msg_text}'")
                        print("Sending automated response...")
                        
                        # Type and send
                        chat_input_selectors = [
                            'textarea[aria-label="Send a message to everyone"]',
                            'textarea[name="chatTextInput"]',
                            'textarea[aria-label="Chat text input"]',
                            'textarea' # Generic fallback
                        ]
                        
                        input_found = False
                        for selector in chat_input_selectors:
                            try:
                                if self.page.locator(selector).is_visible():
                                    self.page.fill(selector, config.RESPONSE_TEXT)
                                    self.page.wait_for_timeout(500)
                                    self.page.keyboard.press('Enter')
                                    print(f"Agent sent response using selector: {selector}")
                                    input_found = True
                                    break
                            except Exception:
                                continue
                                
                        if not input_found:
                            print("ERROR: Agent tried to reply but could not find the chat input box!")
                        
                        # To avoid spamming, we can break or sleep
                        time.sleep(2)
                        
            except Exception as e:
                # Non-fatal error in parsing DOM loop
                pass
                
            time.sleep(2) # Polling interval for chat

        print("Meeting monitoring session complete.")

    def run(self):
        try:
            self.setup()
            if self.join_meeting():
                self.monitor_chat_and_reply()
        finally:
            self.teardown()

if __name__ == "__main__":
    # Test stub
    bot = MeetBot("https://meet.google.com/xxx-yyyy-zzz")
    bot.run()
