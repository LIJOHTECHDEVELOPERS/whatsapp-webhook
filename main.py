from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import PlainTextResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import json
import os
import logging
import sys
import aiohttp
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from urllib.parse import quote, urlencode
import re
import asyncio

# Configure detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="Smart WhatsApp Domain Bot",
    description="Intelligent WhatsApp Business API Bot for .ke Domain Search with Message Status Tracking",
    version="2.1.0"
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

# Enhanced .ke extensions with descriptions
DOMAIN_EXTENSIONS = {
    ".ke": " General Kenya domains",
    ".co.ke": " Commercial organizations",
    ".or.ke": " Non-profit organizations", 
    ".ac.ke": " Academic institutions",
    ".go.ke": " Government entities",
    ".ne.ke": " Network providers",
    ".sc.ke": " Scientific organizations",
    ".me.ke": " Personal domains",
    ".info.ke": " Information sites"
}

# Enhanced user state management
user_states: Dict[str, Dict[str, Any]] = {}
domain_cache: Dict[str, Dict[str, Any]] = {}

# MESSAGE STATUS TRACKING - New feature
message_statuses: Dict[str, Dict[str, Any]] = {}  # Store message delivery statuses
MAX_STATUS_RECORDS = 1000  # Limit to prevent memory overflow

# Smart domain parsing patterns
DOMAIN_PATTERNS = {
    'full_domain': re.compile(r'^([a-z0-9-]+)\.(ke|co\.ke|or\.ke|ac\.ke|go\.ke|ne\.ke|sc\.ke|me\.ke|info\.ke)$', re.IGNORECASE),
    'base_domain': re.compile(r'^([a-z0-9-]+)$', re.IGNORECASE),
    'partial_extension': re.compile(r'^([a-z0-9-]+)\.(co|or|ac|go|ne|sc|me|info)$', re.IGNORECASE)
}

def get_env_var(var_name: str, default: str = None, required: bool = False) -> str:
    """Get environment variable with proper logging"""
    value = os.getenv(var_name, default)
    if required and not value:
        logger.error(f" CRITICAL: Required environment variable {var_name} is not set!")
        raise ValueError(f"Required environment variable {var_name} is missing")
    if value and var_name in ["APP_SECRET", "ACCESS_TOKEN"]:
        logger.info(f" {var_name}: Set (length: {len(value)})")
    elif value:
        logger.info(f" {var_name}: {value}")
    else:
        logger.warning(f" {var_name}: Using default value '{default}'")
    return value

# Load configuration
try:
    WEBHOOK_VERIFY_TOKEN = get_env_var("WEBHOOK_VERIFY_TOKEN", "default_token")
    APP_SECRET = get_env_var("APP_SECRET", "")
    ACCESS_TOKEN = get_env_var("ACCESS_TOKEN", "")
    PHONE_NUMBER_ID = get_env_var("PHONE_NUMBER_ID", None, required=False)
    VERSION = get_env_var("VERSION", "v19.0")
    DOMAIN_CHECK_URL = get_env_var("DOMAIN_CHECK_URL", "https://api.digikenya.co.ke/api/v1/domains/availability/check")
    PORT = int(get_env_var("PORT", "8080"))
    logger.info(" Smart Domain Bot Configuration Loaded Successfully")
except Exception as e:
    logger.error(f" Configuration error: {e}")
    raise

def store_message_status(message_id: str, status: str, recipient: str, timestamp: str, metadata: Dict = None):
    """Store message delivery status with automatic cleanup"""
    # Cleanup old records if limit exceeded
    if len(message_statuses) >= MAX_STATUS_RECORDS:
        # Remove oldest 100 records
        sorted_keys = sorted(message_statuses.keys(), 
                           key=lambda k: message_statuses[k].get('last_updated', ''))
        for key in sorted_keys[:100]:
            del message_statuses[key]
    
    # Store or update status
    if message_id not in message_statuses:
        message_statuses[message_id] = {
            "message_id": message_id,
            "recipient": recipient,
            "status_history": [],
            "created_at": timestamp,
            "last_updated": timestamp,
            "metadata": metadata or {}
        }
    
    # Add status to history
    message_statuses[message_id]["status_history"].append({
        "status": status,
        "timestamp": timestamp
    })
    message_statuses[message_id]["current_status"] = status
    message_statuses[message_id]["last_updated"] = timestamp
    
    logger.info(f"📊 Status stored: {message_id} -> {status} (recipient: {recipient})")

