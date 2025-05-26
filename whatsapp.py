import openai
import os
import json
import httpx
import gspread
import requests
import tempfile
from fastapi import FastAPI, Request, Form, BackgroundTasks, HTTPException
from fastapi.responses import PlainTextResponse
from typing import Dict, Optional, List
import time
from datetime import datetime, timezone, timedelta
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from pathlib import Path
from dotenv import load_dotenv
import re
from memory_fusion import HybridMemoryManager
import base64

# Load environment variables from .env file
load_dotenv()

app = FastAPI()

# Initialize hybrid memory manager
try:
    memory_manager = HybridMemoryManager()
    print("✅ Hybrid memory manager initialized successfully")
except Exception as e:
    print(f"❌ Failed to initialize memory manager: {e}")
    memory_manager = None

# Google Calendar Configuration
SCOPES = ['https://www.googleapis.com/auth/calendar']
REDIRECT_URI = "https://a5d5-2001-8f8-1b69-5a-3918-347f-d5c6-f162.ngrok-free.app/oauth2callback"  # Update with your domain
CREDENTIALS_FILE = "credentials.json"
DUBAI_TZ = timezone(timedelta(hours=4))  # Asia/Dubai timezone

# Google Places API Configuration
GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")

def get_calendar_service(whatsapp_number: str):
    """Get authenticated Google Calendar service for user using same method as Sheets"""
    try:
        # Ensure credentials are available
        if not setup_google_credentials():
            raise Exception("Google credentials not available")
        
        # Use the same OAuth approach as Google Sheets but with calendar scope
        import gspread
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        import os
        
        # Calendar scope
        SCOPES = ['https://www.googleapis.com/auth/calendar']
        
        creds = None
        token_file = f"calendar_token_{whatsapp_number}.json"
        
        # Check if we have stored credentials
        if os.path.exists(token_file):
            creds = Credentials.from_authorized_user_file(token_file, SCOPES)
        
        # If there are no (valid) credentials available, let the user log in
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists("credentials.json"):
                    raise Exception("credentials.json file not found")
                
                flow = InstalledAppFlow.from_client_secrets_file(
                    "credentials.json", SCOPES)
                creds = flow.run_local_server(port=0)
            
            # Save the credentials for the next run
            with open(token_file, 'w') as token:
                token.write(creds.to_json())
        
        # Build calendar service
        service = build('calendar', 'v3', credentials=creds)
        return service
        
    except Exception as e:
        print(f"Calendar service error: {e}")
        raise HTTPException(status_code=401, detail=f"Calendar authorization required. Error: {str(e)}")

def format_datetime_for_google(dt_str: str, is_all_day: bool = False) -> dict:
    """Format datetime string for Google Calendar API"""
    try:
        # Parse ISO-8601 datetime
        dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
        
        # Convert to Dubai timezone if no timezone info
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=DUBAI_TZ)
        
        if is_all_day:
            return {'date': dt.strftime('%Y-%m-%d')}
        else:
            return {'dateTime': dt.isoformat(), 'timeZone': 'Asia/Dubai'}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid datetime format: {str(e)}")

openai.api_key = os.getenv("OPENAI_API_KEY")
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
RESEND_API_URL = "https://api.resend.com/emails"
SENDER_EMAIL = "rahulmenon@mentis-ed.ai"

# Google Sheets constants
SHEET_ID = "1DHwrOScPMkVYss76ETvhHaBONZ3ec2zXpNuRXN8XWyQ"
WORKSHEET_NAME = "Sheet1"

def setup_google_credentials():
    """Setup Google credentials for both local and production environments"""
    import base64
    import json
    
    credentials_available = False
    token_available = False
    
    # Check if we have credentials in environment variable (for production)
    google_creds_base64 = os.getenv("GOOGLE_CREDENTIALS_BASE64")
    google_token_base64 = os.getenv("GOOGLE_TOKEN_BASE64")
    
    if google_creds_base64:
        try:
            # Decode base64 credentials and write to file
            creds_json = base64.b64decode(google_creds_base64).decode('utf-8')
            with open("credentials.json", "w") as f:
                f.write(creds_json)
            print("✅ Google credentials loaded from environment variable")
            credentials_available = True
        except Exception as e:
            print(f"❌ Failed to decode Google credentials from environment: {e}")
    
    if google_token_base64:
        try:
            # Decode base64 token and write to file
            token_json = base64.b64decode(google_token_base64).decode('utf-8')
            with open("combined_token.json", "w") as f:
                f.write(token_json)
            print("✅ Google OAuth token loaded from environment variable")
            token_available = True
        except Exception as e:
            print(f"❌ Failed to decode Google token from environment: {e}")
    
    # Check if credentials.json exists locally (for development)
    if os.path.exists("credentials.json") and not credentials_available:
        print("✅ Using local credentials.json file")
        credentials_available = True
    
    # Check if token file exists locally
    if os.path.exists("combined_token.json") and not token_available:
        print("✅ Using local combined_token.json file")
        token_available = True
    
    # We need either credentials OR token to proceed
    if not credentials_available and not token_available:
        print("❌ No Google credentials or token found. Set GOOGLE_CREDENTIALS_BASE64 or GOOGLE_TOKEN_BASE64 environment variable")
        return False
    
    return True

# Initialize Google Sheets client
gc = None
sheet = None

def initialize_google_sheets():
    """Initialize Google Sheets in a non-blocking way"""
    global gc, sheet
    try:
        if setup_google_credentials():
            # Check if we have credentials.json file
            if os.path.exists("credentials.json"):
                # Check if we're using service account credentials (better for production)
                import json
                with open("credentials.json", "r") as f:
                    creds_data = json.load(f)
                
                if "type" in creds_data and creds_data["type"] == "service_account":
                    # Use service account authentication (no browser needed)
                    gc = gspread.service_account(filename="credentials.json")
                    print("✅ Using Google Service Account authentication")
                else:
                    # Use OAuth2 authentication (requires browser - for local development)
                    try:
                        gc = gspread.oauth(credentials_filename="credentials.json")
                        print("✅ Using Google OAuth authentication")
                    except Exception as oauth_error:
                        print(f"❌ OAuth failed (no browser available): {oauth_error}")
                        print("💡 Trying alternative authentication method...")
                        
                        # Try using the OAuth credentials directly without browser
                        try:
                            from google.oauth2.credentials import Credentials
                            from google.auth.transport.requests import Request
                            
                            # Check if we have a token file
                            if os.path.exists("combined_token.json"):
                                creds = Credentials.from_authorized_user_file("combined_token.json")
                                if creds and creds.valid:
                                    gc = gspread.authorize(creds)
                                    print("✅ Using existing OAuth token")
                                else:
                                    print("❌ No valid OAuth token available")
                                    gc = None
                            else:
                                print("❌ No OAuth token file found")
                                gc = None
                        except Exception as token_error:
                            print(f"❌ Token authentication failed: {token_error}")
                            gc = None
            else:
                # No credentials.json, try to use token file directly
                print("💡 No credentials.json found, trying OAuth token authentication...")
                try:
                    from google.oauth2.credentials import Credentials
                    from google.auth.transport.requests import Request
                    
                    # Check if we have a token file
                    if os.path.exists("combined_token.json"):
                        creds = Credentials.from_authorized_user_file("combined_token.json")
                        
                        # Try to refresh the token if it's expired
                        if creds and creds.expired and creds.refresh_token:
                            print("🔄 Token expired, attempting to refresh...")
                            try:
                                creds.refresh(Request())
                                print("✅ Token refreshed successfully")
                            except Exception as refresh_error:
                                print(f"❌ Token refresh failed: {refresh_error}")
                                creds = None
                        
                        if creds and creds.valid:
                            gc = gspread.authorize(creds)
                            print("✅ Using OAuth token authentication")
                        elif creds:
                            print("❌ OAuth token is not valid and cannot be refreshed")
                            gc = None
                        else:
                            print("❌ Could not load OAuth token")
                            gc = None
                    else:
                        print("❌ No OAuth token file found")
                        gc = None
                except Exception as token_error:
                    print(f"❌ Token authentication failed: {token_error}")
                    gc = None
            
            if gc:
                sheet = gc.open_by_key(SHEET_ID).worksheet(WORKSHEET_NAME)
                print("✅ Google Sheets initialized successfully")
            else:
                print("❌ Google Sheets initialization skipped - authentication failed")
        else:
            print("❌ Google Sheets initialization skipped - no credentials available")
    except Exception as e:
        print(f"❌ Failed to initialize Google Sheets: {e}")
        gc = None
        sheet = None

