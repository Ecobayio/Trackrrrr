"""
Battery Passport Routes
Handles minting, querying, listing, and verification of battery digital twins
Main endpoint: /api/scan_and_list (Zero-Click Listing)
"""

from flask import Blueprint, request, jsonify
from functools import wraps
import logging
from datetime import datetime
import hashlib

from config import Config
from utils.web3_client import get_web3_client
from utils.pinata_client import get_pinata_client
from utils.supabase_client import get_supabase_client

# Setup
battery_bp = Blueprint("battery", __name__)
logger = logging.getLogger(__name__)

# ============================================
# AUTHENTICATION DECORATOR
# ============================================


def require_api_key(f):
    """Require API key for protected endpoints"""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get("X-API-Key")
        if not api_key or api_key != Config.API_KEY:
            logger.warning(f"Unauthorized request: {request.remote_addr}")
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)

    return decorated_function


# ============================================
# MAIN ENDPOINT: SCAN & LIST (Zero-Click Listing)
# ============================================


@battery_bp.route("/scan_and_list", methods=["POST"])
@require_api_key
def scan_and_list():
    """
    Zero-Click Listing: Scan QR → Upload Photo → Mint NFT → Save Metadata
    
    Request body:
    {
        "user_id": "user-uuid-from-supabase",
        "serial_number": "TESLA-2024-Q3-000001",
        "photo_file": <binary photo data>,
        "manufacturer": "Tesla",
        "model": "4680",
        "capacity_wh": 25000,
        "chemistry": "NCA",
        "metadata": { ... optional extra fields }
    }
    
    Returns:
    {
        "success": true,
        "token_id": 1,
        "tx_hash": "0x...",
        "listing": {
            "id": "listing-id",
            "token_id": 1,
            "serial_hash": "0x...",
            "owner": "0x...",
            "status": "draft",
            "ipfs_urls": {
                "photo": "https://gateway.pinata.cloud/ipfs/Qm...",
                "metadata": "https://gateway.pinata.cloud/ipfs/Qm..."
            }
        }
    }
    """
    try:
        # Initialize clients
        web3_client = get_web3_client()
        pinata_client = get_pinata_client()
        supabase_client = get_supabase_client()

        # Get user ID from request
        user_id = request.form.get("user_id") or request.json.get("user_id")
        if not user_id:
            return jsonify({"error": "user_id is required"}), 400

        # Check rate limit
        is_allowed, mints_remaining, reset_time = supabase_client.check_rate_limit(user_id)
        if not is_allowed:
            logger.warning(f"Rate limit exceeded for user {user_id}")
            return (
                jsonify({
                    "error": "Daily mint limit exceeded",
                    "limit": Config.RATE_LIMIT_MINTS_PER_USER,
                    "reset_time": reset_time,
                }),
                429,
            )

        # Extract battery data
        serial_number = request.form.get("serial_number") or request.json.get("serial_number")
        manufacturer = request.form.get("manufacturer") or request.json.get("manufacturer")
        model = request.form.get("model") or request.json.get("model")
        capacity_wh = request.form.get("capacity_wh") or request.json.get("capacity_wh")
        chemistry = request.form.get("chemistry") or request.json.get("chemistry")
        metadata_extra = request.json.get("metadata", {}) if request.is_json else {}

        # Validate required fields
        if not serial_number:
            return jsonify({"error": "serial_number is required"}), 400

        logger.info(f"📋 Starting scan_and_list for {serial_number[:20]}... (user: {user_id})")

        # ============================================
        # STEP 1: Upload Photo to Pinata
        # ============================================
        logger.info("📤 Step 1: Uploading photo to Pinata...")

        if "photo" not in request.files:
            return jsonify({"error": "photo file is required"}), 400

        photo_file = request.files["photo"]
        if photo_file.filename == "":
            return jsonify({"error": "No photo selected"}), 400

        # Save temp file and upload
        import tempfile
        import os

        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
            photo_file.save(tmp.name)
            photo_cid = pinata_client.upload_file(tmp.name, filename=f"{serial_number}_photo.jpg")
            os.unlink(tmp.name)

        if not photo_cid:
            logger.error("❌ Photo upload failed")
            return jsonify({"error": "Failed to upload photo to IPFS"}), 500

        logger.info(f"✅ Photo uploaded: {photo_cid}")

        # ============================================
        # STEP 2: Create Metadata JSON and Upload
        # ============================================
        logger.info("📝 Step 2: Creating and uploading metadata...")

        metadata_json = pinata_client.create_metadata_json(
            serial_number=serial_number,
            photo_cid=photo_cid,
            manufacturer=manufacturer,
            model=model,
            capacity_wh=int(capacity_wh) if capacity_wh else None,
            chemistry=chemistry,
            **metadata_extra,
        )

        metadata_cid = pinata_client.upload_json(
            metadata_json, filename=f"{serial_number}_metadata.json"
        )

        if not metadata_cid:
            logger.error("❌ Metadata upload failed")
            # Queue for retry
            supabase_client.queue_failed_mint(
                user_id, serial_number, photo_cid, None, "Metadata upload failed"
            )
            return jsonify({"error": "Failed to upload metadata to IPFS"}), 500

        logger.info(f"✅ Metadata uploaded: {metadata_cid}")

        # ============================================
        # STEP 3: Mint NFT on Blockchain
        # ============================================
        logger.info("⛓️  Step 3: Minting NFT on blockchain...")

        try:
            tx_hash, token_id = web3_client.mint_battery(serial_number, metadata_cid)

            if not tx_hash:
                logger.error("❌ Mint transaction failed")
                supabase_client.queue_failed_mint(
                    user_id, serial_number, photo_cid, metadata_cid, "Mint transaction failed"
                )
                return jsonify({"error": "Failed to mint on blockchain"}), 500

            logger.info(f"✅ NFT minted: tx_hash={tx_hash}, token_id={token_id}")

        except Exception as e:
            logger.error(f"❌ Mint error: {str(e)}")
            # Queue for retry
            supabase_client.queue_failed_mint(
                user_id, serial_number, photo_cid, metadata_cid, str(e)
            )
            return jsonify({"error": f"Failed to mint: {str(e)}"}), 500

        # ============================================
        # STEP 4: Save to Supabase
        # ============================================
        logger.info("💾 Step 4: Saving to Supabase...")

        # Get block number for record
        try:
            block_number = web3_client.w3.eth.block_number
        except:
            block_number = 0

        # Calculate serial hash (same as contract)
        serial_hash = hashlib.sha256(serial_number.encode()).hexdigest()

        battery_record = supabase_client.create_battery(
            token_id=token_id or 0,
            serial_hash=serial_hash,
            user_id=user_id,
            metadata_cid=metadata_cid,
            photo_cid=photo_cid,
            tx_hash=tx_hash,
            block_number=block_number,
        )

        if not battery_record:
            logger.warning("⚠️  Supabase save failed, but blockchain mint succeeded")
            # Queue for retry/sync
            supabase_client.queue_failed_mint(
                user_id, serial_number, photo_cid, metadata_cid, "Supabase save failed"
            )
            # Return partial success (NFT minted, DB sync will retry)
            return (
                jsonify({
                    "success": True,
                    "partial": True,
                    "warning": "NFT minted but database sync failed. Will retry automatically.",
                    "token_id": token_id,
                    "tx_hash": tx_hash,
                    "listing": {
                        "token_id": token_id,
                        "status": "draft",
                        "ipfs_urls": {
                            "photo": pinata_client.get_file_url(photo_cid),
                            "metadata": pinata_client.get_file_url(metadata_cid),
                        },
                    },
                }),
                201,
            )

        logger.info(f"✅ Battery record created in Supabase")

        # ============================================
        # STEP 5: Increment Rate Limit
        # ============================================
        supabase_client.increment_rate_limit(user_id)

        # ============================================
        # SUCCESS RESPONSE
        # ============================================
        logger.info(f"✅ Scan & List complete: {serial_number}")

        return (
            jsonify({
                "success": True,
                "token_id": token_id,
                "tx_hash": tx_hash,
                "listing": {
                    "token_id": token_id,
                    "serial_hash": f"0x{serial_hash}",
                    "owner": web3_client.deployer_address,
                    "status": "draft",
                    "user_id": user_id,
                    "mints_remaining": mints_remaining - 1,
                    "ipfs_urls": {
                        "photo": pinata_client.get_file_url(photo_cid),
                        "metadata": pinata_client.get_file_url(metadata_cid),
                    },
                    "created_at": datetime.now().isoformat(),
                },
            }),
            201,
        )

    except Exception as e:
        logger.error(f"❌ Scan & List failed: {str(e)}")
        return jsonify({"error": str(e)}), 500


