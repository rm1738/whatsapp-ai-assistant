"""
Memory Fusion Module - PERFORMANCE OPTIMIZED
Combines structured memory (Supabase) and semantic memory (Pinecone) 
to provide comprehensive context for the LLM with parallel execution and caching
"""

import asyncio
from typing import Dict, List, Any, Optional
from functools import lru_cache
import time
from memory_supabase import SupabaseMemoryManager
from memory_pinecone import PineconeMemoryManager

class HybridMemoryManager:
    def __init__(self):
        """Initialize both memory managers with performance optimizations"""
        self.supabase_memory = SupabaseMemoryManager()
        self.pinecone_memory = PineconeMemoryManager()
        
        # PERFORMANCE: Cache for user IDs to avoid repeated lookups
        self._user_id_cache = {}
        self._user_id_cache_timestamp = {}
        self._cache_ttl = 300  # 5 minutes
    
    async def get_user_id(self, whatsapp_number: str) -> str:
        """OPTIMIZED: Get or create user ID with caching"""
        current_time = time.time()
        
        # Check cache first
        if (whatsapp_number in self._user_id_cache and 
            whatsapp_number in self._user_id_cache_timestamp and
            current_time - self._user_id_cache_timestamp[whatsapp_number] < self._cache_ttl):
            return self._user_id_cache[whatsapp_number]
        
        # Fetch from database
        user_id = await self.supabase_memory.get_or_create_user(whatsapp_number)
        
        # Update cache
        self._user_id_cache[whatsapp_number] = user_id
        self._user_id_cache_timestamp[whatsapp_number] = current_time
        
        return user_id
    
    async def store_conversation_with_memory(self, user_id: str, message_text: str, 
                                           intent: Optional[str] = None, 
                                           metadata: Optional[Dict] = None) -> bool:
        """OPTIMIZED: Store conversation with parallel execution"""
        try:
            # PERFORMANCE: Execute both storage operations in parallel
            pinecone_task = asyncio.create_task(
                self.pinecone_memory.store_message_embedding(
                    user_id=user_id,
                    message_text=message_text,
                    intent=intent,
                    metadata=metadata
                )
            )
            
            supabase_task = asyncio.create_task(
                self.supabase_memory.store_conversation(
                    user_id=user_id,
                    message_text=message_text,
                    message_type="user_input",
                    intent=intent,
                    metadata=metadata,
                    pinecone_id=None  # Will be updated after pinecone completes
                )
            )
            
            # Wait for both with timeout
            try:
                pinecone_id, supabase_success = await asyncio.gather(
                    asyncio.wait_for(pinecone_task, timeout=10.0),
                    asyncio.wait_for(supabase_task, timeout=10.0),
                    return_exceptions=True
                )
                
                # Check if both succeeded
                pinecone_success = bool(pinecone_id) and not isinstance(pinecone_id, Exception)
                supabase_success = bool(supabase_success) and not isinstance(supabase_success, Exception)
                
                return pinecone_success and supabase_success
                
            except asyncio.TimeoutError:
                print("Memory storage timed out")
                return False
            
        except Exception as e:
            print(f"Error storing conversation with memory: {e}")
            return False
    
    async def get_comprehensive_context(self, user_id: str, current_message: str, 
                                      include_semantic: bool = True,
                                      include_structured: bool = True,
                                      semantic_limit: int = 5) -> str:
        """OPTIMIZED: Get comprehensive context with parallel retrieval"""
        try:
            # PERFORMANCE: Execute context retrieval in parallel
            tasks = []
            
            if include_structured:
                structured_task = asyncio.create_task(
                    asyncio.get_event_loop().run_in_executor(
                        None, self.supabase_memory.format_structured_memory_context, user_id
                    )
                )
                tasks.append(("structured", structured_task))
            
            if include_semantic:
                semantic_task = asyncio.create_task(
                    self.pinecone_memory.get_conversation_context(
                        user_id=user_id,
                        current_message=current_message,
                        context_limit=semantic_limit
                    )
                )
                tasks.append(("semantic", semantic_task))
            
            # Wait for all tasks with timeout
            context_parts = []
            if tasks:
                try:
                    results = await asyncio.gather(
                        *[task for _, task in tasks],
                        return_exceptions=True
                    )
                    
                    for i, (context_type, _) in enumerate(tasks):
                        result = results[i]
                        if not isinstance(result, Exception):
                            if context_type == "structured" and result:
                                context_parts.append(result)
                            elif context_type == "semantic" and result:
                                semantic_context = self.pinecone_memory.format_semantic_memory_context(result)
                                if semantic_context:
                                    context_parts.append(semantic_context)
                
                except Exception as e:
                    print(f"Error gathering context: {e}")
            
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
        """OPTIMIZED: Update user preferences with parallel operations"""
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
                    # PERFORMANCE: Parallel preference retrieval and update
                    current_prefs_task = asyncio.create_task(
                        self.supabase_memory.get_user_preferences(user_id)
                    )
                    
                    try:
                        current_prefs = await asyncio.wait_for(current_prefs_task, timeout=5.0)
                        favorite_locations = current_prefs.get("favorite_locations", [])
                        
                        # Ensure favorite_locations is a list
                        if not isinstance(favorite_locations, list):
                            favorite_locations = []
                        
                        if location not in favorite_locations:
                            favorite_locations.append(location)
                            preferences_to_update["favorite_locations"] = favorite_locations
                    except asyncio.TimeoutError:
                        print("Preference retrieval timed out")
                        return False
            
            # Update preferences if any changes detected
            if preferences_to_update:
                return await asyncio.wait_for(
                    self.supabase_memory.update_user_preferences(user_id, preferences_to_update),
                    timeout=5.0
                )
            
            return True
            
        except Exception as e:
            print(f"Error updating user preferences: {e}")
            return False
    
    async def create_task_from_conversation(self, user_id: str, intent: str, 
                                          description: str, metadata: Optional[Dict] = None) -> bool:
        """OPTIMIZED: Create a task with timeout"""
        try:
            # Map intents to task types
            task_type_mapping = {
                "send_email": "email_task",
                "calendar_create": "calendar_task",
                "add_contact": "contact_task",
                "find_place": "location_task"
            }
            
            task_type = task_type_mapping.get(intent, "general_task")
            
            return await asyncio.wait_for(
                self.supabase_memory.create_task(
                    user_id=user_id,
                    task_type=task_type,
                    description=description,
                    metadata=metadata
                ),
                timeout=5.0
            )
            
        except Exception as e:
            print(f"Error creating task from conversation: {e}")
            return False
    
    async def get_personalized_prompt_context(self, user_id: str, current_message: str) -> str:
        """OPTIMIZED: Get personalized context with parallel execution"""
        try:
            # PERFORMANCE: Execute preference and semantic retrieval in parallel
            preferences_task = asyncio.create_task(
                self.supabase_memory.get_user_preferences(user_id)
            )
            
            semantic_task = asyncio.create_task(
                self.pinecone_memory.get_conversation_context(
                    user_id=user_id,
                    current_message=current_message,
                    context_limit=3
                )
            )
            
            try:
                preferences, similar_messages = await asyncio.gather(
                    asyncio.wait_for(preferences_task, timeout=5.0),
                    asyncio.wait_for(semantic_task, timeout=5.0),
                    return_exceptions=True
                )
                
                # Handle exceptions
                if isinstance(preferences, Exception):
                    preferences = {}
                if isinstance(similar_messages, Exception):
                    similar_messages = []
                
            except Exception as e:
                print(f"Error gathering personalized context: {e}")
                preferences = {}
                similar_messages = []
            
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
        """OPTIMIZED: Analyze user's conversation patterns with parallel execution"""
        try:
            # PERFORMANCE: Execute analysis queries in parallel
            conversations_task = asyncio.create_task(
                self.supabase_memory.get_recent_conversations(user_id, limit=20)
            )
            
            tasks_task = asyncio.create_task(
                self.supabase_memory.get_user_tasks(user_id, status="pending")
            )
            
            try:
                recent_conversations, pending_tasks = await asyncio.gather(
                    asyncio.wait_for(conversations_task, timeout=10.0),
                    asyncio.wait_for(tasks_task, timeout=10.0),
                    return_exceptions=True
                )
                
                # Handle exceptions
                if isinstance(recent_conversations, Exception):
                    recent_conversations = []
                if isinstance(pending_tasks, Exception):
                    pending_tasks = []
                
            except Exception as e:
                print(f"Error gathering analysis data: {e}")
                recent_conversations = []
                pending_tasks = []
            
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
    
    def clear_cache(self):
        """PERFORMANCE: Clear internal caches"""
        self._user_id_cache.clear()
        self._user_id_cache_timestamp.clear()
        print("✅ Memory manager caches cleared") 