# Initialize Google Sheets (non-blocking)
initialize_google_sheets()

# In-memory store for pending email drafts (keyed by WhatsApp sender)
pending_email_drafts: Dict[str, dict] = {}

# Store for delayed responses
delayed_responses: Dict[str, str] = {}

# Store for pending place queries (keyed by WhatsApp sender)
pending_place_queries: Dict[str, str] = {}

async def send_whatsapp_message(to_number: str, message: str):
    """Send a WhatsApp message using Twilio API"""
    try:
        from twilio.rest import Client
        
        account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        whatsapp_number = os.getenv("TWILIO_WHATSAPP_NUMBER")  # Your registered WhatsApp Business number
        
        if not account_sid or not auth_token or not whatsapp_number:
            print("Twilio credentials or WhatsApp number not found")
            return False
            
        client = Client(account_sid, auth_token)
        
        # Send message using your registered WhatsApp Business number
        message = client.messages.create(
            body=message,
            from_=f'whatsapp:{whatsapp_number}',  # Your registered WhatsApp Business number
            to=to_number
        )
        
        print(f"Sent WhatsApp message to {to_number}: {message.sid}")
        return True
        
    except Exception as e:
        error_str = str(e)
        print(f"Failed to send WhatsApp message: {error_str}")
        
        # Check if it's a daily limit error (shouldn't happen with paid account)
        if "daily messages limit" in error_str or "63038" in error_str:
            print("Hit Twilio daily limit - storing message for webhook fallback")
            # Store the message for potential webhook fallback
            delayed_responses[to_number] = message
            return False
        return False

async def transcribe_audio(audio_url: str) -> str | None:
    """Download audio file and transcribe it using OpenAI Whisper"""
    try:
        # Get Twilio credentials from environment
        twilio_account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        twilio_auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        
        if not twilio_account_sid or not twilio_auth_token:
            print("Twilio credentials not found in environment variables")
            return None
        
        # Download the audio file with Twilio authentication
        response = requests.get(
            audio_url, 
            auth=(twilio_account_sid, twilio_auth_token)
        )
        response.raise_for_status()
        
        # Save to temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as temp_file:
            temp_file.write(response.content)
            temp_file_path = temp_file.name
        
        # Transcribe using OpenAI Whisper
        with open(temp_file_path, "rb") as audio_file:
            transcript = openai.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file
            )
        
        # Clean up temporary file
        os.unlink(temp_file_path)
        
        transcribed_text = transcript.text.strip()
        print(f"Transcribed audio: {transcribed_text}")
        return transcribed_text
        
    except Exception as e:
        print(f"Audio transcription failed: {e}")
        # Clean up temp file if it exists
        try:
            if 'temp_file_path' in locals():
                os.unlink(temp_file_path)
        except:
            pass
        return None

# --- [HELPER FUNCTION: Google Places Text Search] ---
async def find_places(query: str, location: str = None, radius: int = 5000) -> List[Dict]:
    """
    Calls Google Places Text Search API and returns top 5 results with name, address, rating, and place_id.
    """
    api_key = os.getenv("GOOGLE_PLACES_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_PLACES_API_KEY not set in environment.")
    
    # Combine query with location for better results
    search_query = query
    if location and location.lower() not in ["near me", "null", "none", ""]:
        search_query = f"{query} in {location}"
    
    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {
        "query": search_query,
        "key": api_key,
        "radius": radius
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        
        results = []
        for place in data.get("results", [])[:5]:
            results.append({
                "name": place.get("name"),
                "formatted_address": place.get("formatted_address"),
                "rating": place.get("rating"),
                "place_id": place.get("place_id")
            })
        return results

def build_extraction_prompt(user_input: str):
    # Get current date for context
    current_date = datetime.now(DUBAI_TZ)
    current_date_str = current_date.strftime('%Y-%m-%d')
    current_year = current_date.year
    
    return f'''
You are a helpful assistant that extracts structured info from user messages for contact management, email sending, calendar management, or general conversation.

CURRENT DATE CONTEXT: Today is {current_date_str} ({current_date.strftime('%A, %B %d, %Y')})

Extract:
- intent ("send_email" for sending emails; "add_contact" for adding new contacts; "lookup_contact" for finding contact info; "update_contact" for modifying existing contacts; "delete_contact" for removing contacts; "calendar_auth" for calendar authentication; "calendar_create" for creating events; "calendar_list" for listing events; "calendar_update" for updating events; "calendar_delete" for deleting events; "find_place" for finding places using Google Places API; "memory_query" for asking about past actions or conversations; otherwise "other")
- recipient_email (the email address to send to, if explicitly mentioned)
- recipient_name (the person's full name if no email is provided)
- subject (a polished, professional subject line based on the message content)
- email_body (a professional, polite email body based on the user's request, always signed off as "Rahul Menon")
- contact_name (full name of the contact for add/update/delete operations)
- contact_email (email address of the contact for add/update operations)
- contact_phone (phone number of the contact for add/update operations)
- lookup_name (name of the contact to look up)
- lookup_field (what information to find: "email", "phone", "address", or "all")
- update_field (which field to update: "name", "email", "phone", or "all")
- update_value (new value for the field being updated)
- calendar_summary (event title/summary for calendar events)
- calendar_start (start datetime in ISO format for calendar events - IMPORTANT: For delete operations, extract the date even if it's just "May 26", "today", "tomorrow", etc. Convert relative dates to ISO format using the current date context above. If no year is specified, assume the current year {current_year})
- calendar_end (end datetime in ISO format for calendar events)
- calendar_description (event description for calendar events)
- calendar_event_id (event ID for updating/deleting calendar events)
- calendar_field (field to update: "summary", "description", "start", "end")
- calendar_value (new value for calendar field updates)
- place_query (the user's location search string for Google Places)
- place_location (optional: a lat,lng string if user gives coordinates or "near me", else null)
- memory_query (what the user wants to know about their past actions: "emails", "places", "meetings", "contacts", or "all")

User said:
"""{user_input}"""

Examples of calendar intents (using current date context):
- "setup my calendar" or "connect calendar" → calendar_auth
- "create meeting tomorrow 2pm" → calendar_create
- "schedule lunch with John on Friday 1pm to 2pm" → calendar_create
- "list my events" or "show my calendar" → calendar_list
- "what's on my calendar today" → calendar_list
- "update meeting title to Team Sync" → calendar_update
- "delete my 3pm meeting" → calendar_delete
- "Delete My Meeting on May 26" → calendar_delete (calendar_summary: "My Meeting", calendar_start: "{current_year}-05-26")
- "delete my event for today" → calendar_delete (calendar_start: "{current_date_str}")
- "remove my appointment tomorrow" → calendar_delete (calendar_start: "{(current_date + timedelta(days=1)).strftime('%Y-%m-%d')}")

IMPORTANT: For calendar_delete operations, ALWAYS extract the date/time information into calendar_start field, even if it's relative like "today", "tomorrow", "May 26", etc. Convert these to ISO date format (YYYY-MM-DD) using the current date context provided above.

Examples of find_place intent:
- "What are the top sushi spots near me?" → find_place (place_query: "sushi spots", place_location: "near me")
- "Find best pizza in Downtown Dubai." → find_place (place_query: "best pizza", place_location: "Downtown Dubai")
- "Show me vegan restaurants at 25.1972,55.2744" → find_place (place_query: "vegan restaurants", place_location: "25.1972,55.2744")
- "Where can I find good coffee shops?" → find_place (place_query: "coffee shops", place_location: null)

Examples of memory_query intent:
- "Did I send any emails today?" → memory_query (memory_query: "emails")
- "What places did I search for?" → memory_query (memory_query: "places")
- "What meetings did I create?" → memory_query (memory_query: "meetings")
- "Show me my recent activity" → memory_query (memory_query: "all")
- "What did I do earlier?" → memory_query (memory_query: "all")

Examples of send_email intent:
- "Send an email to John about the meeting"
- "Email Sarah saying I'll be late"

Examples of add_contact intent:
- "Add John Smith, email john@example.com, phone 123-456-7890"
- "Save contact: Jane Doe, jane@email.com"

Examples of lookup_contact intent:
- "What's John's email?"
- "Get Sarah's phone number"

Examples of update_contact intent:
- "Update John's email to newemail@example.com"
- "Change Sarah's phone number to 555-1234"

Examples of delete_contact intent:
- "Delete John Smith from contacts"
- "Remove Sarah from my contact list"

Respond ONLY in this JSON format:
{{
  "intent": "send_email" or "add_contact" or "lookup_contact" or "update_contact" or "delete_contact" or "calendar_auth" or "calendar_create" or "calendar_list" or "calendar_update" or "calendar_delete" or "find_place" or "memory_query" or "other",
  "recipient_email": "...",
  "recipient_name": "...",
  "subject": "...",
  "email_body": "...",
  "contact_name": "...",
  "contact_email": "...",
  "contact_phone": "...",
  "lookup_name": "...",
  "lookup_field": "...",
  "update_field": "...",
  "update_value": "...",
  "calendar_summary": "...",
  "calendar_start": "...",
  "calendar_end": "...",
  "calendar_description": "...",
  "calendar_event_id": "...",
  "calendar_field": "...",
  "calendar_value": "...",
  "place_query": "...",
  "place_location": "...",
  "memory_query": "..."
}}
If you cannot extract all required fields, set intent to "other" and leave the other fields empty.
'''

def sanitize_text_for_llm(text: str) -> str:
    """Sanitize text to remove control characters that break JSON parsing"""
    # Remove control characters except newlines and tabs
    sanitized = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', text)
    # Replace problematic quotes and characters
    sanitized = sanitized.replace('"', "'").replace('\n', ' ').replace('\r', ' ')
    # Remove extra whitespace
    sanitized = ' '.join(sanitized.split())
    return sanitized

async def extract_email_info_with_llm(user_input: str, whatsapp_number: str = None):
    # Sanitize the input text first
    sanitized_input = sanitize_text_for_llm(user_input)
    
    # Get memory-enhanced context if memory manager is available
    enhanced_prompt = build_extraction_prompt(sanitized_input)
    
    if memory_manager and whatsapp_number:
        try:
            # Get user ID
            user_id = await memory_manager.get_user_id(whatsapp_number)
            
            # Get personalized context
            memory_context = await memory_manager.get_personalized_prompt_context(user_id, sanitized_input)
            
            if memory_context:
                enhanced_prompt = f"{memory_context}\n\n{enhanced_prompt}"
                
        except Exception as e:
            print(f"Error getting memory context: {e}")
    
    try:
        response = openai.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You extract structured email instructions and generate professional emails signed as Rahul Menon. Use the provided memory context to personalize responses based on user preferences and past interactions."},
                {"role": "user", "content": enhanced_prompt}
            ]
        )
        content = response.choices[0].message.content
        data = json.loads(content)
        return data
    except Exception as e:
        print("LLM extraction failed:", e)
        return None

