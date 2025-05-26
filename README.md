# WhatsApp AI Assistant

A WhatsApp bot with hybrid memory capabilities that can:

- 📧 Send emails with contact lookup
- 📅 Manage Google Calendar events  
- 🗺️ Find places using Google Places API
- 🧠 Remember past conversations and preferences
- 🎯 Provide personalized responses

## Features

- **Email Management**: Draft and send emails with automatic contact lookup
- **Calendar Integration**: Create, list, and delete calendar events
- **Location Search**: Find restaurants, shops, and places nearby
- **Memory System**: Hybrid memory using Supabase + Pinecone for personalized responses
- **Voice Support**: Transcribe voice messages using OpenAI Whisper

## Tech Stack

- **Backend**: FastAPI + Python
- **Memory**: Supabase (structured) + Pinecone (semantic)
- **AI**: OpenAI GPT-4 + Whisper
- **Integrations**: Twilio, Google APIs, Resend

## Environment Variables Required

```
PINECONE_API_KEY=
PINECONE_INDEX_NAME=
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
OPENAI_API_KEY=
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_WHATSAPP_NUMBER=
RESEND_API_KEY=
GOOGLE_PLACES_API_KEY=
```

## Deployment

Configured for Railway deployment with Procfile and requirements.txt included. 