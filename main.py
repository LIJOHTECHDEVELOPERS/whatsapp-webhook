from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import PlainTextResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import json
import os
import logging
import sys
import aiohttp
from datetime import datetime
from typing import Dict, Any, Optional
from urllib.parse import quote, urlencode
import re

# Configure detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="WhatsApp Webhook",
    description="WhatsApp Business API Webhook with .ke Domain Bot",
    version="1.0.0"
)

# Add CORS middleware
allowed_origins = [origin.strip() for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,https://digikenya.co.ke").split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

# Supported .ke extensions
SUPPORTED_EXTENSIONS = [
    ".co.ke", ".or.ke", ".ac.ke", ".go.ke", ".ne.ke", ".sc.ke",
    ".ke", ".me.ke", ".info.ke"
]

# In-memory user state store (replace with Redis/DB in production)
user_states: Dict[str, Dict[str, Any]] = {}

# Debug environment variables
def debug_environment():
    logger.info("🔍 DEBUGGING ENVIRONMENT VARIABLES")
    logger.info("=" * 50)
    logger.info(f"Python version: {sys.version}")
    logger.info(f"Current working directory: {os.getcwd()}")
    logger.info(f"Environment variables count: {len(os.environ)}")
    
    env_vars = [
        "WEBHOOK_VERIFY_TOKEN", "APP_SECRET", "ACCESS_TOKEN", "PHONE_NUMBER_ID",
        "VERSION", "DOMAIN_CHECK_URL", "ALLOWED_ORIGINS", "PORT"
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

# Get environment variable with error handling
def get_env_var(var_name: str, default: str = None, required: bool = False) -> str:
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
debug_environment()
try:
    WEBHOOK_VERIFY_TOKEN = get_env_var("WEBHOOK_VERIFY_TOKEN", "default_token")
    APP_SECRET = get_env_var("APP_SECRET", "")
    ACCESS_TOKEN = get_env_var("ACCESS_TOKEN", "")
    PHONE_NUMBER_ID = get_env_var("PHONE_NUMBER_ID", None, required=False)
    VERSION = get_env_var("VERSION", "v19.0")
    DOMAIN_CHECK_URL = get_env_var("DOMAIN_CHECK_URL", "https://api.digikenya.co.ke/api/v1/domains/availability/check")
    PORT = int(get_env_var("PORT", "8080"))
    
    logger.info(f"🚀 Configuration loaded successfully")
    logger.info(f"   - Verify Token: {WEBHOOK_VERIFY_TOKEN[:10]}...")
    logger.info(f"   - App Secret: {'SET' if APP_SECRET else 'NOT SET'}")
    logger.info(f"   - Access Token: {'SET' if ACCESS_TOKEN else 'NOT SET'}")
    logger.info(f"   - Phone Number ID: {PHONE_NUMBER_ID or 'NOT SET'}")
    logger.info(f"   - API Version: {VERSION}")
    logger.info(f"   - Domain Check URL: {DOMAIN_CHECK_URL}")
    logger.info(f"   - Port: {PORT}")
except Exception as e:
    logger.error(f"❌ Failed to load configuration: {e}")
    raise

@app.on_event("startup")
async def startup_event():
    logger.info("🎉 APPLICATION STARTING UP")
    logger.info(f"FastAPI app starting at {datetime.utcnow().isoformat()}")

def get_user_state(user_phone: str) -> Dict[str, Any]:
    if user_phone not in user_states:
        user_states[user_phone] = {
            "step": "greeting",
            "last_domain": None,
            "selected_extension": None
        }
    return user_states[user_phone]

def update_user_state(user_phone: str, state: Dict[str, Any]):
    user_states[user_phone] = {**get_user_state(user_phone), **state}

def is_greeting(text: str) -> bool:
    greetings = ["hi", "hello", "hey", "hii", "helloo", "start", "begin"]
    return text.lower().strip() in greetings or len(text.strip()) < 3

async def send_interactive_reply(to: str, message: str, replied_msg_id: str = None, buttons: list = None):
    """Send interactive button reply via WhatsApp API"""
    if not ACCESS_TOKEN or not PHONE_NUMBER_ID:
        logger.error("❌ Cannot send reply - ACCESS_TOKEN or PHONE_NUMBER_ID missing")
        return
    
    url = f"https://graph.facebook.com/{VERSION}/{PHONE_NUMBER_ID}/messages"
    
    # Fix button format for WhatsApp
    formatted_buttons = [
        {
            "type": "reply",
            "reply": {
                "id": btn["id"],
                "title": btn["title"][:20]  # WhatsApp limits title to 20 chars
            }
        } for btn in (buttons or [])
    ]
    
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {
                "text": message[:4096]  # WhatsApp text limit
            },
            "action": {
                "buttons": formatted_buttons
            }
        }
    }
    
    if replied_msg_id:
        payload["context"] = {"message_id": replied_msg_id}
    
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    
    logger.info(f"🔘 Sending interactive buttons: {json.dumps(formatted_buttons, indent=2)}")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    reply_id = result.get('messages', [{}])[0].get('id')
                    logger.info(f"✅ Interactive reply sent to {to}: ID {reply_id}")
                else:
                    error_text = await resp.text()
                    logger.error(f"❌ Failed to send interactive reply to {to}: {resp.status} - {error_text}")
                    raise Exception(f"WhatsApp API error: {error_text}")
    except Exception as e:
        logger.error(f"❌ Exception sending interactive reply to {to}: {str(e)}", exc_info=True)