def get_email_by_name(name: str) -> str | None:
    if not sheet:
        print("Google Sheets not initialized")
        return None
    
    try:
        # Get all records from the sheet
        records = sheet.get_all_records()
        
        # Search for name match (case-insensitive, trimmed)
        name_lower = name.strip().lower()
        for record in records:
            full_name = str(record.get('full_name', '')).strip().lower()
            # Try exact match first
            if full_name == name_lower:
                email = record.get('email', '').strip()
                if email:
                    print(f"Found email for {name}: {email}")
                    return email
            # Try partial match (name is contained in full_name)
            elif name_lower in full_name or any(part in full_name for part in name_lower.split()):
                email = record.get('email', '').strip()
                if email:
                    print(f"Found email for {name} (matched {record.get('full_name', '')}): {email}")
                    return email
        
        print(f"No email found for name: {name}")
        return None
    except Exception as e:
        print(f"Error searching Google Sheets for {name}: {e}")
        return None

async def send_email_resend(to_email: str, subject: str, email_body: str):
    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "from": SENDER_EMAIL,
        "to": [to_email],
        "subject": subject,
        "text": email_body
    }
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(RESEND_API_URL, headers=headers, json=payload)
            resp.raise_for_status()
            return True, resp.json()
        except Exception as e:
            print("Resend API error:", e)
            return False, str(e)

async def add_contact_to_sheet(name: str, email: str, phone: str) -> bool:
    """Add a new contact to the Google Sheet"""
    if not sheet:
        print("Google Sheets not initialized")
        return False
    
    try:
        # Check if contact already exists
        records = sheet.get_all_records()
        name_lower = name.strip().lower()
        for record in records:
            existing_name = str(record.get('full_name', '')).strip().lower()
            if existing_name == name_lower:
                print(f"Contact {name} already exists")
                return False
        
        # Append new row to the sheet
        row_data = [name, email or "", phone or ""]
        sheet.append_row(row_data)
        print(f"Added contact: {name}, {email}, {phone}")
        return True
    except Exception as e:
        print(f"Error adding contact to Google Sheets: {e}")
        return False

def update_contact_in_sheet(name: str, field: str, new_value: str) -> tuple[bool, str]:
    """Update an existing contact in the Google Sheet"""
    if not sheet:
        print("Google Sheets not initialized")
        return False, "Google Sheets not available"
    
    try:
        # Get all records and find the contact
        records = sheet.get_all_records()
        name_lower = name.strip().lower()
        
        for i, record in enumerate(records, start=2):  # Start at row 2 (after header)
            existing_name = str(record.get('full_name', '')).strip().lower()
            if existing_name == name_lower:
                # Update the appropriate field
                if field == "name":
                    sheet.update_cell(i, 1, new_value)  # Column A
                    return True, f"Updated {name}'s name to {new_value}"
                elif field == "email":
                    sheet.update_cell(i, 2, new_value)  # Column B
                    return True, f"Updated {name}'s email to {new_value}"
                elif field == "phone":
                    sheet.update_cell(i, 3, new_value)  # Column C
                    return True, f"Updated {name}'s phone to {new_value}"
                else:
                    return False, "Invalid field specified"
        
        return False, f"Contact {name} not found"
        
    except Exception as e:
        print(f"Error updating contact {name}: {e}")
        return False, "Error updating contact"

def delete_contact_from_sheet(name: str) -> tuple[bool, str]:
    """Delete a contact from the Google Sheet"""
    if not sheet:
        print("Google Sheets not initialized")
        return False, "Google Sheets not available"
    
    try:
        # Get all records and find the contact
        records = sheet.get_all_records()
        name_lower = name.strip().lower()
        
        # First try exact match
        for i, record in enumerate(records, start=2):  # Start at row 2 (after header)
            existing_name = str(record.get('full_name', '')).strip().lower()
            if existing_name == name_lower:
                # Delete the row
                sheet.delete_rows(i)
                print(f"Deleted contact: {record.get('full_name', '')}")
                return True, f"Contact {record.get('full_name', '')} deleted successfully"
        
        # If no exact match, try partial match
        for i, record in enumerate(records, start=2):  # Start at row 2 (after header)
            existing_name = str(record.get('full_name', '')).strip().lower()
            full_name_parts = existing_name.split()
            name_parts = name_lower.split()
            
            # Match if all parts of the search name are found in the full name
            if all(any(part in full_part for full_part in full_name_parts) for part in name_parts):
                # Delete the row
                sheet.delete_rows(i)
                print(f"Deleted contact: {record.get('full_name', '')} (matched {name})")
                return True, f"Contact {record.get('full_name', '')} deleted successfully"
        
        return False, f"Contact {name} not found"
        
    except Exception as e:
        print(f"Error deleting contact {name}: {e}")
        return False, "Error deleting contact"

def lookup_contact_info(name: str, field: str) -> str:
    """Look up contact information from the Google Sheet"""
    if not sheet:
        print("Google Sheets not initialized")
        return "Google Sheets not available"
    
    try:
        # Get all records from the sheet
        records = sheet.get_all_records()
        
        # Search for name match (case-insensitive, trimmed)
        name_lower = name.strip().lower()
        for record in records:
            full_name = str(record.get('full_name', '')).strip().lower()
            # Try exact match first
            if full_name == name_lower:
                print(f"Found exact match: {record.get('full_name', '')}")
                return format_contact_result(record, field)
            
        # If no exact match, try partial match but be more strict
        for record in records:
            full_name = str(record.get('full_name', '')).strip().lower()
            # Check if the search name is a significant part of the full name
            name_parts = name_lower.split()
            full_name_parts = full_name.split()
            
            # Match if all parts of the search name are found in the full name
            if all(any(part in full_part for full_part in full_name_parts) for part in name_parts):
                print(f"Found partial match: {record.get('full_name', '')}")
                return format_contact_result(record, field)
        
        print(f"No contact found for name: {name}")
        return f"No contact found for {name}"
        
    except Exception as e:
        print(f"Error looking up contact {name}: {e}")
        return "Error looking up contact"

