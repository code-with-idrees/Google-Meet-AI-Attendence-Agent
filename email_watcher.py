import os
import re
import datetime
import base64
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import config

def authenticate_gmail():
    """Authenticate and return the Gmail API service instance."""
    creds = None
    if os.path.exists(config.TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(config.TOKEN_FILE, config.SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(config.CREDENTIALS_FILE, config.SCOPES)
            creds = flow.run_local_server(port=0)
        with open(config.TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
    return build('gmail', 'v1', credentials=creds)

def fetch_meet_invitations(service):
    """
    Polls the inbox for emails from the TARGET_SENDER_EMAIL that contain a Google Meet link.
    Returns a list of parsed meeting dictionaries: {'url': str, 'scheduled_time': datetime}
    """
    query = f"from:{config.TARGET_SENDER_EMAIL} is:unread meet.google.com"
    print(f"Polling for emails matching: {query}")
    
    try:
        results = service.users().messages().list(userId='me', q=query).execute()
        messages = results.get('messages', [])
        
        extracted_meetings = []
        for msg in messages:
            msg_id = msg['id']
            message = service.users().messages().get(userId='me', id=msg_id, format='full').execute()
            
            # Recursive body extraction logic to handle nested multipart emails
            payload = message.get('payload', {})
            def get_email_body(payload):
                body_data = ""
                if 'body' in payload and 'data' in payload['body']:
                    body_data += base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8')
                if 'parts' in payload:
                    for part in payload['parts']:
                        body_data += get_email_body(part)
                return body_data
                
            body_data = get_email_body(payload)
            
            # Extract Meet URL (making https:// optional since sometimes it's just pasted as text)
            url_match = re.search(r'(?:https?://)?(meet\.google\.com/[a-z0-9-]+)', body_data)
            meet_url = "https://" + url_match.group(1) if url_match else None
            
            if meet_url:
                print(f"Found Meet link: {meet_url}")
                # For demonstration, schedule the meeting 2 minutes from when it's found.
                # In a full production app, you would parse the body or Google Calendar ICS attachment for the exact event time.
                scheduled_time = datetime.datetime.now() + datetime.timedelta(minutes=2)
                
                extracted_meetings.append({
                    'url': meet_url,
                    'scheduled_time': scheduled_time,
                    'msg_id': msg_id
                })
                
                # Mark as read so we don't process it again
                service.users().messages().modify(
                    userId='me', id=msg_id, body={'removeLabelIds': ['UNREAD']}
                ).execute()

        return extracted_meetings
    except Exception as e:
        print(f"Error fetching emails: {e}")
        return []

if __name__ == "__main__":
    service = authenticate_gmail()
    meetings = fetch_meet_invitations(service)
    print("Meetings found:", meetings)