async def send_template_reply(to: str, template_name: str, replied_msg_id: str = None):
    """Send a predefined WhatsApp message template"""
    if not ACCESS_TOKEN or not PHONE_NUMBER_ID:
        logger.error("❌ Cannot send template - ACCESS_TOKEN or PHONE_NUMBER_ID missing")
        return
    
    url = f"https://graph.facebook.com/{VERSION}/{PHONE_NUMBER_ID}/messages"
    
    # Template: Must be created in Meta Business Manager first
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": "en"},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": ", ".join(SUPPORTED_EXTENSIONS)}  # Pass extensions
                    ]
                },
                {
                    "type": "button",
                    "sub_type": "quick_reply",
                    "index": 0,
                    "parameters": [{"type": "payload", "payload": "search_domains"}]
                },
                {
                    "type": "button",
                    "sub_type": "quick_reply",
                    "index": 1,
                    "parameters": [{"type": "payload", "payload": "visit_website"}]
                }
            ]
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
                    logger.info(f"✅ Template reply sent to {to}: ID {reply_id}")
                else:
                    error_text = await resp.text()
                    logger.error(f"❌ Failed to send template reply to {to}: {resp.status} - {error_text}")
                    if "template not found" in error_text.lower():
                        logger.error("⚠️ Template not configured in Meta Business Manager")
    except Exception as e:
        logger.error(f"❌ Exception sending template reply to {to}: {str(e)}", exc_info=True)

async def send_whatsapp_reply(to: str, message: str, replied_msg_id: str = None):
    """Send a text reply via WhatsApp Cloud API"""
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
            "body": message[:4096]
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

