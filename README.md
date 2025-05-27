# WhatsApp AI Assistant with Google Meet Integration

A powerful WhatsApp AI assistant built with FastAPI that handles email drafting, contact management, calendar operations with Google Meet support, places search, web search, and voice transcription.

## 🚀 Features

### 📧 Email Management
- Draft and send emails via WhatsApp
- Smart recipient lookup from Google Sheets
- AI-powered email composition
- Email revision and approval workflow

### 👥 Contact Management  
- Add, update, delete contacts in Google Sheets
- Smart contact lookup and search
- Bulk contact operations
- Contact information retrieval

### 📅 Calendar Management with Google Meet
- **NEW**: Google Meet integration for video meetings
- Create single and bulk calendar events
- Natural language date/time parsing
- Event listing, updating, and deletion
- Automatic Google Meet link generation
- Smart attendee management

### 🎥 Google Meet Features
- Automatic Google Meet link creation when requested
- Natural language detection ("video call", "online meeting", "virtual meeting")
- Meet links automatically shared with attendees
- Support for both single and bulk event creation
- Integration with Google Calendar API

### 🗺️ Places Search
- Find nearby restaurants, shops, services
- Google Places API integration
- Location-based recommendations
- Detailed place information

### 🔍 Web Search
- Enhanced search with context awareness
- Query optimization using user preferences
- Tavily API integration for real-time results
- Search insights and analytics

### 🎤 Voice Support
- Voice message transcription
- OpenAI Whisper integration
- Natural language processing

### 🧠 Memory System
- Hybrid memory (Supabase + Pinecone)
- User preference learning
- Conversation context retention
- Personalized responses

## 📅 Google Meet Usage Examples

### Natural Language Commands

**Create meetings with Google Meet:**
```
✅ "create meeting tomorrow 2pm with Google Meet"
✅ "schedule video call with John on Friday 1pm to 2pm"
✅ "book online meeting for Monday 10am"
✅ "create virtual team meeting tomorrow 3pm"
✅ "schedule video conference with client"
✅ "set up online standup Friday 9am"
```

**Regular meetings (no Google Meet):**
```
❌ "create meeting tomorrow 2pm" (no video keywords)
❌ "schedule lunch with John on Friday" (in-person meeting)
```

**Other platforms (not supported):**
```
❌ "schedule Zoom call with client" (different platform)
❌ "create Teams meeting tomorrow" (different platform)
```

### What Triggers Google Meet

The system automatically detects Google Meet requests when you mention:
- "Google Meet", "google meet", "meet"
- "video call", "video meeting"
- "online meeting", "virtual meeting"  
- "video conference", "online conference"

### Google Meet Features

- **Automatic Link Generation**: Google Meet links are created automatically
- **Attendee Sharing**: Links are automatically shared with all attendees
- **Calendar Integration**: Meetings appear in Google Calendar with Meet links
- **WhatsApp Notification**: You receive the Meet link via WhatsApp
- **Bulk Support**: Works with bulk calendar creation too

## 🛠️ Setup

### Prerequisites
- Python 3.8+
- Google Cloud Project with Calendar API enabled
- Twilio WhatsApp Business Account
- OpenAI API key
- Supabase account
- Pinecone account
- Tavily API key

### Environment Variables
```env
# Core APIs
OPENAI_API_KEY=your_openai_key
TWILIO_ACCOUNT_SID=your_twilio_sid
TWILIO_AUTH_TOKEN=your_twilio_token
TWILIO_WHATSAPP_NUMBER=your_whatsapp_number

# Google Services
GOOGLE_CREDENTIALS_BASE64=your_base64_encoded_credentials
GOOGLE_TOKEN_BASE64=your_base64_encoded_oauth_token
GOOGLE_PLACES_API_KEY=your_places_api_key

# Memory & Search
SUPABASE_URL=your_supabase_url
SUPABASE_KEY=your_supabase_key
PINECONE_API_KEY=your_pinecone_key
PINECONE_INDEX_NAME=your_index_name
TAVILY_API_KEY=your_tavily_key

# Email
RESEND_API_KEY=your_resend_key
```

