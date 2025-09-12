from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import PlainTextResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import json
import os
import logging
import sys
import aiohttp
from datetime import datetime
from typing import Dict, Any
from urllib.parse import quote  # For URL encoding domain

# Configure detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="WhatsApp Webhook",
    description="WhatsApp Business API Webhook with .ke Domain Bot",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,https://your-frontend-domain.com").split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

# Debug environment variables loading
def debug_environment():
    """Debug environment variables and system info"""
    logger.info("🔍 DEBUGGING ENVIRONMENT VARIABLES")
    logger.info("=" * 50)
    
    logger.info(f"Python version: {sys.version}")
    logger.info(f"Current working directory: {os.getcwd()}")
    logger.info(f"Environment variables count: {len(os.environ)}")
    
    env_vars = [
        "WEBHOOK_VERIFY_TOKEN",
        "APP_SECRET",
        "ACCESS_TOKEN",
        "PHONE_NUMBER_ID",
        "VERSION",
        "DOMAIN_CHECK_URL",
        "ALLOWED_ORIGINS",
        "PORT",
        "RAILWAY_ENVIRONMENT",
        "RAILWAY_PROJECT_ID",
        "RAILWAY_SERVICE_ID"
    ]
    
    logger.info("\n📋 ENVIRONMENT VARIABLES:")
    for var in env_vars:
        value = os.getenv(var)
        if value:
            if var in ["APP_SECRET", "ACCESS_TOKEN"]:
                masked_value = f"{value[:10]}{'*' * (len(value) - 10)}" if len(value) > 10 else "*" * len(value)
                logger.info(f"✅ {var}: {masked_value} (length: {len(value)})")
            else:
                logger.info(f"✅ {var}: {value}")
        else:
            logger.warning(f"❌ {var}: NOT SET")
    
    logger.info("\n🌍 ALL ENVIRONMENT VARIABLES:")
    for key, value in sorted(os.environ.items()):
        if any(sensitive in key.upper() for sensitive in ['TOKEN', 'SECRET', 'KEY', 'PASSWORD']):
            masked_value = f"{value[:5]}***" if len(value) > 5 else "***"
            logger.info(f"  {key}: {masked_value}")
        else:
            logger.info(f"  {key}: {value}")
    
    logger.info("=" * 50)

# Call debug function at startup
debug_environment()

# Get environment variables with better error handling
def get_env_var(var_name: str, default: str = None, required: bool = False) -> str:
    """Get environment variable with detailed logging"""
    value = os.getenv(var_name, default)
    
    if required and not value:
        logger.error(f"❌ CRITICAL: Required environment variable {var_name} is not set!")
        raise ValueError(f"Required environment variable {var_name} is missing")
    
    if value:
        if var_name in ["APP_SECRET", "ACCESS_TOKEN"]:
            logger.info(f"✅ {var_name}: Set (length: {len(value)})")
        else:
            logger.info(f"✅ {var_name}: {value}")
    else:
        logger.warning(f"⚠️ {var_name}: Using default value '{default}'")
    
    return value

# Load environment variables
try:
    WEBHOOK_VERIFY_TOKEN = get_env_var("WEBHOOK_VERIFY_TOKEN", "default_token")
    APP_SECRET = get_env_var("APP_SECRET", "")
    ACCESS_TOKEN = get_env_var("ACCESS_TOKEN", "")
    PHONE_NUMBER_ID = get_env_var("PHONE_NUMBER_ID", None, required=False)
    VERSION = get_env_var("VERSION", "v19.0")
    DOMAIN_CHECK_URL = get_env_var("DOMAIN_CHECK_URL", "https://api.digikenya.co.ke/api/v1/domains/availability/check")
    PORT = int(get_env_var("PORT", "8080"))  # Match Railway default
    ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,https://your-frontend-domain.com")
    
    logger.info(f"🚀 Configuration loaded successfully")
    logger.info(f"   - Verify Token: {WEBHOOK_VERIFY_TOKEN[:10]}...")
    logger.info(f"   - App Secret: {'SET' if APP_SECRET else 'NOT SET'}")
    logger.info(f"   - Access Token: {'SET' if ACCESS_TOKEN else 'NOT SET'}")
    logger.info(f"   - Phone Number ID: {PHONE_NUMBER_ID or 'NOT SET'}")
    logger.info(f"   - API Version: {VERSION}")
    logger.info(f"   - Domain Check URL: {DOMAIN_CHECK_URL}")
    logger.info(f"   - Allowed Origins: {ALLOWED_ORIGINS}")
    logger.info(f"   - Port: {PORT}")
    
except Exception as e:
    logger.error(f"❌ Failed to load configuration: {e}")
    raise

@app.on_event("startup")
async def startup_event():
    """Log startup information"""
    logger.info("🎉 APPLICATION STARTING UP")
    logger.info(f"FastAPI app starting at {datetime.utcnow().isoformat()}")
    debug_environment()

