"""
Supabase Memory Helper Module
Handles structured memory storage and retrieval using Supabase
"""

import os
import json
from typing import Dict, List, Optional, Any
from datetime import datetime
from supabase import create_client, Client
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class SupabaseMemoryManager:
    def __init__(self):
        """Initialize Supabase client"""
        self.supabase_url = os.getenv("SUPABASE_URL")
        self.supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        
        if not self.supabase_url or not self.supabase_key:
            raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
        
        self.client: Client = create_client(self.supabase_url, self.supabase_key)
    
    async def get_or_create_user(self, whatsapp_number: str) -> str:
        """Get existing user or create new user based on WhatsApp number"""
        try:
            # First, try to find existing user by WhatsApp number in metadata
            response = self.client.table("user_preferences").select("user_id").eq("metadata->>whatsapp_number", whatsapp_number).execute()
            
            if response.data:
                return response.data[0]["user_id"]
            
            # Create new user if not found
            new_user_data = {
                "email_tone": "professional",
                "email_signoff": "Best regards",
                "work_hours": "9am–5pm",
                "favorite_locations": [],  # This will be stored as JSONB
                "metadata": {"whatsapp_number": whatsapp_number}
            }
            
            response = self.client.table("user_preferences").insert(new_user_data).execute()
            return response.data[0]["user_id"]
            
        except Exception as e:
            print(f"Error getting/creating user: {e}")
            raise
    
    async def get_user_preferences(self, user_id: str) -> Dict[str, Any]:
        """Fetch user preferences from Supabase"""
        try:
            response = self.client.table("user_preferences").select("*").eq("user_id", user_id).execute()
            
            if response.data:
                return response.data[0]
            else:
                return {}
                
        except Exception as e:
            print(f"Error fetching user preferences: {e}")
            return {}
    
    async def update_user_preferences(self, user_id: str, preferences: Dict[str, Any]) -> bool:
        """Update user preferences in Supabase"""
        try:
            response = self.client.table("user_preferences").update(preferences).eq("user_id", user_id).execute()
            return len(response.data) > 0
            
        except Exception as e:
            print(f"Error updating user preferences: {e}")
            return False
    
    async def get_user_tasks(self, user_id: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch user tasks from Supabase"""
        try:
            query = self.client.table("user_tasks").select("*").eq("user_id", user_id)
            
            if status:
                query = query.eq("status", status)
            
            response = query.order("created_at", desc=True).execute()
            return response.data
            
        except Exception as e:
            print(f"Error fetching user tasks: {e}")
            return []
    
    async def create_task(self, user_id: str, task_type: str, description: str, metadata: Optional[Dict] = None) -> bool:
        """Create a new task for the user"""
        try:
            task_data = {
                "user_id": user_id,
                "task_type": task_type,
                "description": description,
                "status": "pending",
                "metadata": metadata or {}
            }
            
            response = self.client.table("user_tasks").insert(task_data).execute()
            return len(response.data) > 0
            
        except Exception as e:
            print(f"Error creating task: {e}")
            return False
    
    async def update_task_status(self, task_id: str, status: str) -> bool:
        """Update task status"""
        try:
            response = self.client.table("user_tasks").update({"status": status}).eq("id", task_id).execute()
            return len(response.data) > 0
            
        except Exception as e:
            print(f"Error updating task status: {e}")
            return False
    
    async def store_conversation(self, user_id: str, message_text: str, message_type: str = "user_input", 
                                intent: Optional[str] = None, metadata: Optional[Dict] = None, 
                                pinecone_id: Optional[str] = None) -> bool:
        """Store conversation history in Supabase"""
        try:
            conversation_data = {
                "user_id": user_id,
                "message_text": message_text,
                "message_type": message_type,
                "intent": intent,
                "metadata": metadata or {},
                "pinecone_id": pinecone_id
            }
            
            response = self.client.table("conversation_history").insert(conversation_data).execute()
            return len(response.data) > 0
            
        except Exception as e:
            print(f"Error storing conversation: {e}")
            return False
    
    async def get_recent_conversations(self, user_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent conversation history"""
        try:
            response = self.client.table("conversation_history").select("*").eq("user_id", user_id).order("created_at", desc=True).limit(limit).execute()
            return response.data
            
        except Exception as e:
            print(f"Error fetching conversations: {e}")
            return []
    
    def format_structured_memory_context(self, user_id: str) -> str:
        """Format structured memory as context string for LLM"""
        try:
            # Get user preferences
            preferences = self.client.table("user_preferences").select("*").eq("user_id", user_id).execute().data
            
            # Get pending tasks
            tasks = self.client.table("user_tasks").select("*").eq("user_id", user_id).eq("status", "pending").execute().data
            
            # Get recent conversations
            conversations = self.client.table("conversation_history").select("message_text, intent, created_at").eq("user_id", user_id).order("created_at", desc=True).limit(5).execute().data
            
            context_parts = []
            
            # Add user preferences
            if preferences:
                pref = preferences[0]
                context_parts.append(f"USER PREFERENCES:")
                context_parts.append(f"- Email tone: {pref.get('email_tone', 'neutral')}")
                context_parts.append(f"- Email signoff: {pref.get('email_signoff', 'Best regards')}")
                context_parts.append(f"- Work hours: {pref.get('work_hours', '9am–5pm')}")
                
                if pref.get('favorite_locations'):
                    # Handle both list and JSONB formats
                    locations = pref['favorite_locations']
                    if isinstance(locations, list):
                        locations_str = ', '.join(locations)
                    else:
                        # If it's a string representation of JSON, parse it
                        import json
                        try:
                            if isinstance(locations, str):
                                locations = json.loads(locations)
                            locations_str = ', '.join(locations) if locations else ''
                        except:
                            locations_str = str(locations)
                    
                    if locations_str:
                        context_parts.append(f"- Favorite locations: {locations_str}")
            
            # Add pending tasks
            if tasks:
                context_parts.append(f"\nPENDING TASKS:")
                for task in tasks[:3]:  # Show top 3 pending tasks
                    context_parts.append(f"- {task['task_type']}: {task['description']}")
            
            # Add recent conversation context
            if conversations:
                context_parts.append(f"\nRECENT CONVERSATION CONTEXT:")
                for conv in conversations:
                    intent_info = f" ({conv['intent']})" if conv['intent'] else ""
                    context_parts.append(f"- {conv['message_text'][:100]}...{intent_info}")
            
            return "\n".join(context_parts)
            
        except Exception as e:
            print(f"Error formatting structured memory context: {e}")
            return "" 