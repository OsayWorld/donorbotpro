from telegram import Bot, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode
from datetime import datetime
import logging
from config import Config
from sync import DatabaseSync

logger = logging.getLogger(__name__)
cfg = Config()

class PaymentNotifier:
    """Handles all payment-related notifications using SQLite database"""
    
    def __init__(self, bot: Bot):
        self.bot = bot
        self.admin_chat = cfg.ADMIN_CHAT_ID
        self.support_username = cfg.SUPPORT_USERNAME
        self.db = DatabaseSync()

    async def send_admin_alert(self, message: str, urgent: bool = False):
        """Send notification to admin with priority handling"""
        try:
            prefix = "🚨 URGENT: " if urgent else "ℹ️ "
            await self.bot.send_message(
                chat_id=self.admin_chat,
                text=f"{prefix}{message}",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Failed to send admin alert: {e}")

    async def send_donation_receipt(self, payment_data: dict):
        """
        Send thank you message with payment details to donor
        Args:
            payment_data (dict): {
                'user_id': int,
                'chat_id': int,
                'user_name': str,
                'amount': float,
                'phone_number': str,
                'mpesa_receipt': str,
                'currency': str (optional)
            }
        """
        try:
            # Log payment in database
            self.db.log_payment({
                'reference': payment_data.get('mpesa_receipt'),
                'phone': payment_data.get('phone_number'),
                'amount': payment_data.get('amount'),
                'status': 'completed',
                'user_id': payment_data.get('user_id'),
                'user_name': payment_data.get('user_name'),
                'chat_id': payment_data.get('chat_id'),
                'currency': payment_data.get('currency', cfg.CURRENCY)
            })

            # Get user info
            user = self.db.get_user(payment_data['user_id'])
            if not user:
                logger.warning(f"User {payment_data['user_id']} not found in database")
                return False

            # Get accessible channels
            channels = self.db.get_channels_by_donation(payment_data['amount'])
            
            # Build message
            message = [
                f"💖 Thank you for your donation of {payment_data['amount']} {payment_data.get('currency', cfg.CURRENCY)}!",
                f"📅 Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                f"📱 Phone: {payment_data.get('phone_number', 'N/A')}",
                f"📝 Receipt: {payment_data.get('mpesa_receipt', 'Pending')}"
            ]

            # Add channel access if available
            keyboard = []
            if channels:
                message.append("\n🔓 <b>New Channel Access:</b>")
                for channel in channels:
                    if channel.get('invite_link'):
                        message.append(f"- {channel['title']}")
                        keyboard.append([InlineKeyboardButton(
                            text=f"Join {channel['title']}",
                            url=channel['invite_link']
                        )])

            await self.bot.send_message(
                chat_id=payment_data['chat_id'],
                text="\n".join(message),
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
            )
            return True
            
        except Exception as e:
            logger.error(f"Failed to send donation receipt: {e}")
            return False

    async def notify_verification_timeout(self, payment_data: dict):
        """Handle payment verification timeout scenarios"""
        try:
            admin_msg = (
                f"⚠️ <b>Payment Verification Timeout</b>\n\n"
                f"👤 User: {payment_data.get('user_name', 'Unknown')}\n"
                f"📱 Phone: {payment_data.get('phone', 'N/A')}\n"
                f"💵 Amount: {payment_data.get('amount', 0)} {payment_data.get('currency', 'KES')}\n"
                f"🔖 References:\n"
                f"- Internal: {payment_data.get('payment_reference', 'N/A')}\n"
                f"- Gateway: {payment_data.get('gateway_reference', 'N/A')}\n\n"
                f"Please verify manually with @{self.support_username}"
            )
            
            await self.bot.send_message(
                chat_id=self.admin_chat,
                text=admin_msg,
                parse_mode=ParseMode.HTML
            )

            if payment_data.get('chat_id'):
                user_msg = (
                    "⏳ We're still verifying your payment\n\n"
                    "Our team has been notified and will contact you shortly.\n"
                    f"For faster assistance, contact @{self.support_username}"
                )
                
                await self.bot.send_message(
                    chat_id=payment_data['chat_id'],
                    text=user_msg,
                    parse_mode=ParseMode.HTML
                )
                
        except Exception as e:
            logger.error(f"Failed to send timeout notifications: {e}")

    async def notify_payment_failure(self, payment_data: dict, error_msg: str):
        """Handle payment failure notifications"""
        try:
            admin_msg = (
                f"❌ <b>Payment Failed</b>\n\n"
                f"User: {payment_data.get('user_name', 'Unknown')}\n"
                f"Amount: {payment_data.get('amount', 0)} {cfg.CURRENCY}\n"
                f"Error: {error_msg}\n\n"
                f"References:\n"
                f"- Internal: {payment_data.get('payment_reference', 'N/A')}\n"
                f"- Gateway: {payment_data.get('gateway_reference', 'N/A')}"
            )
            
            await self.send_admin_alert(admin_msg, urgent=True)

            if payment_data.get('chat_id'):
                user_msg = (
                    "❌ Payment Failed\n\n"
                    f"Reason: {error_msg}\n\n"
                    "Please try again or contact support if the issue persists."
                )
                
                await self.bot.send_message(
                    chat_id=payment_data['chat_id'],
                    text=user_msg,
                    parse_mode=ParseMode.HTML
                )
                
        except Exception as e:
            logger.error(f"Failed to send failure notifications: {e}")

    async def notify_system_alert(self, title: str, details: str):
        """Send critical system alerts to admin"""
        try:
            message = (
                f"🚨 <b>{title}</b>\n\n"
                f"{details}\n\n"
                f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            
            await self.bot.send_message(
                chat_id=self.admin_chat,
                text=message,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Failed to send system alert: {e}")

# Legacy compatibility functions
async def notify_admin(bot: Bot, message: str):
    """Legacy admin notification"""
    notifier = PaymentNotifier(bot)
    await notifier.send_admin_alert(message)

async def thank_you_notification(bot: Bot, chat_id: int, amount: int):
    """Legacy thank you notification"""
    notifier = PaymentNotifier(bot)
    await notifier.send_donation_receipt({
        'user_id': chat_id,
        'chat_id': chat_id,
        'amount': amount,
        'phone_number': 'N/A',
        'mpesa_receipt': 'LEGACY_PAYMENT'
    })

async def notify_payment_timeout(bot: Bot, payment_data: dict):
    """Legacy timeout notification"""
    notifier = PaymentNotifier(bot)
    await notifier.notify_verification_timeout(payment_data)