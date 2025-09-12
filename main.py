from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import PlainTextResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import json
import os
import logging
import sys
import aiohttp
from datetime import datetime
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
    description="Intelligent WhatsApp Business API Bot for .ke Domain Search",
    version="2.0.0"
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
    ".ke": "🇰🇪 General Kenya domains",
    ".co.ke": "🏢 Commercial organizations",
    ".or.ke": "🏛️ Non-profit organizations", 
    ".ac.ke": "🎓 Academic institutions",
    ".go.ke": "🏛️ Government entities",
    ".ne.ke": "🌐 Network providers",
    ".sc.ke": "🔬 Scientific organizations",
    ".me.ke": "👤 Personal domains",
    ".info.ke": "ℹ️ Information sites"
}

# Enhanced user state management
user_states: Dict[str, Dict[str, Any]] = {}
domain_cache: Dict[str, Dict[str, Any]] = {}  # Cache API results

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
        logger.error(f"❌ CRITICAL: Required environment variable {var_name} is not set!")
        raise ValueError(f"Required environment variable {var_name} is missing")
    
    if value and var_name in ["APP_SECRET", "ACCESS_TOKEN"]:
        logger.info(f"✅ {var_name}: Set (length: {len(value)})")
    elif value:
        logger.info(f"✅ {var_name}: {value}")
    else:
        logger.warning(f"⚠️ {var_name}: Using default value '{default}'")
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
    
    logger.info("🚀 Smart Domain Bot Configuration Loaded Successfully")
except Exception as e:
    logger.error(f"❌ Configuration error: {e}")
    raise