def format_contact_result(record: dict, field: str) -> str:
    """Format the contact result based on the requested field"""
    try:
        if field == "all":
            # Return all available information
            email = str(record.get('email', '') or "N/A")
            phone = str(record.get('phone_number', '') or "N/A")
            address = str(record.get('address', '') or "N/A")
            return f"Contact: {record.get('full_name', '')}\nEmail: {email}\nPhone: {phone}\nAddress: {address}"
        elif field == "email":
            return str(record.get('email', '') or "N/A")
        elif field == "phone":
            return str(record.get('phone_number', '') or "N/A")
        elif field == "address":
            return str(record.get('address', '') or "N/A")
        else:
            return "N/A"
    except Exception as e:
        print(f"Error formatting contact result: {e}")
        return "Error formatting contact information"

@app.post("/webhook", response_class=PlainTextResponse)
async def whatsapp_webhook(
    background_tasks: BackgroundTasks,
    request: Request,
    Body: str = Form(...),
    From: str = Form(...),
    NumMedia: str = Form("0"),
    MediaContentType0: str = Form(""),
    MediaUrl0: str = Form("")
):
    start_time = time.time()
    
    try:
        print(f"Message from {From}: {Body}")
        print(f"NumMedia: {NumMedia}, MediaContentType0: {MediaContentType0}")
        
        # Check if there's a delayed response for this number (due to API limits)
        if From in delayed_responses:
            response_message = delayed_responses.pop(From)
            print(f"Returning delayed response due to API limits: {response_message}")
            return PlainTextResponse(response_message)
        
        # Add the message processing to background tasks
        background_tasks.add_task(
            process_message_background,
            From,
            Body,
            NumMedia,
            MediaContentType0,
            MediaUrl0
        )
        
        # Respond immediately to Twilio with empty response
        print(f"Responding immediately to Twilio, processing in background")
        return PlainTextResponse("")
        
    except Exception as e:
        print(f"ERROR in webhook: {e}")
        import traceback
        traceback.print_exc()
        return PlainTextResponse("")
    
    finally:
        end_time = time.time()
        print(f"Webhook response time: {end_time - start_time:.2f} seconds")

async def revise_email_with_ai(to_email: str, subject: str, email_body: str, revision_instruction: str) -> dict | None:
    """Use AI to revise an email draft based on user feedback"""
    try:
        prompt = f"""
You are helping to revise an email draft based on user feedback.

Current email:
To: {to_email}
Subject: {subject}
Body: {email_body}

User's revision request: "{revision_instruction}"

Please revise the email according to the user's request. Keep the same recipient but you can modify the subject and body as needed. Always sign off as "Rahul Menon".

Respond ONLY in this JSON format:
{{
  "to_email": "{to_email}",
  "subject": "revised subject line",
  "email_body": "revised email body content"
}}
"""
        
        response = openai.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are an expert email writer who revises emails based on user feedback. Always maintain professionalism and sign as Rahul Menon."},
                {"role": "user", "content": prompt}
            ]
        )
        
        content = response.choices[0].message.content
        revised_data = json.loads(content)
        print(f"AI revised email: {revised_data}")
        return revised_data
        
    except Exception as e:
        print(f"Email revision failed: {e}")
        return None

