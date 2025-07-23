from telegram.ext import Application
from config import Config
import logging
import sys
import asyncio
import platform
from typing import Optional
from sync import initialize_database  # Import the database initialization function

class BotRunner:
    """Enhanced bot runner with proper database initialization"""
    
    def __init__(self):
        self.logger = self._configure_logging()
        self.cfg = Config()
        self.application: Optional[Application] = None

    @staticmethod
    def _configure_logging() -> logging.Logger:
        """Configure structured logging with rotation"""
        logger = logging.getLogger()
        logger.setLevel(logging.INFO)
        
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        
        # File handler with rotation
        try:
            from logging.handlers import RotatingFileHandler
            file_handler = RotatingFileHandler(
                'bot.log',
                maxBytes=5*1024*1024,  # 5MB
                backupCount=3,
                encoding='utf-8'
            )
            file_handler.setFormatter(formatter)
        except ImportError:
            file_handler = logging.FileHandler('bot.log', encoding='utf-8')
        
        logger.addHandler(console_handler)
        logger.addHandler(file_handler)
        
        return logging.getLogger(__name__)

    async def _initialize_application(self) -> None:
        """Initialize and configure the bot application with database setup"""
        try:
            # Initialize database FIRST
            self.logger.info("Initializing database...")
            try:
                initialize_database()  # This ensures tables exist
                self.logger.info("Database initialized successfully")
            except Exception as db_error:
                self.logger.critical(f"Database initialization failed: {db_error}")
                raise RuntimeError("Database setup failed") from db_error

            from bot import BotApplication
            
            self.logger.info("Initializing donation bot...")
            
            # Create and configure bot application
            bot_app = BotApplication()
            self.application = bot_app.create_application()
            
            try:
                bot_app.setup_handlers(self.application)
            except SyntaxError as e:
                self.logger.critical(f"Syntax error in handlers: {e}")
                raise
            except ImportError as e:
                self.logger.critical(f"Import error in handlers: {e}")
                raise
                
            # Debug handler registration
            if self.logger.isEnabledFor(logging.DEBUG):
                for handler_group in self.application.handlers.values():
                    for handler in handler_group:
                        if hasattr(handler, 'commands'):
                            self.logger.debug(f"Registered command: {handler.commands}")
                        elif hasattr(handler, 'pattern'):
                            self.logger.debug(f"Registered callback: {handler.pattern}")

        except Exception as e:
            self.logger.critical(f"Application initialization failed: {e}")
            raise

    async def _run_polling(self) -> None:
        """Start the bot in polling mode"""
        if not self.application:
            raise RuntimeError("Application not initialized")
            
        self.logger.info("Starting bot polling...")
        try:
            await self.application.initialize()
            await self.application.start()
            
            if self.application.updater:
                await self.application.updater.start_polling()
            
            self.logger.info("Bot is now running. Press Ctrl+C to stop.")
            while True:
                await asyncio.sleep(3600)  # Sleep indefinitely
        except Exception as e:
            self.logger.error(f"Polling error: {e}")
            raise

    async def _shutdown(self) -> None:
        """Graceful shutdown procedure with database cleanup"""
        if not self.application:
            return
            
        self.logger.info("Initiating graceful shutdown...")
        try:
            if self.application.updater:
                await self.application.updater.stop()
            await self.application.stop()
            await self.application.shutdown()
            
            # Add any additional cleanup here
            from sync import database
            if hasattr(database, 'cleanup_expired'):
                database.cleanup_expired()
                
        except Exception as e:
            self.logger.error(f"Error during shutdown: {e}")
        finally:
            self.logger.info("Bot shutdown complete")

    async def run(self) -> None:
        """Main execution flow with enhanced error handling"""
        try:
            await self._initialize_application()
            await self._run_polling()
        except asyncio.CancelledError:
            self.logger.info("Bot shutdown requested")
        except Exception as e:
            self.logger.critical(f"Bot startup failed: {e}", exc_info=True)
            raise
        finally:
            await self._shutdown()

def main():
    """Entry point with platform-specific setup"""
    # Windows-specific event loop policy
    if platform.system() == 'Windows':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    runner = BotRunner()
    
    try:
        asyncio.run(runner.run())
    except KeyboardInterrupt:
        print("\nBot stopped by user")
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()