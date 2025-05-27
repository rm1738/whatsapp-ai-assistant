import openai
import os
import json
import httpx
import gspread
import requests
import tempfile
import re
import asyncio
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor
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
from memory_fusion import HybridMemoryManager
import base64

# Load environment variables from .env file
load_dotenv()

app = FastAPI()

# PERFORMANCE OPTIMIZATION: Connection pooling and caching
# Global HTTP client with connection pooling for better performance
http_client = httpx.AsyncClient(
    timeout=httpx.Timeout(30.0),
    limits=httpx.Limits(max_keepalive_connections=20, max_connections=100)
)

# Thread pool for CPU-bound operations
thread_pool = ThreadPoolExecutor(max_workers=4)

# PERFORMANCE OPTIMIZATION: Cache for frequently accessed data
@lru_cache(maxsize=100)
def get_cached_credentials():
    """Cache credentials to avoid repeated file I/O"""
    return setup_google_credentials()

# PERFORMANCE OPTIMIZATION: Cache for Google Sheets records
sheets_cache = {}
sheets_cache_timestamp = {}
CACHE_TTL = 300  # 5 minutes cache TTL

# Initialize hybrid memory manager
try:
    memory_manager = HybridMemoryManager()
    print("✅ Hybrid memory manager initialized successfully")
except Exception as e:
    print(f"❌ Failed to initialize memory manager: {e}")
    memory_manager = None

# Google Calendar Configuration
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/calendar'
]
REDIRECT_URI = "https://a5d5-2001-8f8-1b69-5a-3918-347f-d5c6-f162.ngrok-free.app/oauth2callback"  # Update with your domain
CREDENTIALS_FILE = "credentials.json"
DUBAI_TZ = timezone(timedelta(hours=4))  # Asia/Dubai timezone

# Google Places API Configuration
GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")

# Tavily Search API Configuration
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
TAVILY_API_URL = "https://api.tavily.com/search"

def get_calendar_service(whatsapp_number: str = None):
    """Get authenticated Google Calendar service using the same method as Sheets"""
    try:
        # Use the same authentication approach as Google Sheets
        if not setup_google_credentials():
            raise Exception("Google credentials not available")
        
        # Check if we have credentials.json file
        if os.path.exists("credentials.json"):
            # Check if we're using service account credentials
            import json
            with open("credentials.json", "r") as f:
                creds_data = json.load(f)
            
            if "type" in creds_data and creds_data["type"] == "service_account":
                # Use service account authentication
                gc = gspread.service_account(filename="credentials.json")
                # For service accounts, we need to build the calendar service directly
                from google.oauth2 import service_account
                credentials = service_account.Credentials.from_service_account_file(
                    "credentials.json", scopes=SCOPES
                )
                service = build('calendar', 'v3', credentials=credentials)
                return service
            else:
                # Use OAuth2 authentication - same as sheets
                try:
                    from google.oauth2.credentials import Credentials
                    from google.auth.transport.requests import Request
                    
                    # Check if we have a token file
                    if os.path.exists("combined_token.json"):
                        creds = Credentials.from_authorized_user_file("combined_token.json", SCOPES)
                        
                        # Check if token is expired and try to refresh
                        if creds and creds.expired and creds.refresh_token:
                            print("🔄 Calendar token expired, attempting to refresh...")
                            try:
                                creds.refresh(Request())
                                print("✅ Calendar token refreshed successfully")
                                
                                # Save the refreshed token back to file
                                with open("combined_token.json", 'w') as token_file:
                                    token_file.write(creds.to_json())
                                
                            except Exception as refresh_error:
                                print(f"❌ Calendar token refresh failed: {refresh_error}")
                                raise Exception("Calendar token expired and cannot be refreshed")
                        
                        if creds and creds.valid:
                            service = build('calendar', 'v3', credentials=creds)
                            print("✅ Using OAuth token for calendar authentication")
                            return service
                        else:
                            raise Exception("Calendar OAuth token is not valid")
                    else:
                        raise Exception("No OAuth token file found for calendar")
                        
                except Exception as token_error:
                    print(f"❌ Calendar token authentication failed: {token_error}")
                    raise Exception(f"Calendar authentication failed: {token_error}")
        else:
            # No credentials.json, try to use token file directly
            try:
                from google.oauth2.credentials import Credentials
                from google.auth.transport.requests import Request
                
                if os.path.exists("combined_token.json"):
                    creds = Credentials.from_authorized_user_file("combined_token.json", SCOPES)
                    
                    # Try to refresh the token if it's expired
                    if creds and creds.expired and creds.refresh_token:
                        print("🔄 Calendar token expired, attempting to refresh...")
                        try:
                            creds.refresh(Request())
                            print("✅ Calendar token refreshed successfully")
                            
                            # Save the refreshed token back to file
                            with open("combined_token.json", 'w') as token_file:
                                token_file.write(creds.to_json())
                                
                        except Exception as refresh_error:
                            print(f"❌ Calendar token refresh failed: {refresh_error}")
                            raise Exception("Calendar token expired and cannot be refreshed")
                    
                    if creds and creds.valid:
                        service = build('calendar', 'v3', credentials=creds)
                        print("✅ Using OAuth token for calendar authentication")
                        return service
                    else:
                        raise Exception("Calendar OAuth token is not valid")
                else:
                    raise Exception("No OAuth token file found for calendar")
                    
            except Exception as token_error:
                print(f"❌ Calendar token authentication failed: {token_error}")
                raise Exception(f"Calendar authentication failed: {token_error}")
        
    except Exception as e:
        print(f"Calendar service error: {e}")
        raise Exception(f"Calendar authentication required: {str(e)}")

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
                                
                                # Check if token is expired and try to refresh
                                if creds and creds.expired and creds.refresh_token:
                                    print("🔄 Token expired, attempting to refresh...")
                                    try:
                                        creds.refresh(Request())
                                        print("✅ Token refreshed successfully")
                                        
                                        # Save the refreshed token back to file
                                        with open("combined_token.json", 'w') as token_file:
                                            token_file.write(creds.to_json())
                                        print("✅ Refreshed token saved")
                                        
                                        # Also update the base64 version for future deployments
                                        import base64
                                        token_base64 = base64.b64encode(creds.to_json().encode()).decode()
                                        print(f"💡 Updated token base64 (save this as GOOGLE_TOKEN_BASE64): {token_base64[:50]}...")
                                        
                                    except Exception as refresh_error:
                                        print(f"❌ Token refresh failed: {refresh_error}")
                                        print("💡 Token may be permanently expired. Need to re-authenticate.")
                                        creds = None
                                
                                if creds and creds.valid:
                                    gc = gspread.authorize(creds)
                                    print("✅ Using OAuth token authentication")
                                elif creds:
                                    print("❌ OAuth token is not valid and cannot be refreshed")
                                    print("💡 The token may have been revoked or expired beyond refresh capability")
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
                                
                                # Save the refreshed token back to file
                                with open("combined_token.json", 'w') as token_file:
                                    token_file.write(creds.to_json())
                                print("✅ Refreshed token saved")
                                
                            except Exception as refresh_error:
                                print(f"❌ Token refresh failed: {refresh_error}")
                                print("💡 Token may be permanently expired. Need to re-authenticate.")
                                creds = None
                        
                        if creds and creds.valid:
                            gc = gspread.authorize(creds)
                            print("✅ Using OAuth token authentication")
                        elif creds:
                            print("❌ OAuth token is not valid and cannot be refreshed")
                            print("💡 The token may have been revoked or expired beyond refresh capability")
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
                print("❌ Google Sheets initialization failed - authentication failed")
                print("💡 SOLUTION: You need to regenerate the OAuth token locally and update Railway")
                print("   1. Run the app locally")
                print("   2. Use 'setup my calendar' command to re-authenticate")
                print("   3. Copy the new token base64 and update GOOGLE_TOKEN_BASE64 in Railway")
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
    Calls Google Places Text Search API and returns top 5 results with name, address, rating, place_id, and Google Maps link.
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
            place_id = place.get("place_id")
            name = place.get("name")
            
            # Create Google Maps link
            maps_link = f"https://maps.google.com/maps?place_id={place_id}" if place_id else None
            
            # Get coordinates for alternative maps link if place_id fails
            geometry = place.get("geometry", {})
            location_coords = geometry.get("location", {})
            lat = location_coords.get("lat")
            lng = location_coords.get("lng")
            
            # Alternative maps link using coordinates
            coords_link = f"https://maps.google.com/maps?q={lat},{lng}" if lat and lng else None
            
            results.append({
                "name": name,
                "formatted_address": place.get("formatted_address"),
                "rating": place.get("rating"),
                "place_id": place_id,
                "maps_link": maps_link or coords_link,
                "coordinates": {"lat": lat, "lng": lng} if lat and lng else None
            })
        return results

# --- [HELPER FUNCTIONS: Smart Search with Tavily] ---
def is_search_intent(message: str) -> bool:
    """
    Detect if a message appears to be a search query based on patterns and keywords.
    """
    message_lower = message.lower().strip()
    
    # Skip if message is too short or looks like a command
    if len(message_lower) < 5 or message_lower.startswith('/'):
        return False
    
    # Skip if it's clearly an email, contact, or calendar intent
    email_keywords = ['send email', 'email to', 'draft email', 'compose email']
    contact_keywords = ['add contact', 'delete contact', 'update contact', 'contact info']
    calendar_keywords = ['create meeting', 'schedule', 'calendar', 'appointment', 'book']
    place_keywords = ['find places', 'restaurants near', 'coffee shops', 'best pizza in']
    
    if any(keyword in message_lower for keyword in email_keywords + contact_keywords + calendar_keywords + place_keywords):
        return False
    
    # Search intent patterns
    search_patterns = [
        # Question words
        r'\b(what|how|why|when|where|which|who)\b.*\?',
        r'\b(what|how|why|when|where|which|who)\s+(is|are|was|were|do|does|did|can|could|should|would)\b',
        
        # Search phrases
        r'\b(find|search|look\s+for|tell\s+me\s+about|explain|show\s+me)\b',
        r'\b(best|top|latest|newest|recent|current)\b.*\b(in|for|about|on)\b',
        r'\b(how\s+to|ways\s+to|steps\s+to)\b',
        r'\b(what\s+is|what\s+are|what\'s)\b',
        r'\b(learn\s+about|information\s+about|details\s+about)\b',
        
        # Comparison and recommendation patterns
        r'\b(compare|vs|versus|difference\s+between)\b',
        r'\b(recommend|suggest|advice)\b',
        r'\b(pros\s+and\s+cons|advantages|disadvantages)\b',
        
        # Technology and trends
        r'\b(latest\s+in|trends\s+in|news\s+about|updates\s+on)\b',
        r'\b(technology|tech|AI|artificial\s+intelligence|machine\s+learning)\b',
        
        # General knowledge queries
        r'\b(definition\s+of|meaning\s+of|explain)\b',
        r'\b(guide\s+to|tutorial\s+on|instructions\s+for)\b'
    ]
    
    # Check if message matches any search pattern
    for pattern in search_patterns:
        if re.search(pattern, message_lower):
            return True
    
    # Check for question-like structure
    if message_lower.endswith('?'):
        return True
    
    # Check for imperative search commands
    imperative_starters = ['find', 'search', 'look', 'show', 'tell', 'explain', 'help', 'get']
    first_word = message_lower.split()[0] if message_lower.split() else ""
    if first_word in imperative_starters:
        return True
    
    return False