@app.post("/webhook")
async def handle_webhook(request: Request):
    """Handle incoming WhatsApp webhooks with conversational domain bot logic"""
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
                            
                            # Handle interactive button responses
                            if msg_type == "interactive":
                                interactive = message.get("interactive", {})
                                if interactive.get("type") == "button_reply":
                                    reply_data = interactive.get("button_reply", {})
                                    payload = reply_data.get("id", "")
                                    logger.info(f"            🔘 Button clicked: {payload}")
                                    state = get_user_state(sender)
                                    if payload == "search_domains":
                                        update_user_state(sender, {"step": "searching"})
                                        reply_text = "🔍 Great! What domain would you like to check? (e.g., 'example' for .ke extensions)"
                                        await send_whatsapp_reply(sender, reply_text, msg_id)
                                        return JSONResponse({"status": "success", "processed_entries": len(entries)})
                                    elif payload == "visit_website":
                                        reply_text = "🌐 Visit our website to browse all services: https://digikenya.co.ke\n\nOr reply 'menu' to return to main options."
                                        await send_whatsapp_reply(sender, reply_text, msg_id)
                                        return JSONResponse({"status": "success", "processed_entries": len(entries)})
                                    elif payload.startswith("register_"):
                                        domain = payload.replace("register_", "")
                                        register_url = f"https://digikenya.co.ke/register?domain={quote(domain)}"
                                        reply_text = f"🛒 Ready to register {domain}? Click here: {register_url}\n\nOr reply 'menu' for more options."
                                        await send_whatsapp_reply(sender, reply_text, msg_id)
                                        update_user_state(sender, {"step": "greeting"})
                                        return JSONResponse({"status": "success", "processed_entries": len(entries)})
                                    else:
                                        logger.warning(f"Unknown button payload: {payload}")
                            
                            if msg_type == "text":
                                text_content = message.get("text", {})
                                text_body = text_content.get("body", "").strip().lower()
                                logger.info(f"            📝 Text: '{text_body}'")
                                
                                state = get_user_state(sender)
                                logger.info(f"            🗂️ User state: {state}")
                                
                                # Greeting or menu request
                                if is_greeting(text_body) or text_body == "menu":
                                    update_user_state(sender, {"step": "greeting"})
                                    extensions_list = "\n".join([f"• {ext}" for ext in SUPPORTED_EXTENSIONS])
                                    reply_text = (
                                        f"👋 Hi! Welcome to DigiKenya Domain Bot.\n\n"
                                        f"Available .ke extensions you can register:\n"
                                        f"{extensions_list}\n\n"
                                        f"What would you like to do?"
                                    )
                                    # Send interactive message with buttons
                                    await send_interactive_reply(sender, reply_text, msg_id, [
                                        {"id": "search_domains", "title": "🔍 Search Domains"},
                                        {"id": "visit_website", "title": "🌐 Visit Website"}
                                    ])
                                    return JSONResponse({"status": "success", "processed_entries": len(entries)})
                                
                                # Handle search step
                                if state.get("step") == "searching":
                                    domain_match = re.match(r'^([a-z0-9-]+\.?)(ke|co\.ke|or\.ke|ac\.ke|go\.ke|ne\.ke|sc\.ke|me\.ke|info\.ke)?$', text_body)
                                    if domain_match:
                                        domain_query = domain_match.group(1)
                                        if not domain_match.group(2):
                                            domain_query += ".ke"  # Default to .ke
                                        logger.info(f"🤖 Domain search for: {domain_query}")
                                        
                                        # Call domain API
                                        domain_result = {"available": False, "error": "Unknown error"}
                                        try:
                                            params = {
                                                "domain": quote(domain_query),
                                                "include_pricing": "true",
                                                "include_suggestions": "true"
                                            }
                                            api_url = f"{DOMAIN_CHECK_URL}?{urlencode(params)}"
                                            logger.info(f"🔗 Calling domain API: {api_url}")
                                            async with aiohttp.ClientSession() as session:
                                                async with session.get(
                                                    DOMAIN_CHECK_URL,
                                                    params=params,
                                                    timeout=aiohttp.ClientTimeout(total=10)
                                                ) as resp:
                                                    logger.info(f"📡 Domain API response status: {resp.status}")
                                                    if resp.status == 200:
                                                        api_response = await resp.json()
                                                        logger.info(f"📊 Domain API response preview: {json.dumps(api_response, indent=2)[:500]}...")
                                                        if api_response.get("success"):
                                                            domain_result = api_response.get("data", {})
                                                        else:
                                                            domain_result = {"error": api_response.get("error", "API error")}
                                                    else:
                                                        error_text = await resp.text()
                                                        logger.error(f"Domain API failed: {resp.status} - {error_text[:500]}...")
                                                        domain_result = {"error": f"HTTP {resp.status}: {error_text[:100] if len(error_text) > 100 else error_text}"}
                                        except Exception as domain_error:
                                            logger.error(f"Domain check exception: {str(domain_error)}", exc_info=True)
                                            domain_result = {"error": "Service unavailable"}
                                        
                                        # Format results with button for registration
                                        if domain_result.get("error"):
                                            reply_text = f"❌ Sorry, couldn't check {domain_query} right now ({domain_result['error']}). Reply 'menu' to start over."
                                            await send_whatsapp_reply(sender, reply_text, msg_id)
                                        elif domain_result.get("available", False):
                                            price = domain_result.get("price", domain_result.get("pricing", {}).get("first_year", "N/A"))
                                            reply_text = f"✅ {domain_query} is AVAILABLE!\n💰 First year: {price}\n\nReady to register?"
                                            await send_interactive_reply(sender, reply_text, msg_id, [
                                                {"id": f"register_{domain_query}", "title": "🛒 Register Now"}
                                            ])
                                            update_user_state(sender, {"step": "results", "last_domain": domain_query})
                                        else:
                                            suggestions = domain_result.get("suggestions", [])
                                            reply_text = f"❌ {domain_query} is NOT available.\n\n"
                                            if suggestions:
                                                sug_domains = [s.get("domain", "") for s in suggestions if isinstance(s, dict)][:3]
                                                if sug_domains:
                                                    reply_text += f"💡 Suggestions: {', '.join(sug_domains)}\n"
                                            reply_text += "Pick one to check or reply 'menu'."
                                            buttons = [{"id": f"register_{s.get('domain', '')}", "title": s.get("domain", "")[:20] if s.get("available") else "Taken"} for s in suggestions[:3] if s.get("available")]
                                            if buttons:
                                                await send_interactive_reply(sender, reply_text, msg_id, buttons)
                                            else:
                                                await send_whatsapp_reply(sender, reply_text, msg_id)
                                            update_user_state(sender, {"step": "results", "last_domain": domain_query})
                                        
                                        return JSONResponse({"status": "success", "processed_entries": len(entries)})
                                    else:
                                        reply_text = "❌ Invalid domain format. Try something like 'example' or 'example.ke'. Reply 'menu' for options."
                                        await send_whatsapp_reply(sender, reply_text, msg_id)
                                        return JSONResponse({"status": "success", "processed_entries": len(entries)})
                                
                                # Default: Show menu
                                reply_text = "👋 Hi! Reply 'menu' for main options or say 'hi' to start."
                                await send_whatsapp_reply(sender, reply_text, msg_id)
                                return JSONResponse({"status": "success", "processed_entries": len(entries)})
                                
                            elif msg_type == "image":
                                image_info = message.get("image", {})
                                logger.info(f"            🖼️ Image ID: {image_info.get('id')}")
                                reply_text = "📸 Thanks for the image! Reply 'menu' to continue with domain search."
                                await send_whatsapp_reply(sender, reply_text, msg_id)
                            elif msg_type == "document":
                                doc_info = message.get("document", {})
                                logger.info(f"            📄 Document: {doc_info.get('filename')}")
                                reply_text = "📄 Received your document! Reply 'menu' to continue with domain search."
                                await send_whatsapp_reply(sender, reply_text, msg_id)
                        
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

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "1.0.0",
        "bot_features": {
            "domain_check": bool(DOMAIN_CHECK_URL),
            "whatsapp_replies": bool(ACCESS_TOKEN and PHONE_NUMBER_ID),
            "interactive_buttons": True,
            "active_users": len(user_states)
        }
    }

@app.exception_handler(404)
async def not_found_handler(request: Request, exc: HTTPException):
    logger.warning(f"404 Not Found: {request.url}")
    return JSONResponse(
        status_code=404,
        content={
            "error": "Not found",
            "path": str(request.url),
            "method": request.method,
            "timestamp": datetime.utcnow().isoformat()
        }
    )

@app.exception_handler(500)
async def internal_error_handler(request: Request, exc: Exception):
    logger.error(f"500 Internal Server Error: {exc}")
    logger.exception("Full traceback:")
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "timestamp": datetime.utcnow().isoformat(),
            "error_type": type(exc).__name__
        }
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    logger.warning(f"HTTP {exc.status_code}: {exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.detail,
            "status_code": exc.status_code,
            "timestamp": datetime.utcnow().isoformat()
        }
    )

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