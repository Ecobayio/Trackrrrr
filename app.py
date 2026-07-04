"""
EcoBay Battery Passport API - Main Flask Application
Endpoints: /api/battery/scan_and_list (Zero-Click Listing)
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from functools import wraps
import os
import logging
import sys

# Try to load .env (for local dev), but don't fail if missing (Replit Secrets)
try:
    from dotenv import load_dotenv
    if os.path.exists(".env"):
        load_dotenv()
except ImportError:
    pass

# Import config
from config import Config

# Validate config
try:
    Config.validate()
except ValueError as e:
    print(f"\n❌ Configuration Error:\n{str(e)}\n")
    sys.exit(1)

# Initialize Flask app
app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False

# Security: CORS
CORS(app, resources={
    r"/api/*": {
        "origins": Config.ALLOWED_ORIGINS,
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization", "X-API-Key"]
    }
})

# Security: Rate limiting
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)

# Security: Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================
# INITIALIZE WEB3 & CLIENTS
# ============================================

try:
    from utils.web3_client import get_web3_client
    from utils.pinata_client import get_pinata_client
    from utils.supabase_client import get_supabase_client
    
    # Warm up connections
    logger.info("🔌 Initializing blockchain client...")
    web3_client = get_web3_client()
    logger.info("🔌 Initializing IPFS client...")
    pinata_client = get_pinata_client()
    logger.info("🔌 Initializing database client...")
    supabase_client = get_supabase_client()
    
    logger.info("✅ All clients initialized successfully")
    
except Exception as e:
    logger.error(f"❌ Failed to initialize clients: {str(e)}")
    sys.exit(1)

# ============================================
# SECURITY: API KEY AUTHENTICATION
# ============================================

def require_api_key(f):
    """Decorator to require API key for endpoints"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get("X-API-Key")

        if not api_key or api_key != Config.API_KEY:
            logger.warning(f"Unauthorized API request from {get_remote_address()}")
            return jsonify({"error": "Unauthorized"}), 401

        return f(*args, **kwargs)
    return decorated_function

# ============================================
# IMPORT ROUTES
# ============================================

from routes.battery import battery_bp

app.register_blueprint(battery_bp, url_prefix="/api/battery")

# ============================================
# HEALTH CHECK ENDPOINTS
# ============================================

@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint (no auth required)"""
    try:
        # Check blockchain connection
        balance = web3_client.get_account_balance()
        
        return jsonify({
            "status": "ok",
            "service": "EcoBay Battery Passport API",
            "version": "1.0.0",
            "timestamp": __import__("datetime").datetime.now().isoformat(),
            "blockchain": {
                "network": "Polygon Amoy",
                "contract": Config.CONTRACT_ADDRESS,
                "deployer_balance": f"{balance:.4f} MATIC",
            },
            "config": Config.get_summary()
        }), 200
    except Exception as e:
        logger.error(f"❌ Health check failed: {str(e)}")
        return jsonify({
            "status": "degraded",
            "error": str(e)
        }), 503

@app.route("/", methods=["GET"])
def root():
    """
    Root endpoint - Serves API documentation or dashboard
    """
    from flask import render_template
    import os

    # Check if the standard dashboard template exists
    if os.path.exists("templates/index.html"):
        return render_template("index.html")

    # Fallback: Simple API status page
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>EcoBay Battery Passport API</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body { 
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                display: flex;
                justify-content: center;
                align-items: center;
                height: 100vh;
                margin: 0;
            }
            .card { 
                background: white;
                padding: 2.5rem;
                border-radius: 12px;
                box-shadow: 0 10px 40px rgba(0,0,0,0.2);
                text-align: center;
                max-width: 500px;
            }
            h1 { 
                color: #2c3e50;
                margin: 0 0 0.5rem 0;
                font-size: 2rem;
            }
            .tagline {
                color: #667eea;
                font-size: 0.95rem;
                margin-bottom: 2rem;
            }
            p { 
                color: #555;
                line-height: 1.6;
                margin: 1rem 0;
            }
            code {
                background: #f5f5f5;
                padding: 0.2rem 0.5rem;
                border-radius: 4px;
                font-family: 'Monaco', monospace;
            }
            .endpoint {
                background: #f8f9fa;
                padding: 1rem;
                border-radius: 8px;
                margin: 1rem 0;
                text-align: left;
                border-left: 4px solid #667eea;
            }
            .endpoint-title {
                font-weight: bold;
                color: #2c3e50;
                margin-bottom: 0.3rem;
            }
            .endpoint-desc {
                font-size: 0.9rem;
                color: #666;
            }
            .links {
                margin-top: 2rem;
                display: flex;
                gap: 1rem;
                justify-content: center;
            }
            a { 
                color: #667eea;
                text-decoration: none;
                padding: 0.5rem 1rem;
                border: 2px solid #667eea;
                border-radius: 6px;
                transition: all 0.3s;
            }
            a:hover {
                background: #667eea;
                color: white;
            }
            hr { 
                border: none;
                border-top: 1px solid #eee;
                margin: 2rem 0;
            }
            .small { 
                font-size: 0.85rem;
                color: #999;
            }
        </style>
    </head>
    <body>
        <div class="card">
            <h1>🌿 EcoBay</h1>
            <div class="tagline">Battery Digital Twin Registry</div>
            
            <p>Digital twin platform for EU-compliant EV battery lifecycle management.</p>
            
            <div class="endpoint">
                <div class="endpoint-title">POST /api/battery/scan_and_list</div>
                <div class="endpoint-desc">
                    Zero-Click Listing: Scan QR → Upload Photo → Mint NFT → List
                </div>
            </div>
            
            <div class="endpoint">
                <div class="endpoint-title">GET /api/battery/:token_id</div>
                <div class="endpoint-desc">
                    Retrieve battery data by token ID
                </div>
            </div>
            
            <div class="endpoint">
                <div class="endpoint-title">GET /api/battery/stats</div>
                <div class="endpoint-desc">
                    Global statistics and system health
                </div>
            </div>
            
            <hr>
            
            <div class="links">
                <a href="/health">Health Check</a>
                <a href="/api/battery/stats">Stats</a>
            </div>
            
            <p class="small">
                📚 <a href="https://github.com/Ecobayio/Trackrrrr" style="color: #999; border: none;">GitHub</a> •
                🔗 Contract: <code>0xA09D...</code>
            </p>
        </div>
    </body>
    </html>
    """, 200

# ============================================
# ERROR HANDLERS
# ============================================

@app.errorhandler(400)
def bad_request(error):
    logger.warning(f"Bad request: {str(error)}")
    return jsonify({"error": "Bad request"}), 400

@app.errorhandler(401)
def unauthorized(error):
    return jsonify({"error": "Unauthorized"}), 401

@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({"error": "Rate limit exceeded"}), 429

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {str(error)}")
    return jsonify({"error": "Internal server error"}), 500

# ============================================
# LOGGING MIDDLEWARE
# ============================================

@app.before_request
def log_request():
    """Log incoming requests"""
    logger.info(f"{request.method} {request.path} from {request.remote_addr}")

@app.after_request
def log_response(response):
    """Log outgoing responses"""
    logger.info(f"{response.status_code} {request.method} {request.path}")
    return response

# ============================================
# MAIN
# ============================================

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))

    logger.info(f"🚀 Starting EcoBay API (Env: {Config.FLASK_ENV})")
    logger.info(f"🔗 Contract: {Config.CONTRACT_ADDRESS}")
    logger.info(f"⛓️  Network: Polygon Amoy")
    
    app.run(
        host="0.0.0.0",
        port=port,
        debug=Config.DEBUG,
        threaded=True
    )