class SmartDomainBot:
    """Enhanced domain bot with intelligent conversation flow"""
    
    def __init__(self):
        self.conversation_states = {
            "greeting": "👋 Welcome state",
            "searching": "🔍 Domain search mode",
            "extension_select": "📋 Extension selection",
            "results": "📊 Results display",
            "bulk_search": "🔄 Multiple domain check"
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
        logger.info(f"👤 Updated state for {user_phone}: {updates}")
    
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
                    "include_suggestions": "false"  # Reduce API response size
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
            return f"❌ Sorry, couldn't check domains for '{base_domain}' right now. Please try again later."
        
        message_parts = []
        
        # Available domains (priority)
        if available:
            message_parts.append("✅ *AVAILABLE DOMAINS*")
            for domain_info in available[:5]:  # Limit to top 5
                domain = domain_info.get("domain", "")
                price = domain_info.get("price", domain_info.get("pricing", {}).get("first_year", "Contact us"))
                extension = "." + domain.split(".", 1)[1] if "." in domain else ""
                ext_desc = DOMAIN_EXTENSIONS.get(extension, "Domain")
                message_parts.append(f"🟢 *{domain}*\n   {ext_desc}\n   💰 {price}")
        
        # Unavailable domains summary
        if unavailable:
            unavailable_count = len(unavailable)
            message_parts.append(f"\n❌ *{unavailable_count} domains unavailable*")
            if unavailable_count <= 3:
                for domain_info in unavailable:
                    domain = domain_info.get("domain", "")
                    message_parts.append(f"🔴 {domain}")
        
        # Errors summary
        if errors:
            error_count = len(errors)
            message_parts.append(f"\n⚠️ {error_count} domains couldn't be checked")
        
        result_message = "\n".join(message_parts)
        
        # Add call-to-action
        if available:
            result_message += f"\n\n🎯 *Ready to register?*\nChoose a domain above or visit: https://digikenya.co.ke"
        else:
            result_message += f"\n\n💡 Try different variations of '{base_domain}' or visit our website for more options."
        
        return result_message

    async def send_interactive_message(self, to: str, message: str, buttons: List[Dict] = None, replied_msg_id: str = None):
        """Send enhanced interactive message with better formatting"""
        if not ACCESS_TOKEN or not PHONE_NUMBER_ID:
            logger.error("❌ Cannot send message - credentials missing")
            return False
        
        url = f"https://graph.facebook.com/{VERSION}/{PHONE_NUMBER_ID}/messages"
        
        # Format buttons for WhatsApp API
        if buttons:
            formatted_buttons = []
            for i, btn in enumerate(buttons[:3]):  # WhatsApp allows max 3 buttons
                formatted_buttons.append({
                    "type": "reply",
                    "reply": {
                        "id": btn.get("id", f"btn_{i}"),
                        "title": btn.get("title", "Button")[:20]  # 20 char limit
                    }
                })
            
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": message[:1024]},  # WhatsApp limit
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
                        logger.info(f"✅ Message sent to {to}: ID {msg_id}")
                        return True
                    else:
                        error_text = await resp.text()
                        logger.error(f"❌ Failed to send message to {to}: {resp.status} - {error_text}")
                        return False
        except Exception as e:
            logger.error(f"❌ Exception sending message to {to}: {str(e)}")
            return False

    async def handle_user_message(self, sender: str, message_text: str, message_id: str):
        """Main conversation handler with enhanced flow"""
        text = message_text.strip()
        state = self.get_user_state(sender)
        current_step = state.get("step", "greeting")
        
        logger.info(f"🧠 Processing message from {sender}: '{text}' (Step: {current_step})")
        
        # Handle greetings and menu requests
        if self.is_greeting(text):
            await self.send_welcome_message(sender, message_id)
            return
        
        # Handle domain search
        if current_step in ["greeting", "searching"] or text.startswith(("check", "search", "find")):
            # Extract domain from text (remove common prefixes)
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
            "🚀 *Welcome to DigiKenya Smart Domain Bot!*\n\n"
            "I'll help you find the perfect .ke domain quickly and easily.\n\n"
            "🎯 *Popular Extensions:*\n"
            f"{extensions_list}\n"
            "...and more!\n\n"
            "💬 *Just type your desired domain name*\n"
            "Examples: 'mycompany', 'john.co.ke', 'myblog'"
        )
        
        buttons = [
            {"id": "search_domains", "title": "🔍 Search Now"},
            {"id": "view_extensions", "title": "📋 All Extensions"},
            {"id": "visit_website", "title": "🌐 Visit Website"}
        ]
        
        await self.send_interactive_message(to, welcome_text, buttons, replied_msg_id)

    async def send_search_prompt(self, to: str, replied_msg_id: str = None):
        """Send search prompt message"""
        self.update_user_state(to, {"step": "searching"})
        
        prompt_text = (
            "🔍 *What domain would you like to check?*\n\n"
            "💡 *You can search:*\n"
            "• Just the name: `mycompany`\n"
            "• With extension: `mycompany.co.ke`\n"
            "• Partial: `mycompany.co`\n\n"
            "I'll check all available .ke extensions for you! ✨"
        )
        
        await self.send_interactive_message(to, prompt_text, replied_msg_id=replied_msg_id)

    async def process_domain_search(self, sender: str, domain_input: str, message_id: str, show_all: bool = False):
        """Process domain search with intelligent parsing"""
        # Parse the domain input
        parsed = self.parse_domain_input(domain_input)
        
        if parsed["type"] == "invalid":
            error_text = (
                "❌ *Invalid domain format*\n\n"
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
        
        # Update user state
        self.update_user_state(sender, {
            "step": "searching",
            "current_domain": base_domain
        })
        
        # Send "searching" message
        search_count = len(domains_to_check)
        searching_text = f"🔍 Searching {search_count} domain{'s' if search_count > 1 else ''} for '*{base_domain}*'...\n\nThis may take a moment ⏳"
        await self.send_interactive_message(sender, searching_text, replied_msg_id=message_id)
        
        # Check domains
        results = await self.check_domains_batch(domains_to_check)
        
        # Format and send results
        results_text = self.format_domain_results(results, base_domain)
        
        # Add interactive buttons based on results
        buttons = []
        if results.get("available"):
            buttons.append({"id": "register_domain", "title": "🛒 Register Now"})
            
        if len(results.get("unavailable", [])) > 0 or results.get("errors"):
            buttons.append({"id": "try_variations", "title": "💡 Try Variations"})
            
        buttons.append({"id": "new_search", "title": "🔍 New Search"})
        
        await self.send_interactive_message(sender, results_text, buttons)
        
        # Update state with results
        self.update_user_state(sender, {
            "step": "results",
            "last_results": results,
            "search_history": self.get_user_state(sender)["search_history"] + [base_domain]
        })

    async def handle_button_click(self, sender: str, button_id: str, message_id: str):
        """Handle interactive button clicks"""
        logger.info(f"🔘 Button clicked by {sender}: {button_id}")
        
        if button_id == "search_domains":
            await self.send_search_prompt(sender, message_id)
            
        elif button_id == "view_extensions":
            extensions_text = "📋 *All Available .ke Extensions:*\n\n"
            extensions_text += "\n".join([f"• *{ext}* - {desc}" for ext, desc in DOMAIN_EXTENSIONS.items()])
            extensions_text += "\n\n💬 Type your domain name to get started!"
            await self.send_interactive_message(sender, extensions_text, replied_msg_id=message_id)
            
        elif button_id == "visit_website":
            website_text = (
                "🌐 *Visit DigiKenya*\n\n"
                "Explore all our services:\n"
                "👉 https://digikenya.co.ke\n\n"
                "• Domain Registration\n"
                "• Web Hosting\n"
                "• Website Design\n"
                "• Digital Solutions\n\n"
                "💬 Or continue searching domains here!"
            )
            buttons = [{"id": "new_search", "title": "🔍 Search Domains"}]
            await self.send_interactive_message(sender, website_text, buttons, message_id)
            
        elif button_id == "register_domain":
            state = self.get_user_state(sender)
            last_results = state.get("last_results", {})
            available = last_results.get("available", [])
            
            if available:
                register_text = "🛒 *Ready to Register?*\n\nChoose from your available domains:\n\n"
                for domain_info in available[:3]:
                    domain = domain_info.get("domain", "")
                    price = domain_info.get("price", "Contact us")
                    register_url = f"https://digikenya.co.ke/register?domain={quote(domain)}"
                    register_text += f"• *{domain}* ({price})\n  👉 {register_url}\n\n"
                
                register_text += "💬 Need help? Contact our support team!"
                await self.send_interactive_message(sender, register_text, replied_msg_id=message_id)
            
        elif button_id == "new_search":
            await self.send_search_prompt(sender, message_id)
            
        elif button_id == "try_variations":
            state = self.get_user_state(sender)
            current_domain = state.get("current_domain")
            if current_domain:
                variation_text = (
                    f"💡 *Try these variations of '{current_domain}':*\n\n"
                    f"• {current_domain}ke, {current_domain}kenya\n"
                    f"• my{current_domain}, get{current_domain}\n"
                    f"• {current_domain}online, {current_domain}digital\n\n"
                    "Just type any variation to search! ✨"
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
    logger.info(f"🔐 Webhook verification request")
    logger.info(f"   Mode: {hub_mode}")
    logger.info(f"   Token: {hub_verify_token}")
    logger.info(f"   Challenge: {hub_challenge}")
    
    if hub_mode == "subscribe" and hub_verify_token == WEBHOOK_VERIFY_TOKEN:
        logger.info("✅ Webhook verified successfully")
        return PlainTextResponse(content=hub_challenge, status_code=200)
    else:
        logger.error("❌ Webhook verification failed")
        raise HTTPException(status_code=403, detail="Forbidden")

@app.post("/webhook")
async def handle_webhook(request: Request):
    """Enhanced webhook handler with smart conversation flow"""
    logger.info("📨 Incoming webhook")
    
    try:
        body = await request.body()
        webhook_data = json.loads(body.decode())
        
        logger.info(f"📦 Webhook data: {json.dumps(webhook_data, indent=2)[:500]}...")
        
        if webhook_data.get("object") == "whatsapp_business_account":
            entries = webhook_data.get("entry", [])
            
            for entry in entries:
                changes = entry.get("changes", [])
                
                for change in changes:
                    if change.get("field") == "messages":
                        value = change.get("value", {})
                        messages = value.get("messages", [])
                        
                        for message in messages:
                            sender = message.get("from")
                            msg_type = message.get("type")
                            msg_id = message.get("id")
                            
                            logger.info(f"💬 Message from {sender} (type: {msg_type})")
                            
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
                                    "👋 I can help you search for .ke domains!\n\n"
                                    "Just type the domain name you want to check.\n"
                                    "Example: 'mycompany' or 'myblog.ke'\n\n"
                                    "🚀 Plus explore our digital services:\n"
                                    "• DNS hosting • SSL certificates • AI development"
                                )
                                await smart_bot.send_interactive_message(sender, fallback_text, replied_msg_id=msg_id)
            
            return JSONResponse({"status": "success", "message": "Processed successfully"})
        
        else:
            logger.warning(f"❓ Unknown webhook object: {webhook_data.get('object')}")
            return JSONResponse({"status": "ignored", "reason": "Unknown webhook object"})
            
    except Exception as e:
        logger.error(f"❌ Webhook processing error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")

@app.get("/health")
async def health_check():
    """Enhanced health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "2.0.0",
        "bot_features": {
            "smart_domain_parsing": True,
            "batch_domain_check": True,
            "interactive_buttons": True,
            "domain_caching": True,
            "conversation_flow": True,
            "active_users": len(user_states),
            "cached_domains": len(domain_cache)
        },
        "supported_extensions": list(DOMAIN_EXTENSIONS.keys())
    }

@app.get("/stats")
async def get_stats():
    """Get bot usage statistics"""
    total_searches = sum(len(state.get("search_history", [])) for state in user_states.values())
    
    return {
        "active_users": len(user_states),
        "total_searches": total_searches,
        "cached_domains": len(domain_cache),
        "supported_extensions": len(DOMAIN_EXTENSIONS),
        "timestamp": datetime.utcnow().isoformat()
    }

@app.on_event("startup")
async def startup_event():
    logger.info("🚀 Smart WhatsApp Domain Bot Starting Up")
    logger.info(f"   Supported Extensions: {len(DOMAIN_EXTENSIONS)}")
    logger.info(f"   API Endpoint: {DOMAIN_CHECK_URL}")
    logger.info("✅ Bot Ready!")

if __name__ == "__main__":
    import uvicorn
    
    logger.info("🌟 Starting Smart Domain Bot Server")
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=PORT,
        log_level="info",
        access_log=True,
        reload=False
    )