# ... (rest of your endpoints: /, /debug, /test-webhook, /webhook GET remain unchanged)

@app.post("/webhook")
async def handle_webhook(request: Request):
    """Handle incoming WhatsApp webhooks with domain bot logic"""
    logger.info("📨 INCOMING WEBHOOK")
    
    try:
        logger.info("📋 Request Headers:")
        for header_name, header_value in request.headers.items():
            logger.info(f"   {header_name}: {header_value}")
        
        body = await request.body()
        logger.info(f"📦 Raw body length: {len(body)} bytes")
        
        if not body:
            logger.error("❌ Empty request body")
            raise HTTPException(status_code=400, detail="Empty request body")
        
        try:
            webhook_data = json.loads(body.decode())
            logger.info("✅ JSON parsed successfully")
        except json.JSONDecodeError as e:
            logger.error(f"❌ JSON decode error: {e}")
            logger.error(f"Raw body: {body.decode()[:1000]}...")
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")
        
        logger.info("📨 Webhook Data Structure:")
        logger.info(json.dumps(webhook_data, indent=2))
        
        if not isinstance(webhook_data, dict):
            logger.error("❌ Webhook data is not a dictionary")
            raise HTTPException(status_code=400, detail="Webhook data must be a JSON object")
        
        webhook_object = webhook_data.get("object")
        logger.info(f"🎯 Webhook object: {webhook_object}")
        
        if webhook_object == "whatsapp_business_account":
            logger.info("✅ WhatsApp Business Account webhook detected")
            
            entries = webhook_data.get("entry", [])
            logger.info(f"📋 Processing {len(entries)} entries")
            
            for i, entry in enumerate(entries):
                logger.info(f"🔍 Processing entry {i + 1}/{len(entries)}")
                logger.info(f"   Entry ID: {entry.get('id')}")
                
                changes = entry.get("changes", [])
                logger.info(f"   Changes: {len(changes)}")
                
                for j, change in enumerate(changes):
                    logger.info(f"   🔄 Processing change {j + 1}/{len(changes)}")
                    field = change.get("field")
                    logger.info(f"      Field: {field}")
                    
                    if field == "messages":
                        value = change.get("value", {})
                        messages = value.get("messages", [])
                        logger.info(f"      📱 Messages: {len(messages)}")
                        
                        for k, message in enumerate(messages):
                            logger.info(f"         💬 Message {k + 1}/{len(messages)}")
                            sender = message.get("from")
                            msg_type = message.get("type")
                            msg_id = message.get("id")
                            timestamp = message.get("timestamp")
                            
                            logger.info(f"            From: {sender}")
                            logger.info(f"            Type: {msg_type}")
                            logger.info(f"            ID: {msg_id}")
                            logger.info(f"            Timestamp: {timestamp}")
                            
                            if msg_type == "text":
                                text_content = message.get("text", {})
                                text_body = text_content.get("body", "").strip().lower()
                                logger.info(f"            📝 Text: '{text_body}'")
                                
                                if text_body.endswith('.ke') or '.' not in text_body:
                                    domain_query = text_body if text_body.endswith('.ke') else f"{text_body}.ke"
                                    logger.info(f"🤖 Domain check requested: {domain_query}")
                                    
                                    # FIXED: Use GET with query params instead of POST
                                    domain_result = {"available": False, "error": "Unknown error"}
                                    try:
                                        params = {
                                            "domain": domain_query,
                                            "include_pricing": "true",
                                            "include_suggestions": "true"
                                        }
                                        async with aiohttp.ClientSession() as session:
                                            async with session.get(
                                                DOMAIN_CHECK_URL,
                                                params=params,
                                                timeout=aiohttp.ClientTimeout(total=10)
                                            ) as resp:
                                                if resp.status == 200:
                                                    api_response = await resp.json()
                                                    if api_response.get("success"):
                                                        domain_result = api_response.get("data", {})
                                                    else:
                                                        domain_result = {"error": api_response.get("error", "API error")}
                                                else:
                                                    error_text = await resp.text()
                                                    logger.error(f"Domain API failed: {resp.status} - {error_text[:500]}...")  # Log first 500 chars
                                                    domain_result = {"error": f"HTTP {resp.status}: {error_text[:100] if len(error_text) > 100 else error_text}"}
                                    except Exception as domain_error:
                                        logger.error(f"Domain check exception: {str(domain_error)}", exc_info=True)
                                        domain_result = {"error": "Service unavailable"}
                                    
                                    # Format reply
                                    if domain_result.get("error"):
                                        reply_text = f"❌ Sorry, couldn't check {domain_query} right now ({domain_result['error']}). Try again later!"
                                    elif domain_result.get("available", False):
                                        price = domain_result.get("price", domain_result.get("pricing", {}).get("first_year", "N/A"))
                                        reply_text = f"✅ {domain_query} is AVAILABLE!\n💰 First year: {price}\n\nReply 'register {domain_query}' to start registration (or check another)."
                                    else:
                                        suggestions = domain_result.get("suggestions", [])
                                        reply_text = f"❌ {domain_query} is NOT available.\n\n"
                                        if suggestions:
                                            reply_text += f"💡 Suggestions: {', '.join(suggestions[:3])}\n"
                                        reply_text += "Try another .ke domain!"
                                    
                                    if PHONE_NUMBER_ID and ACCESS_TOKEN:
                                        try:
                                            await send_whatsapp_reply(sender, reply_text, msg_id)
                                            logger.info(f"✅ Domain reply sent to {sender} for {domain_query}")
                                        except Exception as reply_error:
                                            logger.error(f"Failed to send domain reply: {str(reply_error)}", exc_info=True)
                                    else:
                                        logger.warning(f"⚠️ Skipping reply to {sender} - PHONE_NUMBER_ID or ACCESS_TOKEN missing")
                                else:
                                    help_text = (
                                        "Hi! I'm a .ke domain availability bot. "
                                        "Send me a domain like 'example.ke' (or just 'example') to check if it's available. "
                                        "Powered by DigiKenya."
                                    )
                                    if PHONE_NUMBER_ID and ACCESS_TOKEN:
                                        try:
                                            await send_whatsapp_reply(sender, help_text, msg_id)
                                            logger.info(f"ℹ️ Help reply sent to {sender}")
                                        except Exception as help_error:
                                            logger.error(f"Failed to send help reply: {str(help_error)}", exc_info=True)
                                    else:
                                        logger.warning(f"⚠️ Skipping help reply to {sender} - PHONE_NUMBER_ID or ACCESS_TOKEN missing")
                                
                            elif msg_type == "image":
                                image_info = message.get("image", {})
                                logger.info(f"            🖼️ Image ID: {image_info.get('id')}")
                            elif msg_type == "document":
                                doc_info = message.get("document", {})
                                logger.info(f"            📄 Document: {doc_info.get('filename')}")
                        
                        statuses = value.get("statuses", [])
                        logger.info(f"      📊 Statuses: {len(statuses)}")
                        for status in statuses:
                            logger.info(f"         Status: {status.get('status')} for message {status.get('id')}")
                    else:
                        logger.info(f"      ⏭️ Skipping field: {field}")
            
            logger.info("✅ Webhook processed successfully")
            return JSONResponse({"status": "success", "processed_entries": len(entries)})
        
        else:
            logger.warning(f"❓ Unknown webhook object: {webhook_object}")
            return JSONResponse({
                "status": "ignored",
                "reason": f"Unknown webhook object: {webhook_object}",
                "received_object": webhook_object
            })
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ UNEXPECTED ERROR processing webhook")
        logger.error(f"   Error type: {type(e).__name__}")
        logger.error(f"   Error message: {str(e)}")
        logger.exception("Full traceback:")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "Internal server error",
                "error_type": type(e).__name__,
                "error_message": str(e)
            }
        )