# ============================================
# ENDPOINT: GET BATTERY BY TOKEN ID
# ============================================


@battery_bp.route("/<int:token_id>", methods=["GET"])
def get_battery(token_id):
    """Get battery data by token ID"""
    try:
        web3_client = get_web3_client()
        pinata_client = get_pinata_client()
        supabase_client = get_supabase_client()

        logger.info(f"📖 Getting battery: token_id={token_id}")

        # Get from blockchain
        owner, metadata_cid, serial_hash, mint_timestamp = web3_client.get_battery_info(
            token_id
        )

        if not owner:
            return jsonify({"error": "Battery not found"}), 404

        # Get from Supabase
        battery_record = supabase_client.get_battery(token_id)

        # Get from IPFS
        metadata = pinata_client.get_json(metadata_cid)

        if not metadata:
            return jsonify({"error": "Battery metadata not found on IPFS"}), 404

        return (
            jsonify({
                "token_id": token_id,
                "owner": owner,
                "serial_hash": serial_hash,
                "mint_timestamp": mint_timestamp,
                "metadata": metadata,
                "database_record": battery_record,
                "verification_status": battery_record.get("verification_status") if battery_record else None,
                "ipfs_urls": {
                    "metadata": pinata_client.get_file_url(metadata_cid),
                    "photo": pinata_client.get_file_url(metadata.get("photoCid")) if metadata.get("photoCid") else None,
                },
            }),
            200,
        )

    except Exception as e:
        logger.error(f"❌ Error retrieving battery: {str(e)}")
        return jsonify({"error": str(e)}), 500