class SmartDomainBot:
    """Enhanced domain bot with intelligent conversation flow"""
    def __init__(self):
        self.conversation_states = {
            "greeting": " Welcome state",
            "searching": " Domain search mode",
            "extension_select": " Extension selection",
            "results": " Results display",
            "bulk_search": " Multiple domain check"
        }

    def get_user_state(self, user_phone: str) -> Dict[str, Any]:
        """Get or create user state with defaults"""
        if user_phone not in user_states:
            user_states[user_phone] = {
                "step": "greeting",
                "search_history": [],
                "current_domain": None,
                "preferred_extensions": [],
                "last_activity": datetime.utcnow().isoformat()
            }
        return user_states[user_phone]

    def update_user_state(self, user_phone: str, updates: Dict[str, Any]):
        """Update user state with timestamp"""
        state = self.get_user_state(user_phone)
        state.update(updates)
        state["last_activity"] = datetime.utcnow().isoformat()
        user_states[user_phone] = state
        logger.info(f" Updated state for {user_phone}: {updates}")

    def parse_domain_input(self, text: str) -> Dict[str, Any]:
        """Intelligently parse user domain input"""
        text = text.strip().lower()
        
        # Check for full domain (e.g., "elijah.co.ke")
        full_match = DOMAIN_PATTERNS['full_domain'].match(text)
        if full_match:
            return {
                "type": "full_domain",
                "base": full_match.group(1),
                "extension": full_match.group(2),
                "domains_to_check": [text]
            }
        
        # Check for partial extension (e.g., "elijah.co")
        partial_match = DOMAIN_PATTERNS['partial_extension'].match(text)
        if partial_match:
            base = partial_match.group(1)
            partial_ext = partial_match.group(2)
            full_ext = f"{partial_ext}.ke"
            return {
                "type": "partial_extension",
                "base": base,
                "extension": full_ext,
                "domains_to_check": [f"{base}.{full_ext}"]
            }
        
        # Base domain only (e.g., "elijah")
        base_match = DOMAIN_PATTERNS['base_domain'].match(text)
        if base_match:
            base = base_match.group(1)
            return {
                "type": "base_domain",
                "base": base,
                "extension": None,
                "domains_to_check": [f"{base}{ext}" for ext in DOMAIN_EXTENSIONS.keys()]
            }
        
        return {"type": "invalid", "error": "Invalid domain format"}

    def is_greeting(self, text: str) -> bool:
        """Enhanced greeting detection"""
        greetings = [
            "hi", "hello", "hey", "hii", "helloo", "start", "begin", 
            "menu", "main", "home", "help", "hola", "jambo", "habari"
        ]
        text_clean = text.lower().strip()
        return (text_clean in greetings or 
                len(text_clean) <= 3 or 
                any(greeting in text_clean for greeting in ["good morning", "good afternoon", "good evening"]))

    async def check_domains_batch(self, domains: List[str]) -> Dict[str, Any]:
        """Check multiple domains efficiently with caching"""
        results = {"available": [], "unavailable": [], "errors": []}
        
        for domain in domains:
            # Check cache first
            if domain in domain_cache:
                cached = domain_cache[domain]
                if cached.get("available"):
                    results["available"].append(cached)
                else:
                    results["unavailable"].append(cached)
                continue
            
            # API call for uncached domains
            try:
                params = {
                    "domain": quote(domain),
                    "include_pricing": "true",
                    "include_suggestions": "false"
                }
                
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        DOMAIN_CHECK_URL,
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=8)
                    ) as resp:
                        if resp.status == 200:
                            api_response = await resp.json()
                            if api_response.get("success"):
                                domain_data = api_response.get("data", {})
                                domain_data["domain"] = domain
                                
                                # Cache the result
                                domain_cache[domain] = domain_data
                                
                                if domain_data.get("available"):
                                    results["available"].append(domain_data)
                                else:
                                    results["unavailable"].append(domain_data)
                            else:
                                error_info = {"domain": domain, "error": api_response.get("error", "API error")}
                                results["errors"].append(error_info)
                        else:
                            error_info = {"domain": domain, "error": f"HTTP {resp.status}"}
                            results["errors"].append(error_info)
                            
            except asyncio.TimeoutError:
                results["errors"].append({"domain": domain, "error": "Request timeout"})
            except Exception as e:
                results["errors"].append({"domain": domain, "error": str(e)})
        
        return results

    def format_domain_results(self, results: Dict[str, Any], base_domain: str) -> str:
        """Format domain search results in a user-friendly way"""
        available = results.get("available", [])
        unavailable = results.get("unavailable", [])
        errors = results.get("errors", [])
        
        if not available and not unavailable:
            return f" Sorry, couldn't check domains for '{base_domain}' right now. Please try again later."
        
        message_parts = []
        
        # Available domains (priority)
        if available:
            message_parts.append(" *AVAILABLE DOMAINS*")
            for domain_info in available[:5]:
                domain = domain_info.get("domain", "")
                price = domain_info.get("price", domain_info.get("pricing", {}).get("first_year", "Contact us"))
                extension = "." + domain.split(".", 1)[1] if "." in domain else ""
                ext_desc = DOMAIN_EXTENSIONS.get(extension, "Domain")
                message_parts.append(f" *{domain}*\n   {ext_desc}\n    {price}")
        
        # Unavailable domains summary
        if unavailable:
            unavailable_count = len(unavailable)
            message_parts.append(f"\n *{unavailable_count} domains unavailable*")
            if unavailable_count <= 3:
                for domain_info in unavailable:
                    domain = domain_info.get("domain", "")
                    message_parts.append(f" {domain}")
        
        # Errors summary
        if errors:
            error_count = len(errors)
            message_parts.append(f"\n {error_count} domains couldn't be checked")
        
        result_message = "\n".join(message_parts)
        
        # Add call-to-action
        if available:
            result_message += f"\n\n *Ready to register?*\nChoose a domain above or visit: https://digikenya.co.ke"
        else:
            result_message += f"\n\n Try different variations of '{base_domain}' or visit our website for more options."
        
        return result_message

    async def send_interactive_message(self, to: str, message: str, buttons: List[Dict] = None, replied_msg_id: str = None):
        """Send enhanced interactive message with better formatting and status tracking"""
        if not ACCESS_TOKEN or not PHONE_NUMBER_ID:
            logger.error(" Cannot send message - credentials missing")
            return False
        
        url = f"https://graph.facebook.com/{VERSION}/{PHONE_NUMBER_ID}/messages"
        
        # Format buttons for WhatsApp API
        if buttons:
            formatted_buttons = []
            for i, btn in enumerate(buttons[:3]):
                formatted_buttons.append({
                    "type": "reply",
                    "reply": {
                        "id": btn.get("id", f"btn_{i}"),
                        "title": btn.get("title", "Button")[:20]
                    }
                })
            
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": message[:1024]},
                    "action": {"buttons": formatted_buttons}
                }
            }
        else:
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"preview_url": True, "body": message[:4096]}
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
                        msg_id = result.get('messages', [{}])[0].get('id')
                        logger.info(f" Message sent to {to}: ID {msg_id}")
                        
                        # Store initial status
                        if msg_id:
                            store_message_status(
                                message_id=msg_id,
                                status="sent",
                                recipient=to,
                                timestamp=datetime.utcnow().isoformat(),
                                metadata={
                                    "message_type": "interactive" if buttons else "text",
                                    "has_buttons": bool(buttons),
                                    "source": "bot"
                                }
                            )
                        
                        return True
                    else:
                        error_text = await resp.text()
                        logger.error(f" Failed to send message to {to}: {resp.status} - {error_text}")
                        return False
        except Exception as e:
            logger.error(f" Exception sending message to {to}: {str(e)}")
            return False

    async def handle_user_message(self, sender: str, message_text: str, message_id: str):
        """Main conversation handler with enhanced flow"""
        text = message_text.strip()
        state = self.get_user_state(sender)
        current_step = state.get("step", "greeting")
        
        logger.info(f" Processing message from {sender}: '{text}' (Step: {current_step})")
        
        # Handle greetings and menu requests
        if self.is_greeting(text):
            await self.send_welcome_message(sender, message_id)
            return
        
        # Handle domain search
        if current_step in ["greeting", "searching"] or text.startswith(("check", "search", "find")):
            domain_text = re.sub(r'^(check|search|find|domain)\s+', '', text, flags=re.IGNORECASE).strip()
            
            if domain_text:
                await self.process_domain_search(sender, domain_text, message_id)
            else:
                await self.send_search_prompt(sender, message_id)
            return
        
        # Handle button interactions and follow-ups
        if current_step == "results":
            if text.lower() in ["more", "other", "alternatives"]:
                last_domain = state.get("current_domain")
                if last_domain:
                    await self.process_domain_search(sender, last_domain, message_id, show_all=True)
                return
            elif text.lower() in ["new", "different", "another"]:
                await self.send_search_prompt(sender, message_id)
                return
        
        # Default: treat as domain search
        await self.process_domain_search(sender, text, message_id)

    async def send_welcome_message(self, to: str, replied_msg_id: str = None):
        """Send enhanced welcome message"""
        self.update_user_state(to, {"step": "greeting"})
        
        extensions_list = "\n".join([f"• *{ext}* - {desc}" for ext, desc in list(DOMAIN_EXTENSIONS.items())[:6]])
        
        welcome_text = (
            " *Welcome to DigiKenya Smart Domain Bot!*\n\n"
            "I'll help you find the perfect .ke domain quickly and easily.\n\n"
            " *Popular Extensions:*\n"
            f"{extensions_list}\n"
            "...and more!\n\n"
            " *Just type your desired domain name*\n"
            "Examples: 'mycompany', 'john.co.ke', 'myblog'"
        )
        
        buttons = [
            {"id": "search_domains", "title": " Search Now"},
            {"id": "view_extensions", "title": " All Extensions"},
            {"id": "visit_website", "title": " Visit Website"}
        ]
        
        await self.send_interactive_message(to, welcome_text, buttons, replied_msg_id)

    async def send_search_prompt(self, to: str, replied_msg_id: str = None):
        """Send search prompt message"""
        self.update_user_state(to, {"step": "searching"})
        
        prompt_text = (
            " *What domain would you like to check?*\n\n"
            " *You can search:*\n"
            "• Just the name: `mycompany`\n"
            "• With extension: `mycompany.co.ke`\n"
            "• Partial: `mycompany.co`\n\n"
            "I'll check all available .ke extensions for you! "
        )
        
        await self.send_interactive_message(to, prompt_text, replied_msg_id=replied_msg_id)

    async def process_domain_search(self, sender: str, domain_input: str, message_id: str, show_all: bool = False):
        """Process domain search with intelligent parsing"""
        parsed = self.parse_domain_input(domain_input)
        
        if parsed["type"] == "invalid":
            error_text = (
                " *Invalid domain format*\n\n"
                "Please try:\n"
                "• `mycompany` (I'll check all extensions)\n"
                "• `mycompany.co.ke` (specific domain)\n"
                "• `mycompany.co` (I'll add .ke)\n\n"
                "What would you like to search for?"
            )
            await self.send_interactive_message(sender, error_text, replied_msg_id=message_id)
            return
        
        base_domain = parsed["base"]
        domains_to_check = parsed["domains_to_check"]
        
        self.update_user_state(sender, {
            "step": "searching",
            "current_domain": base_domain
        })
        
        search_count = len(domains_to_check)
        searching_text = f" Searching {search_count} domain{'s' if search_count > 1 else ''} for '*{base_domain}*'...\n\nThis may take a moment "
        await self.send_interactive_message(sender, searching_text, replied_msg_id=message_id)
        
        results = await self.check_domains_batch(domains_to_check)
        results_text = self.format_domain_results(results, base_domain)
        
        buttons = []
        if results.get("available"):
            buttons.append({"id": "register_domain", "title": " Register Now"})
            
        if len(results.get("unavailable", [])) > 0 or results.get("errors"):
            buttons.append({"id": "try_variations", "title": " Try Variations"})
            
        buttons.append({"id": "new_search", "title": " New Search"})
        
        await self.send_interactive_message(sender, results_text, buttons)
        
        self.update_user_state(sender, {
            "step": "results",
            "last_results": results,
            "search_history": self.get_user_state(sender)["search_history"] + [base_domain]
        })

    async def handle_button_click(self, sender: str, button_id: str, message_id: str):
        """Handle interactive button clicks"""
        logger.info(f" Button clicked by {sender}: {button_id}")
        
        if button_id == "search_domains":
            await self.send_search_prompt(sender, message_id)
            
        elif button_id == "view_extensions":
            extensions_text = " *All Available .ke Extensions:*\n\n"
            extensions_text += "\n".join([f"• *{ext}* - {desc}" for ext, desc in DOMAIN_EXTENSIONS.items()])
            extensions_text += "\n\n Type your domain name to get started!"
            await self.send_interactive_message(sender, extensions_text, replied_msg_id=message_id)
            
        elif button_id == "visit_website":
            website_text = (
                " *Visit DigiKenya*\n\n"
                "Explore all our services:\n"
                " https://digikenya.co.ke\n\n"
                "• Domain Registration\n"
                "• Web Hosting\n"
                "• Website Design\n"
                "• Digital Solutions\n\n"
                " Or continue searching domains here!"
            )
            buttons = [{"id": "new_search", "title": " Search Domains"}]
            await self.send_interactive_message(sender, website_text, buttons, message_id)
            
        elif button_id == "register_domain":
            register_text = (
                " *Domain Registration Coming Soon!*\n\n"
                "We're working on bringing you a seamless registration experience.\n"
                "For now, you can register your .ke domain directly at:\n"
                " https://digikenya.co.ke\n\n"
                "Visit our website to secure your domain today!"
            )
            buttons = [
                {"id": "new_search", "title": " Search Again"},
                {"id": "visit_website", "title": " Visit Website"}
            ]
            await self.send_interactive_message(sender, register_text, buttons, replied_msg_id=message_id)
            
        elif button_id == "new_search":
            await self.send_search_prompt(sender, message_id)
            
        elif button_id == "try_variations":
            state = self.get_user_state(sender)
            current_domain = state.get("current_domain")
            if current_domain:
                variation_text = (
                    f" *Try these variations of '{current_domain}':*\n\n"
                    f"• {current_domain}ke, {current_domain}kenya\n"
                    f"• my{current_domain}, get{current_domain}\n"
                    f"• {current_domain}online, {current_domain}digital\n\n"
                    "Just type any variation to search! "
                )
                await self.send_interactive_message(sender, variation_text, replied_msg_id=message_id)