async def send_whatsapp_reply(to: str, message: str, replied_msg_id: str = None):
    """Send a reply message via WhatsApp Cloud API with graceful error handling"""
    if not ACCESS_TOKEN or not PHONE_NUMBER_ID:
        logger.error("❌ Cannot send reply - ACCESS_TOKEN or PHONE_NUMBER_ID missing")
        return
    
    url = f"https://graph.facebook.com/{VERSION}/{PHONE_NUMBER_ID}/messages"
    
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": message
        }
    }
    
    if replied_msg_id:
        payload["context"] = {"message_id": replied_msg_id}
    
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    reply_id = result.get('messages', [{}])[0].get('id')
                    logger.info(f"✅ WhatsApp reply sent to {to}: ID {reply_id}")
                else:
                    error_text = await resp.text()
                    logger.error(f"❌ Failed to send WhatsApp reply to {to}: {resp.status} - {error_text}")
                    if "unauthorized" in error_text.lower():
                        logger.error("🔑 ACCESS_TOKEN may be invalid/expired - regenerate in Meta Dashboard")
    except Exception as e:
        logger.error(f"❌ Exception sending WhatsApp reply to {to}: {str(e)}", exc_info=True)

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "1.0.0",
        "bot_features": {
            "domain_check": bool(DOMAIN_CHECK_URL),
            "whatsapp_replies": bool(ACCESS_TOKEN and PHONE_NUMBER_ID)
        }
    }

# ... (exception handlers remain unchanged)

if __name__ == "__main__":
    import uvicorn
    
    logger.info("🚀 STARTING SERVER")
    logger.info(f"   Port: {PORT}")
    logger.info(f"   Host: 0.0.0.0")
    logger.info(f"   Log Level: info")
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=PORT,
        log_level="info",
        access_log=True,
        reload=False
    )