# ============================================
# ENDPOINT: LIST USER BATTERIES
# ============================================


@battery_bp.route("/user/<user_id>", methods=["GET"])
@require_api_key
def list_user_batteries(user_id):
    """List all batteries owned by a user"""
    try:
        supabase_client = get_supabase_client()
        pinata_client = get_pinata_client()

        logger.info(f"📋 Listing batteries for user: {user_id}")

        limit = request.args.get("limit", 50, type=int)
        batteries = supabase_client.list_user_batteries(user_id, limit=limit)

        return (
            jsonify({
                "user_id": user_id,
                "count": len(batteries),
                "batteries": batteries,
            }),
            200,
        )

    except Exception as e:
        logger.error(f"❌ Error listing batteries: {str(e)}")
        return jsonify({"error": str(e)}), 500


# ============================================
# ENDPOINT: STATISTICS
# ============================================


@battery_bp.route("/stats", methods=["GET"])
def get_stats():
    """Get global battery statistics"""
    try:
        web3_client = get_web3_client()

        total = web3_client.get_total_minted()
        balance = web3_client.get_account_balance()

        return (
            jsonify({
                "total_batteries_minted": total,
                "contract_address": Config.CONTRACT_ADDRESS,
                "deployer_address": Config.DEPLOYER_ADDRESS,
                "deployer_balance_matic": balance,
                "network": "Polygon Amoy",
                "rate_limits": {
                    "per_user_per_day": Config.RATE_LIMIT_MINTS_PER_USER,
                    "global_per_day": Config.RATE_LIMIT_MINTS_PER_DAY_GLOBAL,
                },
            }),
            200,
        )

    except Exception as e:
        logger.error(f"❌ Error getting stats: {str(e)}")
        return jsonify({"error": str(e)}), 500


# ============================================
# ENDPOINT: HEALTH CHECK
# ============================================


@battery_bp.route("/health", methods=["GET"])
def battery_health():
    """Health check for battery service"""
    try:
        web3_client = get_web3_client()
        pinata_client = get_pinata_client()

        return (
            jsonify({
                "status": "healthy",
                "web3": "connected",
                "contract": Config.CONTRACT_ADDRESS,
                "pinata": "configured" if Config.PINATA_API_KEY else "not configured",
            }),
            200,
        )

    except Exception as e:
        logger.error(f"❌ Health check failed: {str(e)}")
        return jsonify({"status": "unhealthy", "error": str(e)}), 503