# Initialize the smart bot
smart_bot = SmartDomainBot()

@app.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"), 
    hub_challenge: str = Query(None, alias="hub.challenge")
):
    """Webhook verification endpoint"""
    logger.info(f" Webhook verification request")
    logger.info(f"   Mode: {hub_mode}")
    logger.info(f"   Token: {hub_verify_token}")
    logger.info(f"   Challenge: {hub_challenge}")
    if hub_mode == "subscribe" and hub_verify_token == WEBHOOK_VERIFY_TOKEN:
        logger.info(" Webhook verified successfully")
        return PlainTextResponse(content=hub_challenge, status_code=200)
    else:
        logger.error(" Webhook verification failed")
        raise HTTPException(status_code=403, detail="Forbidden")

@app.post("/webhook")
async def handle_webhook(request: Request):
    """Enhanced webhook handler with message status tracking"""
    logger.info(" Incoming webhook")
    try:
        body = await request.body()
        webhook_data = json.loads(body.decode())
        
        logger.info(f" Webhook data: {json.dumps(webhook_data, indent=2)[:500]}...")
        
        if webhook_data.get("object") == "whatsapp_business_account":
            entries = webhook_data.get("entry", [])
            
            for entry in entries:
                changes = entry.get("changes", [])
                
                for change in changes:
                    value = change.get("value", {})
                    
                    # Handle message status updates
                    if change.get("field") == "messages":
                        statuses = value.get("statuses", [])
                        for status_update in statuses:
                            msg_id = status_update.get("id")
                            status = status_update.get("status")
                            timestamp = status_update.get("timestamp")
                            recipient = status_update.get("recipient_id")
                            
                            if msg_id and status:
                                store_message_status(
                                    message_id=msg_id,
                                    status=status,
                                    recipient=recipient,
                                    timestamp=datetime.fromtimestamp(int(timestamp)).isoformat() if timestamp else datetime.utcnow().isoformat(),
                                    metadata={
                                        "pricing": status_update.get("pricing"),
                                        "conversation": status_update.get("conversation"),
                                        "errors": status_update.get("errors", [])
                                    }
                                )
                        
                        # Handle incoming messages
                        messages = value.get("messages", [])
                        for message in messages:
                            sender = message.get("from")
                            msg_type = message.get("type")
                            msg_id = message.get("id")
                            
                            logger.info(f" Message from {sender} (type: {msg_type})")
                            
                            # Store incoming message status
                            store_message_status(
                                message_id=msg_id,
                                status="received",
                                recipient=sender,
                                timestamp=datetime.utcnow().isoformat(),
                                metadata={
                                    "message_type": msg_type,
                                    "direction": "incoming"
                                }
                            )
                            
                            # Handle interactive button responses
                            if msg_type == "interactive":
                                interactive = message.get("interactive", {})
                                if interactive.get("type") == "button_reply":
                                    button_data = interactive.get("button_reply", {})
                                    button_id = button_data.get("id", "")
                                    await smart_bot.handle_button_click(sender, button_id, msg_id)
                            
                            # Handle text messages
                            elif msg_type == "text":
                                text_content = message.get("text", {})
                                text_body = text_content.get("body", "").strip()
                                await smart_bot.handle_user_message(sender, text_body, msg_id)
                            
                            # Handle other message types
                            else:
                                fallback_text = (
                                    " I can help you search for .ke domains!\n\n"
                                    "Just type the domain name you want to check.\n"
                                    "Example: 'mycompany' or 'myblog.ke'\n\n"
                                    " Plus explore our digital services:\n"
                                    "• DNS hosting • SSL certificates • AI development"
                                )
                                await smart_bot.send_interactive_message(sender, fallback_text, replied_msg_id=msg_id)
            
            return JSONResponse({"status": "success", "message": "Processed successfully"})
        
        else:
            logger.warning(f" Unknown webhook object: {webhook_data.get('object')}")
            return JSONResponse({"status": "ignored", "reason": "Unknown webhook object"})
            
    except Exception as e:
        logger.error(f" Webhook processing error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")