async def process_message_background(from_number: str, body: str, num_media: str, media_content_type: str, media_url: str):
    """Process the message in the background and send response via Twilio API"""
    try:
        print(f"Background processing message from {from_number}: {body}")
        
        # Handle voice notes and audio messages
        if num_media != "0" and media_content_type.startswith("audio"):
            print(f"Audio message detected, transcribing: {media_url}")
            transcribed_text = await transcribe_audio(media_url)
            if transcribed_text:
                body = transcribed_text
                print(f"Using transcribed text: {body}")
            else:
                await send_whatsapp_message(from_number, "Sorry, I couldn't transcribe your voice message. Please try again.")
                return

        # Handle simple date/time queries before LLM extraction
        body_lower = body.strip().lower()
        if any(phrase in body_lower for phrase in [
            "what is the date", "what's the date", "what date is it", 
            "what is today's date", "what's today's date", "date today",
            "what time is it", "what's the time", "current time", "time now"
        ]):
            current_time = datetime.now(DUBAI_TZ)
            if any(phrase in body_lower for phrase in ["time", "what time"]):
                reply = f"🕐 Current time in Dubai: {current_time.strftime('%I:%M %p')}\n📅 Date: {current_time.strftime('%A, %B %d, %Y')}"
            else:
                reply = f"📅 Today's date: {current_time.strftime('%A, %B %d, %Y')}\n🕐 Current time in Dubai: {current_time.strftime('%I:%M %p')}"
            await send_whatsapp_message(from_number, reply)
            return

        # Check for approval to send a pending draft
        if from_number in pending_email_drafts:
            approval_text = body.strip().lower()
            if any(phrase in approval_text for phrase in ["yes", "send it", "please send", "go ahead", "confirm", "approve"]):
                draft = pending_email_drafts.pop(from_number)
                to_email = draft["to_email"]
                subject = draft["subject"]
                email_body = draft["email_body"]
                print(f"Sending email to {to_email} with subject: {subject}")
                
                # Actually send the email
                success, result = await send_email_resend(to_email, subject, email_body)
                if success:
                    print(f"Email sent successfully to {to_email}")
                    reply = f"✅ EMAIL SENT SUCCESSFULLY!\n\nTo: {to_email}\nSubject: {subject}\n\nYour email has been delivered!"
                else:
                    print(f"Failed to send email via Resend API: {result}")
                    reply = f"❌ Failed to send email. Error: {result}"
                
                await send_whatsapp_message(from_number, reply)
                return
            elif any(phrase in approval_text for phrase in ["no", "cancel", "don't send", "do not send"]):
                pending_email_drafts.pop(from_number)
                await send_whatsapp_message(from_number, "❌ Email draft cancelled. No email was sent.")
                return
            else:
                # User wants to edit the draft
                print(f"User wants to edit draft with instruction: {body}")
                draft = pending_email_drafts[from_number]  # Keep the draft, don't pop it yet
                
                # Use AI to revise the email based on user feedback
                revised_draft = await revise_email_with_ai(
                    draft["to_email"], 
                    draft["subject"], 
                    draft["email_body"], 
                    body
                )
                
                if revised_draft:
                    # Update the pending draft with revised version
                    pending_email_drafts[from_number] = {
                        "to_email": revised_draft["to_email"],
                        "subject": revised_draft["subject"],
                        "email_body": revised_draft["email_body"]
                    }
                    reply = (
                        f"Here is your revised email draft:\n\n"
                        f"To: {revised_draft['to_email']}\nSubject: {revised_draft['subject']}\n\n{revised_draft['email_body']}\n\n"
                        "Reply 'Yes, send it' to send this email, provide more editing instructions, or 'No' to cancel."
                    )
                    await send_whatsapp_message(from_number, reply)
                    return
                else:
                    await send_whatsapp_message(from_number, "Sorry, I couldn't revise the email. Please try again or reply 'Yes' to send the original draft.")
                    return

        data = await extract_email_info_with_llm(body, from_number)
        print(f"LLM extraction result: {data}")

        # Store conversation in memory if memory manager is available
        if memory_manager and data:
            try:
                user_id = await memory_manager.get_user_id(from_number)
                
                # Store the conversation with extracted intent
                await memory_manager.store_conversation_with_memory(
                    user_id=user_id,
                    message_text=body,
                    intent=data.get("intent"),
                    metadata={
                        "whatsapp_number": from_number,
                        "timestamp": datetime.now().isoformat(),
                        "intent": data.get("intent", "unknown"),
                        "recipient_name": data.get("recipient_name", ""),
                        "place_query": data.get("place_query", "")
                    }
                )
                
                # Update user preferences based on conversation
                await memory_manager.update_user_preferences_from_conversation(user_id, data)
                
                print(f"✅ Stored conversation in hybrid memory for user {user_id}")
                
            except Exception as e:
                print(f"❌ Error storing conversation in memory: {e}")

        if data and data.get("intent") == "calendar_auth":
            try:
                # Ensure credentials are available
                if not setup_google_credentials():
                    reply = "❌ Google credentials not available. Please contact administrator."
                    await send_whatsapp_message(from_number, reply)
                    return
                
                # Create OAuth flow with both Sheets and Calendar scopes
                from google_auth_oauthlib.flow import InstalledAppFlow
                from google.auth.transport.requests import Request
                from google.oauth2.credentials import Credentials
                import os
                
                # Combined scopes for both Sheets and Calendar
                COMBINED_SCOPES = [
                    'https://www.googleapis.com/auth/spreadsheets',
                    'https://www.googleapis.com/auth/calendar'
                ]
                
                creds = None
                token_file = "combined_token.json"
                
                # Check if we have stored credentials
                if os.path.exists(token_file):
                    creds = Credentials.from_authorized_user_file(token_file, COMBINED_SCOPES)
                
                # If there are no (valid) credentials available, let the user log in
                if not creds or not creds.valid:
                    if creds and creds.expired and creds.refresh_token:
                        creds.refresh(Request())
                    else:
                        if not os.path.exists("credentials.json"):
                            raise Exception("credentials.json file not found")
                        
                        flow = InstalledAppFlow.from_client_secrets_file(
                            "credentials.json", COMBINED_SCOPES)
                        creds = flow.run_local_server(port=0)
                    
                    # Save the credentials for the next run
                    with open(token_file, 'w') as token:
                        token.write(creds.to_json())
                
                # Test calendar access
                service = build('calendar', 'v3', credentials=creds)
                calendar_list = service.calendarList().list().execute()
                
                reply = "✅ Google Calendar connected successfully! You can now:\n\n📅 Create events: 'create meeting tomorrow 2pm to 3pm'\n📋 List events: 'list my events'\n\nYour calendar is ready to use!"
                await send_whatsapp_message(from_number, reply)
                return
                
            except Exception as e:
                print(f"Calendar auth error: {e}")
                reply = f"❌ Calendar authorization failed: {str(e)}\n\nPlease make sure Google Calendar API is enabled in your Google Cloud Console."
                await send_whatsapp_message(from_number, reply)
                return

        elif data and data.get("intent") == "calendar_create":
            try:
                # Use combined credentials for calendar access
                from google.oauth2.credentials import Credentials
                import os
                
                token_file = "combined_token.json"
                COMBINED_SCOPES = [
                    'https://www.googleapis.com/auth/spreadsheets',
                    'https://www.googleapis.com/auth/calendar'
                ]
                
                if not os.path.exists(token_file):
                    reply = "❌ Calendar not set up. Please send 'Set up my calendar' first."
                    await send_whatsapp_message(from_number, reply)
                    return
                
                creds = Credentials.from_authorized_user_file(token_file, COMBINED_SCOPES)
                service = build('calendar', 'v3', credentials=creds)
                
                summary = data.get("calendar_summary", "New Event")
                start_time = data.get("calendar_start")
                end_time = data.get("calendar_end")
                description = data.get("calendar_description", "")
                
                if not start_time or not end_time:
                    reply = "❌ Please provide both start and end times for the event.\nExample: 'Create meeting tomorrow 2pm to 3pm'"
                    await send_whatsapp_message(from_number, reply)
                    return
                
                event_body = {
                    'summary': summary,
                    'start': format_datetime_for_google(start_time),
                    'end': format_datetime_for_google(end_time),
                }
                
                if description:
                    event_body['description'] = description
                
                event = service.events().insert(calendarId='primary', body=event_body).execute()
                event_link = event.get('htmlLink', 'No link available')
                
                reply = f"✅ Calendar event created successfully!\n\n📅 {summary}\n🔗 {event_link}"
                await send_whatsapp_message(from_number, reply)
                return
                
            except Exception as e:
                print(f"Calendar create error: {e}")
                reply = f"❌ Failed to create calendar event: {str(e)}\n\nTry 'setup my calendar' first."
                await send_whatsapp_message(from_number, reply)
                return

        elif data and data.get("intent") == "calendar_list":
            try:
                # Use combined credentials for calendar access
                from google.oauth2.credentials import Credentials
                import os
                
                token_file = "combined_token.json"
                COMBINED_SCOPES = [
                    'https://www.googleapis.com/auth/spreadsheets',
                    'https://www.googleapis.com/auth/calendar'
                ]
                
                if not os.path.exists(token_file):
                    reply = "❌ Calendar not set up. Please send 'Set up my calendar' first."
                    await send_whatsapp_message(from_number, reply)
                    return
                
                creds = Credentials.from_authorized_user_file(token_file, COMBINED_SCOPES)
                service = build('calendar', 'v3', credentials=creds)
                
                # Default to current time and next 7 days
                time_min = datetime.now(DUBAI_TZ).isoformat()
                time_max = (datetime.now(DUBAI_TZ) + timedelta(days=7)).isoformat()
                
                events_result = service.events().list(
                    calendarId='primary',
                    timeMin=time_min,
                    timeMax=time_max,
                    maxResults=10,
                    singleEvents=True,
                    orderBy='startTime'
                ).execute()
                
                events = events_result.get('items', [])
                
                if not events:
                    reply = "📅 No upcoming events found in the next 7 days."
                else:
                    event_list = []
                    for event in events:
                        start_time = event['start'].get('dateTime', event['start'].get('date'))
                        summary = event.get('summary', 'No title')
                        
                        # Format start time for display
                        try:
                            if 'T' in start_time:  # DateTime
                                dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                                formatted_time = dt.strftime('%m/%d %H:%M')
                            else:  # Date only
                                formatted_time = start_time
                        except:
                            formatted_time = start_time
                        
                        event_list.append(f"📅 {formatted_time} - {summary}")
                    
                    reply = f"📅 Your upcoming events:\n\n" + "\n".join(event_list)
                
                await send_whatsapp_message(from_number, reply)
                return
                
            except Exception as e:
                print(f"Calendar list error: {e}")
                reply = f"❌ Failed to list calendar events: {str(e)}\n\nTry 'setup my calendar' first."
                await send_whatsapp_message(from_number, reply)
                return

        elif data and data.get("intent") == "calendar_delete":
            try:
                # Use combined credentials for calendar access
                from google.oauth2.credentials import Credentials
                import os
                
                token_file = "combined_token.json"
                COMBINED_SCOPES = [
                    'https://www.googleapis.com/auth/spreadsheets',
                    'https://www.googleapis.com/auth/calendar'
                ]
                
                if not os.path.exists(token_file):
                    reply = "❌ Calendar not set up. Please send 'Set up my calendar' first."
                    await send_whatsapp_message(from_number, reply)
                    return
                
                creds = Credentials.from_authorized_user_file(token_file, COMBINED_SCOPES)
                service = build('calendar', 'v3', credentials=creds)
                
                event_id = data.get("calendar_event_id")
                start_date = data.get("calendar_start")
                event_summary = data.get("calendar_summary")
                
                if event_id:
                    # Delete specific event by ID
                    service.events().delete(calendarId='primary', eventId=event_id).execute()
                    reply = f"✅ Event deleted successfully!"
                elif start_date:
                    # Find and delete events on specific date
                    
                    # Parse the date
                    try:
                        if 'T' in start_date:
                            target_date = datetime.fromisoformat(start_date.replace('Z', '+00:00')).date()
                        else:
                            target_date = datetime.fromisoformat(start_date).date()
                        
                        # Get events for that day
                        time_min = datetime.combine(target_date, datetime.min.time()).isoformat() + 'Z'
                        time_max = datetime.combine(target_date, datetime.max.time()).isoformat() + 'Z'
                        
                        events_result = service.events().list(
                            calendarId='primary',
                            timeMin=time_min,
                            timeMax=time_max,
                            singleEvents=True,
                            orderBy='startTime'
                        ).execute()
                        
                        events = events_result.get('items', [])
                        
                        # If we have an event summary, filter by title
                        if event_summary and events:
                            matching_events = []
                            for event in events:
                                event_title = event.get('summary', '').lower()
                                if event_summary.lower() in event_title or event_title in event_summary.lower():
                                    matching_events.append(event)
                            events = matching_events
                        
                        if not events:
                            date_str = target_date.strftime('%B %d, %Y')
                            if event_summary:
                                reply = f"❌ No events found matching '{event_summary}' on {date_str}"
                            else:
                                reply = f"❌ No events found on {date_str}"
                        elif len(events) == 1:
                            # Delete the single event
                            event = events[0]
                            service.events().delete(calendarId='primary', eventId=event['id']).execute()
                            reply = f"✅ Deleted event: {event.get('summary', 'Untitled')} on {target_date.strftime('%B %d, %Y')}"
                        else:
                            # Multiple events - list them for user to choose
                            event_list = []
                            for i, event in enumerate(events, 1):
                                start_time = event['start'].get('dateTime', event['start'].get('date'))
                                summary = event.get('summary', 'No title')
                                if 'T' in start_time:
                                    dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                                    time_str = dt.strftime('%H:%M')
                                    event_list.append(f"{i}. {time_str} - {summary}")
                                else:
                                    event_list.append(f"{i}. {summary}")
                            
                            reply = f"❌ Multiple events found on {target_date.strftime('%B %d, %Y')}:\n\n" + "\n".join(event_list) + "\n\nPlease specify which event to delete by saying 'Delete event [number]' or provide the event name."
                    except Exception as date_error:
                        reply = f"❌ Could not parse date: {str(date_error)}"
                elif event_summary:
                    # Search for events by title across next 30 days
                    try:
                        time_min = datetime.now(DUBAI_TZ).isoformat()
                        time_max = (datetime.now(DUBAI_TZ) + timedelta(days=30)).isoformat()
                        
                        events_result = service.events().list(
                            calendarId='primary',
                            timeMin=time_min,
                            timeMax=time_max,
                            singleEvents=True,
                            orderBy='startTime'
                        ).execute()
                        
                        events = events_result.get('items', [])
                        
                        # Filter events by title
                        matching_events = []
                        for event in events:
                            event_title = event.get('summary', '').lower()
                            if event_summary.lower() in event_title or event_title in event_summary.lower():
                                matching_events.append(event)
                        
                        if not matching_events:
                            reply = f"❌ No events found matching '{event_summary}' in the next 30 days"
                        elif len(matching_events) == 1:
                            # Delete the single matching event
                            event = matching_events[0]
                            service.events().delete(calendarId='primary', eventId=event['id']).execute()
                            
                            # Format the event date for display
                            start_time = event['start'].get('dateTime', event['start'].get('date'))
                            if 'T' in start_time:
                                dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                                date_str = dt.strftime('%B %d, %Y at %H:%M')
                            else:
                                dt = datetime.fromisoformat(start_time)
                                date_str = dt.strftime('%B %d, %Y')
                            
                            reply = f"✅ Deleted event: {event.get('summary', 'Untitled')} on {date_str}"
                        else:
                            # Multiple matching events - list them for user to choose
                            event_list = []
                            for i, event in enumerate(matching_events, 1):
                                start_time = event['start'].get('dateTime', event['start'].get('date'))
                                summary = event.get('summary', 'No title')
                                if 'T' in start_time:
                                    dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                                    date_time_str = dt.strftime('%m/%d %H:%M')
                                    event_list.append(f"{i}. {date_time_str} - {summary}")
                                else:
                                    dt = datetime.fromisoformat(start_time)
                                    date_str = dt.strftime('%m/%d')
                                    event_list.append(f"{i}. {date_str} - {summary}")
                            
                            reply = f"❌ Multiple events found matching '{event_summary}':\n\n" + "\n".join(event_list) + "\n\nPlease specify which event to delete by saying 'Delete event [number]' or provide a more specific date."
                    except Exception as search_error:
                        reply = f"❌ Error searching for events: {str(search_error)}"
                else:
                    reply = "❌ Please specify which event to delete. You can say 'Delete my meeting on May 26' or provide an event ID."
                
                await send_whatsapp_message(from_number, reply)
                return
                
            except Exception as e:
                print(f"Calendar delete error: {e}")
                reply = f"❌ Failed to delete calendar event: {str(e)}"
                await send_whatsapp_message(from_number, reply)
                return

        elif data and data.get("intent") == "delete_contact":
            contact_name = data.get("contact_name")
            
            if contact_name:
                success, message = delete_contact_from_sheet(contact_name)
                if success:
                    reply = f"✅ {message}"
                else:
                    reply = f"❌ {message}"
                await send_whatsapp_message(from_number, reply)
                return
            else:
                await send_whatsapp_message(from_number, "Please specify which contact you want to delete.")
                return

        elif data and data.get("intent") == "update_contact":
            contact_name = data.get("contact_name")
            update_field = data.get("update_field")
            update_value = data.get("update_value")
            
            if contact_name and update_field and update_value:
                success, message = update_contact_in_sheet(contact_name, update_field, update_value)
                if success:
                    reply = f"✅ {message}"
                else:
                    reply = f"❌ {message}"
                await send_whatsapp_message(from_number, reply)
                return
            else:
                await send_whatsapp_message(from_number, "Please specify the contact name, field to update, and new value.")
                return

        elif data and data.get("intent") == "lookup_contact":
            lookup_name = data.get("lookup_name")
            lookup_field = data.get("lookup_field")

            if lookup_name:
                result = lookup_contact_info(lookup_name, lookup_field or "all")
                reply = f"📋 {result}"
                await send_whatsapp_message(from_number, reply)
                return
            else:
                await send_whatsapp_message(from_number, "Please specify which contact you want to look up.")
                return

        elif data and data.get("intent") == "add_contact":
            contact_name = data.get("contact_name")
            contact_email = data.get("contact_email")
            contact_phone = data.get("contact_phone")

            if contact_name:
                # Check if we have at least one contact method
                if not contact_email and not contact_phone:
                    reply = f"I have the name '{contact_name}'. Please provide either an email address or phone number (or say 'N/A' if you don't have it)."
                    await send_whatsapp_message(from_number, reply)
                    return
                
                # Clean up email format (handle "at" -> "@")
                if contact_email:
                    contact_email = contact_email.replace(" at ", "@").replace(" AT ", "@")
                
                success = await add_contact_to_sheet(
                    contact_name, 
                    contact_email or "N/A", 
                    contact_phone or "N/A"
                )
                if success:
                    reply = f"✅ Contact '{contact_name}' added successfully!"
                else:
                    reply = f"❌ Contact '{contact_name}' already exists or failed to add."
                await send_whatsapp_message(from_number, reply)
                return
            else:
                await send_whatsapp_message(from_number, "Please provide at least a contact name.")
                return

        elif data and data.get("intent") == "send_email":
            print("Email intent detected!")
            to_email = data.get("recipient_email")
            recipient_name = data.get("recipient_name")
            subject = data.get("subject")
            email_body = data.get("email_body")
            print(f"Extracted: to_email={to_email}, recipient_name={recipient_name}, subject={subject}")

            # If no email provided, try to find it by name
            if not to_email and recipient_name:
                print(f"Looking up email for: {recipient_name}")
                to_email = get_email_by_name(recipient_name)
                print(f"Found email: {to_email}")
                if not to_email:
                    reply = f"❌ Couldn't find an email for {recipient_name} in your contacts."
                    await send_whatsapp_message(from_number, reply)
                    return

            if to_email and subject and email_body:
                print(f"Creating draft for: {to_email}")
                # Store the draft and ask for approval
                pending_email_drafts[from_number] = {
                    "to_email": to_email,
                    "subject": subject,
                    "email_body": email_body
                }
                reply = (
                    f"Here is your email draft:\n\n"
                    f"To: {to_email}\nSubject: {subject}\n\n{email_body}\n\n"
                    "Reply 'Yes, send it' to send this email, or 'No' to cancel."
                )
                print(f"Sending draft via Twilio API")
                await send_whatsapp_message(from_number, reply)
                return
            else:
                print(f"Missing email details: to_email={to_email}, subject={subject}, email_body={email_body}")
                reply = "❌ Couldn't extract all email details. Please try again."
                await send_whatsapp_message(from_number, reply)
                return

        elif data and data.get("intent") == "find_place":
            place_query = data.get("place_query")
            place_location = data.get("place_location")
            
            if not place_query:
                await send_whatsapp_message(from_number, "Sorry, I couldn't understand what kind of place you're looking for.")
                return
            
            # Check if we have a pending place query for this user
            if from_number in pending_place_queries:
                # User is providing location for a previous query
                previous_query = pending_place_queries.pop(from_number)
                place_query = previous_query
                place_location = body.strip()  # Use the current message as location
            
            if not place_location or place_location.lower() in ["", "null", "none"]:
                # Ask user for area if not provided
                pending_place_queries[from_number] = place_query
                await send_whatsapp_message(from_number, "Sure—what area should I search in?")
                return
            
            try:
                results = await find_places(place_query, place_location)
                if not results:
                    reply = "Sorry, I couldn't find any places matching that."
                else:
                    lines = []
                    for place in results:
                        name = place.get('name', 'Unknown')
                        rating = place.get('rating')
                        address = place.get('formatted_address', 'No address available')
                        
                        if rating:
                            rating_str = f" (⭐{rating})"
                        else:
                            rating_str = ""
                        
                        lines.append(f"{name}{rating_str}\n{address}")
                    
                    reply = "🗺️ Here are the places I found:\n\n" + "\n\n".join(lines)
                
                await send_whatsapp_message(from_number, reply)
                return
                
            except Exception as e:
                print(f"Places search error: {e}")
                await send_whatsapp_message(from_number, f"Sorry, there was an error searching for places: {str(e)}")
                return

        elif data and data.get("intent") == "memory_query":
            memory_query = data.get("memory_query")
            
            if not memory_query:
                await send_whatsapp_message(from_number, "Sorry, I couldn't understand what you want to know about your past actions.")
                return
            
            try:
                if memory_manager:
                    user_id = await memory_manager.get_user_id(from_number)
                    
                    # Get recent conversations based on query type
                    if memory_query == "emails":
                        conversations = await memory_manager.supabase_memory.get_recent_conversations(user_id, limit=20)
                        email_conversations = [conv for conv in conversations if conv.get('intent') == 'send_email']
                        
                        if email_conversations:
                            reply = "📧 **Yes, you sent emails today!**\n\n"
                            for i, conv in enumerate(email_conversations[:5], 1):
                                message = conv.get('message_text', '')
                                created_at = conv.get('created_at', '')
                                
                                # Parse timestamp to make it more readable
                                try:
                                    dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                                    time_str = dt.strftime('%I:%M %p')
                                except:
                                    time_str = created_at
                                
                                # Extract recipient from message
                                if "to " in message.lower():
                                    recipient_part = message.lower().split("to ")[1].split(" ")[0]
                                    reply += f"🕐 **{time_str}** - Email to {recipient_part.title()}\n"
                                    reply += f"   📝 {message[:80]}{'...' if len(message) > 80 else ''}\n\n"
                                else:
                                    reply += f"🕐 **{time_str}** - {message[:100]}{'...' if len(message) > 100 else ''}\n\n"
                        else:
                            reply = "📧 **No emails sent today.** You haven't sent any emails recently."
                    
                    elif memory_query == "places":
                        conversations = await memory_manager.supabase_memory.get_recent_conversations(user_id, limit=20)
                        place_conversations = [conv for conv in conversations if conv.get('intent') == 'find_place']
                        
                        if place_conversations:
                            reply = "🗺️ **Yes, you searched for places today!**\n\n"
                            for i, conv in enumerate(place_conversations[:5], 1):
                                message = conv.get('message_text', '')
                                created_at = conv.get('created_at', '')
                                
                                # Parse timestamp
                                try:
                                    dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                                    time_str = dt.strftime('%I:%M %p')
                                except:
                                    time_str = created_at
                                
                                # Extract location/place type from message
                                if "find" in message.lower() or "show" in message.lower():
                                    reply += f"🕐 **{time_str}** - {message}\n\n"
                                else:
                                    reply += f"🕐 **{time_str}** - {message}\n\n"
                        else:
                            reply = "🗺️ **No location searches today.** You haven't searched for places recently."
                    
                    elif memory_query == "meetings":
                        conversations = await memory_manager.supabase_memory.get_recent_conversations(user_id, limit=20)
                        calendar_conversations = [conv for conv in conversations if conv.get('intent') in ['calendar_create', 'calendar_list', 'calendar_delete']]
                        
                        if calendar_conversations:
                            reply = "📅 **Yes, you had calendar activity today!**\n\n"
                            for i, conv in enumerate(calendar_conversations[:5], 1):
                                message = conv.get('message_text', '')
                                created_at = conv.get('created_at', '')
                                intent = conv.get('intent', '')
                                
                                # Parse timestamp
                                try:
                                    dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                                    time_str = dt.strftime('%I:%M %p')
                                except:
                                    time_str = created_at
                                
                                # Format based on intent
                                if intent == 'calendar_create':
                                    action = "📝 Created"
                                elif intent == 'calendar_delete':
                                    action = "🗑️ Deleted"
                                else:
                                    action = "📋 Checked"
                                
                                reply += f"🕐 **{time_str}** - {action}: {message[:80]}{'...' if len(message) > 80 else ''}\n\n"
                        else:
                            reply = "📅 **No calendar activity today.** You haven't had any calendar activity recently."
                    
                    else:  # "all" or other
                        conversations = await memory_manager.supabase_memory.get_recent_conversations(user_id, limit=10)
                        
                        if conversations:
                            reply = "📋 **Here's what you did today:**\n\n"
                            for i, conv in enumerate(conversations, 1):
                                intent = conv.get('intent', 'unknown')
                                message = conv.get('message_text', '')
                                created_at = conv.get('created_at', '')
                                
                                # Parse timestamp
                                try:
                                    dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                                    time_str = dt.strftime('%I:%M %p')
                                except:
                                    time_str = created_at
                                
                                # Add emoji based on intent
                                if intent == 'send_email':
                                    emoji = "📧"
                                elif intent == 'find_place':
                                    emoji = "🗺️"
                                elif intent.startswith('calendar'):
                                    emoji = "📅"
                                elif intent == 'memory_query':
                                    emoji = "🧠"
                                else:
                                    emoji = "💬"
                                
                                reply += f"🕐 **{time_str}** {emoji} {message[:70]}{'...' if len(message) > 70 else ''}\n\n"
                        else:
                            reply = "📋 **No recent activity found.** You haven't done anything recently that I can remember."
                else:
                    reply = "❌ Memory system not available."
                
                await send_whatsapp_message(from_number, reply)
                return
                
            except Exception as e:
                print(f"Memory query error: {e}")
                reply = f"Sorry, there was an error retrieving your memory. Please try again later."
                await send_whatsapp_message(from_number, reply)
                return

        else:
            if data is None:
                print("LLM extraction failed: No data returned from LLM.")
            else:
                print(f"LLM extraction did not detect any specific intent. Data: {data}")
            reply = f"Hi! You said: {body}\n\nI can help you:\n📧 Send emails\n👤 Add/update/delete contacts\n🔍 Look up contact info\n📅 Manage your calendar\n🗺️ Find places nearby\n\nCalendar commands:\n• 'setup my calendar' - Connect Google Calendar\n• 'create meeting tomorrow 2pm to 3pm' - Create events\n• 'list my events' - Show upcoming events\n\nPlace search:\n• 'Find best pizza in Downtown Dubai'\n• 'What are the top sushi spots near me?'"
            await send_whatsapp_message(from_number, reply)
            return
            
    except Exception as e:
        print(f"ERROR in background processing: {e}")
        import traceback
        traceback.print_exc()
        await send_whatsapp_message(from_number, "Sorry, something went wrong. Please try again.")

