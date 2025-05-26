"""
Memory Fusion Module
Combines structured memory (Supabase) and semantic memory (Pinecone) 
to provide comprehensive context for the LLM
"""

import asyncio
from typing import Dict, List, Any, Optional
from memory_supabase import SupabaseMemoryManager
from memory_pinecone import PineconeMemoryManager

class HybridMemoryManager:
    def __init__(self):
        """Initialize both memory managers"""
        self.supabase_memory = SupabaseMemoryManager()
        self.pinecone_memory = PineconeMemoryManager()
    
    async def get_user_id(self, whatsapp_number: str) -> str:
        """Get or create user ID based on WhatsApp number"""
        return await self.supabase_memory.get_or_create_user(whatsapp_number)
    
    async def store_conversation_with_memory(self, user_id: str, message_text: str, 
                                           intent: Optional[str] = None, 
                                           metadata: Optional[Dict] = None) -> bool:
        """Store conversation in both structured and semantic memory"""
        try:
            # Store in Pinecone for semantic search
            pinecone_id = await self.pinecone_memory.store_message_embedding(
                user_id=user_id,
                message_text=message_text,
                intent=intent,
                metadata=metadata
            )
            
            # Store in Supabase for structured access
            supabase_success = await self.supabase_memory.store_conversation(
                user_id=user_id,
                message_text=message_text,
                message_type="user_input",
                intent=intent,
                metadata=metadata,
                pinecone_id=pinecone_id
            )
            
            return supabase_success and bool(pinecone_id)
            
        except Exception as e:
            print(f"Error storing conversation with memory: {e}")
            return False
    
    async def get_comprehensive_context(self, user_id: str, current_message: str, 
                                      include_semantic: bool = True,
                                      include_structured: bool = True,
                                      semantic_limit: int = 5) -> str:
        """Get comprehensive context combining both memory types"""
        context_parts = []
        
        try:
            # Get structured memory context
            if include_structured:
                structured_context = self.supabase_memory.format_structured_memory_context(user_id)
                if structured_context:
                    context_parts.append(structured_context)
            
            # Get semantic memory context
            if include_semantic:
                similar_messages = await self.pinecone_memory.get_conversation_context(
                    user_id=user_id,
                    current_message=current_message,
                    context_limit=semantic_limit
                )
                
                if similar_messages:
                    semantic_context = self.pinecone_memory.format_semantic_memory_context(similar_messages)
                    context_parts.append(semantic_context)
            
            # Combine all context
            if context_parts:
                full_context = "\n\n".join(context_parts)
                return f"MEMORY CONTEXT:\n{full_context}\n\nCURRENT MESSAGE: {current_message}"
            else:
                return f"CURRENT MESSAGE: {current_message}"
                
        except Exception as e:
            print(f"Error getting comprehensive context: {e}")
            return f"CURRENT MESSAGE: {current_message}"
    
    async def update_user_preferences_from_conversation(self, user_id: str, 
                                                       conversation_data: Dict[str, Any]) -> bool:
        """Update user preferences based on conversation patterns"""
        try:
            # Extract preferences from conversation
            preferences_to_update = {}
            
            # Example: Update email tone based on user's language style
            if conversation_data.get("intent") == "send_email":
                # Analyze tone from email content
                email_body = conversation_data.get("email_body", "")
                if any(word in email_body.lower() for word in ["please", "kindly", "would appreciate"]):
                    preferences_to_update["email_tone"] = "polite"
                elif any(word in email_body.lower() for word in ["urgent", "asap", "immediately"]):
                    preferences_to_update["email_tone"] = "direct"
            
            # Example: Update favorite locations from place searches
            if conversation_data.get("intent") == "find_place":
                location = conversation_data.get("place_location")
                if location and location not in ["near me", None]:
                    # Get current preferences
                    current_prefs = await self.supabase_memory.get_user_preferences(user_id)
                    favorite_locations = current_prefs.get("favorite_locations", [])
                    
                    # Ensure favorite_locations is a list
                    if not isinstance(favorite_locations, list):
                        favorite_locations = []
                    
                    if location not in favorite_locations:
                        favorite_locations.append(location)
                        preferences_to_update["favorite_locations"] = favorite_locations
            
            # Update preferences if any changes detected
            if preferences_to_update:
                return await self.supabase_memory.update_user_preferences(user_id, preferences_to_update)
            
            return True
            
        except Exception as e:
            print(f"Error updating user preferences: {e}")
            return False
    
    async def create_task_from_conversation(self, user_id: str, intent: str, 
                                          description: str, metadata: Optional[Dict] = None) -> bool:
        """Create a task based on conversation intent"""
        try:
            # Map intents to task types
            task_type_mapping = {
                "send_email": "email_task",
                "calendar_create": "calendar_task",
                "add_contact": "contact_task",
                "find_place": "location_task"
            }
            
            task_type = task_type_mapping.get(intent, "general_task")
            
            return await self.supabase_memory.create_task(
                user_id=user_id,
                task_type=task_type,
                description=description,
                metadata=metadata
            )
            
        except Exception as e:
            print(f"Error creating task from conversation: {e}")
            return False
    
    async def get_personalized_prompt_context(self, user_id: str, current_message: str) -> str:
        """Get personalized context for LLM prompt enhancement"""
        try:
            # Get user preferences
            preferences = await self.supabase_memory.get_user_preferences(user_id)
            
            # Get semantic context
            similar_messages = await self.pinecone_memory.get_conversation_context(
                user_id=user_id,
                current_message=current_message,
                context_limit=3
            )
            
            # Build personalized context
            context_parts = []
            
            # Add user preferences for personalization
            if preferences:
                context_parts.append("USER PERSONALIZATION:")
                context_parts.append(f"- Preferred email tone: {preferences.get('email_tone', 'neutral')}")
                context_parts.append(f"- Email signature: {preferences.get('email_signoff', 'Best regards')}")
                
                if preferences.get('favorite_locations'):
                    locations = preferences['favorite_locations']
                    # Ensure it's a list and handle JSONB format
                    if isinstance(locations, list) and locations:
                        locations_str = ', '.join(locations[:3])  # Top 3
                        context_parts.append(f"- Frequently mentioned locations: {locations_str}")
            
            # Add relevant past conversations
            if similar_messages:
                context_parts.append("\nRELEVANT PAST INTERACTIONS:")
                for msg in similar_messages[:2]:  # Top 2 most relevant
                    intent_info = f"[{msg['intent']}]" if msg['intent'] != 'unknown' else ""
                    context_parts.append(f"- {intent_info} {msg['message_text'][:100]}...")
            
            if context_parts:
                return "\n".join(context_parts)
            else:
                return ""
                
        except Exception as e:
            print(f"Error getting personalized prompt context: {e}")
            return ""
    
    async def analyze_conversation_patterns(self, user_id: str) -> Dict[str, Any]:
        """Analyze user's conversation patterns for insights"""
        try:
            # Get recent conversations
            recent_conversations = await self.supabase_memory.get_recent_conversations(user_id, limit=20)
            
            # Get pending tasks
            pending_tasks = await self.supabase_memory.get_user_tasks(user_id, status="pending")
            
            # Analyze patterns
            intent_counts = {}
            for conv in recent_conversations:
                intent = conv.get('intent', 'unknown')
                intent_counts[intent] = intent_counts.get(intent, 0) + 1
            
            # Most common intents
            most_common_intent = max(intent_counts.items(), key=lambda x: x[1]) if intent_counts else ("unknown", 0)
            
            analysis = {
                "total_conversations": len(recent_conversations),
                "pending_tasks_count": len(pending_tasks),
                "most_common_intent": most_common_intent[0],
                "intent_frequency": intent_counts,
                "has_pending_tasks": len(pending_tasks) > 0
            }
            
            return analysis
            
        except Exception as e:
            print(f"Error analyzing conversation patterns: {e}")
            return {}
    
    async def cleanup_old_memories(self, user_id: str, days_to_keep: int = 30) -> bool:
        """Clean up old memories to maintain performance"""
        try:
            # This is a placeholder for memory cleanup logic
            # In production, you'd implement:
            # 1. Delete old conversation history from Supabase
            # 2. Delete corresponding vectors from Pinecone
            # 3. Archive completed tasks older than threshold
            
            print(f"TODO: Implement memory cleanup for user {user_id}, keeping {days_to_keep} days")
            return True
            
        except Exception as e:
            print(f"Error cleaning up old memories: {e}")
            return False 