# NEW ENDPOINTS FOR MESSAGE STATUS TRACKING

@app.get("/api/messages/status")
async def get_all_message_statuses(
    limit: int = Query(50, ge=1, le=500),
    status: Optional[str] = Query(None),
    recipient: Optional[str] = Query(None)
):
    """
    Get all message delivery statuses with optional filtering
    
    Query parameters:
    - limit: Maximum number of records to return (1-500, default 50)
    - status: Filter by status (sent, delivered, read, failed)
    - recipient: Filter by recipient phone number
    """
    filtered_statuses = list(message_statuses.values())
    
    # Apply filters
    if status:
        filtered_statuses = [
            msg for msg in filtered_statuses 
            if msg.get("current_status") == status
        ]
    
    if recipient:
        filtered_statuses = [
            msg for msg in filtered_statuses 
            if msg.get("recipient") == recipient
        ]
    
    # Sort by most recent first
    filtered_statuses.sort(
        key=lambda x: x.get("last_updated", ""), 
        reverse=True
    )
    
    # Apply limit
    filtered_statuses = filtered_statuses[:limit]
    
    return {
        "success": True,
        "count": len(filtered_statuses),
        "total_tracked": len(message_statuses),
        "messages": filtered_statuses
    }

@app.get("/api/messages/status/{message_id}")
async def get_message_status(message_id: str):
    """
    Get detailed status for a specific message ID
    
    Returns complete status history and metadata
    """
    if message_id not in message_statuses:
        raise HTTPException(
            status_code=404, 
            detail=f"Message ID {message_id} not found"
        )
    
    return {
        "success": True,
        "message": message_statuses[message_id]
    }