# Google Calendar API Routes

@app.get("/calendar/auth")
async def calendar_auth(whatsapp_number: str):
    """Initiate Google Calendar OAuth2 flow"""
    try:
        if not Path(CREDENTIALS_FILE).exists():
            raise HTTPException(status_code=500, detail="OAuth2 credentials file not found")
        
        flow = Flow.from_client_secrets_file(
            CREDENTIALS_FILE,
            scopes=SCOPES,
            redirect_uri=REDIRECT_URI
        )
        
        # Store the whatsapp_number in the state parameter
        auth_url, _ = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            state=whatsapp_number
        )
        
        return {"auth_url": auth_url, "message": "Please visit this URL to authorize calendar access"}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create auth URL: {str(e)}")

@app.get("/oauth2callback")
async def oauth2_callback(code: str, state: str):
    """Handle OAuth2 callback and store tokens"""
    try:
        whatsapp_number = state  # Extract whatsapp_number from state
        
        if not Path(CREDENTIALS_FILE).exists():
            raise HTTPException(status_code=500, detail="OAuth2 credentials file not found")
        
        flow = Flow.from_client_secrets_file(
            CREDENTIALS_FILE,
            scopes=SCOPES,
            redirect_uri=REDIRECT_URI
        )
        
        # Exchange authorization code for tokens
        flow.fetch_token(code=code)
        credentials = flow.credentials
        
        # TODO: Store tokens when database is implemented
        # store_user_tokens(whatsapp_number, credentials)
        
        return {"message": "Calendar access authorized successfully! You can now use calendar features."}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"OAuth2 callback failed: {str(e)}")

