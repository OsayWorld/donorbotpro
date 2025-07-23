"""
Enhanced M-Pesa Payment Processor with Phone Number Validation
"""

import re
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Union

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    Bot,
    Message
)
from telegram.error import RetryAfter, TimedOut
from telegram.ext import (
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    CommandHandler
)
from httpx import AsyncClient, Timeout, Limits, AsyncHTTPTransport

from config import Config
from sync import DatabaseSync
from notifications import PaymentNotifier
from verification import PaymentVerifier


# Constants
PAYMENT_API_URL = "https://backend.payhero.co.ke/api/v2/payments"
PHONE_REGEX = re.compile(r'^07\d{8}$')
CUSTOM_AMOUNT_KEY = 'awaiting_custom_amount'
MAX_AMOUNT = 150000  # 150,000 KES

# Initialize modules
logger = logging.getLogger(__name__)
cfg = Config()
db = DatabaseSync()  # Using the advanced DatabaseSync


class PaymentProcessor:
    """Handles M-Pesa payments with robust phone number validation"""
    
    def __init__(self, bot: Bot = None):
        self.bot = bot
        self.notifier = PaymentNotifier(bot)
        self.verifier = PaymentVerifier(bot)
        self.active_payments = {}
        self.payment_lock = asyncio.Lock()
        
        self.http_client = AsyncClient(
            timeout=Timeout(cfg.HTTPX_TIMEOUT),
            limits=Limits(max_connections=100),
            transport=AsyncHTTPTransport(retries=3))
        
        self.auth_header = self._prepare_auth_header(cfg.PAYHERO_API_KEY)

    def _prepare_auth_header(self, api_key: str) -> str:
        """Prepare Basic Auth header from API key"""
        return f"Basic {api_key}" if not api_key.startswith("Basic ") else api_key

    async def start_donation_flow(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Entry point for donation flow"""
        try:
            user = update.effective_user
            logger.info(f"Starting donation flow for user {user.id}")
            
            # Store/update user profile
            db.update_user_profile({
                'user_id': user.id,
                'first_name': user.first_name,
                'last_name': user.last_name,
                'username': user.username,
                'language_code': user.language_code,
                'is_premium': user.is_premium or False
            })
            
            context.user_data.clear()
            context.user_data['flow_start'] = datetime.now()
            
            keyboard = [
                [InlineKeyboardButton(f"🌟 {amount} {cfg.CURRENCY}", 
                 callback_data=f"amount_{amount}")]
                for amount in cfg.DONATION_AMOUNTS
            ]
            keyboard.append([InlineKeyboardButton("📝 Any Amount", 
                             callback_data="custom_amount")])
            
            await self._safe_send_message(
                update.message,
                "💙 Support Our Community 💙\n\n"
                "Choose donation amount:",
                reply_markup=InlineKeyboardMarkup(keyboard))
            
        except Exception as e:
            logger.error(f"Donation start failed: {e}", exc_info=True)
            await self._send_error_message(update, "start")

    async def handle_amount_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Process amount selection from callback"""
        try:
            query = update.callback_query
            await query.answer()
            
            if query.data == "custom_amount":
                context.user_data[CUSTOM_AMOUNT_KEY] = True
                await self._safe_edit_message_text(
                    query.message,
                    f"📝 Enter amount ({cfg.MIN_DONATION}-{MAX_AMOUNT} {cfg.CURRENCY}):\n"
                    "Type /cancel to abort.")
            else:
                amount = int(query.data.split('_')[1])
                if not self._validate_amount(amount):
                    raise ValueError("Invalid amount")
                    
                context.user_data['amount'] = amount
                await self._safe_edit_message_text(
                    query.message,
                    f"✅ Amount: {amount} {cfg.CURRENCY}\n\n"
                    "📱 Enter your M-Pesa number (07XXXXXXXX):")
                
        except Exception as e:
            logger.error(f"Amount selection failed: {e}")
            await self._send_error_message(update, "amount", query.message)

    async def process_custom_amount(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Process custom amount input"""
        try:
            if not context.user_data.get(CUSTOM_AMOUNT_KEY):
                return
                
            if self._check_timeout(context):
                await self._safe_send_message(
                    update.message,
                    "⌛ Session expired. Use /donate to restart")
                return
                
            try:
                amount = int(update.message.text.strip())
                if not self._validate_amount(amount):
                    await self._safe_send_message(
                        update.message,
                        f"❌ Amount must be {cfg.MIN_DONATION}-{MAX_AMOUNT} {cfg.CURRENCY}")
                    return
                    
                context.user_data.update({
                    'amount': amount,
                    CUSTOM_AMOUNT_KEY: False
                })
                await self._safe_send_message(
                    update.message,
                    f"✅ Amount: {amount} {cfg.CURRENCY}\n\n"
                    "📱 Enter your M-Pesa number (07XXXXXXXX):")
                
            except ValueError:
                await self._safe_send_message(
                    update.message,
                    "❌ Please enter numbers only")
                
        except Exception as e:
            logger.error(f"Custom amount processing failed: {e}")
            await self._send_error_message(update, "custom_amount", update.message)

    async def process_phone_number(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
     """Process M-Pesa number with complete user profile handling"""
     try:
        if self._check_timeout(context):
            await self._safe_send_message(
                update.message,
                "⌛ Session expired. Use /donate to restart")
            return
            
        amount = context.user_data.get('amount')
        if not amount:
            await self._safe_send_message(
                update.message,
                "❌ No amount selected. Use /donate")
            return
            
        phone = update.message.text.strip()
        if not PHONE_REGEX.match(phone):
            await self._safe_send_message(
                update.message,
                "⚠️ Invalid M-Pesa number\n"
                "Format: 07XXXXXXXX\n"
                "Example: 0712345678")
            return
            
        # Get complete user data with defaults
        user = update.effective_user
        profile_data = {
            'user_id': user.id,
            'first_name': user.first_name or '',
            'last_name': user.last_name or '',
            'username': user.username or '',
            'phone': phone,
            'language_code': user.language_code or '',
            'is_premium': user.is_premium or False
        }
        
        # Update user profile with all required fields
        if not db.update_user_profile(profile_data):
            raise Exception("Failed to update user profile with phone number")
        
        # Initiate payment
        processing_msg = await self._safe_send_message(
            update.message,
            "⏳ Initiating payment request...\n\n"
            f"Amount: {amount} {cfg.CURRENCY}\n"
            f"Phone: {phone}")
        
        await self._initiate_payment(
            context=context,
            user=user,
            phone=phone,
            amount=amount,
            processing_msg=processing_msg)
        
     except Exception as e:
        logger.error(f"Phone processing failed: {e}", exc_info=True)
        await self._handle_payment_failure(
            context=context,
            processing_msg=update.message,
            error=str(e),
            is_connection_error=isinstance(e, (asyncio.TimeoutError, ConnectionError)))

    async def _initiate_payment(self, context: ContextTypes.DEFAULT_TYPE, user: Any, 
                             phone: str, amount: int, processing_msg: Message) -> None:
        """Handle STK Push initiation with phone validation"""
        try:
            # Double-check phone exists in user profile
            user_data = db.get_user(user.id)
            if not user_data or not user_data.get('phone'):
                raise ValueError("Phone number not registered in user profile")
                
            formatted_phone = f"254{phone[1:]}"  # Convert to international format
            external_ref = f"DON-{user.id}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            
            # Prepare payment payload
            payload = {
                "amount": amount,
                "phone_number": formatted_phone,
                "channel_id": cfg.PAYHERO_CHANNEL_ID,
                "provider": "m-pesa",
                "external_reference": external_ref,
                "customer_name": f"{user.first_name} {user.last_name}",
                "callback_url": cfg.PAYHERO_CALLBACK_URL
            }
            
            headers = {
                "Authorization": self.auth_header,
                "Content-Type": "application/json"
            }
            
            # Call PayHero API
            response = await self._call_payment_gateway(payload, headers)
            payment_ref = response['reference']
            
            # Store payment data in context
            context.user_data['payment_data'] = {
                'internal_ref': external_ref,
                'gateway_ref': payment_ref,
                'checkout_id': response['CheckoutRequestID'],
                'amount': amount,
                'phone': phone,
                'user_id': user.id,
                'chat_id': processing_msg.chat_id,
                'message_id': processing_msg.message_id,
                'currency': cfg.CURRENCY,
                'verification_start': datetime.now().isoformat()
            }
            
            # Log payment in database
            payment_id = db.log_payment({
                'internal_ref': external_ref,
                'gateway_ref': payment_ref,
                'checkout_id': response['CheckoutRequestID'],
                'user_id': user.id,
                'amount': amount,
                'currency': cfg.CURRENCY,
                'phone': phone,
                'status': 'pending',
                'gateway_name': 'mpesa',
                'expiry_time': datetime.now() + timedelta(seconds=cfg.VERIFICATION_TIMEOUT)
            })
            
            if not payment_id:
                raise Exception("Failed to log payment in database")
            
            # Store phone mapping for callback handling
            db.store_phone_mapping(
                phone=phone,
                user_id=user.id,
                chat_id=processing_msg.chat_id,
                message_id=processing_msg.message_id,
                expiry=datetime.now() + timedelta(hours=1)
            )
            
            # Update user message
            await self._safe_edit_message_text(
                processing_msg,
                "✅ Payment Request Sent!\n\n"
                f"📱 Sent to: {phone}\n"
                f"💵 Amount: {amount} {cfg.CURRENCY}\n"
                f"🔖 Ref: {payment_ref[-8:]}\n\n"
                "Check your phone to complete payment...")
            
            # Track active payment
            async with self.payment_lock:
                self.active_payments[external_ref] = {
                    'context': context,
                    'processing_msg': processing_msg,
                    'expiry': datetime.now() + timedelta(seconds=cfg.VERIFICATION_TIMEOUT)
                }
            
            # Start verification
            await self.verifier.start_verification(context)
            
        except Exception as e:
            logger.error(f"Payment initiation failed: {e}", exc_info=True)
            await self._handle_payment_failure(
                context,
                processing_msg,
                str(e),
                is_connection_error=isinstance(e, (asyncio.TimeoutError, ConnectionError)))

    async def _call_payment_gateway(self, payload: Dict, headers: Dict) -> Dict:
        """Make API call to payment gateway"""
        try:
            response = await self.http_client.post(
                PAYMENT_API_URL,
                json=payload,
                headers=headers
            )
            response.raise_for_status()
            data = response.json()
            
            if not data.get('success'):
                raise Exception(data.get('message', 'Payment request failed'))
                
            return {
                'reference': data['reference'],
                'CheckoutRequestID': data['CheckoutRequestID'],
                'status': data['status']
            }
        except Exception as e:
            logger.error(f"Payment gateway call failed: {e}")
            raise Exception("Payment service unavailable. Please try again later.")

    async def _handle_payment_failure(self, context: ContextTypes.DEFAULT_TYPE,
                                    processing_msg: Message, error: str, 
                                    is_connection_error: bool = False) -> None:
        """Enhanced payment failure handler with phone-specific messaging"""
        try:
            if "phone" in error.lower():
                error_display = ("📱 Phone Number Required\n\n"
                               "Please register your phone number first.\n"
                               "Use /donate to try again.")
            else:
                error_display = ("⚠️ Service Unavailable\n\nPayment service is currently unavailable.\n"
                               if is_connection_error else
                               f"❌ Payment Failed\n\nReason: {error}\n\nPlease try again with /donate")
            
            await self._safe_edit_message_text(processing_msg, error_display)
            
            if 'payment_data' in context.user_data:
                db.update_payment_status(
                    reference=context.user_data['payment_data']['internal_ref'],
                    status='failed',
                    error_message=error
                )
        except Exception as e:
            logger.error(f"Failed to handle payment failure: {e}")
            await self._safe_send_message(
                processing_msg,
                "⚠️ An error occurred while processing your payment. Please contact support.")

    def _validate_amount(self, amount: int) -> bool:
        """Validate donation amount"""
        return cfg.MIN_DONATION <= amount <= MAX_AMOUNT

    def _check_timeout(self, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """Check if session has timed out"""
        start = context.user_data.get('flow_start')
        return not start or (datetime.now() - start).seconds > cfg.PAYMENT_TIMEOUT

    async def _send_error_message(self, update: Update, flow_stage: str, message: Optional[Message] = None) -> None:
        """Send appropriate error message"""
        messages = {
            "start": "Failed to start donation process",
            "amount": "Invalid amount selection",
            "custom_amount": "Invalid custom amount",
            "payment": "Payment processing failed"
        }
        
        if not message:
            if hasattr(update, 'message') and update.message:
                message = update.message
            elif hasattr(update, 'callback_query') and update.callback_query:
                message = update.callback_query.message
        
        if not message:
            logger.error(f"No message available to send error for {flow_stage}")
            return
            
        try:
            await self._safe_send_message(
                message,
                f"❌ {messages.get(flow_stage, 'An error occurred')}\n\n"
                "Please try again with /donate")
        except Exception as e:
            logger.error(f"Failed to send error message: {e}")

    async def _safe_send_message(self, message: Message, text: str, **kwargs) -> Message:
        """Send message with retry logic"""
        for attempt in range(cfg.TELEGRAM_RETRIES):
            try:
                return await message._bot.send_message(
                    chat_id=message.chat_id,
                    text=text,
                    reply_to_message_id=message.message_id,
                    **kwargs
                )
            except RetryAfter as e:
                if attempt == cfg.TELEGRAM_RETRIES - 1:
                    raise
                await asyncio.sleep(e.retry_after)
            except TimedOut:
                if attempt == cfg.TELEGRAM_RETRIES - 1:
                    raise
                await asyncio.sleep(1)
            except Exception:
                if attempt == cfg.TELEGRAM_RETRIES - 1:
                    raise
                await asyncio.sleep(1)

    async def _safe_edit_message_text(self, message: Message, text: str, **kwargs) -> Message:
        """Edit message with retry logic"""
        for attempt in range(cfg.TELEGRAM_RETRIES):
            try:
                return await message.edit_text(text, **kwargs)
            except RetryAfter as e:
                if attempt == cfg.TELEGRAM_RETRIES - 1:
                    raise
                await asyncio.sleep(e.retry_after)
            except TimedOut:
                if attempt == cfg.TELEGRAM_RETRIES - 1:
                    raise
                await asyncio.sleep(1)
            except Exception:
                if attempt == cfg.TELEGRAM_RETRIES - 1:
                    raise
                await asyncio.sleep(1)

    async def cancel_payment(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Cancel current payment session with proper cleanup"""
        try:
            if 'payment_data' in context.user_data:
                payment_ref = context.user_data['payment_data']['internal_ref']
                
                # Update database
                db.update_payment_status(
                    reference=payment_ref,
                    status='cancelled',
                    error_message='User cancelled'
                )
                
                # Remove from active payments
                async with self.payment_lock:
                    if payment_ref in self.active_payments:
                        del self.active_payments[payment_ref]
                
            await self.verifier.cleanup_verification_tasks()
            context.user_data.clear()
            
            await self._safe_send_message(
                update.message,
                "❌ Payment cancelled. Use /donate to start over.")
                
        except Exception as e:
            logger.error(f"Payment cancellation failed: {e}")
            await self._safe_send_message(
                update.message,
                "⚠️ Failed to cancel payment. Please try again.")

    def register_handlers(self, application):
        """Register all payment handlers"""
        handlers = [
            CommandHandler("donate", self.start_donation_flow),
            CommandHandler("cancel", self.cancel_payment),
            CallbackQueryHandler(self.handle_amount_selection, pattern="^amount_"),
            CallbackQueryHandler(self.handle_amount_selection, pattern="^custom_amount$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(PHONE_REGEX), 
                self.process_phone_number),
            MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(r'^\d+$'), 
                self.process_custom_amount)
        ]
        application.add_handlers(handlers)
        logger.info("Payment handlers registered")


    async def cleanup(self):
        """Clean up resources"""
        try:
            async with self.payment_lock:
                self.active_payments.clear()
                
            if hasattr(self, 'verifier'):
                await self.verifier.cleanup_verification_tasks()
                
            await self.http_client.aclose()
        except Exception as e:
            logger.error(f"Cleanup failed: {e}")

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle errors"""
        logger.error(msg="Exception while handling update:", exc_info=context.error)
        
        if update and hasattr(update, 'effective_message'):
            try:
                await update.effective_message.reply_text(
                    "⚠️ An error occurred. Please try again later."
                )
            except Exception:
                pass