@app.get("/api/messages/statistics")
async def get_message_statistics(
    hours: int = Query(24, ge=1, le=168)
):
    """
    Get message delivery statistics for the specified time period
    
    Query parameters:
    - hours: Number of hours to include in statistics (1-168, default 24)
    """
    cutoff_time = datetime.utcnow() - timedelta(hours=hours)
    cutoff_iso = cutoff_time.isoformat()
    
    # Filter messages within time period
    recent_messages = [
        msg for msg in message_statuses.values()
        if msg.get("last_updated", "") >= cutoff_iso
    ]
    
    # Count by status
    status_counts = {
        "sent": 0,
        "delivered": 0,
        "read": 0,
        "failed": 0,
        "received": 0
    }
    
    for msg in recent_messages:
        current_status = msg.get("current_status", "unknown")
        if current_status in status_counts:
            status_counts[current_status] += 1
        else:
            status_counts[current_status] = status_counts.get(current_status, 0) + 1
    
    # Calculate delivery rate
    total_sent = status_counts.get("sent", 0) + status_counts.get("delivered", 0) + status_counts.get("read", 0)
    delivered = status_counts.get("delivered", 0) + status_counts.get("read", 0)
    delivery_rate = (delivered / total_sent * 100) if total_sent > 0 else 0
    
    # Calculate read rate
    read_count = status_counts.get("read", 0)
    read_rate = (read_count / delivered * 100) if delivered > 0 else 0
    
    # Count by recipient
    recipient_counts = {}
    for msg in recent_messages:
        recipient = msg.get("recipient", "unknown")
        recipient_counts[recipient] = recipient_counts.get(recipient, 0) + 1
    
    # Get top recipients
    top_recipients = sorted(
        recipient_counts.items(), 
        key=lambda x: x[1], 
        reverse=True
    )[:10]
    
    return {
        "success": True,
        "period_hours": hours,
        "total_messages": len(recent_messages),
        "status_breakdown": status_counts,
        "metrics": {
            "delivery_rate": round(delivery_rate, 2),
            "read_rate": round(read_rate, 2),
            "failed_count": status_counts.get("failed", 0)
        },
        "top_recipients": [
            {"recipient": r[0], "message_count": r[1]} 
            for r in top_recipients
        ]
    }

