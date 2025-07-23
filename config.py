import os
from dotenv import load_dotenv
from typing import List, Union

load_dotenv()  # Load environment variables from .env file

class Config:
    """Centralized configuration for the donation bot"""
    
    def __init__(self):
        # Telegram Configuration
        self.BOT_TOKEN = os.getenv("BOT_TOKEN")
        self.ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID"))
        self.BOT_USERNAME = os.getenv("BOT_USERNAME")
        self.TELEGRAM_CHANNEL_LINK = os.getenv("TELEGRAM_CHANNEL_LINK")
        self.SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME")
        
        # Database Configuration
        self.DATABASE_PATH = os.getenv("DATABASE_PATH", "payments.db")
        self.DB_URI = f"sqlite:///{self.DATABASE_PATH}"
        self.DB_ECHO = self._str_to_bool(os.getenv("DB_ECHO", "False"))
        self.DB_TIMEOUT = int(os.getenv("DB_TIMEOUT", "30"))
        self.DB_JOURNAL_MODE = os.getenv("DB_JOURNAL_MODE", "WAL")
        
        # Payment Gateway Configuration
        self.PAYHERO_API_KEY = os.getenv("PAYHERO_API_KEY")
        self.PAYHERO_CHANNEL_ID = int(os.getenv("PAYHERO_CHANNEL_ID"))
        self.PAYHERO_CALLBACK_URL = os.getenv("PAYHERO_CALLBACK_URL")
        self.MIN_DONATION = int(os.getenv("MIN_DONATION", "10"))
        self.CURRENCY = os.getenv("CURRENCY", "KES")
        self.DONATION_AMOUNTS = self._parse_donation_amounts(os.getenv("DONATION_AMOUNTS", "10,50,100,500,1000"))
        
        # API Configuration
        self.API_BASE_URL = os.getenv("API_BASE_URL")
        self.API_AUTH_TOKEN = os.getenv("API_AUTH_TOKEN")
        self.FLASK_API_URL = f"{self.API_BASE_URL}/api"
        self.API_VERIFY_ENDPOINT = os.getenv("API_VERIFY_ENDPOINT", "/api/verify_payment")
        
        # HTTP Configuration
        self.HTTPX_TIMEOUT = float(os.getenv("HTTPX_TIMEOUT", "30.0"))
        self.HTTPX_RETRIES = int(os.getenv("HTTPX_RETRIES", "3"))
        self.TELEGRAM_TIMEOUT = float(os.getenv("TELEGRAM_TIMEOUT", "20.0"))
        self.TELEGRAM_RETRIES = int(os.getenv("TELEGRAM_RETRIES", "3"))
        
        # System Configuration
        self.SCHEDULER_ENABLED = self._str_to_bool(os.getenv("SCHEDULER_ENABLED", "True"))
        self.SCHEDULER_TIMEZONE = os.getenv("SCHEDULER_TIMEZONE", "Africa/Nairobi")
        self.MAX_PAYMENT_ATTEMPTS = int(os.getenv("MAX_PAYMENT_ATTEMPTS", "3"))
        self.PAYMENT_TIMEOUT = int(os.getenv("PAYMENT_TIMEOUT", "1500"))
        self.VERIFICATION_RETRY_DELAY = int(os.getenv("VERIFICATION_RETRY_DELAY", "5"))
        self.VERIFICATION_MAX_ATTEMPTS = int(os.getenv("VERIFICATION_MAX_ATTEMPTS", "6"))
        self.VERIFICATION_TIMEOUT = int(os.getenv("VERIFICATION_TIMEOUT", "300"))
        self.VERIFICATION_INTERVAL = int(os.getenv("VERIFICATION_INTERVAL", "15"))
        
        # Security
        self.API_RATE_LIMIT = os.getenv("API_RATE_LIMIT", "100 per hour")
        
        self._validate()

    def _str_to_bool(self, value: str) -> bool:
        """Convert string to boolean"""
        return value.lower() in ('true', '1', 't', 'y', 'yes')

    def _parse_donation_amounts(self, amounts_str: str) -> List[int]:
        """Parse donation amounts from comma-separated string"""
        return [int(amount.strip()) for amount in amounts_str.split(",")]

    def get_database_config(self):
        """Returns database-related configuration"""
        return {
            'database_path': self.DATABASE_PATH,
            'uri': self.DB_URI,
            'echo': self.DB_ECHO,
            'timeout': self.DB_TIMEOUT,
            'journal_mode': self.DB_JOURNAL_MODE
        }

    def _validate(self):
        """Validate all critical configuration values"""
        required_configs = {
            'BOT_TOKEN': (str, lambda x: len(x) > 30 and ':' in x),
            'ADMIN_CHAT_ID': (int, lambda x: x > 0),
            'PAYHERO_API_KEY': (str, lambda x: len(x) > 30),
            'PAYHERO_CHANNEL_ID': (int, lambda x: x > 0),
            'MIN_DONATION': (int, lambda x: x > 0),
            'API_BASE_URL': (str, lambda x: x.startswith(('http://', 'https://'))),
            'TELEGRAM_CHANNEL_LINK': (str, lambda x: x.startswith('https://t.me/')),
            'SUPPORT_USERNAME': (str, lambda x: len(x) > 3),
            'VERIFICATION_MAX_ATTEMPTS': (int, lambda x: x > 0),
            'VERIFICATION_RETRY_DELAY': (int, lambda x: x > 0),
            'HTTPX_TIMEOUT': (float, lambda x: x > 0),
            'HTTPX_RETRIES': (int, lambda x: x > 0),
            'TELEGRAM_TIMEOUT': (float, lambda x: x > 0),
            'TELEGRAM_RETRIES': (int, lambda x: x > 0)
        }
        
        for name, (type_, validator) in required_configs.items():
            value = getattr(self, name, None)
            if value is None or not isinstance(value, type_) or not validator(value):
                raise ValueError(f"Invalid configuration for {name}")

        if any(amount < self.MIN_DONATION for amount in self.DONATION_AMOUNTS):
            raise ValueError("Donation amounts must be >= MIN_DONATION")

    def get_telegram_config(self):
        """Returns telegram-related configuration"""
        return {
            'bot_token': self.BOT_TOKEN,
            'admin_chat_id': self.ADMIN_CHAT_ID,
            'bot_username': self.BOT_USERNAME,
            'channel_link': self.TELEGRAM_CHANNEL_LINK,
            'support_username': self.SUPPORT_USERNAME
        }

    def get_payment_config(self):
        """Returns payment-related configuration"""
        return {
            'api_key': self.PAYHERO_API_KEY,
            'channel_id': self.PAYHERO_CHANNEL_ID,
            'callback_url': self.PAYHERO_CALLBACK_URL,
            'min_amount': self.MIN_DONATION,
            'currency': self.CURRENCY,
            'amounts': self.DONATION_AMOUNTS
        }