@app.post("/calendar/create")
async def create_calendar_event(
    whatsapp_number: str,
    summary: str,
    start: str,
    end: str,
    description: Optional[str] = None,
    all_day: bool = False
):
    """Create a new calendar event"""
    try:
        service = get_calendar_service(whatsapp_number)
        
        event_body = {
            'summary': summary,
            'start': format_datetime_for_google(start, all_day),
            'end': format_datetime_for_google(end, all_day),
        }
        
        if description:
            event_body['description'] = description
        
        # Create event in primary calendar
        event = service.events().insert(calendarId='primary', body=event_body).execute()
        
        event_link = event.get('htmlLink', 'No link available')
        event_id = event.get('id')
        
        return {
            "event_id": event_id,
            "event_link": event_link,
            "message": f"Event '{summary}' created successfully!"
        }
        
    except HttpError as e:
        if e.resp.status == 403:
            raise HTTPException(status_code=403, detail="Insufficient permissions. Please re-authenticate.")
        raise HTTPException(status_code=400, detail=f"Google Calendar API error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create event: {str(e)}")

@app.post("/calendar/list")
async def list_calendar_events(
    whatsapp_number: str,
    time_min: Optional[str] = None,
    time_max: Optional[str] = None,
    max_results: int = 10
):
    """List calendar events"""
    try:
        service = get_calendar_service(whatsapp_number)
        
        # Default to current time if time_min not provided
        if not time_min:
            time_min = datetime.now(DUBAI_TZ).isoformat()
        
        # Default to 1 week from now if time_max not provided
        if not time_max:
            time_max = (datetime.now(DUBAI_TZ) + timedelta(days=7)).isoformat()
        
        events_result = service.events().list(
            calendarId='primary',
            timeMin=time_min,
            timeMax=time_max,
            maxResults=max_results,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        
        if not events:
            return {"message": "No upcoming events found."}
        
        event_list = []
        for event in events:
            start_time = event['start'].get('dateTime', event['start'].get('date'))
            summary = event.get('summary', 'No title')
            event_id = event.get('id')
            
            # Format start time for display
            try:
                if 'T' in start_time:  # DateTime
                    dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                    formatted_time = dt.strftime('%Y-%m-%d %H:%M')
                else:  # Date only
                    formatted_time = start_time
            except:
                formatted_time = start_time
            
            event_list.append(f"{formatted_time} - {summary} (ID: {event_id})")
        
        events_text = "\n".join(event_list)
        return {"events": events_text, "count": len(events)}
        
    except HttpError as e:
        if e.resp.status == 403:
            raise HTTPException(status_code=403, detail="Insufficient permissions. Please re-authenticate.")
        raise HTTPException(status_code=400, detail=f"Google Calendar API error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list events: {str(e)}")

@app.post("/calendar/update")
async def update_calendar_event(
    whatsapp_number: str,
    event_id: str,
    field: str,  # summary, description, start, end
    value: str
):
    """Update a calendar event"""
    try:
        service = get_calendar_service(whatsapp_number)
        
        # Get the existing event
        event = service.events().get(calendarId='primary', eventId=event_id).execute()
        
        # Update the specified field
        if field == 'summary':
            event['summary'] = value
        elif field == 'description':
            event['description'] = value
        elif field == 'start':
            event['start'] = format_datetime_for_google(value)
        elif field == 'end':
            event['end'] = format_datetime_for_google(value)
        else:
            raise HTTPException(status_code=400, detail="Invalid field. Use: summary, description, start, or end")
        
        # Update the event
        updated_event = service.events().update(
            calendarId='primary',
            eventId=event_id,
            body=event
        ).execute()
        
        event_link = updated_event.get('htmlLink', 'No link available')
        
        return {
            "event_link": event_link,
            "message": f"Event updated successfully! Field '{field}' changed to '{value}'"
        }
        
    except HttpError as e:
        if e.resp.status == 404:
            raise HTTPException(status_code=404, detail="Event not found")
        elif e.resp.status == 403:
            raise HTTPException(status_code=403, detail="Insufficient permissions. Please re-authenticate.")
        raise HTTPException(status_code=400, detail=f"Google Calendar API error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update event: {str(e)}")

@app.post("/calendar/delete")
async def delete_calendar_event(
    whatsapp_number: str,
    event_id: str
):
    """Delete a calendar event"""
    try:
        service = get_calendar_service(whatsapp_number)
        
        # Delete the event
        service.events().delete(calendarId='primary', eventId=event_id).execute()
        
        return {"message": f"Event with ID '{event_id}' deleted successfully!"}
        
    except HttpError as e:
        if e.resp.status == 404:
            raise HTTPException(status_code=404, detail="Event not found")
        elif e.resp.status == 403:
            raise HTTPException(status_code=403, detail="Insufficient permissions. Please re-authenticate.")
        raise HTTPException(status_code=400, detail=f"Google Calendar API error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete event: {str(e)}")

# Memory Analysis Endpoints

@app.get("/memory/analysis/{whatsapp_number}")
async def get_memory_analysis(whatsapp_number: str):
    """Get memory analysis for a user"""
    if not memory_manager:
        raise HTTPException(status_code=503, detail="Memory manager not available")
    
    try:
        user_id = await memory_manager.get_user_id(whatsapp_number)
        analysis = await memory_manager.analyze_conversation_patterns(user_id)
        return {"user_id": user_id, "analysis": analysis}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to analyze memory: {str(e)}")

@app.get("/memory/preferences/{whatsapp_number}")
async def get_user_preferences(whatsapp_number: str):
    """Get user preferences"""
    if not memory_manager:
        raise HTTPException(status_code=503, detail="Memory manager not available")
    
    try:
        user_id = await memory_manager.get_user_id(whatsapp_number)
        preferences = await memory_manager.supabase_memory.get_user_preferences(user_id)
        return {"user_id": user_id, "preferences": preferences}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get preferences: {str(e)}")

@app.post("/memory/preferences/{whatsapp_number}")
async def update_user_preferences(whatsapp_number: str, preferences: dict):
    """Update user preferences"""
    if not memory_manager:
        raise HTTPException(status_code=503, detail="Memory manager not available")
    
    try:
        user_id = await memory_manager.get_user_id(whatsapp_number)
        success = await memory_manager.supabase_memory.update_user_preferences(user_id, preferences)
        return {"user_id": user_id, "success": success, "updated_preferences": preferences}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update preferences: {str(e)}")

@app.get("/memory/tasks/{whatsapp_number}")
async def get_user_tasks(whatsapp_number: str, status: Optional[str] = None):
    """Get user tasks"""
    if not memory_manager:
        raise HTTPException(status_code=503, detail="Memory manager not available")
    
    try:
        user_id = await memory_manager.get_user_id(whatsapp_number)
        tasks = await memory_manager.supabase_memory.get_user_tasks(user_id, status)
        return {"user_id": user_id, "tasks": tasks}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get tasks: {str(e)}")

@app.get("/memory/conversations/{whatsapp_number}")
async def get_recent_conversations(whatsapp_number: str, limit: int = 10):
    """Get recent conversations"""
    if not memory_manager:
        raise HTTPException(status_code=503, detail="Memory manager not available")
    
    try:
        user_id = await memory_manager.get_user_id(whatsapp_number)
        conversations = await memory_manager.supabase_memory.get_recent_conversations(user_id, limit)
        return {"user_id": user_id, "conversations": conversations}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get conversations: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 5001))
    uvicorn.run(app, host="0.0.0.0", port=port)
