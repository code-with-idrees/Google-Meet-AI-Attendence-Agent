"""
audio_handler.py — PulseAudio loopback setup & audio recording for Google Meet.

Creates a virtual PulseAudio sink, redirects browser audio to it,
and records audio chunks as WAV files for Whisper transcription.
"""

import os
import subprocess
import time
import wave
import struct
import tempfile
import shutil

# Directory for temporary audio chunks
AUDIO_TMP_DIR = "/tmp/meet_audio"

# Module ID for cleanup
_module_id = None
_sink_name = "meet_capture"


def setup_loopback():
    """
    Create a PulseAudio null-sink for capturing browser audio.
    Returns the monitor source name to record from.
    """
    global _module_id

    # Ensure temp dir exists
    os.makedirs(AUDIO_TMP_DIR, exist_ok=True)

    # Remove any pre-existing meet_capture sink
    try:
        cleanup_loopback()
    except Exception:
        pass

    # Create a virtual null-sink
    # Audio sent to this sink is available on its .monitor source
    result = subprocess.run(
        [
            "pactl", "load-module", "module-null-sink",
            f"sink_name={_sink_name}",
            "sink_properties=device.description=MeetCapture",
            "rate=16000", "channels=1"
        ],
        capture_output=True, text=True, timeout=10
    )

    if result.returncode != 0:
        raise RuntimeError(f"Failed to create PulseAudio null-sink: {result.stderr.strip()}")

    _module_id = result.stdout.strip()
    monitor_source = f"{_sink_name}.monitor"
    print(f"[AudioHandler] Created virtual sink '{_sink_name}' (module {_module_id})")
    print(f"[AudioHandler] Monitor source: {monitor_source}")

    return monitor_source


def move_browser_audio_to_sink():
    """
    Find the Chromium/Chrome browser sink-input and move it to our virtual sink.
    This redirects browser audio output to our capture pipeline.
    Returns True if a browser audio stream was found and redirected.
    """
    # List all sink-inputs (audio streams from applications)
    result = subprocess.run(
        ["pactl", "list", "short", "sink-inputs"],
        capture_output=True, text=True, timeout=10
    )

    if result.returncode != 0:
        print(f"[AudioHandler] Warning: Could not list sink-inputs: {result.stderr.strip()}")
        return False

    moved = False
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 1:
            continue

        sink_input_id = parts[0]

        # Check if this sink-input belongs to a browser
        info_result = subprocess.run(
            ["pactl", "list", "sink-inputs"],
            capture_output=True, text=True, timeout=10
        )

        # Look for chromium/chrome/playwright in the sink-input properties
        browser_keywords = ["chromium", "chrome", "playwright", "Chromium", "Chrome"]
        if any(kw in info_result.stdout for kw in browser_keywords):
            # Move this sink-input to our capture sink
            move_result = subprocess.run(
                ["pactl", "move-sink-input", sink_input_id, _sink_name],
                capture_output=True, text=True, timeout=10
            )
            if move_result.returncode == 0:
                print(f"[AudioHandler] Moved sink-input {sink_input_id} to {_sink_name}")
                moved = True
            else:
                # Might fail if this particular input is not a browser; ignore
                pass

    if not moved:
        print("[AudioHandler] Warning: No browser audio stream found yet. "
              "Will retry when recording starts.")
    return moved


def move_browser_audio_to_sink_with_retry(max_retries=3, delay=2):
    """
    Retry wrapper for move_browser_audio_to_sink.
    The browser sink-input may not be available immediately after joining.
    """
    for attempt in range(max_retries):
        if move_browser_audio_to_sink():
            return True
        if attempt < max_retries - 1:
            print(f"[AudioHandler] Retry {attempt + 2}/{max_retries} in {delay}s...")
            time.sleep(delay)
    print("[AudioHandler] Could not redirect browser audio after all retries.")
    return False


