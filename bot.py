from telegram import (
    Update,
    MenuButtonCommands,
    BotCommand,
    BotCommandScopeDefault
)
from telegram.ext import (
    ApplicationBuilder,
    Application,
    ContextTypes,
    CommandHandler
)
import logging
import asyncio
from config import Config

# -------------------- Enhanced Logging Setup --------------------
def configure_logging():
    """Centralized logging configuration with UTF-8 support"""
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("bot.log", encoding='utf-8')
        ]
    )
    return logging.getLogger(__name__)

logger = configure_logging()

# -------------------- Command Handlers with Enhanced Features --------------------
class BotHandlers:
    """Encapsulates all command handlers with improved error handling"""
    
    def __init__(self):
        self.cfg = Config()
        self.commands = [
            BotCommand("start", "Restart the bot"),
            BotCommand("donate", "Support our community"),
            BotCommand("channel", "Join premium channel"),
            BotCommand("help", "Get assistance")
        ]

    async def register_user(self, user) -> None:
        """Enhanced user registration with validation"""
        try:
            if not user or not user.id:
                raise ValueError("Invalid user object")
                
            logger.info(f"New user registration: ID:{user.id} Name:{user.full_name}")
            # TODO: Add actual DB registration
            return True
        except Exception as e:
            logger.error(f"Registration failed for {getattr(user, 'id', 'unknown')}: {e}", exc_info=True)
            return False

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Enhanced start command with user tracking"""
        try:
            user = update.effective_user
            if not user:
                return

            # Async registration that won't block response
            asyncio.create_task(self.register_user(user))

            welcome_msg = (
                f"👋 Welcome, {user.first_name}!\n\n"
                "💙 Support our community and get exclusive content!\n\n"
                "✨ <b>Main Features:</b>\n"
                "• /donate - Make a secure donation\n"
                "• /channel - Join our premium channel\n"
                "• /help - Get assistance\n\n"
                f"Join our community: {self.cfg.TELEGRAM_CHANNEL_LINK}"
            )

            await update.message.reply_text(
                welcome_msg,
                parse_mode='HTML',
                disable_web_page_preview=True
            )
        except Exception as e:
            logger.error("Start command failed", exc_info=True)
            await self._send_error_response(update, "start")

    async def donate(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Donation command with payment system integration"""
        try:
            from payments import PaymentProcessor
            processor = PaymentProcessor(context.bot)
            await processor.start_donation_flow(update, context)
        except ImportError:
            logger.critical("Payment system unavailable")
            await self._send_error_response(update, "payment_system")
        except Exception as e:
            logger.error("Donate command failed", exc_info=True)
            await self._send_error_response(update, "donate")

    async def channel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Channel command with enhanced formatting"""
        try:
            channel_msg = (
                "📢 <b>Join Our Premium Channel</b>\n\n"
                f"{self.cfg.TELEGRAM_CHANNEL_LINK}\n\n"
                "💎 <b>Benefits:</b>\n"
                "• Exclusive investment tips\n"
                "• Early access to content\n"
                "• VIP community access"
            )
            await update.message.reply_text(
                channel_msg,
                parse_mode='HTML',
                disable_web_page_preview=True
            )
        except Exception as e:
            logger.error("Channel command failed", exc_info=True)
            await self._send_error_response(update, "channel")

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Help command with support information"""
        try:
            help_msg = (
                "🆘 <b>Help Center</b>\n\n"
                "💵 /donate - Support us\n"
                "📢 /channel - Premium content\n"
                f"❓ Message @{self.cfg.SUPPORT_USERNAME} for help"
            )
            await update.message.reply_text(help_msg, parse_mode='HTML')
        except Exception as e:
            logger.error("Help command failed", exc_info=True)
            await self._send_error_response(update, "help")

    async def _send_error_response(self, update: Update, error_type: str) -> None:
        """Centralized error response handler"""
        error_messages = {
            "start": "Failed to initialize the bot",
            "donate": "Couldn't start donation process",
            "channel": "Failed to load channel info",
            "help": "Couldn't load help information",
            "payment_system": "Payment system is currently unavailable"
        }
        
        message = f"⚠️ {error_messages.get(error_type, 'Something went wrong')}. Please try again later."
        await update.message.reply_text(message)

# -------------------- Enhanced Application Setup --------------------
class BotApplication:
    """Manages bot application lifecycle with improved error handling"""
    
    def __init__(self):
        self.handlers = BotHandlers()
        self.cfg = Config()

    async def post_init(self, application: Application) -> None:
        """Enhanced post-init with command validation"""
        try:
            await application.bot.set_my_commands(
                self.handlers.commands, 
                scope=BotCommandScopeDefault()
            )
            await application.bot.set_chat_menu_button(
                chat_id=0, 
                menu_button=MenuButtonCommands()
            )
            logger.info("Bot commands and menu configured successfully")
        except Exception as e:
            logger.critical("Failed to configure bot commands", exc_info=True)
            raise

    def setup_handlers(self, application: Application) -> None:
        """Handler registration with dependency validation"""
        try:
            # Core commands
            application.add_handler(CommandHandler("start", self.handlers.start))
            application.add_handler(CommandHandler("help", self.handlers.help_command))
            application.add_handler(CommandHandler("channel", self.handlers.channel_command))
            application.add_handler(CommandHandler("donate", self.handlers.donate))

            # Payment system
            from payments import PaymentProcessor
            PaymentProcessor().register_handlers(application)
            
            logger.info("All handlers registered successfully")
        except ImportError as e:
            logger.critical("Failed to import payment system", exc_info=True)
            raise
        except Exception as e:
            logger.error("Handler setup failed", exc_info=True)
            raise

    def create_application(self) -> Application:
        """Application factory with validation"""
        try:
            if not self.cfg.BOT_TOKEN:
                raise ValueError("Missing bot token in configuration")
                
            return (
                ApplicationBuilder()
                .token(self.cfg.BOT_TOKEN)
                .post_init(self.post_init)
                .build()
            )
        except Exception as e:
            logger.critical("Application creation failed", exc_info=True)
            raise

# -------------------- Launch Entry Point --------------------
def run_bot():
    """Main entry point with enhanced error handling"""
    try:
        bot_app = BotApplication()
        application = bot_app.create_application()
        bot_app.setup_handlers(application)
        
        logger.info("Starting bot application...")
        application.run_polling()
    except Exception as e:
        logger.critical("Fatal error during bot operation", exc_info=True)
        raise

if __name__ == "__main__":
    run_bot()