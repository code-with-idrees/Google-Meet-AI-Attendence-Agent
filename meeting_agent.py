"""
meeting_agent.py — Full Classroom Proxy: Google Meet Bot with Audio + AI + OCR.

Joins Google Meet, listens to the teacher (via Whisper), detects keywords,
responds via chat (powered by Gemini), and takes OCR notes of slides.
"""

from playwright.sync_api import sync_playwright
import time
import threading
import os
import datetime
import config
import queue


class MeetBot:
    def __init__(self, meeting_url: str):
        self.meeting_url = meeting_url
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.chat_history_set = set()  # To store processed chat messages

        # New: AI/Audio components (initialized in setup)
        self._whisper_model = None
        self._transcript_buffer = None
        self._audio_source = None
        self._stop_event = threading.Event()
        self._note_count = 0
        
        # Thread-safe queue for background threads to send chat messages
        self._chat_queue = queue.Queue()
        # Track messages we've sent so we don't re-process them
        self._sent_messages = set()

    def setup(self):
        """Set up the browser and AI components."""
        self.playwright = sync_playwright().start()

        # Browser setup with improved stealth
        context_args = {
            "headless": config.HEADLESS_BROWSER,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--use-fake-ui-for-media-stream",
                "--disable-infobars",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--window-size=1280,720"
            ],
            "ignore_default_args": ["--enable-automation"],
            "user_agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            # Empty permissions array blocks all requests for mic/cam by Google Meet
            "permissions": [],
            "viewport": {"width": 1280, "height": 720},
            "device_scale_factor": 2  # Renders at 2x resolution (2560x1440) for high-quality OCR screenshots
        }

        self.context = self.playwright.chromium.launch_persistent_context(
            user_data_dir="./playwright_profile",
            **context_args
        )
        if not self.context:
            raise RuntimeError("Failed to launch Playwright browser context.")
            
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        if not self.page:
            raise RuntimeError("Failed to create new Playwright page.")

        # Verify config
        if not config.STUDENT_NAME or config.STUDENT_NAME == "Idrees":
            print(f"[MeetBot] Warning: STUDENT_NAME is still default ('{config.STUDENT_NAME}').")

        # === NEW: Set up AI components ===

        # Set up audio loopback
        if config.ENABLE_AUDIO_CAPTURE:
            try:
                from audio_handler import setup_loopback
                self._audio_source = setup_loopback()
                print("[MeetBot] Audio loopback initialized.")
            except Exception as e:
                print(f"[MeetBot] Warning: Audio setup failed: {e}")
                print("[MeetBot] Continuing without audio capture.")
                self._audio_source = None

        # Load Whisper model
        if config.ENABLE_AUDIO_CAPTURE:
            try:
                from brain import load_whisper_model, TranscriptBuffer
                self._whisper_model = load_whisper_model(config.FASTER_WHISPER_MODEL)
                self._transcript_buffer = TranscriptBuffer(
                    max_duration_seconds=300,
                    chunk_duration=config.AUDIO_CHUNK_SECONDS
                )
                print("[MeetBot] Whisper model loaded.")
            except Exception as e:
                print(f"[MeetBot] Warning: Whisper setup failed: {e}")
                print("[MeetBot] Continuing without speech recognition.")

        # Create notes directory
        if config.ENABLE_OCR_NOTES:
            os.makedirs(config.NOTES_DIR, exist_ok=True)
            print(f"[MeetBot] Notes directory ready: {config.NOTES_DIR}/")

    def teardown(self):
        """Clean up all resources."""
        self._stop_event.set()

        # Clean up audio
        if self._audio_source:
            try:
                from audio_handler import cleanup_loopback
                cleanup_loopback()
            except Exception:
                pass

        # Close browser
        try:
            if self.page and not self.page.is_closed():
                self.page.close()
        except Exception:
            pass
        try:
            if self.context:
                self.context.close()
        except Exception:
            pass
        try:
            if self.browser:
                self.browser.close()
        except Exception:
            pass
        try:
            if self.playwright:
                self.playwright.stop()
        except Exception:
            pass

    def join_meeting(self):
        """Navigate to the meeting and join with robust error handling."""
        meeting_url = self.meeting_url
        if not meeting_url:
            print("[MeetBot] Error: No meeting URL found.")
            return False

        # Prepend https:// if protocol is missing
        if not meeting_url.startswith(("http://", "https://")):
            print(f"[MeetBot] Prepending protocol to URL: {meeting_url}")
            meeting_url = f"https://{meeting_url}"
            self.meeting_url = meeting_url

        print(f"Navigating to {meeting_url}...")
        try:
            self.page.goto(meeting_url, wait_until="networkidle", timeout=60000)
        except Exception as e:
            print(f"Error navigating to page: {e}")
            return False

        # 0. Check for "You can't join this video call" error screen
        error_indicators = [
            "You can't join this video call",
            "Returning to home screen",
            "meeting is safe",
            "No one can join a meeting unless invited"
        ]
        self.page.wait_for_timeout(3000) # Wait for redirects
        body_text = self.page.inner_text("body")
        if any(indicator in body_text for indicator in error_indicators):
            print("\n*** ERROR: GOOGLE MEET BLOCKED JOIN ***")
            print("Message: 'You can't join this video call'.")
            print("Possible reasons: Wrong meeting code, expired link, or you are not logged in with an invited account.\n")
            return False

        # 1. Check for Google Login
        if "accounts.google.com" in self.page.url:
            print("\n*** GOOGLE LOGIN REQUIRED ***")
            print("Please log into your Google Account in the Playwright browser.")
            print("The bot will wait up to 5 minutes.\n")
            try:
                self.page.wait_for_url("**/meet.google.com/**", timeout=300000)
                print("Login successful! Continuing...")
                self.page.wait_for_timeout(3000)
            except Exception:
                print("Timed out waiting for Google Login.")
                return False

        # 2. Pre-joining Lobby (Mute Mic & Camera)
        # We wait until we see some meeting UI or the 'Ready to join?' text
        print("Waiting for meeting lobby to load...")
        try:
            self.page.wait_for_selector('text="Ready to join?"', timeout=15000)
        except:
            pass # Continue even if text not found, might be already joined or different UI

        print("Muting Microphone and Camera...")
        self.page.wait_for_timeout(2000)
        self.page.keyboard.press('Control+d')  # Mute Mic
        self.page.wait_for_timeout(1000)
        self.page.keyboard.press('Control+e')  # Camera off
        self.page.wait_for_timeout(1000)

        # 3. Click Join button
        print("Attempting to join meeting...")
        # Expanded list of join buttons including variants
        join_selectors = [
            'button:has-text("Ask to join")',
            'button:has-text("Join now")',
            'button:has-text("Join")',
            '[aria-label="Ask to join"]',
            '[aria-label="Join now"]',
            '[aria-label="Join"]',
            'span:has-text("Join now")',
            'span:has-text("Ask to join")'
        ]

        clicked = False
        # Try multiple times in case the button takes a second to be clickable
        for attempt in range(3):
            for selector in join_selectors:
                try:
                    loc = self.page.locator(selector)
                    if loc.is_visible():
                        loc.click()
                        clicked = True
                        print(f"Clicked join button: {selector}")
                        break
                except Exception:
                    continue
            if clicked: break
            self.page.wait_for_timeout(2000)

        if not clicked:
            print("Could not find a join button. You might already be in or need manual approval.")

        # 4. Wait to be admitted
        print("Waiting to be admitted... (Timeout: 5 minutes)")
        chat_btn_selector = 'button[aria-label="Chat with everyone"]'
        try:
            # We also check for the "Denied" or "Can't join" screens here in case admission is rejected
            start_wait = time.time()
            while time.time() - start_wait < 300:
                if self.page.locator(chat_btn_selector).is_visible():
                    print("Successfully entered the meeting!")
                    self.page.click(chat_btn_selector)
                    self.page.wait_for_timeout(2000)
                    print("Chat pane opened.")
                    
                    # Redirect browser audio after joining
                    if self._audio_source:
                        self.page.wait_for_timeout(3000)
                        try:
                            from audio_handler import move_browser_audio_to_sink_with_retry
                            move_browser_audio_to_sink_with_retry(max_retries=3, delay=2)
                        except Exception as e:
                            print(f"[MeetBot] Warning: Could not redirect audio: {e}")
                    return True
                
                # Check for "You've been denied" or similar
                current_text = self.page.inner_text("body")
                if "denied" in current_text.lower() or "can't join" in current_text.lower():
                    print("\n*** ERROR: ENTRY DENIED BY HOST ***")
                    return False
                
                self.page.wait_for_timeout(2000)
                
            print("Timed out waiting to be admitted.")
            return False
        except Exception as e:
            print(f"Error during admission wait: {e}")
            return False

    def _send_chat_message(self, message):
        """Send a message in the Google Meet chat."""
        chat_input_selectors = [
            'textarea[aria-label="Send a message to everyone"]',
            'textarea[name="chatTextInput"]',
            'textarea[aria-label="Chat text input"]',
            'textarea'
        ]

        for selector in chat_input_selectors:
            try:
                if self.page.locator(selector).is_visible():
                    self.page.fill(selector, message)
                    self.page.wait_for_timeout(500)
                    self.page.keyboard.press('Enter')
                    print(f"[MeetBot] Chat sent: \"{message[:60]}...\"")
                    # Track this message so we don't re-process it
                    self._sent_messages.add(message.strip().lower())
                    return True
            except Exception:
                continue

        print("[MeetBot] ERROR: Could not find chat input box!")
        return False

    def _ensure_chat_open(self):
        """Make sure the chat pane is open."""
        try:
            is_open = (
                self.page.locator('text="In-call messages"').is_visible() or
                self.page.locator('textarea').is_visible()
            )
            if not is_open:
                btn = self.page.locator('button[aria-label="Chat with everyone"]')
                if btn.is_visible():
                    if btn.get_attribute("aria-pressed") != "true":
                        btn.click()
                        self.page.wait_for_timeout(1000)
        except Exception:
            pass

    # =================================================================
    # NEW: Audio Capture + Whisper Transcription Loop (runs in thread)
    # =================================================================

    def _audio_loop(self):
        """
        Background thread: continuously records audio chunks,
        transcribes them with Whisper, and checks for keywords.
        """
        if not self._audio_source or not self._whisper_model:
            print("[AudioLoop] Skipping — audio/whisper not available.")
            return

        from audio_handler import record_chunk, cleanup_old_chunks
        from brain import transcribe_audio, detect_keyword, classify_and_respond

        print(f"[AudioLoop] Started! Recording {config.AUDIO_CHUNK_SECONDS}s chunks...")
        cooldown_until = 0  # Timestamp: don't trigger again until after this time

        while not self._stop_event.is_set():
            try:
                # Record an audio chunk
                audio_path = record_chunk(
                    self._audio_source,
                    duration=config.AUDIO_CHUNK_SECONDS,
                    sample_rate=config.AUDIO_SAMPLE_RATE
                )

                if not audio_path:
                    time.sleep(2)
                    continue

                # Transcribe with Whisper
                text = transcribe_audio(audio_path, self._whisper_model)

                if text:
                    # Add to rolling buffer
                    self._transcript_buffer.add(text)

                    # Check for keywords (with cooldown to avoid spam)
                    if time.time() > cooldown_until:
                        keyword, _ = detect_keyword(text)

                        if keyword:
                            print(f"[AudioLoop] Name called! Keyword: '{keyword}'")
                            # Get last 120s of context for the AI (useful for answering questions)
                            context = self._transcript_buffer.get_recent(seconds=120)
                            # Ask Ollama for the right response
                            response = classify_and_respond(text, context=context)

                            if response:
                                # Put into queue for the main thread to send safely
                                print(f"[AudioLoop] Queuing response to send: {response[:30]}...")
                                self._chat_queue.put(response)
                                # Cool down for 30 seconds to avoid double-triggering
                                cooldown_until = time.time() + 30

                # Clean up old audio files to save disk space
                cleanup_old_chunks(max_age_seconds=120)

            except Exception as e:
                print(f"[AudioLoop] Error: {e}")
                time.sleep(5)

        print("[AudioLoop] Stopped.")

    # =================================================================
    # OCR: Process a screenshot (runs in background thread)
    # =================================================================

    def _process_ocr(self, screenshot_path):
        """
        Background thread target: run OCR on a screenshot and save notes.
        This does NOT use Playwright — only file I/O and pytesseract.
        """
        try:
            import pytesseract
            from PIL import Image
        except ImportError:
            return

        # Add brain imports for cleaning
        from brain import clean_transcript_for_notes, _is_whisper_garbage

        try:
            img = Image.open(screenshot_path)
            raw_ocr = pytesseract.image_to_string(img).strip()
            
            # Filter out Meet UI artifacts from OCR
            ui_patterns = [
                "In-call messages", "ee ees", "Duce", "eR eet", "@ -O:.", 
                "Rees ee ceca", "Reseed es nas", "Renee ee oe Ee cs",
                "M fee ee", "DY Teatro", "DY Tattle", "eee ters",
            ]
            ocr_lines = []
            for line in raw_ocr.split('\n'):
                line = line.strip()
                if len(line) < 3:
                    continue
                if any(p.lower() in line.lower() for p in ui_patterns):
                    continue
                ocr_lines.append(line)
            ocr_text = "\n".join(ocr_lines).strip()
            
        except Exception as e:
            print(f"[OCR] Text extraction failed: {e}")
            ocr_text = ""

        # Get the latest transcript
        transcript_text = ""
        if self._transcript_buffer:
            raw_transcript = self._transcript_buffer.get_recent(
                seconds=config.OCR_INTERVAL_SECONDS
            )
            # Only save if it's not garbage
            if not _is_whisper_garbage(raw_transcript):
                transcript_text = clean_transcript_for_notes(raw_transcript)

        # Save notes only if there's actual content
        if ocr_text or transcript_text:
            self._note_count += 1
            now = datetime.datetime.now()
            date_str = now.strftime("%Y-%m-%d")

            notes_file = os.path.join(
                config.NOTES_DIR,
                f"class_notes_{date_str}.txt"
            )

            with open(notes_file, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"Timestamp: {now.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"{'='*60}\n")

                if transcript_text:
                    f.write(f"\n--- TEACHER (Audio Transcript) ---\n")
                    f.write(f"{transcript_text}\n")

                if ocr_text:
                    f.write(f"\n--- SLIDE (OCR) ---\n")
                    f.write(f"{ocr_text}\n")

                f.write("\n")

            print(f"[OCR] Notes saved (entry #{self._note_count}) -> {notes_file}")

        # Clean up screenshot
        try:
            os.remove(screenshot_path)
        except Exception:
            pass

    # =================================================================
    # Main Monitor Loop (Chat + OCR screenshots from main thread)
    # =================================================================

    def monitor_chat_and_reply(self):
        """
        Monitor chat for the target word and auto-reply.
        Also takes periodic OCR screenshots from the main thread
        (Playwright requires all calls from the same thread).
        """
        print(f"[Chat] Monitoring for target word: '{config.TARGET_WORD}'...")
        if config.ENABLE_OCR_NOTES:
            print(f"[OCR] Will capture slides every {config.OCR_INTERVAL_SECONDS}s from main thread...")

        start_time = time.time()
        last_ocr_time = time.time()
        self._note_count = 0

        while (not self._stop_event.is_set() and
               time.time() - start_time < 3600):
            try:
                # 1. Immediately check if the teacher ended the meeting / browser is closed
                if getattr(self, "page", None) and self.page.is_closed():
                    print("\n[MeetBot] Browser page closed (meeting ended). Triggering shutdown sequence...")
                    break

                self._ensure_chat_open()

                # --- Chat Monitoring ---
                selectors = [
                    'div[data-message-id]',
                    'div[jscontroller="S7m8ub"]',
                    'div[data-message-text]',
                    'div.GDhqjd',           # Google Meet chat message container
                    'div[data-sender-id]',   # Alternative message wrapper
                ]
                found_messages = []

                for selector in selectors:
                    try:
                        msgs = self.page.locator(selector).all_inner_texts()
                        found_messages.extend(msgs)
                    except Exception:
                        continue

                # --- Process Queued Outgoing Messages (from background threads) ---
                while not self._chat_queue.empty():
                    out_msg = self._chat_queue.get_nowait()
                    print(f"[Chat] Sending queued message: {out_msg[:30]}...")
                    self._send_chat_message(out_msg)
                    time.sleep(1)

                messages = list(dict.fromkeys(found_messages))

                for msg_text in messages:
                    msg_clean = msg_text.strip()

                    if not msg_clean or msg_clean in self.chat_history_set:
                        continue

                    self.chat_history_set.add(msg_clean)

                    # --- Clean the message: strip Google Meet UI artifacts ---
                    from brain import clean_chat_text
                    msg_cleaned = clean_chat_text(msg_clean)
                    if not msg_cleaned:
                        continue

                    print(f"[Chat] Message: \"{msg_cleaned[:50]}...\"")

                    # --- Skip our own messages ---
                    msg_lower = msg_cleaned.lower().strip()
                    # Check against tracked sent messages
                    is_own_message = False
                    for sent in self._sent_messages:
                        if sent in msg_lower or msg_lower in sent:
                            is_own_message = True
                            break
                    if is_own_message:
                        continue
                    # Also check against the hardcoded response text
                    if config.RESPONSE_TEXT.lower() in msg_lower:
                        continue

                    # Check for attendance keyword in chat
                    if config.TARGET_WORD.lower() in msg_lower:
                        print(f"[Chat] !!! ATTENDANCE MATCH: '{config.TARGET_WORD}' found!")
                        self._send_chat_message(config.RESPONSE_TEXT)
                        time.sleep(2)
                        continue

                    # Check for student name/keywords in chat (using fuzzy matching)
                    name_found = False
                    # First: exact keyword check
                    for kw in config.KEYWORDS:
                        if kw and kw.lower() in msg_lower:
                            name_found = True
                            break
                    # Second: fuzzy check via brain's detect_keyword
                    if not name_found:
                        from brain import detect_keyword
                        kw_hit, _ = detect_keyword(msg_cleaned)
                        if kw_hit:
                            name_found = True

                    if name_found:
                        print(f"[Chat] !!! NAME DETECTED in chat: '{msg_cleaned[:60]}'")
                        # Use the smart chat classifier (attendance vs question)
                        try:
                            from brain import classify_chat_message
                            # Inject the context memory from audio!
                            audio_context = ""
                            if self._transcript_buffer:
                                audio_context = self._transcript_buffer.get_recent(seconds=120)

                            response = classify_chat_message(msg_cleaned, context=audio_context)
                            if response:
                                self._send_chat_message(response)
                                time.sleep(2)
                        except Exception as e:
                            print(f"[Chat] Classification error: {e}")
                            # Only fall back to attendance if it looks like attendance
                            attendance_words = ["present", "attendance", "roll"]
                            if any(w in msg_lower for w in attendance_words):
                                self._send_chat_message(config.RESPONSE_TEXT)
                            else:
                                self._send_chat_message(config.QUESTION_FALLBACK_TEXT)
                            time.sleep(2)

                # --- OCR Screenshot (from main thread) ---
                if config.ENABLE_OCR_NOTES:
                    elapsed = time.time() - last_ocr_time
                    if elapsed >= config.OCR_INTERVAL_SECONDS:
                        last_ocr_time = time.time()
                        screenshot_path = f"/tmp/meet_slide_{int(time.time())}.png"
                        try:
                            self.page.screenshot(path=screenshot_path)
                            # Process OCR in a background thread (no Playwright calls)
                            ocr_thread = threading.Thread(
                                target=self._process_ocr,
                                args=(screenshot_path,),
                                daemon=True
                            )
                            ocr_thread.start()
                        except Exception as e:
                            print(f"[OCR] Screenshot failed: {e}")

            except Exception as e:
                if "Target page, context or browser has been closed" in str(e):
                    print("[Chat] Browser was closed. Stopping.")
                    break

            time.sleep(2)

        print("[Chat] Monitoring complete.")

    # =================================================================
    # Main Run Loop
    # =================================================================

    def run(self):
        """
        Main entry point: set up, join, and run all monitoring loops.
        Audio runs in background thread; chat + OCR run in main thread.
        """
        try:
            self.setup()

            if not self.join_meeting():
                return

            threads = []

            # Start audio capture + Whisper thread
            if config.ENABLE_AUDIO_CAPTURE and self._audio_source and self._whisper_model:
                audio_thread = threading.Thread(
                    target=self._audio_loop,
                    name="AudioLoop",
                    daemon=True
                )
                audio_thread.start()
                threads.append(audio_thread)
                print("[MeetBot] Audio capture thread started.")

            # OCR is now handled in the main thread alongside chat monitoring
            if config.ENABLE_OCR_NOTES:
                print("[MeetBot] OCR notes will be captured from main thread.")

            # Chat monitor runs in main thread
            if config.ENABLE_CHAT_MONITOR:
                print("\n" + "="*60)
                print("  FULL CLASSROOM PROXY IS ACTIVE  ")
                print("  Listening | Watching | Note-Taking")
                print("="*60 + "\n")
                self.monitor_chat_and_reply()

            # Wait for background threads to finish
            self._stop_event.set()
            for t in threads:
                t.join(timeout=5)

        except KeyboardInterrupt:
            print("\n[MeetBot] Interrupted by user.")
        finally:
            self.teardown()
            print("[MeetBot] Session complete. Cleaning up...")
            
            # === NEW: Generate PDF Summary at the end ===
            date_str = datetime.datetime.now().strftime("%Y-%m-%d")
            notes_file = os.path.join(config.NOTES_DIR, f"class_notes_{date_str}.txt")
            if os.path.exists(notes_file) and config.ENABLE_OCR_NOTES:
                generate_pdf_summary(notes_file)

            print("[MeetBot] Check the 'notes/' folder for your files!")


def generate_pdf_summary(notes_file):
    """Generate a high-detail PDF document from a notes text file using Ollama."""
    print(f"\n[MeetBot] Creating comprehensive PDF for {os.path.basename(notes_file)}...")
    try:
        import re
        from brain import generate_comprehensive_notes
        from fpdf import FPDF
        
        # Get the deep notes content from Ollama
        full_notes_md = generate_comprehensive_notes(notes_file)
        
        if full_notes_md:
            # Parse date from filename for the header
            base_name = os.path.basename(notes_file)
            date_str = base_name.replace("class_notes_", "").replace(".txt", "")
            
            class PDF(FPDF):
                def header(self):
                    self.set_font("helvetica", "B", 16)
                    self.cell(0, 10, f"Class Notes: {date_str}", align="C")
                    self.ln(12)
                    self.set_draw_color(200, 200, 200)
                    self.line(10, 22, 200, 22) # Divider line
                    self.ln(5)
                    
                def footer(self):
                    self.set_y(-15)
                    self.set_font("helvetica", "I", 8)
                    self.cell(0, 10, f"Page {self.page_no()}", align="C")

            pdf = PDF()
            pdf.add_page()
            pdf.set_font("helvetica", size=10) # 10pt is better for high-density academic notes
            
            # Clean text for FPDF (Standard Latin-1 encoding mostly)
            # Replace common non-latin characters that crash basic FPDF
            clean_text = full_notes_md.replace('’', "'").replace('“', '"').replace('”', '"').replace('—', '-')
            clean_text = re.sub(r'[*_`]', '', clean_text) # remove markers
            clean_text = re.sub(r'#+\s*', '', clean_text) # remove header hashes but keep text
            
            # Ensure text is encoded correctly for FPDF
            try:
                clean_text = clean_text.encode('latin-1', 'replace').decode('latin-1')
            except:
                pass

            # Add text (multi_cell handles line breaks and margins)
            pdf.multi_cell(0, 6, text=clean_text)
            
            pdf_file = os.path.join(config.NOTES_DIR, f"class_notes_{date_str}_{int(time.time())}.pdf")
            pdf.output(pdf_file)
            print(f"[MeetBot] ✅ Comprehensive PDF successfully saved to: {pdf_file}")
            
            # Mark the txt file as processed
            os.rename(notes_file, notes_file + ".processed")
        else:
            print("[MeetBot] Ollama returned no content for PDF.")
    except Exception as e:
        print(f"[MeetBot] ⚠️ Failed to generate comprehensive PDF: {e}")


def process_pending_notes():
    """Scan the notes folder at startup for any stranded .txt files and process them."""
    if not os.path.exists(config.NOTES_DIR):
        return
    
    pending_files = []
    for f in os.listdir(config.NOTES_DIR):
        if f.startswith("class_notes_") and f.endswith(".txt"):
            pending_files.append(os.path.join(config.NOTES_DIR, f))
            
    if pending_files:
        print(f"\n[MeetBot] Found {len(pending_files)} unprocessed notes file(s) from previous sessions.")
        for pf in pending_files:
            generate_pdf_summary(pf)
        print("[MeetBot] Finished processing orphaned notes.\n")


if __name__ == "__main__":
    # Ensure any pending notes from crashes are generated first
    process_pending_notes()

    # Direct test with a meeting URL
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else "https://meet.google.com/xxx-yyyy-zzz"
    bot = MeetBot(url)
    bot.run()