### Installation
```bash
# Clone repository
git clone <repository-url>
cd whatsapp-ai-assistant

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set up environment variables
cp .env.example .env
# Edit .env with your API keys

# Run the application
python whatsapp.py
```

## 🧪 Testing Google Meet Integration

Run the Google Meet test suite:
```bash
python test_google_meet.py
```

This will test:
- Google Meet detection accuracy
- Natural language parsing
- Calendar creation flow
- Error handling

## 📱 Usage Examples

### Calendar with Google Meet
```
User: "create video call with team tomorrow 3pm"
Assistant: 
📅 Event Created Successfully!

📋 Team Video Call
📅 Thu, Dec 14
🕐 3:00 PM - 4:00 PM

🎥 Google Meet: https://meet.google.com/abc-defg-hij
💡 Meeting link automatically shared with attendees

✅ Added to your Google Calendar!
```

### Regular Calendar Event
```
User: "create meeting tomorrow 2pm"
Assistant:
📅 Event Created Successfully!

📋 Meeting
📅 Thu, Dec 14  
🕐 2:00 PM - 3:00 PM

✅ Added to your Google Calendar!
```

### Bulk Events with Mixed Google Meet
```
User: "create multiple meetings: standup tomorrow 9am with video, lunch Friday 12pm"
Assistant:
📅 Bulk Calendar Creation Summary

✅ Successfully Created (2 events):
📋 Standup
   📅 Thu, Dec 14
   🕐 9:00 AM - 10:00 AM
   🎥 Google Meet: https://meet.google.com/xyz-uvw-rst

📋 Lunch
   📅 Fri, Dec 15
   🕐 12:00 PM - 1:00 PM

📊 Summary:
• Total: 2 events
• Created: 2
• Failed: 0
• Google Meet links: 1

🎉 All events added to your Google Calendar! 1 events include Google Meet links!
```

## 🔧 API Endpoints

### Calendar with Google Meet
```http
POST /calendar/create
{
  "whatsapp_number": "+1234567890",
  "summary": "Team Meeting",
  "start": "2024-12-14T15:00:00",
  "end": "2024-12-14T16:00:00",
  "attendees": ["john@example.com", "jane@example.com"],
  "google_meet": true
}
```

Response:
```json
{
  "event_id": "abc123",
  "event_link": "https://calendar.google.com/event?eid=...",
  "google_meet_link": "https://meet.google.com/abc-defg-hij",
  "message": "Event 'Team Meeting' created successfully! Invitations sent to 2 attendees. Google Meet link: https://meet.google.com/abc-defg-hij"
}
```

## 🏗️ Architecture

### Google Meet Integration Flow
1. **Natural Language Processing**: Detect video meeting keywords
2. **Intent Extraction**: LLM identifies `calendar_conference_type: "google_meet"`
3. **Calendar API Call**: Create event with `conferenceData` and `conferenceDataVersion=1`
4. **Link Extraction**: Parse Google Meet link from API response
5. **User Notification**: Send WhatsApp message with meeting details and link

### Performance Optimizations
- Connection pooling for HTTP requests
- Parallel execution for memory operations
- Caching for Google Sheets data
- Async/await patterns throughout
- Thread pool for CPU-bound operations

## 🔒 Security

- Environment variable configuration
- OAuth2 authentication for Google services
- Secure token storage
- Input sanitization
- Rate limiting considerations

## 📊 Monitoring

- Health check endpoint: `/health`
- Performance metrics tracking
- Error logging and handling
- Memory usage optimization

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Ensure all tests pass
5. Submit a pull request

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🆘 Support

For issues and questions:
1. Check the troubleshooting section
2. Review the test suite results
3. Check Google Calendar API quotas
4. Verify OAuth token permissions include calendar scope

## 🔮 Roadmap

- [ ] Microsoft Teams integration
- [ ] Zoom integration  
- [ ] Recurring meeting support
- [ ] Meeting room booking
- [ ] Calendar conflict detection
- [ ] Meeting analytics and insights 