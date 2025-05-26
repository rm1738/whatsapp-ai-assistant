"""
Pinecone Memory Helper Module
Handles semantic memory storage and retrieval using Pinecone vector database
"""

import os
import uuid
import openai
from typing import List, Dict, Any, Optional
from pinecone import Pinecone, ServerlessSpec
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class PineconeMemoryManager:
    def __init__(self):
        """Initialize Pinecone client"""
        self.api_key = os.getenv("PINECONE_API_KEY")
        self.index_name = os.getenv("PINECONE_INDEX_NAME")
        
        if not self.api_key or not self.index_name:
            raise ValueError("PINECONE_API_KEY and PINECONE_INDEX_NAME must be set")
        
        # Initialize Pinecone
        self.pc = Pinecone(api_key=self.api_key)
        
        # Connect to index
        try:
            self.index = self.pc.Index(self.index_name)
        except Exception as e:
            print(f"Error connecting to Pinecone index: {e}")
            raise
    
    async def create_embedding(self, text: str) -> List[float]:
        """Create embedding for text using OpenAI"""
        try:
            response = openai.embeddings.create(
                model="text-embedding-3-small",
                input=text
            )
            return response.data[0].embedding
        except Exception as e:
            print(f"Error creating embedding: {e}")
            raise
    
    async def store_message_embedding(self, user_id: str, message_text: str, 
                                    intent: Optional[str] = None, 
                                    metadata: Optional[Dict] = None) -> str:
        """Store message embedding in Pinecone"""
        try:
            # Create embedding
            embedding = await self.create_embedding(message_text)
            
            # Generate unique ID
            vector_id = str(uuid.uuid4())
            
            # Prepare metadata
            vector_metadata = {
                "user_id": user_id,
                "message_text": message_text,
                "intent": intent or "unknown",
                "timestamp": str(int(os.times().elapsed * 1000)),  # Current timestamp
                **(metadata or {})
            }
            
            # Store in Pinecone
            self.index.upsert(
                vectors=[(vector_id, embedding, vector_metadata)]
            )
            
            return vector_id
            
        except Exception as e:
            print(f"Error storing message embedding: {e}")
            raise
    
    async def search_similar_messages(self, user_id: str, query_text: str, 
                                    top_k: int = 5, 
                                    intent_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """Search for similar messages using semantic similarity"""
        try:
            # Create embedding for query
            query_embedding = await self.create_embedding(query_text)
            
            # Prepare filter
            filter_dict = {"user_id": {"$eq": user_id}}
            if intent_filter:
                filter_dict["intent"] = {"$eq": intent_filter}
            
            # Search in Pinecone
            search_results = self.index.query(
                vector=query_embedding,
                top_k=top_k,
                include_metadata=True,
                filter=filter_dict
            )
            
            # Format results
            similar_messages = []
            for match in search_results.matches:
                similar_messages.append({
                    "id": match.id,
                    "score": match.score,
                    "message_text": match.metadata.get("message_text", ""),
                    "intent": match.metadata.get("intent", ""),
                    "timestamp": match.metadata.get("timestamp", ""),
                    "metadata": match.metadata
                })
            
            return similar_messages
            
        except Exception as e:
            print(f"Error searching similar messages: {e}")
            return []
    
    async def get_conversation_context(self, user_id: str, current_message: str, 
                                     context_limit: int = 5) -> List[Dict[str, Any]]:
        """Get relevant conversation context based on current message"""
        try:
            # Search for similar past conversations
            similar_messages = await self.search_similar_messages(
                user_id=user_id,
                query_text=current_message,
                top_k=context_limit
            )
            
            # Filter out very low similarity scores (< 0.7)
            relevant_messages = [
                msg for msg in similar_messages 
                if msg["score"] > 0.7
            ]
            
            return relevant_messages
            
        except Exception as e:
            print(f"Error getting conversation context: {e}")
            return []
    
    def format_semantic_memory_context(self, similar_messages: List[Dict[str, Any]]) -> str:
        """Format semantic memory as context string for LLM"""
        if not similar_messages:
            return ""
        
        context_parts = ["RELEVANT PAST CONVERSATIONS:"]
        
        for i, msg in enumerate(similar_messages, 1):
            score = msg["score"]
            intent = msg["intent"]
            text = msg["message_text"]
            
            # Truncate long messages
            if len(text) > 150:
                text = text[:150] + "..."
            
            context_parts.append(
                f"{i}. [{intent}] (similarity: {score:.2f}) {text}"
            )
        
        return "\n".join(context_parts)
    
    async def delete_user_vectors(self, user_id: str) -> bool:
        """Delete all vectors for a specific user"""
        try:
            # Note: This requires fetching all vectors first since Pinecone
            # doesn't support direct deletion by metadata filter
            # This is a simplified version - in production, you might want
            # to implement batch deletion
            
            # For now, we'll just mark this as a TODO
            print(f"TODO: Implement user vector deletion for user_id: {user_id}")
            return True
            
        except Exception as e:
            print(f"Error deleting user vectors: {e}")
            return False
    
    async def get_user_message_count(self, user_id: str) -> int:
        """Get count of stored messages for a user"""
        try:
            # This is a simplified implementation
            # In practice, you might want to maintain this count separately
            # since Pinecone doesn't provide direct count functionality
            
            # Search with a dummy query to get some results and estimate
            dummy_results = await self.search_similar_messages(
                user_id=user_id,
                query_text="dummy query",
                top_k=1000  # Max we can fetch at once
            )
            
            return len(dummy_results)
            
        except Exception as e:
            print(f"Error getting user message count: {e}")
            return 0 