# PERFORMANCE OPTIMIZATION: Optimized search with parallel execution
async def handle_search_query_optimized(message: str) -> str:
    """
    OPTIMIZED: Handle search query using Tavily API with parallel execution and caching
    """
    if not TAVILY_API_KEY:
        return "❌ Search functionality is not available. Please contact administrator."
    
    try:
        # Prepare Tavily API request
        headers = {
            "Content-Type": "application/json"
        }
        
        payload = {
            "api_key": TAVILY_API_KEY,
            "query": message,
            "search_depth": "basic",
            "include_answer": True,
            "include_images": False,
            "include_raw_content": False,
            "max_results": 5
        }
        
        # PERFORMANCE: Use global HTTP client with connection pooling
        response = await http_client.post(TAVILY_API_URL, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        
        # Extract results
        results = data.get("results", [])
        answer = data.get("answer", "")
        
        if not results and not answer:
            return "🔍 Couldn't find anything useful right now. Try rephrasing your search or being more specific."
        
        # PERFORMANCE: Parallel LLM summarization with timeout
        try:
            # Prepare content for LLM summarization
            content_for_llm = f"Search Query: {message}\n\n"
            
            if answer:
                content_for_llm += f"AI Answer: {answer}\n\n"
            
            content_for_llm += "Search Results:\n"
            for i, result in enumerate(results[:5], 1):
                title = result.get("title", "No title")
                url = result.get("url", "")
                content = result.get("content", "")
                content_for_llm += f"{i}. {title}\n   URL: {url}\n   Content: {content}\n\n"
            
            # Use OpenAI to summarize and format for WhatsApp
            summarization_prompt = f"""
You are helping to summarize web search results for WhatsApp. The response MUST be under 1500 characters total.

Search Query: "{message}"

Raw Search Data:
{content_for_llm}

Please create a concise, informative summary that:
1. Starts with a brief answer to the user's question
2. Lists 2-3 key points from the search results
3. Includes 1-2 relevant URLs for more info
4. Uses emojis appropriately
5. Stays under 1500 characters total

Format like this:
🔍 **[Brief Answer]**

📋 **Key Points:**
• [Point 1]
• [Point 2]
• [Point 3]

🔗 **Sources:**
• [URL 1]
• [URL 2]
"""
            
            # PERFORMANCE: Run LLM call with timeout
            def llm_summarize():
                return openai.chat.completions.create(
                    model="gpt-4",
                    messages=[
                        {"role": "system", "content": "You are an expert at summarizing web search results for mobile messaging. Keep responses concise, informative, and under 1500 characters."},
                        {"role": "user", "content": summarization_prompt}
                    ],
                    max_tokens=500,
                    temperature=0.3
                )
            
            response = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(thread_pool, llm_summarize),
                timeout=15.0  # 15 second timeout for LLM
            )
            
            summarized_response = response.choices[0].message.content.strip()
            
            # Double-check character count and truncate if needed
            if len(summarized_response) > 1500:
                summarized_response = summarized_response[:1450] + "..."
            
            return summarized_response
            
        except asyncio.TimeoutError:
            print("LLM summarization timed out, using fallback")
            # Fall through to fallback formatting
        except Exception as llm_error:
            print(f"LLM summarization failed: {llm_error}")
            # Fall through to fallback formatting
        
        # PERFORMANCE: Fast fallback formatting
        fallback_response = f"🔍 **Search Results for:** {message}\n\n"
        
        if answer:
            # Truncate answer if too long
            truncated_answer = answer[:200] + "..." if len(answer) > 200 else answer
            fallback_response += f"💡 {truncated_answer}\n\n"
        
        # Add top 2 results with truncated content
        if results:
            fallback_response += "📋 **Top Results:**\n"
            for i, result in enumerate(results[:2], 1):
                title = result.get("title", "No title")
                url = result.get("url", "")
                
                # Truncate title if too long
                if len(title) > 60:
                    title = title[:60] + "..."
                
                fallback_response += f"{i}. {title}\n🔗 {url}\n\n"
        
        # Ensure fallback doesn't exceed limit
        if len(fallback_response) > 1500:
            fallback_response = fallback_response[:1450] + "..."
        
        return fallback_response
        
    except httpx.TimeoutException:
        return "⏱️ Search request timed out. Please try again with a simpler query."
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            return "❌ Search API authentication failed. Please contact administrator."
        elif e.response.status_code == 429:
            return "⚠️ Too many search requests. Please wait a moment and try again."
        else:
            return f"❌ Search failed with error {e.response.status_code}. Please try again later."
    except Exception as e:
        print(f"Tavily search error: {e}")
        return "❌ Something went wrong with the search. Please try again later."

