from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import PlainTextResponse, JSONResponse
import json
import os
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="WhatsApp Webhook",
    description="WhatsApp Business API Webhook",
    version="1.0.0"
)

# Get environment variables
WEBHOOK_VERIFY_TOKEN = os.getenv("WEBHOOK_VERIFY_TOKEN", "default_token")
APP_SECRET = os.getenv("APP_SECRET", "")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN", "")

logger.info(f"Starting app with verify token: {WEBHOOK_VERIFY_TOKEN[:10]}...")

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "status": "running",
        "message": "WhatsApp Webhook Server is running!",
        "timestamp": datetime.utcnow().isoformat(),
        "endpoints": {
            "health": "/health",
            "webhook": "/webhook",
            "docs": "/docs"
        }
    }

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "environment": {
            "has_verify_token": bool(WEBHOOK_VERIFY_TOKEN and WEBHOOK_VERIFY_TOKEN != "default_token"),
            "has_app_secret": bool(APP_SECRET),
            "has_access_token": bool(ACCESS_TOKEN)
        }
    }

@app.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge")
):
    """Webhook verification for WhatsApp"""
    logger.info(f"Webhook verification request - Mode: {hub_mode}")
    
    if hub_mode == "subscribe" and hub_verify_token == WEBHOOK_VERIFY_TOKEN:
        logger.info("✅ Webhook verified successfully!")
        return PlainTextResponse(hub_challenge)
    else:
        logger.error(f"❌ Webhook verification failed - Token mismatch")
        raise HTTPException(status_code=403, detail="Verification failed")

@app.post("/webhook")
async def handle_webhook(request: Request):
    """Handle incoming WhatsApp webhooks"""
    try:
        body = await request.body()
        webhook_data = json.loads(body.decode())
        
        logger.info("📨 Received webhook data")
        logger.info(json.dumps(webhook_data, indent=2))
        
        # Basic webhook processing
        if webhook_data.get("object") == "whatsapp_business_account":
            
            # Process entries
            for entry in webhook_data.get("entry", []):
                for change in entry.get("changes", []):
                    if change.get("field") == "messages":
                        value = change.get("value", {})
                        
                        # Handle messages
                        messages = value.get("messages", [])
                        for message in messages:
                            sender = message.get("from")
                            msg_type = message.get("type")
                            msg_id = message.get("id")
                            
                            logger.info(f"💬 Message from {sender}: type={msg_type}, id={msg_id}")
                            
                            if msg_type == "text":
                                text = message.get("text", {}).get("body", "")
                                logger.info(f"📝 Text: {text}")
                        
                        # Handle status updates
                        statuses = value.get("statuses", [])
                        for status in statuses:
                            logger.info(f"📊 Status update: {status.get('status')} for message {status.get('id')}")
            
            return JSONResponse({"status": "success"})
        
        else:
            logger.warning(f"Unknown webhook object: {webhook_data.get('object')}")
            return JSONResponse({"status": "ignored"})
            
    except json.JSONDecodeError:
        logger.error("❌ Invalid JSON received")
        raise HTTPException(status_code=400, detail="Invalid JSON")
    except Exception as e:
        logger.error(f"❌ Error processing webhook: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

# Add error handler
@app.exception_handler(404)
async def not_found_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=404,
        content={"error": "Not found", "path": str(request.url)}
    )

@app.exception_handler(500)
async def internal_error_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error"}
    )

# This is CRUCIAL for Railway
if __name__ == "__main__":
    import uvicorn
    
    # Railway sets the PORT environment variable
    port = int(os.getenv("PORT", 8000))
    
    logger.info(f"🚀 Starting server on port {port}")
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        log_level="info"
    )