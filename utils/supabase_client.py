"""
Supabase Client - Handles database operations and rate limiting
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List
import json

from config import Config

logger = logging.getLogger(__name__)


class SupabaseClient:
    """Client for Supabase database operations"""

    def __init__(self):
        """Initialize Supabase client"""
        try:
            from supabase import create_client

            self.client = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)
            logger.info("✅ Supabase client initialized")
        except ImportError:
            logger.warning("⚠️  supabase package not installed, using in-memory fallback")
            self.client = None
            self._in_memory_db = {
                "batteries": {},
                "rate_limits": {},
                "retry_queue": [],
            }

    # ============================================
    # BATTERY OPERATIONS
    # ============================================

    def create_battery(
        self,
        token_id: int,
        serial_hash: str,
        user_id: str,
        metadata_cid: str,
        photo_cid: str,
        tx_hash: str,
        block_number: int,
    ) -> Optional[Dict]:
        """
        Create a battery record in Supabase
        
        Args:
            token_id: NFT token ID
            serial_hash: Keccak256 hash of serial number
            user_id: Supabase user ID
            metadata_cid: IPFS CID of metadata
            photo_cid: IPFS CID of photo
            tx_hash: Blockchain transaction hash
            block_number: Block number of mint
            
        Returns:
            Created battery record or None on failure
        """
        try:
            battery_data = {
                "token_id": token_id,
                "serial_hash": serial_hash,
                "user_id": user_id,
                "metadata_cid": metadata_cid,
                "photo_cid": photo_cid,
                "verification_status": "pending",
                "confidence_score": None,
                "listing_status": "draft",
                "listing_price": None,
                "tx_hash": tx_hash,
                "block_number": block_number,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            }

            if self.client:
                response = self.client.table("batteries").insert(battery_data).execute()
                logger.info(f"✅ Battery created: token_id={token_id}")
                return response.data[0] if response.data else battery_data
            else:
                # In-memory fallback
                self._in_memory_db["batteries"][token_id] = battery_data
                logger.info(f"✅ Battery created (in-memory): token_id={token_id}")
                return battery_data

        except Exception as e:
            logger.error(f"❌ Failed to create battery: {str(e)}")
            return None

    def get_battery(self, token_id: int) -> Optional[Dict]:
        """Get battery by token ID"""
        try:
            if self.client:
                response = (
                    self.client.table("batteries")
                    .select("*")
                    .eq("token_id", token_id)
                    .single()
                    .execute()
                )
                return response.data if response.data else None
            else:
                return self._in_memory_db["batteries"].get(token_id)

        except Exception as e:
            logger.error(f"❌ Failed to get battery: {str(e)}")
            return None

    def update_battery(self, token_id: int, updates: Dict) -> Optional[Dict]:
        """Update battery record"""
        try:
            updates["updated_at"] = datetime.now().isoformat()

            if self.client:
                response = (
                    self.client.table("batteries")
                    .update(updates)
                    .eq("token_id", token_id)
                    .execute()
                )
                logger.info(f"✅ Battery updated: token_id={token_id}")
                return response.data[0] if response.data else updates
            else:
                if token_id in self._in_memory_db["batteries"]:
                    self._in_memory_db["batteries"][token_id].update(updates)
                    return self._in_memory_db["batteries"][token_id]
                return None

        except Exception as e:
            logger.error(f"❌ Failed to update battery: {str(e)}")
            return None

    def list_user_batteries(self, user_id: str, limit: int = 50) -> List[Dict]:
        """List all batteries owned by a user"""
        try:
            if self.client:
                response = (
                    self.client.table("batteries")
                    .select("*")
                    .eq("user_id", user_id)
                    .order("created_at", desc=True)
                    .limit(limit)
                    .execute()
                )
                return response.data if response.data else []
            else:
                return [
                    b
                    for b in self._in_memory_db["batteries"].values()
                    if b.get("user_id") == user_id
                ][:limit]

        except Exception as e:
            logger.error(f"❌ Failed to list user batteries: {str(e)}")
            return []

    # ============================================
    # RATE LIMITING
    # ============================================

    def check_rate_limit(self, user_id: str) -> tuple:
        """
        Check if user has exceeded rate limits
        
        Returns:
            (is_allowed: bool, mints_remaining: int, reset_time: str)
        """
        try:
            mints_today = 0
            reset_time = None

            if self.client:
                response = (
                    self.client.table("rate_limits")
                    .select("mints_today, reset_at")
                    .eq("user_id", user_id)
                    .single()
                    .execute()
                )

                if response.data:
                    mints_today = response.data.get("mints_today", 0)
                    reset_time = response.data.get("reset_at")

                    # Check if reset time has passed
                    reset_dt = datetime.fromisoformat(reset_time)
                    if datetime.now() > reset_dt:
                        # Reset counter
                        self._reset_rate_limit(user_id)
                        mints_today = 0
                        reset_time = (datetime.now() + timedelta(days=1)).isoformat()
            else:
                # In-memory fallback
                if user_id in self._in_memory_db["rate_limits"]:
                    limit_data = self._in_memory_db["rate_limits"][user_id]
                    mints_today = limit_data.get("mints_today", 0)
                    reset_time = limit_data.get("reset_at")

                    reset_dt = datetime.fromisoformat(reset_time)
                    if datetime.now() > reset_dt:
                        mints_today = 0
                        reset_time = (datetime.now() + timedelta(days=1)).isoformat()
                        self._in_memory_db["rate_limits"][user_id] = {
                            "mints_today": mints_today,
                            "reset_at": reset_time,
                        }

            mints_remaining = max(0, Config.RATE_LIMIT_MINTS_PER_USER - mints_today)
            is_allowed = mints_remaining > 0

            if not reset_time:
                reset_time = (datetime.now() + timedelta(days=1)).isoformat()

            logger.info(
                f"📊 Rate limit check: user={user_id}, remaining={mints_remaining}"
            )

            return is_allowed, mints_remaining, reset_time

        except Exception as e:
            logger.warning(f"⚠️  Rate limit check failed: {str(e)}, allowing request")
            return True, Config.RATE_LIMIT_MINTS_PER_USER, None

    def increment_rate_limit(self, user_id: str) -> bool:
        """Increment user's mint counter"""
        try:
            if self.client:
                # Try to update first
                response = (
                    self.client.table("rate_limits")
                    .update({"mints_today": self.client.table("rate_limits").select("mints_today").eq("user_id", user_id).single().execute().data["mints_today"] + 1})
                    .eq("user_id", user_id)
                    .execute()
                )

                if not response.data:
                    # If not found, create new record
                    reset_time = (datetime.now() + timedelta(days=1)).isoformat()
                    self.client.table("rate_limits").insert({
                        "user_id": user_id,
                        "mints_today": 1,
                        "reset_at": reset_time,
                    }).execute()

                logger.info(f"✅ Rate limit incremented for user={user_id}")
                return True
            else:
                # In-memory
                if user_id not in self._in_memory_db["rate_limits"]:
                    reset_time = (datetime.now() + timedelta(days=1)).isoformat()
                    self._in_memory_db["rate_limits"][user_id] = {
                        "mints_today": 1,
                        "reset_at": reset_time,
                    }
                else:
                    self._in_memory_db["rate_limits"][user_id]["mints_today"] += 1

                return True

        except Exception as e:
            logger.error(f"❌ Failed to increment rate limit: {str(e)}")
            return False

    def _reset_rate_limit(self, user_id: str) -> None:
        """Reset user's daily mint counter"""
        try:
            reset_time = (datetime.now() + timedelta(days=1)).isoformat()

            if self.client:
                self.client.table("rate_limits").update({
                    "mints_today": 0,
                    "reset_at": reset_time,
                }).eq("user_id", user_id).execute()
            else:
                self._in_memory_db["rate_limits"][user_id] = {
                    "mints_today": 0,
                    "reset_at": reset_time,
                }

            logger.info(f"✅ Rate limit reset for user={user_id}")

        except Exception as e:
            logger.warning(f"⚠️  Failed to reset rate limit: {str(e)}")

    # ============================================
    # RETRY QUEUE (for failed transactions)
    # ============================================

    def queue_failed_mint(
        self,
        user_id: str,
        serial_number: str,
        photo_cid: str,
        metadata_cid: str,
        error_message: str,
    ) -> bool:
        """
        Queue a failed mint for retry
        
        Args:
            user_id: User who initiated the mint
            serial_number: Battery serial number
            photo_cid: IPFS photo CID
            metadata_cid: IPFS metadata CID
            error_message: Error that occurred
            
        Returns:
            True on success
        """
        try:
            retry_data = {
                "user_id": user_id,
                "serial_number": serial_number,
                "photo_cid": photo_cid,
                "metadata_cid": metadata_cid,
                "error_message": error_message,
                "retry_count": 0,
                "status": "pending",
                "created_at": datetime.now().isoformat(),
                "next_retry_at": (datetime.now() + timedelta(minutes=5)).isoformat(),
            }

            if self.client:
                self.client.table("retry_queue").insert(retry_data).execute()
            else:
                self._in_memory_db["retry_queue"].append(retry_data)

            logger.info(f"📋 Queued failed mint for retry: {serial_number[:20]}...")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to queue mint for retry: {str(e)}")
            return False

    def get_pending_retries(self, limit: int = 10) -> List[Dict]:
        """Get pending retry tasks"""
        try:
            if self.client:
                response = (
                    self.client.table("retry_queue")
                    .select("*")
                    .eq("status", "pending")
                    .lte("next_retry_at", datetime.now().isoformat())
                    .lt("retry_count", 5)
                    .limit(limit)
                    .execute()
                )
                return response.data if response.data else []
            else:
                return [
                    r
                    for r in self._in_memory_db["retry_queue"]
                    if r["status"] == "pending"
                    and datetime.fromisoformat(r["next_retry_at"]) <= datetime.now()
                    and r["retry_count"] < 5
                ][:limit]

        except Exception as e:
            logger.error(f"❌ Failed to get pending retries: {str(e)}")
            return []


# Singleton instance
_supabase_client = None


def get_supabase_client():
    """Get or create Supabase client singleton"""
    global _supabase_client
    if _supabase_client is None:
        _supabase_client = SupabaseClient()
    return _supabase_client