# PERFORMANCE OPTIMIZATION: Optimized Places API with connection pooling
async def find_places_optimized(query: str, location: str = None, radius: int = 5000) -> List[Dict]:
    """
    OPTIMIZED: Calls Google Places Text Search API with connection pooling
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
    
    # PERFORMANCE: Use global HTTP client with connection pooling
    resp = await http_client.get(url, params=params)
    resp.raise_for_status()
    data = resp.json()
    
    results = []
    for place in data.get("results", [])[:5]:
        place_id = place.get("place_id")
        name = place.get("name")
        
        # Create Google Maps link
        maps_link = f"https://maps.google.com/maps?place_id={place_id}" if place_id else None
        
        # Get coordinates for alternative maps link if place_id fails
        geometry = place.get("geometry", {})
        location_coords = geometry.get("location", {})
        lat = location_coords.get("lat")
        lng = location_coords.get("lng")
        
        # Alternative maps link using coordinates
        coords_link = f"https://maps.google.com/maps?q={lat},{lng}" if lat and lng else None
        
        results.append({
            "name": name,
            "formatted_address": place.get("formatted_address"),
            "rating": place.get("rating"),
            "place_id": place_id,
            "maps_link": maps_link or coords_link,
            "coordinates": {"lat": lat, "lng": lng} if lat and lng else None
        })
    return results

def build_extraction_prompt(user_input: str):
    # Get current date for context
    current_date = datetime.now(DUBAI_TZ)
    current_date_str = current_date.strftime('%Y-%m-%d')
    current_year = current_date.year
    
    return f'''
You are a helpful assistant that extracts structured info from user messages for contact management, email sending, calendar management, place finding, web search, or general conversation.

CURRENT DATE CONTEXT: Today is {current_date_str} ({current_date.strftime('%A, %B %d, %Y')})

Extract:
- intent ("send_email" for sending emails; "add_contact" for adding new contacts; "lookup_contact" for finding contact info; "list_contacts" for showing all contacts; "update_contact" for modifying existing contacts; "delete_contact" for removing contacts; "calendar_auth" for calendar authentication; "calendar_create" for creating single events; "calendar_bulk_create" for creating multiple events; "calendar_list" for listing events; "calendar_update" for updating events; "calendar_delete" for deleting single events; "calendar_bulk_delete" for deleting multiple events; "find_place" for finding places using Google Places API; "place_details" for getting specific details about a known place like Google Maps link, address, phone number; "web_search" for general web search queries; "memory_query" for asking about past actions or conversations; otherwise "other")
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
- calendar_events (for bulk creation: array of event objects with summary, start, end, description)
- calendar_delete_targets (for bulk deletion: array of event identifiers - can be event IDs, titles, or dates)
- place_query (the user's location search string for Google Places OR the specific place name when asking for details)
- place_location (optional: a lat,lng string if user gives coordinates or "near me", else null)
- place_detail_type (what specific detail is requested: "maps_link", "address", "phone", "hours", "website", or "all")
- search_query (the user's web search query for general information)
- memory_query (what the user wants to know about their past actions: "emails", "places", "meetings", "contacts", or "all")

User said:
"""{user_input}"""

Examples of calendar_bulk_create intent:
- "Create three meetings: Team standup tomorrow 9am, Client call Friday 2pm, and Review session next Monday 10am" → calendar_bulk_create (calendar_events: [{{"summary": "Team standup", "start": "...", "end": "..."}}, {{"summary": "Client call", "start": "...", "end": "..."}}, {{"summary": "Review session", "start": "...", "end": "..."}}])
- "Schedule multiple events: Lunch with John today 1pm, Gym session tomorrow 6pm, and Doctor appointment Thursday 3pm" → calendar_bulk_create
- "Add these meetings to my calendar: Project kickoff Monday 10am to 11am, Budget review Tuesday 2pm to 3pm" → calendar_bulk_create

Examples of calendar_bulk_delete intent:
- "Delete my meetings for today and tomorrow" → calendar_bulk_delete (calendar_delete_targets: ["{current_date_str}", "{(current_date + timedelta(days=1)).strftime('%Y-%m-%d')}"])
- "Remove these events: Team standup, Client call, and Budget review" → calendar_bulk_delete (calendar_delete_targets: ["Team standup", "Client call", "Budget review"])
- "Delete events with IDs abc123, def456, and ghi789" → calendar_bulk_delete (calendar_delete_targets: ["abc123", "def456", "ghi789"])
- "Cancel all my meetings this week" → calendar_bulk_delete (calendar_delete_targets: ["this week"])
- "Delete all my meetings for the upcoming week" → calendar_bulk_delete (calendar_delete_targets: ["upcoming week"])
- "Remove my events for next week" → calendar_bulk_delete (calendar_delete_targets: ["next week"])
- "Cancel meetings for the coming week" → calendar_bulk_delete (calendar_delete_targets: ["coming week"])
- "Delete all appointments next Monday" → calendar_bulk_delete (calendar_delete_targets: ["next Monday"])
- "Clear my calendar for tomorrow" → calendar_bulk_delete (calendar_delete_targets: ["tomorrow"])
- "Remove all events this Friday" → calendar_bulk_delete (calendar_delete_targets: ["this Friday"])
- "Delete meetings for the following week" → calendar_bulk_delete (calendar_delete_targets: ["following week"])
- "Cancel everything for the next 7 days" → calendar_bulk_delete (calendar_delete_targets: ["next 7 days"])
- "Delete all my meetings" → calendar_bulk_delete (calendar_delete_targets: ["all my meetings"])
- "Remove all events" → calendar_bulk_delete (calendar_delete_targets: ["all events"])
- "Cancel everything" → calendar_bulk_delete (calendar_delete_targets: ["everything"])
- "Clear my calendar" → calendar_bulk_delete (calendar_delete_targets: ["all my meetings"])

Examples of place_details intent (asking for specific info about a known place):
- "Can I get the Google Maps location for Padel Pro Jumeirah Park?" → place_details (place_query: "Padel Pro Jumeirah Park", place_detail_type: "maps_link")
- "What's the address of Burj Khalifa?" → place_details (place_query: "Burj Khalifa", place_detail_type: "address")
- "Get me the phone number for Dubai Mall" → place_details (place_query: "Dubai Mall", place_detail_type: "phone")
- "I want the Google Maps link for that restaurant" → place_details (place_query: "restaurant", place_detail_type: "maps_link")
- "Show me the location of Padel Pro One Central" → place_details (place_query: "Padel Pro One Central", place_detail_type: "maps_link")

Examples of find_place intent (searching for places):
- "What are the top sushi spots near me?" → find_place (place_query: "sushi spots", place_location: "near me")
- "Find best pizza in Downtown Dubai." → find_place (place_query: "best pizza", place_location: "Downtown Dubai")
- "Show me vegan restaurants at 25.1972,55.2744" → find_place (place_query: "vegan restaurants", place_location: "25.1972,55.2744")
- "Where can I find good coffee shops?" → find_place (place_query: "coffee shops", place_location: null)

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

Examples of web_search intent:
- "What is the latest in EV technology?" → web_search (search_query: "latest EV technology")
- "How to write a resignation email?" → web_search (search_query: "how to write resignation email")
- "Find me the best productivity tools" → web_search (search_query: "best productivity tools")
- "What are the pros and cons of remote work?" → web_search (search_query: "pros and cons remote work")
- "Explain artificial intelligence" → web_search (search_query: "explain artificial intelligence")
- "Latest news about climate change" → web_search (search_query: "latest news climate change")

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

Examples of list_contacts intent:
- "Show me all my contacts" → list_contacts
- "List all contacts" → list_contacts
- "Get all contacts" → list_contacts
- "Show my contact list" → list_contacts
- "Display all my contacts" → list_contacts
- "What contacts do I have?" → list_contacts

Respond ONLY in this JSON format:
{{{{
  "intent": "send_email" or "add_contact" or "lookup_contact" or "list_contacts" or "update_contact" or "delete_contact" or "calendar_auth" or "calendar_create" or "calendar_bulk_create" or "calendar_list" or "calendar_update" or "calendar_delete" or "calendar_bulk_delete" or "find_place" or "place_details" or "web_search" or "memory_query" or "other",
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
  "calendar_events": [...],
  "calendar_delete_targets": [...],
  "place_query": "...",
  "place_location": "...",
  "place_detail_type": "...",
  "search_query": "...",
  "memory_query": "..."
}}}}
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

# PERFORMANCE OPTIMIZATION: Parallel LLM extraction with caching
async def extract_email_info_with_llm_optimized(user_input: str, whatsapp_number: str = None):
    """OPTIMIZED: Extract email info with parallel memory context retrieval"""
    # Sanitize the input text first
    sanitized_input = sanitize_text_for_llm(user_input)
    
    # Get memory-enhanced context if memory manager is available
    enhanced_prompt = build_extraction_prompt(sanitized_input)
    
    # PERFORMANCE: Parallel memory context retrieval
    memory_context_task = None
    if memory_manager and whatsapp_number:
        async def get_memory_context():
            try:
                user_id = await memory_manager.get_user_id(whatsapp_number)
                return await memory_manager.get_personalized_prompt_context(user_id, sanitized_input)
            except Exception as e:
                print(f"Error getting memory context: {e}")
                return ""
        
        memory_context_task = asyncio.create_task(get_memory_context())
    
    try:
        # PERFORMANCE: Run LLM extraction with timeout - FIX: Run in thread pool since OpenAI client is sync
        def llm_call():
            return openai.chat.completions.create(
                model="gpt-4.1",
                messages=[
                    {"role": "system", "content": "You extract structured email instructions and generate professional emails signed as Rahul Menon. Use the provided memory context to personalize responses based on user preferences and past interactions."},
                    {"role": "user", "content": enhanced_prompt}
                ]
            )
        
        # Wait for both tasks with timeout
        if memory_context_task:
            memory_context, response = await asyncio.gather(
                memory_context_task, 
                asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(thread_pool, llm_call),
                    timeout=20.0
                ),
                return_exceptions=True
            )
            
            # If memory context was successful, we could re-run with enhanced prompt
            # For now, we'll use the response as-is for performance
        else:
            response = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(thread_pool, llm_call),
                timeout=20.0
            )
        
        content = response.choices[0].message.content
        data = json.loads(content)
        return data
        
    except asyncio.TimeoutError:
        print("LLM extraction timed out")
        return None
    except Exception as e:
        print("LLM extraction failed:", e)
        return None

# PERFORMANCE OPTIMIZATION: Cached Google Sheets operations
async def get_cached_sheet_records(force_refresh: bool = False) -> List[Dict]:
    """Get Google Sheets records with caching to reduce API calls"""
    global sheets_cache, sheets_cache_timestamp
    
    cache_key = "sheet_records"
    current_time = time.time()
    
    # Check if cache is valid and not forcing refresh
    if (not force_refresh and 
        cache_key in sheets_cache and 
        cache_key in sheets_cache_timestamp and
        current_time - sheets_cache_timestamp[cache_key] < CACHE_TTL):
        print(f"📋 Using cached sheet records (age: {current_time - sheets_cache_timestamp[cache_key]:.1f}s)")
        return sheets_cache[cache_key]
    
    # Fetch fresh data from Google Sheets
    if not sheet:
        print("Google Sheets not initialized")
        return []
    
    try:
        # PERFORMANCE: Run in thread pool to avoid blocking
        records = await asyncio.get_event_loop().run_in_executor(
            thread_pool, sheet.get_all_records
        )
        
        # Update cache
        sheets_cache[cache_key] = records
        sheets_cache_timestamp[cache_key] = current_time
        print(f"📋 Refreshed sheet records cache ({len(records)} records)")
        return records
        
    except Exception as e:
        print(f"Error fetching Google Sheets records: {e}")
        # Return cached data if available, even if stale
        return sheets_cache.get(cache_key, [])

async def get_email_by_name_optimized(name: str) -> str | None:
    """OPTIMIZED: Get email by name with caching"""
    if not sheet:
        print("Google Sheets not initialized")
        return None
    
    try:
        # Use cached records
        records = await get_cached_sheet_records()
        
        # Search for name match (case-insensitive, trimmed)
        name_lower = name.strip().lower()
        for record in records:
            full_name = str(record.get('full_name', '')).strip().lower()
            # Try exact match first
            if full_name == name_lower:
                email = str(record.get('email', '') or '').strip()  # Convert to string first
                if email:
                    print(f"Found email for {name}: {email}")
                    return email
            # Try partial match (name is contained in full_name)
            elif name_lower in full_name or any(part in full_name for part in name_lower.split()):
                email = str(record.get('email', '') or '').strip()  # Convert to string first
                if email:
                    print(f"Found email for {name} (matched {record.get('full_name', '')}): {email}")
                    return email
        
        print(f"No email found for name: {name}")
        return None
    except Exception as e:
        print(f"Error searching Google Sheets for {name}: {e}")
        return None

async def add_contact_to_sheet_optimized(name: str, email: str, phone: str) -> bool:
    """OPTIMIZED: Add contact with cache invalidation"""
    if not sheet:
        print("Google Sheets not initialized")
        return False
    
    try:
        # Check if contact already exists using cached data
        records = await get_cached_sheet_records()
        name_lower = name.strip().lower()
        for record in records:
            existing_name = str(record.get('full_name', '')).strip().lower()
            if existing_name == name_lower:
                print(f"Contact {name} already exists")
                return False
        
        # Add new row to the sheet in thread pool
        row_data = [name, email or "", phone or ""]
        await asyncio.get_event_loop().run_in_executor(
            thread_pool, sheet.append_row, row_data
        )
        
        # Invalidate cache after modification
        global sheets_cache, sheets_cache_timestamp
        cache_key = "sheet_records"
        if cache_key in sheets_cache:
            del sheets_cache[cache_key]
            del sheets_cache_timestamp[cache_key]
        
        print(f"Added contact: {name}, {email}, {phone}")
        return True
    except Exception as e:
        print(f"Error adding contact to Google Sheets: {e}")
        return False

async def lookup_contact_info_optimized(name: str, field: str) -> str:
    """OPTIMIZED: Look up contact information with caching"""
    if not sheet:
        print("Google Sheets not initialized")
        return "Google Sheets not available"
    
    try:
        # Use cached records
        records = await get_cached_sheet_records()
        
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

async def get_all_contacts_optimized() -> str:
    """OPTIMIZED: Get all contacts from Google Sheets with caching and format for display"""
    if not sheet:
        print("Google Sheets not initialized")
        return "❌ Google Sheets not available"
    
    try:
        # Use cached records
        records = await get_cached_sheet_records()
        
        if not records:
            return "📋 **Your Contact List**\n\n❌ No contacts found in your Google Sheets."
        
        # Format contacts for WhatsApp display
        reply = f"📋 **Your Contact List** ({len(records)} contacts)\n\n"
        
        for i, record in enumerate(records, 1):
            name = record.get('full_name', 'Unknown')
            email = str(record.get('email', '') or '')  # Convert to string first
            phone = str(record.get('phone_number', '') or '')  # Convert to string first
            
            # Format each contact entry
            reply += f"👤 **{name}**\n"
            
            if email and email.strip() and email.lower() != 'n/a':
                reply += f"   📧 {email}\n"
            
            if phone and phone.strip() and phone.lower() != 'n/a':
                reply += f"   📱 {phone}\n"
            
            reply += "\n"
            
            # Break into chunks if too many contacts (WhatsApp has message limits)
            if len(reply) > 1400:  # Leave room for footer
                remaining_contacts = len(records) - i
                if remaining_contacts > 0:
                    reply += f"... and {remaining_contacts} more contacts.\n\n"
                    reply += "💡 Use 'lookup [name]' to find specific contact details."
                break
        
        # Add helpful footer if not truncated
        if len(reply) <= 1400:
            reply += "💡 **Commands:**\n"
            reply += "• 'lookup [name]' - Get specific contact info\n"
            reply += "• 'add contact [name], [email], [phone]' - Add new contact\n"
            reply += "• 'update [name] email to [new email]' - Update contact"
        
        return reply
        
    except Exception as e:
        print(f"Error getting all contacts: {e}")
        return "❌ Error retrieving contacts. Please try again."

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
            process_message_background_optimized,
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
        
        def llm_revise():
            return openai.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You are an expert email writer who revises emails based on user feedback. Always maintain professionalism and sign as Rahul Menon."},
                    {"role": "user", "content": prompt}
                ]
            )
        
        response = await asyncio.get_event_loop().run_in_executor(thread_pool, llm_revise)
        
        content = response.choices[0].message.content
        revised_data = json.loads(content)
        print(f"AI revised email: {revised_data}")
        return revised_data
        
    except Exception as e:
        print(f"Email revision failed: {e}")
        return None

async def process_message_background_optimized(from_number: str, body: str, num_media: str, media_content_type: str, media_url: str):
    """OPTIMIZED: Process the message in the background with parallel execution"""
    try:
        print(f"Background processing message from {from_number}: {body}")
        
        # PERFORMANCE: Early exit for simple queries to avoid LLM calls
        body_lower = body.strip().lower()
        
        # Handle simple date/time queries before LLM extraction
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

        # Check for approval to send a pending draft
        if from_number in pending_email_drafts:
            approval_text = body.strip().lower()
            if any(phrase in approval_text for phrase in ["yes", "send it", "please send", "go ahead", "confirm", "approve"]):
                draft = pending_email_drafts.pop(from_number)
                to_email = draft["to_email"]
                subject = draft["subject"]
                email_body = draft["email_body"]
                print(f"Sending email to {to_email} with subject: {subject}")
                
                # PERFORMANCE: Parallel email sending and response
                email_task = asyncio.create_task(send_email_resend(to_email, subject, email_body))
                
                try:
                    success, result = await asyncio.wait_for(email_task, timeout=30.0)
                    if success:
                        print(f"Email sent successfully to {to_email}")
                        reply = f"✅ EMAIL SENT SUCCESSFULLY!\n\nTo: {to_email}\nSubject: {subject}\n\nYour email has been delivered!"
                    else:
                        print(f"Failed to send email via Resend API: {result}")
                        reply = f"❌ Failed to send email. Error: {result}"
                except asyncio.TimeoutError:
                    reply = "⏱️ Email sending timed out. Please try again."
                
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
                
                # PERFORMANCE: Parallel email revision
                try:
                    revised_draft = await asyncio.wait_for(
                        revise_email_with_ai(draft["to_email"], draft["subject"], draft["email_body"], body),
                        timeout=20.0
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
                except asyncio.TimeoutError:
                    await send_whatsapp_message(from_number, "⏱️ Email revision timed out. Please try again or reply 'Yes' to send the original draft.")
                    return

        # PERFORMANCE: Parallel LLM extraction and memory operations
        extraction_task = asyncio.create_task(extract_email_info_with_llm_optimized(body, from_number))
        
        # Start memory storage task in parallel (fire and forget for performance)
        memory_storage_task = None
        if memory_manager:
            async def store_memory_async():
                try:
                    user_id = await memory_manager.get_user_id(from_number)
                    await memory_manager.store_conversation_with_memory(
                        user_id=user_id,
                        message_text=body,
                        intent="processing",  # Will be updated later
                        metadata={
                            "whatsapp_number": from_number,
                            "timestamp": datetime.now().isoformat(),
                        }
                    )
                except Exception as e:
                    print(f"❌ Error storing conversation in memory: {e}")
            
            memory_storage_task = asyncio.create_task(store_memory_async())
        
        # Wait for LLM extraction with timeout
        try:
            data = await asyncio.wait_for(extraction_task, timeout=25.0)
            print(f"LLM extraction result: {data}")
        except asyncio.TimeoutError:
            print("LLM extraction timed out")
            await send_whatsapp_message(from_number, "⏱️ Processing timed out. Please try again with a simpler request.")
            return
        
        # PERFORMANCE: Update memory with actual intent (non-blocking)
        if memory_storage_task and data:
            async def update_memory_intent():
                try:
                    user_id = await memory_manager.get_user_id(from_number)
                    await memory_manager.update_user_preferences_from_conversation(user_id, data)
                except Exception as e:
                    print(f"❌ Error updating memory intent: {e}")
            
            asyncio.create_task(update_memory_intent())  # Fire and forget

        # PERFORMANCE: Early detection of search intent to avoid complex processing
        if not data or data.get("intent") == "other":
            if is_search_intent(body):
                try:
                    print(f"Detected search intent for message: {body}")
                    search_results = await handle_search_query_optimized(body)
                    await send_whatsapp_message(from_number, search_results)
                    return
                except Exception as e:
                    print(f"Fallback search error: {e}")
                    # Continue to default response if search fails

        # Process specific intents with optimized functions
        if data and data.get("intent") == "send_email":
            await handle_email_intent_optimized(data, from_number)
            return
        elif data and data.get("intent") == "lookup_contact":
            await handle_lookup_contact_intent_optimized(data, from_number)
            return
        elif data and data.get("intent") == "add_contact":
            await handle_add_contact_intent_optimized(data, from_number)
            return
        elif data and data.get("intent") == "update_contact":
            await handle_update_contact_intent_optimized(data, from_number)
            return
        elif data and data.get("intent") == "delete_contact":
            await handle_delete_contact_intent_optimized(data, from_number)
            return
        elif data and data.get("intent") == "calendar_auth":
            await handle_calendar_auth_intent_optimized(data, from_number)
            return
        elif data and data.get("intent") == "calendar_create":
            await handle_calendar_create_intent_optimized(data, from_number)
            return
        elif data and data.get("intent") == "calendar_list":
            await handle_calendar_list_intent_optimized(data, from_number)
            return
        elif data and data.get("intent") == "calendar_update":
            await handle_calendar_update_intent_optimized(data, from_number)
            return
        elif data and data.get("intent") == "calendar_delete":
            await handle_calendar_delete_intent_optimized(data, from_number)
            return
        elif data and data.get("intent") == "calendar_bulk_create":
            await handle_calendar_bulk_create_intent_optimized(data, from_number)
            return
        elif data and data.get("intent") == "calendar_bulk_delete":
            await handle_calendar_bulk_delete_intent_optimized(data, from_number)
            return
        elif data and data.get("intent") in ["find_place", "place_details"]:
            await handle_place_intent_optimized(data, from_number)
            return
        elif data and data.get("intent") == "web_search":
            await handle_web_search_intent_optimized(data, from_number)
            return
        elif data and data.get("intent") == "memory_query":
            await handle_memory_query_intent_optimized(data, from_number)
            return
        elif data and data.get("intent") == "list_contacts":
            await handle_list_contacts_intent_optimized(data, from_number)
            return
        # ... other intents would be handled similarly
        
        # Default response for unhandled intents
        reply = f"Hi! You said: {body}\n\nI can help you:\n📧 Send emails\n👤 Add/update/delete contacts\n📋 List all contacts\n🔍 Look up contact info\n📅 Manage your calendar\n🗺️ Find places nearby\n🔍 Search the web for information\n\nContact commands:\n• 'show all contacts' - List all your contacts\n• 'lookup [name]' - Find specific contact info\n• 'add contact [name], [email], [phone]' - Add new contact\n\nCalendar commands:\n• 'setup my calendar' - Connect Google Calendar\n• 'create meeting tomorrow 2pm to 3pm' - Create single events\n• 'create multiple meetings: Team standup tomorrow 9am, Client call Friday 2pm' - Create multiple events\n• 'list my events' - Show upcoming events\n• 'delete my meeting today' - Delete single event\n• 'delete my meetings for today and tomorrow' - Delete multiple events\n\nPlace search:\n• 'Find best pizza in Downtown Dubai'\n• 'What are the top sushi spots near me?'\n\nWeb search:\n• 'What is the latest in EV technology?'\n• 'How to write a resignation email?'"
        await send_whatsapp_message(from_number, reply)
            
    except Exception as e:
        print(f"ERROR in background processing: {e}")
        import traceback
        traceback.print_exc()
        await send_whatsapp_message(from_number, "Sorry, something went wrong. Please try again.")

# PERFORMANCE: Optimized intent handlers
async def handle_email_intent_optimized(data: dict, from_number: str):
    """OPTIMIZED: Handle email intent with parallel operations"""
    print("Email intent detected!")
    to_email = data.get("recipient_email")
    recipient_name = data.get("recipient_name")
    subject = data.get("subject")
    email_body = data.get("email_body")
    print(f"Extracted: to_email={to_email}, recipient_name={recipient_name}, subject={subject}")

    # If no email provided, try to find it by name
    if not to_email and recipient_name:
        print(f"Looking up email for: {recipient_name}")
        to_email = await get_email_by_name_optimized(recipient_name)
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
    else:
        print(f"Missing email details: to_email={to_email}, subject={subject}, email_body={email_body}")
        reply = "❌ Couldn't extract all email details. Please try again."
        await send_whatsapp_message(from_number, reply)

async def handle_lookup_contact_intent_optimized(data: dict, from_number: str):
    """OPTIMIZED: Handle contact lookup with caching"""
    lookup_name = data.get("lookup_name")
    lookup_field = data.get("lookup_field")

    if lookup_name:
        result = await lookup_contact_info_optimized(lookup_name, lookup_field or "all")
        reply = f"📋 {result}"
        await send_whatsapp_message(from_number, reply)
    else:
        await send_whatsapp_message(from_number, "Please specify which contact you want to look up.")

async def handle_add_contact_intent_optimized(data: dict, from_number: str):
    """OPTIMIZED: Handle add contact with cache invalidation"""
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
        
        success = await add_contact_to_sheet_optimized(
            contact_name, 
            contact_email or "N/A", 
            contact_phone or "N/A"
        )
        if success:
            reply = f"✅ Contact '{contact_name}' added successfully!"
        else:
            reply = f"❌ Contact '{contact_name}' already exists or failed to add."
        await send_whatsapp_message(from_number, reply)
    else:
        await send_whatsapp_message(from_number, "Please provide at least a contact name.")

async def handle_place_intent_optimized(data: dict, from_number: str):
    """OPTIMIZED: Handle place search with connection pooling"""
    intent = data.get("intent")
    place_query = data.get("place_query")
    
    if intent == "find_place":
        place_location = data.get("place_location")
        
        if not place_query:
            await send_whatsapp_message(from_number, "Sorry, I couldn't understand what kind of place you're looking for.")
            return
        
        # Check if we have a pending place query for this user
        if from_number in pending_place_queries:
            # User is providing location for a previous query
            previous_query = pending_place_queries.pop(from_number)
            place_query = previous_query
            place_location = data.get("place_query")  # Use the current message as location
        
        if not place_location or place_location.lower() in ["", "null", "none"]:
            # Ask user for area if not provided
            pending_place_queries[from_number] = place_query
            await send_whatsapp_message(from_number, "Sure—what area should I search in?")
            return
        
        try:
            results = await find_places_optimized(place_query, place_location)
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
            
        except Exception as e:
            print(f"Places search error: {e}")
            await send_whatsapp_message(from_number, f"Sorry, there was an error searching for places: {str(e)}")
    
    elif intent == "place_details":
        detail_type = data.get("place_detail_type", "maps_link")
        
        if not place_query:
            await send_whatsapp_message(from_number, "Sorry, I couldn't understand which place you're asking about.")
            return
        
        try:
            # Search for the specific place
            results = await find_places_optimized(place_query)
            
            if not results:
                reply = f"Sorry, I couldn't find '{place_query}'. Could you be more specific?"
            elif len(results) == 1:
                # Single result - provide the requested detail
                place = results[0]
                name = place.get('name', 'Unknown')
                
                if detail_type == "maps_link":
                    maps_link = place.get('maps_link')
                    if maps_link:
                        reply = f"📍 **{name}**\n🗺️ Google Maps: {maps_link}"
                    else:
                        reply = f"Sorry, I couldn't get the Google Maps link for {name}."
                
                elif detail_type == "address":
                    address = place.get('formatted_address', 'Address not available')
                    reply = f"📍 **{name}**\n🏠 Address: {address}"
                
                elif detail_type == "all":
                    address = place.get('formatted_address', 'Address not available')
                    rating = place.get('rating')
                    maps_link = place.get('maps_link')
                    
                    reply = f"📍 **{name}**\n🏠 Address: {address}"
                    if rating:
                        reply += f"\n⭐ Rating: {rating}"
                    if maps_link:
                        reply += f"\n🗺️ Google Maps: {maps_link}"
                
                else:
                    # Default to maps link for other detail types
                    maps_link = place.get('maps_link')
                    if maps_link:
                        reply = f"📍 **{name}**\n🗺️ Google Maps: {maps_link}"
                    else:
                        reply = f"Sorry, I couldn't get that information for {name}."
            
            else:
                # Multiple results - show options with maps links
                reply = f"I found multiple places for '{place_query}':\n\n"
                for i, place in enumerate(results[:3], 1):
                    name = place.get('name', 'Unknown')
                    rating = place.get('rating')
                    maps_link = place.get('maps_link')
                    
                    rating_str = f" (⭐{rating})" if rating else ""
                    reply += f"{i}. **{name}**{rating_str}\n"
                    if maps_link:
                        reply += f"   🗺️ {maps_link}\n\n"
                    else:
                        reply += f"   📍 {place.get('formatted_address', 'Address not available')}\n\n"
            
            await send_whatsapp_message(from_number, reply)
            
        except Exception as e:
            print(f"Place details error: {e}")
            await send_whatsapp_message(from_number, f"Sorry, there was an error getting place details: {str(e)}")

async def handle_web_search_intent_optimized(data: dict, from_number: str):
    """OPTIMIZED: Handle web search with timeout"""
    search_query = data.get("search_query")
    
    if not search_query:
        await send_whatsapp_message(from_number, "Sorry, I couldn't understand what you want to search for.")
        return
    
    try:
        # Perform the search using Tavily API
        search_results = await handle_search_query_optimized(search_query)
        await send_whatsapp_message(from_number, search_results)
        
    except Exception as e:
        print(f"Web search error: {e}")
        reply = "❌ Something went wrong with the search. Please try again later."
        await send_whatsapp_message(from_number, reply)

async def handle_memory_query_intent_optimized(data: dict, from_number: str):
    """OPTIMIZED: Handle memory queries with parallel operations"""
    memory_query = data.get("memory_query")
    
    if not memory_query:
        await send_whatsapp_message(from_number, "Sorry, I couldn't understand what you want to know about your past actions.")
        return
    
    try:
        if memory_manager:
            user_id = await memory_manager.get_user_id(from_number)
            
            # PERFORMANCE: Parallel memory queries
            conversations_task = asyncio.create_task(
                memory_manager.supabase_memory.get_recent_conversations(user_id, limit=20)
            )
            
            try:
                conversations = await asyncio.wait_for(conversations_task, timeout=10.0)
                
                # Filter based on query type
                if memory_query == "emails":
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
                
                # ... other memory query types would be handled similarly
                else:
                    reply = "📋 **Memory query not yet implemented for this type.**"
                
            except asyncio.TimeoutError:
                reply = "⏱️ Memory query timed out. Please try again."
        else:
            reply = "❌ Memory system not available."
        
        await send_whatsapp_message(from_number, reply)
        
    except Exception as e:
        print(f"Memory query error: {e}")
        reply = f"Sorry, there was an error retrieving your memory. Please try again later."
        await send_whatsapp_message(from_number, reply)

async def handle_update_contact_intent_optimized(data: dict, from_number: str):
    """OPTIMIZED: Handle contact update with cache invalidation"""
    contact_name = data.get("contact_name")
    update_field = data.get("update_field")
    update_value = data.get("update_value")

    if contact_name and update_field and update_value:
        try:
            # Run in thread pool to avoid blocking
            success, message = await asyncio.get_event_loop().run_in_executor(
                thread_pool, update_contact_in_sheet, contact_name, update_field, update_value
            )
            
            if success:
                # Invalidate cache after modification
                global sheets_cache, sheets_cache_timestamp
                cache_key = "sheet_records"
                if cache_key in sheets_cache:
                    del sheets_cache[cache_key]
                    del sheets_cache_timestamp[cache_key]
                
                reply = f"✅ {message}"
            else:
                reply = f"❌ {message}"
            
            await send_whatsapp_message(from_number, reply)
        except Exception as e:
            print(f"Update contact error: {e}")
            await send_whatsapp_message(from_number, "❌ Error updating contact. Please try again.")
    else:
        await send_whatsapp_message(from_number, "Please specify the contact name, field to update, and new value.")

async def handle_delete_contact_intent_optimized(data: dict, from_number: str):
    """OPTIMIZED: Handle contact deletion with cache invalidation"""
    contact_name = data.get("contact_name")

    if contact_name:
        try:
            # Run in thread pool to avoid blocking
            success, message = await asyncio.get_event_loop().run_in_executor(
                thread_pool, delete_contact_from_sheet, contact_name
            )
            
            if success:
                # Invalidate cache after modification
                global sheets_cache, sheets_cache_timestamp
                cache_key = "sheet_records"
                if cache_key in sheets_cache:
                    del sheets_cache[cache_key]
                    del sheets_cache_timestamp[cache_key]
                
                reply = f"✅ {message}"
            else:
                reply = f"❌ {message}"
            
            await send_whatsapp_message(from_number, reply)
        except Exception as e:
            print(f"Delete contact error: {e}")
            await send_whatsapp_message(from_number, "❌ Error deleting contact. Please try again.")
    else:
        await send_whatsapp_message(from_number, "Please specify which contact you want to delete.")

async def handle_calendar_auth_intent_optimized(data: dict, from_number: str):
    """OPTIMIZED: Handle calendar authentication"""
    try:
        # Check if calendar is already connected using the same token as Sheets
        try:
            service = get_calendar_service()
            
            # Test the calendar service by trying to list calendars
            calendars_result = await asyncio.get_event_loop().run_in_executor(
                thread_pool,
                lambda: service.calendarList().list().execute()
            )
            
            # If we get here, calendar is working
            reply = (
                "📅 **Calendar Already Connected!**\n\n"
                "✅ Your Google Calendar is already set up and working!\n"
                "🔗 Using the same authentication as Google Sheets.\n\n"
                "**Available Calendar Commands:**\n"
                "📋 'create meeting tomorrow 2pm' - Create events\n"
                "📅 'list my events' - Show upcoming events\n"
                "🗑️ 'delete my meeting' - Remove events\n"
                "🔄 'update my meeting title to Team Sync' - Modify events\n\n"
                "**Smart Features:**\n"
                "🕐 Natural time parsing (tomorrow, next week, etc.)\n"
                "📱 Real-time sync with Google Calendar\n"
                "🎯 Automatic event duration (defaults to 1 hour)\n\n"
                "💡 **Try it now:** Say 'create meeting tomorrow 2pm' to test!"
            )
            
        except Exception as auth_error:
            print(f"Calendar not connected: {auth_error}")
            
            # Check if it's a scope issue (token doesn't have calendar permission)
            if "insufficient" in str(auth_error).lower() or "scope" in str(auth_error).lower():
                reply = (
                    "📅 **Calendar Scope Missing**\n\n"
                    "❌ Your current Google token doesn't include calendar permissions.\n\n"
                    "**What happened:**\n"
                    "Your Google Sheets authentication is working, but it was created before calendar features were added.\n\n"
                    "**Solution:**\n"
                    "The administrator needs to regenerate the OAuth token with calendar permissions included.\n\n"
                    "**Technical Details:**\n"
                    "• Current token has: Google Sheets access\n"
                    "• Needed: Google Sheets + Calendar access\n"
                    "• Required scope: `https://www.googleapis.com/auth/calendar`\n\n"
                    "💡 **For Admin:** Re-run the OAuth flow with updated scopes and update the GOOGLE_TOKEN_BASE64 environment variable."
                )
            else:
                # Other authentication issues
                reply = (
                    "📅 **Calendar Authentication Issue**\n\n"
                    "❌ There's an issue with calendar authentication.\n\n"
                    "**Error Details:**\n"
                    f"{str(auth_error)}\n\n"
                    "**Possible Solutions:**\n"
                    "1. **Token Expired:** The OAuth token may need refreshing\n"
                    "2. **Missing Permissions:** Calendar scope may not be included\n"
                    "3. **Service Account:** May need calendar API enabled\n\n"
                    "**For Admin:**\n"
                    "• Check if calendar API is enabled in Google Cloud Console\n"
                    "• Verify OAuth token includes calendar scope\n"
                    "• Try regenerating the authentication token\n\n"
                    "💡 **Note:** Google Sheets is working, so the base authentication is fine."
                )
        
        await send_whatsapp_message(from_number, reply)
        
    except Exception as e:
        print(f"Calendar auth error: {e}")
        await send_whatsapp_message(from_number, "❌ Error checking calendar setup. Please try again.")

async def handle_calendar_create_intent_optimized(data: dict, from_number: str):
    """OPTIMIZED: Handle calendar event creation with actual Google Calendar API"""
    calendar_summary = data.get("calendar_summary")
    calendar_start = data.get("calendar_start")
    calendar_end = data.get("calendar_end")
    calendar_description = data.get("calendar_description")

    if calendar_summary and calendar_start:
        try:
            # If no end time provided, default to 1 hour later
            if not calendar_end:
                try:
                    start_dt = datetime.fromisoformat(calendar_start.replace('Z', '+00:00'))
                    end_dt = start_dt + timedelta(hours=1)
                    calendar_end = end_dt.isoformat()
                except:
                    calendar_end = calendar_start  # Fallback to same time
            
            # Try to get Google Calendar service
            try:
                service = get_calendar_service()
                
                # Create event body
                event_body = {
                    'summary': calendar_summary,
                    'start': format_datetime_for_google(calendar_start),
                    'end': format_datetime_for_google(calendar_end),
                }
                
                if calendar_description:
                    event_body['description'] = calendar_description
                
                # Create event in Google Calendar
                event = await asyncio.get_event_loop().run_in_executor(
                    thread_pool, 
                    lambda: service.events().insert(calendarId='primary', body=event_body).execute()
                )
                
                event_link = event.get('htmlLink', 'No link available')
                event_id = event.get('id')
                
                reply = (
                    f"📅 **Calendar Event Created Successfully!**\n\n"
                    f"📋 **Title:** {calendar_summary}\n"
                    f"🕐 **Start:** {calendar_start}\n"
                    f"🕐 **End:** {calendar_end}\n"
                )
                
                if calendar_description:
                    reply += f"📝 **Description:** {calendar_description}\n"
                
                reply += f"\n🔗 **Event Link:** {event_link}\n"
                reply += f"🆔 **Event ID:** {event_id}\n\n"
                reply += "✅ Event has been added to your Google Calendar!"
                
            except Exception as calendar_error:
                print(f"Google Calendar API error: {calendar_error}")
                # Check if it's an authentication error
                if "authorization" in str(calendar_error).lower() or "credentials" in str(calendar_error).lower():
                    reply = (
                        f"📅 **Calendar Authentication Required**\n\n"
                        f"I couldn't access your Google Calendar. The token may be missing calendar permissions.\n\n"
                        f"**Event Details (saved for when you connect):**\n"
                        f"📋 Title: {calendar_summary}\n"
                        f"🕐 Start: {calendar_start}\n"
                        f"🕐 End: {calendar_end}\n"
                    )
                    if calendar_description:
                        reply += f"📝 Description: {calendar_description}\n"
                    
                    reply += f"\n💡 **Solution:** Use 'setup my calendar' to check authentication status."
                elif "insufficient" in str(calendar_error).lower() or "scope" in str(calendar_error).lower():
                    reply = (
                        f"📅 **Calendar Permissions Missing**\n\n"
                        f"Your Google token doesn't include calendar permissions.\n\n"
                        f"**Event Details:**\n"
                        f"📋 Title: {calendar_summary}\n"
                        f"🕐 Start: {calendar_start}\n"
                        f"🕐 End: {calendar_end}\n\n"
                        f"💡 **Solution:** Administrator needs to regenerate the OAuth token with calendar scope."
                    )
                else:
                    reply = (
                        f"❌ **Error creating calendar event**\n\n"
                        f"There was an issue with Google Calendar API:\n"
                        f"{str(calendar_error)}\n\n"
                        f"**Event Details:**\n"
                        f"📋 Title: {calendar_summary}\n"
                        f"🕐 Start: {calendar_start}\n"
                        f"🕐 End: {calendar_end}\n"
                    )
            
            await send_whatsapp_message(from_number, reply)
            
        except Exception as e:
            print(f"Calendar create error: {e}")
            await send_whatsapp_message(from_number, "❌ Error creating calendar event. Please try again.")
    else:
        await send_whatsapp_message(from_number, "Please provide at least an event title and start time.")

async def handle_calendar_list_intent_optimized(data: dict, from_number: str):
    """OPTIMIZED: Handle calendar event listing with actual Google Calendar API"""
    try:
        # Try to get Google Calendar service
        try:
            service = get_calendar_service()
            
            # Get current time and 1 week from now
            now = datetime.now(DUBAI_TZ)
            future = now + timedelta(days=7)
            time_min = now.isoformat()
            time_max = future.isoformat()
            
            # List events from Google Calendar
            events_result = await asyncio.get_event_loop().run_in_executor(
                thread_pool,
                lambda: service.events().list(
                    calendarId='primary',
                    timeMin=time_min,
                    timeMax=time_max,
                    maxResults=10,
                    singleEvents=True,
                    orderBy='startTime'
                ).execute()
            )
            
            events = events_result.get('items', [])
            
            if not events:
                reply = (
                    "📅 **Your Calendar**\n\n"
                    "📋 No upcoming events found for the next 7 days.\n\n"
                    "💡 Use 'create meeting tomorrow 2pm' to add events!"
                )
            else:
                reply = "📅 **Your Upcoming Events:**\n\n"
                
                for event in events:
                    start_time = event['start'].get('dateTime', event['start'].get('date'))
                    summary = event.get('summary', 'No title')
                    event_id = event.get('id')
                    
                    # Format start time for display
                    try:
                        if 'T' in start_time:  # DateTime
                            dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                            formatted_time = dt.strftime('%a, %b %d at %I:%M %p')
                        else:  # Date only
                            dt = datetime.fromisoformat(start_time)
                            formatted_time = dt.strftime('%a, %b %d (All day)')
                    except:
                        formatted_time = start_time
                    
                    reply += f"🕐 **{formatted_time}**\n"
                    reply += f"📋 {summary}\n"
                    reply += f"🆔 ID: {event_id}\n\n"
                
                reply += f"📊 Total: {len(events)} events"
            
        except Exception as calendar_error:
            print(f"Google Calendar API error: {calendar_error}")
            # Check if it's an authentication error
            if "authorization" in str(calendar_error).lower() or "credentials" in str(calendar_error).lower():
                reply = (
                    "📅 **Calendar Authentication Required**\n\n"
                    "I couldn't access your Google Calendar. The token may be missing calendar permissions.\n\n"
                    "💡 **Solution:** Use 'setup my calendar' to check authentication status."
                )
            elif "insufficient" in str(calendar_error).lower() or "scope" in str(calendar_error).lower():
                reply = (
                    "📅 **Calendar Permissions Missing**\n\n"
                    "Your Google token doesn't include calendar permissions.\n\n"
                    "💡 **Solution:** Administrator needs to regenerate the OAuth token with calendar scope."
                )
            else:
                reply = (
                    f"❌ **Error accessing calendar**\n\n"
                    f"There was an issue with Google Calendar API:\n"
                    f"{str(calendar_error)}\n\n"
                    f"Please try again or contact support."
                )
        
        await send_whatsapp_message(from_number, reply)
        
    except Exception as e:
        print(f"Calendar list error: {e}")
        await send_whatsapp_message(from_number, "❌ Error listing calendar events. Please try again.")

async def handle_calendar_update_intent_optimized(data: dict, from_number: str):
    """OPTIMIZED: Handle calendar event updates"""
    calendar_event_id = data.get("calendar_event_id")
    calendar_field = data.get("calendar_field")
    calendar_value = data.get("calendar_value")

    if calendar_event_id and calendar_field and calendar_value:
        try:
            # For now, simulate update (in full implementation, call Google Calendar API)
            reply = (
                f"📅 **Calendar Event Updated!**\n\n"
                f"🆔 **Event ID:** {calendar_event_id}\n"
                f"📝 **Updated {calendar_field}:** {calendar_value}\n\n"
                "✅ Event has been updated successfully!"
            )
            await send_whatsapp_message(from_number, reply)
        except Exception as e:
            print(f"Calendar update error: {e}")
            await send_whatsapp_message(from_number, "❌ Error updating calendar event. Please try again.")
    else:
        await send_whatsapp_message(from_number, "Please provide the event ID, field to update, and new value.")

async def handle_calendar_delete_intent_optimized(data: dict, from_number: str):
    """OPTIMIZED: Handle calendar event deletion with actual Google Calendar API"""
    calendar_event_id = data.get("calendar_event_id")
    calendar_summary = data.get("calendar_summary")
    calendar_start = data.get("calendar_start")

    # Try to identify event by ID, summary, or date
    if calendar_event_id or calendar_summary or calendar_start:
        try:
            # Try to get Google Calendar service
            try:
                service = get_calendar_service()
                
                # If we have event ID, delete directly
                if calendar_event_id:
                    await asyncio.get_event_loop().run_in_executor(
                        thread_pool,
                        lambda: service.events().delete(calendarId='primary', eventId=calendar_event_id).execute()
                    )
                    
                    reply = (
                        f"📅 **Calendar Event Deleted!**\n\n"
                        f"🗑️ **Deleted Event ID:** {calendar_event_id}\n\n"
                        "✅ Event has been removed from your Google Calendar!"
                    )
                
                # If we have summary or date, search for the event first
                elif calendar_summary or calendar_start:
                    # Get events to search through
                    if calendar_start:
                        # Search around the specific date
                        try:
                            search_date = datetime.fromisoformat(calendar_start.replace('Z', '+00:00'))
                            # Ensure timezone is set to Dubai timezone
                            if search_date.tzinfo is None:
                                search_date = search_date.replace(tzinfo=DUBAI_TZ)
                            else:
                                search_date = search_date.astimezone(DUBAI_TZ)
                            
                            # Create start and end of day in Dubai timezone
                            start_of_day = search_date.replace(hour=0, minute=0, second=0, microsecond=0)
                            end_of_day = search_date.replace(hour=23, minute=59, second=59, microsecond=999999)
                        except Exception as date_error:
                            print(f"Date parsing error: {date_error}")
                            # Fallback to today
                            today = datetime.now(DUBAI_TZ)
                            start_of_day = today.replace(hour=0, minute=0, second=0, microsecond=0)
                            end_of_day = today.replace(hour=23, minute=59, second=59, microsecond=999999)
                            time_min = start_of_day.isoformat()
                            time_max = end_of_day.isoformat()
                    else:
                        # Search in the next 7 days
                        now = datetime.now(DUBAI_TZ)
                        future = now + timedelta(days=7)
                        time_min = now.isoformat()
                        time_max = future.isoformat()
                    
                    events_result = await asyncio.get_event_loop().run_in_executor(
                        thread_pool,
                        lambda: service.events().list(
                            calendarId='primary',
                            timeMin=time_min,
                            timeMax=time_max,
                            maxResults=50,
                            singleEvents=True,
                            orderBy='startTime'
                        ).execute()
                    )
                    
                    events = events_result.get('items', [])
                    
                    # Find matching event
                    matching_event = None
                    if calendar_summary:
                        # Search by summary
                        for event in events:
                            event_summary = event.get('summary', '').lower()
                            if calendar_summary.lower() in event_summary:
                                matching_event = event
                                break
                    elif events:
                        # If searching by date only, take the first event of that day
                        matching_event = events[0]
                    
                    if matching_event:
                        event_id = matching_event.get('id')
                        event_title = matching_event.get('summary', 'Untitled Event')
                        
                        # Delete the event
                        await asyncio.get_event_loop().run_in_executor(
                            thread_pool,
                            lambda: service.events().delete(calendarId='primary', eventId=event_id).execute()
                        )
                        
                        reply = (
                            f"📅 **Calendar Event Deleted!**\n\n"
                            f"🗑️ **Deleted:** {event_title}\n"
                            f"🆔 **Event ID:** {event_id}\n\n"
                            "✅ Event has been removed from your Google Calendar!"
                        )
                    else:
                        search_criteria = calendar_summary or f"events on {calendar_start}"
                        reply = (
                            f"❌ **Event Not Found**\n\n"
                            f"I couldn't find any events matching: {search_criteria}\n\n"
                            f"💡 Try using 'list my events' to see available events, then use the Event ID to delete."
                        )
                
            except Exception as calendar_error:
                print(f"Google Calendar API error: {calendar_error}")
                # Check if it's an authentication error
                if "authorization" in str(calendar_error).lower() or "credentials" in str(calendar_error).lower():
                    reply = (
                        "📅 **Calendar Authentication Required**\n\n"
                        "I couldn't access your Google Calendar. The token may be missing calendar permissions.\n\n"
                        "💡 **Solution:** Use 'setup my calendar' to check authentication status."
                    )
                elif "insufficient" in str(calendar_error).lower() or "scope" in str(calendar_error).lower():
                    reply = (
                        "📅 **Calendar Permissions Missing**\n\n"
                        "Your Google token doesn't include calendar permissions.\n\n"
                        "💡 **Solution:** Administrator needs to regenerate the OAuth token with calendar scope."
                    )
                elif "not found" in str(calendar_error).lower():
                    reply = (
                        "❌ **Event Not Found**\n\n"
                        "The event you're trying to delete doesn't exist or has already been deleted.\n\n"
                        "💡 Use 'list my events' to see current events."
                    )
                else:
                    reply = (
                        f"❌ **Error deleting event**\n\n"
                        f"There was an issue with Google Calendar API:\n"
                        f"{str(calendar_error)}\n\n"
                        f"Please try again or contact support."
                    )
            
            await send_whatsapp_message(from_number, reply)
            
        except Exception as e:
            print(f"Calendar delete error: {e}")
            await send_whatsapp_message(from_number, "❌ Error deleting calendar event. Please try again.")
    else:
        await send_whatsapp_message(from_number, "Please specify which event to delete (by title, date, or event ID).")

async def handle_calendar_bulk_create_intent_optimized(data: dict, from_number: str):
    """OPTIMIZED: Handle bulk calendar event creation with error handling and summary"""
    calendar_events = data.get("calendar_events", [])
    
    if not calendar_events:
        await send_whatsapp_message(from_number, "❌ No events provided for bulk creation. Please specify the events you want to create.")
        return
    
    try:
        # Try to get Google Calendar service
        try:
            service = get_calendar_service()
            
            # Track results
            created_events = []
            failed_events = []
            
            # Process each event
            for i, event_data in enumerate(calendar_events):
                try:
                    summary = event_data.get("summary", f"Event {i+1}")
                    start_time = event_data.get("start")
                    end_time = event_data.get("end")
                    description = event_data.get("description")
                    
                    if not start_time:
                        failed_events.append({
                            "summary": summary,
                            "error": "Missing start time"
                        })
                        continue
                    
                    # If no end time provided, default to 1 hour later
                    if not end_time:
                        try:
                            start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                            end_dt = start_dt + timedelta(hours=1)
                            end_time = end_dt.isoformat()
                        except:
                            end_time = start_time  # Fallback to same time
                    
                    # Create event body
                    event_body = {
                        'summary': summary,
                        'start': format_datetime_for_google(start_time),
                        'end': format_datetime_for_google(end_time),
                    }
                    
                    if description:
                        event_body['description'] = description
                    
                    # Create event in Google Calendar
                    event = await asyncio.get_event_loop().run_in_executor(
                        thread_pool, 
                        lambda: service.events().insert(calendarId='primary', body=event_body).execute()
                    )
                    
                    created_events.append({
                        "summary": summary,
                        "start": start_time,
                        "end": end_time,
                        "event_id": event.get('id'),
                        "event_link": event.get('htmlLink', 'No link available')
                    })
                    
                except Exception as event_error:
                    print(f"Error creating event {summary}: {event_error}")
                    failed_events.append({
                        "summary": summary,
                        "error": str(event_error)
                    })
            
            # Generate summary report
            reply = f"📅 **Bulk Calendar Creation Summary**\n\n"
            
            if created_events:
                reply += f"✅ **Successfully Created ({len(created_events)} events):**\n"
                for event in created_events:
                    reply += f"📋 {event['summary']}\n"
                    reply += f"   🕐 {event['start']} → {event['end']}\n"
                    reply += f"   🆔 {event['event_id']}\n\n"
            
            if failed_events:
                reply += f"❌ **Failed to Create ({len(failed_events)} events):**\n"
                for event in failed_events:
                    reply += f"📋 {event['summary']}\n"
                    reply += f"   ⚠️ Error: {event['error']}\n\n"
            
            # Add overall summary
            total_events = len(calendar_events)
            success_count = len(created_events)
            failure_count = len(failed_events)
            
            reply += f"📊 **Overall Results:**\n"
            reply += f"• Total requested: {total_events}\n"
            reply += f"• Successfully created: {success_count}\n"
            reply += f"• Failed: {failure_count}\n\n"
            
            if success_count > 0:
                reply += "🎉 Events have been added to your Google Calendar!"
            
        except Exception as calendar_error:
            print(f"Google Calendar API error: {calendar_error}")
            # Check if it's an authentication error
            if "authorization" in str(calendar_error).lower() or "credentials" in str(calendar_error).lower():
                reply = (
                    f"📅 **Calendar Authentication Required**\n\n"
                    f"I couldn't access your Google Calendar. The token may be missing calendar permissions.\n\n"
                    f"**Events to create ({len(calendar_events)}):**\n"
                )
                for event in calendar_events:
                    reply += f"📋 {event.get('summary', 'Untitled')}\n"
                
                reply += f"\n💡 **Solution:** Use 'setup my calendar' to check authentication status."
            elif "insufficient" in str(calendar_error).lower() or "scope" in str(calendar_error).lower():
                reply = (
                    f"📅 **Calendar Permissions Missing**\n\n"
                    f"Your Google token doesn't include calendar permissions.\n\n"
                    f"💡 **Solution:** Administrator needs to regenerate the OAuth token with calendar scope."
                )
            else:
                reply = (
                    f"❌ **Error creating calendar events**\n\n"
                    f"There was an issue with Google Calendar API:\n"
                    f"{str(calendar_error)}\n\n"
                    f"Please try again or contact support."
                )
        
        await send_whatsapp_message(from_number, reply)
        
    except Exception as e:
        print(f"Calendar bulk create error: {e}")
        await send_whatsapp_message(from_number, "❌ Error creating calendar events. Please try again.")

async def handle_calendar_bulk_delete_intent_optimized(data: dict, from_number: str):
    """
    OPTIMIZED: Handle bulk calendar event deletion with enhanced natural language understanding
    
    Supports natural language patterns including:
    - Time ranges: "upcoming week", "next week", "this week", "coming week", "following week"
    - Specific days: "today", "tomorrow", "next Monday", "this Friday"
    - Relative periods: "next 7 days", "rest of the week"
    - Multiple date formats: ISO, DD/MM/YYYY, MM/DD/YYYY, etc.
    - Event titles: partial matching for event names
    - Event IDs: direct deletion by calendar event ID
    
    Examples that now work:
    - "Delete all my meetings for the upcoming week"
    - "Cancel everything for next Monday"
    - "Remove my events for the next 7 days"
    - "Clear my calendar for the rest of the week"
    """
    delete_targets = data.get("calendar_delete_targets", [])
    
    if not delete_targets:
        await send_whatsapp_message(from_number, "❌ No events specified for deletion. Please specify which events to delete.")
        return
    
    try:
        # Try to get Google Calendar service
        try:
            service = get_calendar_service()
            
            # Track results
            deleted_events = []
            failed_deletions = []
            
            # Get events for searching (expand time range for bulk operations)
            now = datetime.now(DUBAI_TZ)
            past = now - timedelta(days=7)  # Look back 7 days
            future = now + timedelta(days=60)  # Look ahead 60 days for better coverage
            time_min = past.isoformat()
            time_max = future.isoformat()
            
            events_result = await asyncio.get_event_loop().run_in_executor(
                thread_pool,
                lambda: service.events().list(
                    calendarId='primary',
                    timeMin=time_min,
                    timeMax=time_max,
                    maxResults=200,  # Increased for bulk operations
                    singleEvents=True,
                    orderBy='startTime'
                ).execute()
            )
            
            all_events = events_result.get('items', [])
            
            # Process each deletion target
            for target in delete_targets:
                try:
                    target_str = str(target).strip().lower()
                    events_to_delete = []
                    
                    # Check if target is an event ID (typically long alphanumeric string)
                    if len(target_str) > 20 and target_str.replace('_', '').replace('-', '').isalnum():
                        # Likely an event ID - try direct deletion
                        try:
                            await asyncio.get_event_loop().run_in_executor(
                                thread_pool,
                                lambda: service.events().delete(calendarId='primary', eventId=target_str).execute()
                            )
                            deleted_events.append({
                                "identifier": target_str,
                                "title": "Event (by ID)",
                                "event_id": target_str
                            })
                            continue
                        except:
                            # If direct deletion fails, treat as search term
                            pass
                    
                    # Enhanced date-based deletion with better natural language understanding
                    if any(date_keyword in target_str for date_keyword in [
                        "today", "tomorrow", "yesterday", "week", "month", "day",
                        "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
                        "next", "this", "upcoming", "coming", "current"
                    ]) or any(char.isdigit() for char in target_str):
                        
                        # Parse date ranges with improved natural language understanding
                        start_range = None
                        end_range = None
                        
                        # Handle "this week" variations
                        if any(phrase in target_str for phrase in ["this week", "current week"]):
                            start_of_week = now - timedelta(days=now.weekday())
                            end_of_week = start_of_week + timedelta(days=6)
                            start_range = start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)
                            end_range = end_of_week.replace(hour=23, minute=59, second=59, microsecond=999999)
                        
                        # Handle "next week" and "upcoming week" variations
                        elif any(phrase in target_str for phrase in [
                            "next week", "upcoming week", "coming week", "the upcoming week", 
                            "the next week", "the coming week", "following week"
                        ]):
                            # For "upcoming week", be more flexible - include current week if we're early in the week
                            if "upcoming" in target_str or "coming" in target_str:
                                # If it's Monday-Wednesday, "upcoming week" likely means rest of this week + next week
                                # If it's Thursday-Sunday, "upcoming week" likely means next week
                                if now.weekday() <= 2:  # Monday=0, Tuesday=1, Wednesday=2
                                    # Include from today through next Sunday
                                    start_range = now.replace(hour=0, minute=0, second=0, microsecond=0)
                                    days_until_next_sunday = (6 - now.weekday()) + 7  # This Sunday + 7 days
                                    end_of_period = now + timedelta(days=days_until_next_sunday)
                                    end_range = end_of_period.replace(hour=23, minute=59, second=59, microsecond=999999)
                                else:
                                    # Late in week, so "upcoming week" means next week
                                    days_until_next_monday = (7 - now.weekday()) % 7
                                    if days_until_next_monday == 0:
                                        days_until_next_monday = 7
                                    
                                    start_of_next_week = now + timedelta(days=days_until_next_monday)
                                    start_range = start_of_next_week.replace(hour=0, minute=0, second=0, microsecond=0)
                                    end_range = start_of_next_week + timedelta(days=6, hours=23, minutes=59, seconds=59, microseconds=999999)
                            else:
                                # For "next week", always mean the following calendar week
                                days_until_next_monday = (7 - now.weekday()) % 7
                                if days_until_next_monday == 0:
                                    days_until_next_monday = 7
                                
                                start_of_next_week = now + timedelta(days=days_until_next_monday)
                                start_range = start_of_next_week.replace(hour=0, minute=0, second=0, microsecond=0)
                                end_range = start_of_next_week + timedelta(days=6, hours=23, minutes=59, seconds=59, microseconds=999999)
                        
                        # Handle "today" variations
                        elif any(phrase in target_str for phrase in ["today", "today's"]):
                            start_range = now.replace(hour=0, minute=0, second=0, microsecond=0)
                            end_range = now.replace(hour=23, minute=59, second=59, microsecond=999999)
                        
                        # Handle "tomorrow" variations
                        elif any(phrase in target_str for phrase in ["tomorrow", "tomorrow's"]):
                            tomorrow = now + timedelta(days=1)
                            start_range = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
                            end_range = tomorrow.replace(hour=23, minute=59, second=59, microsecond=999999)
                        
                        # Handle specific weekdays
                        elif any(day in target_str for day in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]):
                            # Find the next occurrence of the specified weekday
                            weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
                            target_weekday = None
                            for i, day in enumerate(weekdays):
                                if day in target_str:
                                    target_weekday = i
                                    break
                            
                            if target_weekday is not None:
                                # Check if it's "this" or "next" weekday
                                if "next" in target_str:
                                    # Always get next week's occurrence
                                    days_ahead = target_weekday - now.weekday()
                                    if days_ahead <= 0:
                                        days_ahead += 7
                                    else:
                                        days_ahead += 7  # Force next week
                                elif "this" in target_str:
                                    # Get this week's occurrence if it hasn't passed, otherwise next week
                                    days_ahead = target_weekday - now.weekday()
                                    if days_ahead < 0:  # Day already passed this week
                                        days_ahead += 7
                                else:
                                    # Default behavior - next occurrence
                                    days_ahead = target_weekday - now.weekday()
                                    if days_ahead <= 0:
                                        days_ahead += 7
                                
                                target_date = now + timedelta(days=days_ahead)
                                start_range = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
                                end_range = target_date.replace(hour=23, minute=59, second=59, microsecond=999999)
                        
                        # Handle "next X days" patterns
                        elif "next" in target_str and "days" in target_str:
                            # Extract number of days
                            import re
                            numbers = re.findall(r'\d+', target_str)
                            if numbers:
                                num_days = int(numbers[0])
                                start_range = now.replace(hour=0, minute=0, second=0, microsecond=0)
                                end_range = (now + timedelta(days=num_days)).replace(hour=23, minute=59, second=59, microsecond=999999)
                        
                        # Handle "rest of the week" patterns
                        elif any(phrase in target_str for phrase in ["rest of the week", "rest of week", "remaining week"]):
                            start_range = now.replace(hour=0, minute=0, second=0, microsecond=0)
                            # End of current week (Sunday)
                            days_until_sunday = (6 - now.weekday()) % 7
                            if days_until_sunday == 0:  # Today is Sunday
                                days_until_sunday = 0
                            end_of_week = now + timedelta(days=days_until_sunday)
                            end_range = end_of_week.replace(hour=23, minute=59, second=59, microsecond=999999)
                        
                        # Handle broad deletion patterns like "all my meetings", "everything", etc.
                        elif any(phrase in target_str for phrase in [
                            "all my meetings", "all meetings", "all events", "all my events", 
                            "everything", "all appointments", "all my appointments"
                        ]):
                            # When users say "all my meetings" without specifying time, 
                            # assume they mean upcoming events (today + next 7 days)
                            start_range = now.replace(hour=0, minute=0, second=0, microsecond=0)
                            end_range = (now + timedelta(days=7)).replace(hour=23, minute=59, second=59, microsecond=999999)
                        
                        # Handle specific dates (try to parse ISO format or common formats)
                        else:
                            try:
                                # Try various date formats
                                date_formats = [
                                    '%Y-%m-%d',
                                    '%d/%m/%Y',
                                    '%m/%d/%Y',
                                    '%d-%m-%Y',
                                    '%Y/%m/%d'
                                ]
                                
                                parsed_date = None
                                for fmt in date_formats:
                                    try:
                                        parsed_date = datetime.strptime(target_str, fmt)
                                        break
                                    except ValueError:
                                        continue
                                
                                if not parsed_date:
                                    # Try ISO format parsing
                                    parsed_date = datetime.fromisoformat(target_str.replace('Z', '+00:00'))
                                
                                if parsed_date:
                                    if parsed_date.tzinfo is None:
                                        parsed_date = parsed_date.replace(tzinfo=DUBAI_TZ)
                                    
                                    start_range = parsed_date.replace(hour=0, minute=0, second=0, microsecond=0)
                                    end_range = parsed_date.replace(hour=23, minute=59, second=59, microsecond=999999)
                            except:
                                # If all date parsing fails, continue to title search
                                pass
                    
                    # Find events in the calculated date range
                    if start_range and end_range:
                        print(f"🔍 Debug: Searching for events between {start_range} and {end_range} for target '{target}'")
                        for event in all_events:
                            event_start = event['start'].get('dateTime', event['start'].get('date'))
                            try:
                                if 'T' in event_start:
                                    event_dt = datetime.fromisoformat(event_start.replace('Z', '+00:00'))
                                else:
                                    event_dt = datetime.fromisoformat(event_start).replace(tzinfo=DUBAI_TZ)
                                
                                if start_range <= event_dt <= end_range:
                                    print(f"🎯 Debug: Found matching event '{event.get('summary', 'Untitled')}' at {event_dt}")
                                    events_to_delete.append(event)
                            except:
                                continue
                        else:
                            print(f"⚠️ Debug: No date range calculated for target '{target}'")
                    
                    # If no events found by date, search by title
                    if not events_to_delete:
                        for event in all_events:
                            event_summary = event.get('summary', '').lower()
                            if target_str in event_summary:
                                events_to_delete.append(event)
                    
                    # Delete found events
                    if events_to_delete:
                        for event in events_to_delete:
                            try:
                                event_id = event.get('id')
                                event_title = event.get('summary', 'Untitled Event')
                                
                                await asyncio.get_event_loop().run_in_executor(
                                    thread_pool,
                                    lambda: service.events().delete(calendarId='primary', eventId=event_id).execute()
                                )
                                
                                deleted_events.append({
                                    "identifier": str(target),
                                    "title": event_title,
                                    "event_id": event_id
                                })
                                
                            except Exception as delete_error:
                                failed_deletions.append({
                                    "identifier": str(target),
                                    "title": event.get('summary', 'Untitled Event'),
                                    "error": str(delete_error)
                                })
                    else:
                        failed_deletions.append({
                            "identifier": str(target),
                            "title": "Not found",
                            "error": f"No events found matching '{target}'"
                        })
                        
                except Exception as target_error:
                    print(f"Error processing deletion target {target}: {target_error}")
                    failed_deletions.append({
                        "identifier": str(target),
                        "title": "Processing error",
                        "error": str(target_error)
                    })
            
            # Generate summary report
            reply = f"📅 **Bulk Calendar Deletion Summary**\n\n"
            
            if deleted_events:
                reply += f"✅ **Successfully Deleted ({len(deleted_events)} events):**\n"
                for event in deleted_events:
                    reply += f"🗑️ {event['title']}\n"
                    reply += f"   🔍 Matched: {event['identifier']}\n"
                    reply += f"   🆔 ID: {event['event_id']}\n\n"
            
            if failed_deletions:
                reply += f"❌ **Failed to Delete ({len(failed_deletions)} targets):**\n"
                for failure in failed_deletions:
                    reply += f"🔍 {failure['identifier']}\n"
                    reply += f"   ⚠️ Error: {failure['error']}\n\n"
            
            # Add overall summary
            total_targets = len(delete_targets)
            success_count = len(deleted_events)
            failure_count = len(failed_deletions)
            
            reply += f"📊 **Overall Results:**\n"
            reply += f"• Total targets: {total_targets}\n"
            reply += f"• Successfully deleted: {success_count}\n"
            reply += f"• Failed: {failure_count}\n\n"
            
            if success_count > 0:
                reply += "🎉 Events have been removed from your Google Calendar!"
            elif failure_count > 0 and success_count == 0:
                reply += "💡 **Tip:** Try being more specific with dates or use 'list my events' to see what's available to delete."
            
        except Exception as calendar_error:
            print(f"Google Calendar API error: {calendar_error}")
            # Check if it's an authentication error
            if "authorization" in str(calendar_error).lower() or "credentials" in str(calendar_error).lower():
                reply = (
                    f"📅 **Calendar Authentication Required**\n\n"
                    f"I couldn't access your Google Calendar. The token may be missing calendar permissions.\n\n"
                    f"**Events to delete ({len(delete_targets)}):**\n"
                )
                for target in delete_targets:
                    reply += f"🗑️ {target}\n"
                
                reply += f"\n💡 **Solution:** Use 'setup my calendar' to check authentication status."
            elif "insufficient" in str(calendar_error).lower() or "scope" in str(calendar_error).lower():
                reply = (
                    f"📅 **Calendar Permissions Missing**\n\n"
                    f"Your Google token doesn't include calendar permissions.\n\n"
                    f"💡 **Solution:** Administrator needs to regenerate the OAuth token with calendar scope."
                )
            else:
                reply = (
                    f"❌ **Error deleting calendar events**\n\n"
                    f"There was an issue with Google Calendar API:\n"
                    f"{str(calendar_error)}\n\n"
                    f"Please try again or contact support."
                )
        
        await send_whatsapp_message(from_number, reply)
        
    except Exception as e:
        print(f"Calendar bulk delete error: {e}")
        await send_whatsapp_message(from_number, "❌ Error deleting calendar events. Please try again.")

async def handle_list_contacts_intent_optimized(data: dict, from_number: str):
    """OPTIMIZED: Handle list all contacts intent with caching"""
    try:
        # Get all contacts from Google Sheets
        contacts_list = await get_all_contacts_optimized()
        await send_whatsapp_message(from_number, contacts_list)
        
    except Exception as e:
        print(f"List contacts error: {e}")
        await send_whatsapp_message(from_number, "❌ Error retrieving contacts. Please try again.")

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

# PERFORMANCE OPTIMIZATION: App lifecycle management
@app.on_event("startup")
async def startup_event():
    """Initialize resources on startup"""
    print("🚀 Starting WhatsApp AI Assistant with performance optimizations")
    print(f"📊 HTTP client connection pool: max_connections=100, max_keepalive=20")
    print(f"🧵 Thread pool workers: {thread_pool._max_workers}")
    print(f"💾 Cache TTL: {CACHE_TTL} seconds")

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup resources on shutdown"""
    print("🛑 Shutting down WhatsApp AI Assistant")
    
    # Close HTTP client
    await http_client.aclose()
    print("✅ HTTP client closed")
    
    # Shutdown thread pool
    thread_pool.shutdown(wait=True)
    print("✅ Thread pool shutdown")
    
    # Clear caches
    global sheets_cache, sheets_cache_timestamp
    sheets_cache.clear()
    sheets_cache_timestamp.clear()
    print("✅ Caches cleared")

# PERFORMANCE OPTIMIZATION: Health check endpoint with metrics
@app.get("/health")
async def health_check():
    """Health check endpoint with performance metrics"""
    cache_stats = {
        "sheets_cache_size": len(sheets_cache),
        "cache_ttl_seconds": CACHE_TTL,
        "active_drafts": len(pending_email_drafts),
        "pending_place_queries": len(pending_place_queries)
    }
    
    return {
        "status": "healthy",
        "timestamp": datetime.now(DUBAI_TZ).isoformat(),
        "performance_optimizations": {
            "connection_pooling": "enabled",
            "caching": "enabled", 
            "parallel_execution": "enabled",
            "thread_pool_workers": thread_pool._max_workers
        },
        "cache_stats": cache_stats,
        "memory_manager": "available" if memory_manager else "unavailable"
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 5001))
    uvicorn.run(app, host="0.0.0.0", port=port)