@app.get("/api/messages/recipient/{phone_number}")
async def get_recipient_messages(
    phone_number: str,
    limit: int = Query(20, ge=1, le=100)
):
    """
    Get all messages for a specific recipient phone number
    
    Useful for tracking conversation history and delivery status
    """
    recipient_messages = [
        msg for msg in message_statuses.values()
        if msg.get("recipient") == phone_number
    ]
    
    # Sort by most recent first
    recipient_messages.sort(
        key=lambda x: x.get("last_updated", ""), 
        reverse=True
    )
    
    recipient_messages = recipient_messages[:limit]
    
    # Calculate statistics for this recipient
    status_counts = {}
    for msg in recipient_messages:
        status = msg.get("current_status", "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    
    return {
        "success": True,
        "recipient": phone_number,
        "message_count": len(recipient_messages),
        "status_breakdown": status_counts,
        "messages": recipient_messages
    }

@app.delete("/api/messages/status/cleanup")
async def cleanup_old_statuses(
    days: int = Query(7, ge=1, le=30)
):
    """
    Clean up message statuses older than specified days
    
    Query parameters:
    - days: Remove statuses older than this many days (1-30, default 7)
    """
    cutoff_time = datetime.utcnow() - timedelta(days=days)
    cutoff_iso = cutoff_time.isoformat()
    
    initial_count = len(message_statuses)
    
    # Remove old entries
    keys_to_delete = [
        msg_id for msg_id, msg_data in message_statuses.items()
        if msg_data.get("last_updated", "") < cutoff_iso
    ]
    
    for key in keys_to_delete:
        del message_statuses[key]
    
    removed_count = len(keys_to_delete)
    
    logger.info(f"🗑️ Cleaned up {removed_count} old message statuses")
    
    return {
        "success": True,
        "removed_count": removed_count,
        "remaining_count": len(message_statuses),
        "cutoff_date": cutoff_iso
    }

@app.get("/api/messages/failed")
async def get_failed_messages(
    limit: int = Query(50, ge=1, le=200)
):
    """
    Get all failed messages for troubleshooting
    
    Returns messages with 'failed' status including error details
    """
    failed_messages = [
        msg for msg in message_statuses.values()
        if msg.get("current_status") == "failed"
    ]
    
    # Sort by most recent first
    failed_messages.sort(
        key=lambda x: x.get("last_updated", ""), 
        reverse=True
    )
    
    failed_messages = failed_messages[:limit]
    
    # Extract error information
    for msg in failed_messages:
        errors = msg.get("metadata", {}).get("errors", [])
        if errors:
            msg["error_details"] = errors
    
    return {
        "success": True,
        "failed_count": len(failed_messages),
        "total_tracked": len(message_statuses),
        "messages": failed_messages
    }

@app.get("/health")
async def health_check():
    """Enhanced health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "2.1.0",
        "bot_features": {
            "smart_domain_parsing": True,
            "batch_domain_check": True,
            "interactive_buttons": True,
            "domain_caching": True,
            "conversation_flow": True,
            "message_status_tracking": True,
            "active_users": len(user_states),
            "cached_domains": len(domain_cache),
            "tracked_messages": len(message_statuses)
        },
        "supported_extensions": list(DOMAIN_EXTENSIONS.keys())
    }

@app.get("/stats")
async def get_stats():
    """Get comprehensive bot usage statistics"""
    total_searches = sum(len(state.get("search_history", [])) for state in user_states.values())
    
    # Message status breakdown
    status_breakdown = {}
    for msg in message_statuses.values():
        status = msg.get("current_status", "unknown")
        status_breakdown[status] = status_breakdown.get(status, 0) + 1
    
    return {
        "active_users": len(user_states),
        "total_searches": total_searches,
        "cached_domains": len(domain_cache),
        "tracked_messages": len(message_statuses),
        "message_status_breakdown": status_breakdown,
        "supported_extensions": len(DOMAIN_EXTENSIONS),
        "timestamp": datetime.utcnow().isoformat()
    }

@app.on_event("startup")
async def startup_event():
    logger.info(" Smart WhatsApp Domain Bot Starting Up")
    logger.info(f"   Supported Extensions: {len(DOMAIN_EXTENSIONS)}")
    logger.info(f"   API Endpoint: {DOMAIN_CHECK_URL}")
    logger.info(f"   Message Status Tracking: Enabled")
    logger.info(f"   Max Status Records: {MAX_STATUS_RECORDS}")
    logger.info(" Bot Ready!")

if __name__ == "__main__":
    import uvicorn
    logger.info(" Starting Smart Domain Bot Server")
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=PORT,
        log_level="info",
        access_log=True,
        reload=False
    )