def record_chunk(source_name, duration=10, sample_rate=16000):
    """
    Record `duration` seconds of audio from the given PulseAudio source
    using `parec` (PulseAudio recording utility).

    Returns the path to the saved WAV file.
    """
    os.makedirs(AUDIO_TMP_DIR, exist_ok=True)

    # Try to move browser audio before each recording (in case it wasn't captured yet)
    try:
        move_browser_audio_to_sink()
    except Exception as e:
        print(f"[AudioHandler] Warning: Could not redirect browser audio: {e}")

    timestamp = int(time.time() * 1000)
    raw_path = os.path.join(AUDIO_TMP_DIR, f"chunk_{timestamp}.raw")
    wav_path = os.path.join(AUDIO_TMP_DIR, f"chunk_{timestamp}.wav")

    # Record raw PCM data (avoid parec's buggy WAV header creation on termination)
    try:
        proc = subprocess.Popen(
            [
                "parec",
                f"--device={source_name}",
                "--rate", str(sample_rate),
                "--channels", "1",
                "--format", "s16le",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL
        )
        
        # Calculate exactly how many bytes we need: 16000 samples/sec * 2 bytes/sample = 32000 bytes/sec
        bytes_to_read = sample_rate * 2 * duration
        raw_data = b""
        start_time = time.time()
        
        # Read continuously to avoid OS pipe buffer deadlock
        while len(raw_data) < bytes_to_read:
            # Failsafe timeout
            if time.time() - start_time > duration + 2:
                break
            # parec outputs continuous data, so read(4096) will return as soon as data arrives
            chunk = proc.stdout.read(4096)
            if not chunk:
                break
            raw_data += chunk

        # Cleanup process
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()

        if raw_data:
            # Safely create a well-formed WAV file using Python's native library
            with wave.open(wav_path, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)  # 16-bit = 2 bytes
                wf.setframerate(sample_rate)
                wf.writeframes(raw_data)
        else:
            print("[AudioHandler] Warning: No audio data captured.")
            return None

    except Exception as e:
        print(f"[AudioHandler] Error recording audio: {e}")
        return None

    # Clean up raw file if it exists (not used anymore, but just in case)
    if os.path.exists(raw_path):
        os.remove(raw_path)

    if os.path.exists(wav_path) and os.path.getsize(wav_path) > 0:
        file_size = os.path.getsize(wav_path)
        # Filter out suspiciously small files (just WAV header, no real audio)
        if file_size < 1024:
            print(f"[AudioHandler] Warning: Recording too small ({file_size} bytes), likely no audio.")
            try:
                os.remove(wav_path)
            except Exception:
                pass
            return None
        print(f"[AudioHandler] Recorded {duration}s audio -> {wav_path} ({file_size} bytes)")
        return wav_path
    else:
        print("[AudioHandler] Warning: Recording produced empty file.")
        return None


def cleanup_loopback():
    """
    Remove the virtual PulseAudio sink and clean up temp files.
    """
    global _module_id

    if _module_id:
        try:
            subprocess.run(
                ["pactl", "unload-module", str(_module_id)],
                capture_output=True, text=True, timeout=5
            )
            print(f"[AudioHandler] Unloaded PulseAudio module {_module_id}")
        except Exception as e:
            print(f"[AudioHandler] Warning during cleanup: {e}")
        _module_id = None
    else:
        # Try to find and remove by sink name
        try:
            result = subprocess.run(
                ["pactl", "list", "short", "modules"],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.strip().split("\n"):
                if _sink_name in line:
                    mod_id = line.split("\t")[0]
                    subprocess.run(
                        ["pactl", "unload-module", mod_id],
                        capture_output=True, text=True, timeout=5
                    )
                    print(f"[AudioHandler] Cleaned up orphan module {mod_id}")
        except Exception:
            pass

    # Clean up temp audio files
    if os.path.exists(AUDIO_TMP_DIR):
        try:
            shutil.rmtree(AUDIO_TMP_DIR)
            print("[AudioHandler] Cleaned up temp audio directory.")
        except Exception:
            pass


def cleanup_old_chunks(max_age_seconds=120):
    """Remove audio chunks older than max_age_seconds to save disk space."""
    if not os.path.exists(AUDIO_TMP_DIR):
        return

    now = time.time()
    for f in os.listdir(AUDIO_TMP_DIR):
        fpath = os.path.join(AUDIO_TMP_DIR, f)
        if os.path.isfile(fpath) and (now - os.path.getmtime(fpath)) > max_age_seconds:
            try:
                os.remove(fpath)
            except Exception:
                pass


if __name__ == "__main__":
    # Quick test: set up loopback and record 5 seconds
    print("Testing audio capture...")
    source = setup_loopback()
    print(f"Source: {source}")
    print("Recording 5 seconds of audio (play something in your browser)...")
    path = record_chunk(source, duration=5)
    if path:
        print(f"Success! Audio saved to: {path}")
        print(f"File size: {os.path.getsize(path)} bytes")
    else:
        print("Failed to record audio.")
    cleanup_loopback()
