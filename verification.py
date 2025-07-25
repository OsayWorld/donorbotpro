import asyncio
import logging
from typing import Dict, Optional, Any, List
import aiohttp
import telegram
from telegram.constants import ChatAction
from config import Config
from telegram.ext import ContextTypes
from telegram import Bot
from datetime import datetime

logger = logging.getLogger(__name__)
cfg = Config()

class GroupLinkResolver:
    """Handles Telegram group ID resolution from invite links"""
    
    @staticmethod
    async def resolve_chat_id(bot: Bot, invite_link: str) -> Optional[str]:
        """Resolve chat ID from invite link"""
        try:
            chat = await bot.get_chat(invite_link)
            return str(chat.id)
        except telegram.error.BadRequest as e:
            logger.error(f"Failed to resolve chat ID from {invite_link}: {str(e)}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error resolving chat ID: {str(e)}")
            return None

class PaymentVerifier:
    """Production-ready payment verification with tier information"""
    
    def __init__(self, bot: Optional[Bot] = None):
        self._bot = bot
        self.api_base_url = cfg.API_BASE_URL.rstrip('/')
        self.headers = {
            'Content-Type': 'application/json',
            'X-API-KEY': cfg.API_AUTH_TOKEN
        }
        self.http_timeout = aiohttp.ClientTimeout(
            total=cfg.VERIFICATION_TIMEOUT + 10
        )
        self.session = None
        self.active_verifications: Dict[str, Dict[str, Any]] = {}
        self.lock = asyncio.Lock()
        self._shutdown = False
        
        # Tier configuration with invite links
        self.tiers = [
            {
                'min': 10,
                'max': 999,
                'link': '',
                'name': 'Helpers',
                'next_tier': {
                    'min': 1000,
                    'name': 'Knights'
                }
            },
            {
                'min': 1000,
                'max': 49999,
                'link': '',
                'name': 'Knights',
                'next_tier': {
                    'min': 50000,
                    'name': 'Kings'
                }
            },
            {
                'min': 50000,
                'max': 150000,
                'link': '',
                'name': 'Kings',
                'next_tier': None
            }
        ]

    def _get_tier_for_amount(self, amount: int) -> Optional[Dict[str, Any]]:
        """Find the appropriate tier for a given amount"""
        eligible_tiers = [t for t in self.tiers if t['min'] <= amount <= t['max']]
        if not eligible_tiers:
            return None
            
        # Get highest eligible tier
        tier = max(eligible_tiers, key=lambda x: x['min'])
        
        # Calculate remaining amount for next tier
        if tier.get('next_tier'):
            tier['next_tier']['remaining'] = tier['next_tier']['min'] - amount
            
        return tier

    async def get_payment_status(self, payment_ref: str) -> Optional[Dict[str, Any]]:
        """Get payment status with robust error handling"""
        try:
            if self._shutdown:
                logger.warning(f"System shutting down, aborting verification for {payment_ref}")
                return None

            if not self.session or self.session.closed:
                transport = aiohttp.TCPConnector(
                    force_close=True,
                    enable_cleanup_closed=True
                )
                self.session = aiohttp.ClientSession(
                    timeout=self.http_timeout,
                    connector=transport
                )
            
            url = f"{self.api_base_url}/api/verify_payment"
            payload = {
                "CheckoutRequestID": payment_ref,
                "reference": payment_ref,
                "timestamp": datetime.now().isoformat()
            }
            
            async with self.session.post(url, json=payload, headers=self.headers) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    if data.get('status') is False:
                        return {
                            'status': 'pending',
                            'data': data,
                            'reference': payment_ref,
                            'timestamp': datetime.now().isoformat()
                        }
                    
                    response_data = data.get('response', {})
                    
                    if response_data.get('ResultCode') == 0:
                        return {
                            'status': 'completed',
                            'data': response_data,
                            'reference': payment_ref,
                            'timestamp': datetime.now().isoformat()
                        }
                    elif response_data.get('ResultCode') is not None:
                        return {
                            'status': 'failed',
                            'data': response_data,
                            'reference': payment_ref,
                            'timestamp': datetime.now().isoformat()
                        }
                
                logger.warning(f"API verification failed for {payment_ref}: HTTP {response.status}")
                return None
                
        except asyncio.CancelledError:
            logger.info(f"Verification cancelled for {payment_ref}")
            return None
        except Exception as e:
            logger.error(f"Verification error for {payment_ref}: {str(e)}", exc_info=True)
            return None

    async def start_verification(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Start payment verification process"""
        try:
            if self._shutdown:
                logger.warning("System shutting down, not starting new verifications")
                return

            payment_data = context.user_data.get('payment_data', {})
            payment_ref = payment_data.get('internal_ref')
            
            if not payment_ref:
                logger.error("Missing payment reference in context")
                return

            async with self.lock:
                if payment_ref in self.active_verifications:
                    logger.warning(f"Duplicate verification for {payment_ref}")
                    return
                
                self.active_verifications[payment_ref] = {
                    'context': context,
                    'bot': self._bot or context.bot,
                    'start_time': datetime.now(),
                    'task': None,
                    'user_id': payment_data.get('user_id')
                }

            verification_task = asyncio.create_task(
                self._verify_payment_loop(payment_ref),
                name=f"verify_{payment_ref}"
            )
            
            async with self.lock:
                self.active_verifications[payment_ref]['task'] = verification_task

            logger.info(f"Started verification for {payment_ref}")

        except Exception as e:
            logger.error(f"Verification setup failed: {str(e)}", exc_info=True)
            await self._notify_error(
                context.user_data.get('payment_data', {}).get('user_id'),
                "Verification failed to start"
            )

    async def _verify_payment_loop(self, payment_ref: str) -> None:
        """Main verification loop with state management"""
        max_attempts = cfg.VERIFICATION_MAX_ATTEMPTS
        attempt = 0
        
        try:
            while attempt < max_attempts and not self._shutdown:
                await asyncio.sleep(cfg.VERIFICATION_INTERVAL)
                
                status = await self.get_payment_status(payment_ref)
                if not status:
                    attempt += 1
                    continue
                
                async with self.lock:
                    verification_data = self.active_verifications.get(payment_ref)
                    if not verification_data:
                        logger.warning(f"Verification data missing for {payment_ref}")
                        return
                    
                    context = verification_data['context']
                    bot = verification_data['bot']
                
                if status['status'] == 'completed':
                    await self._handle_success(context, status, bot)
                    break
                elif status['status'] == 'failed':
                    await self._handle_failure(context, status, bot)
                    break
                
                attempt += 1
                if attempt % 3 == 0:
                    await self._send_update(context, payment_ref, attempt, max_attempts, bot)
            
            if attempt >= max_attempts and not self._shutdown:
                async with self.lock:
                    verification_data = self.active_verifications.get(payment_ref)
                    if verification_data:
                        await self._handle_timeout(verification_data['context'], verification_data['bot'])
        
        except asyncio.CancelledError:
            logger.info(f"Verification task cancelled for {payment_ref}")
        except Exception as e:
            logger.error(f"Verification crashed for {payment_ref}: {str(e)}", exc_info=True)
        finally:
            async with self.lock:
                if payment_ref in self.active_verifications:
                    self.active_verifications[payment_ref]['context'] = None
                    self.active_verifications[payment_ref]['bot'] = None
                    del self.active_verifications[payment_ref]

    async def _handle_success(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        status: Dict[str, Any],
        bot: Bot
    ) -> None:
        """Handle successful payment with tier information"""
        payment_data = context.user_data.get('payment_data', {})
        user_id = payment_data.get('user_id')
        amount = payment_data.get('amount', 0)
        
        if not user_id:
            logger.error("Missing user_id in successful payment")
            return

        try:
            tier = self._get_tier_for_amount(amount)
            
            if not tier:
                logger.error(f"No tier found for amount {amount}")
                raise ValueError("No matching tier found")
            
            message = [
                "✅ Payment verified successfully!",
                f"Amount: {amount} {payment_data.get('currency')}",
                f"Receipt: {status['data'].get('MpesaReceiptNumber', 'N/A')}",
                f"Phone: {status['data'].get('Phone', 'N/A')}",
                f"Reference: {status['reference'][-8:]}",
                "",
                f"🎉 You've unlocked the {tier['name']} tier!",
                "",
                f"Join the {tier['name']} group here:",
                tier['link']
            ]

            if tier.get('next_tier'):
                message.extend([
                    "",
                    f"💎 Need {tier['next_tier']['remaining']} more to unlock {tier['next_tier']['name']} tier!"
                ])

            await bot.send_message(
                chat_id=user_id,
                text="\n".join(message)
            )
            
            if 'payment_data' in context.user_data:
                del context.user_data['payment_data']
                
            logger.info(f"Successfully processed payment {status['reference']}")
                
        except Exception as e:
            logger.error(f"Failed to handle success: {str(e)}", exc_info=True)
            await bot.send_message(
                chat_id=user_id,
                text="⚠️ Payment verified but we encountered an error. Please contact support."
            )

    async def _handle_failure(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        status: Dict[str, Any],
        bot: Bot
    ) -> None:
        """Handle failed payment with detailed error reporting"""
        payment_data = context.user_data.get('payment_data', {})
        user_id = payment_data.get('user_id')
        
        if not user_id:
            logger.error("Missing user_id in failed payment")
            return

        try:
            error_msg = status['data'].get('ResultDesc', 'Payment failed')
            
            await bot.send_message(
                chat_id=user_id,
                text=(
                    "❌ Payment Failed\n\n"
                    f"Reason: {error_msg}\n"
                    f"Reference: {status['reference'][-8:]}\n"
                    f"Amount: {payment_data.get('amount')} {payment_data.get('currency')}\n\n"
                    "Please try again with /donate"
                )
            )
            
            if 'payment_data' in context.user_data:
                del context.user_data['payment_data']
                
            logger.warning(f"Payment failed for {status['reference']}: {error_msg}")
                
        except Exception as e:
            logger.error(f"Failed to handle failure: {str(e)}", exc_info=True)

    async def _handle_timeout(self, context: ContextTypes.DEFAULT_TYPE, bot: Bot) -> None:
        """Handle verification timeout"""
        payment_data = context.user_data.get('payment_data', {})
        user_id = payment_data.get('user_id')
        
        if not user_id:
            logger.error("Missing user_id in timeout")
            return

        try:
            await bot.send_message(
                chat_id=user_id,
                text=(
                    "⚠️ Payment Verification Timeout\n\n"
                    "We couldn't verify your payment status within the expected time.\n"
                    "If you completed the payment, please contact support with:\n"
                    f"- Reference: {payment_data.get('internal_ref', 'N/A')[-8:]}\n"
                    f"- Amount: {payment_data.get('amount')} {payment_data.get('currency')}\n"
                    f"- Phone: {payment_data.get('phone')}"
                )
            )
            
            if 'payment_data' in context.user_data:
                del context.user_data['payment_data']
                
            logger.warning(f"Verification timeout for {payment_data.get('internal_ref')}")
                
        except Exception as e:
            logger.error(f"Failed to handle timeout: {str(e)}", exc_info=True)

    async def _send_update(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        payment_ref: str,
        attempt: int,
        max_attempts: int,
        bot: Bot
    ) -> None:
        """Send periodic verification updates"""
        payment_data = context.user_data.get('payment_data', {})
        user_id = payment_data.get('user_id')
        
        if not user_id:
            return

        try:
            progress = min(100, int((attempt / max_attempts) * 100))
            await bot.send_message(
                chat_id=user_id,
                text=(
                    f"⏳ Payment Verification Progress: {progress}%\n"
                    f"Attempt {attempt} of {max_attempts}\n"
                    f"Reference: {payment_ref[-8:]}\n\n"
                    "Please wait while we confirm your payment..."
                )
            )
        except Exception as e:
            logger.warning(f"Failed to send update: {str(e)}")

    async def _notify_error(self, user_id: Optional[int], message: str) -> None:
        """Notify user of errors"""
        if not user_id or not self._bot:
            return
            
        try:
            await self._bot.send_message(
                chat_id=user_id,
                text=f"⚠️ {message}\n\nPlease try again later or contact support."
            )
        except Exception as e:
            logger.warning(f"Failed to send error notification: {str(e)}")

    async def cleanup_verification_tasks(self) -> None:
        """Clean up all active verification tasks"""
        async with self.lock:
            for payment_ref, data in list(self.active_verifications.items()):
                if data['task'] and not data['task'].done():
                    data['task'].cancel()
                    try:
                        await data['task']
                    except asyncio.CancelledError:
                        logger.info(f"Cancelled verification for {payment_ref}")
                    except Exception as e:
                        logger.error(f"Error cancelling task {payment_ref}: {str(e)}")
                
                data['context'] = None
                data['bot'] = None
                del self.active_verifications[payment_ref]

    async def shutdown(self) -> None:
        """Graceful shutdown"""
        self._shutdown = True
        await self.cleanup_verification_tasks()
        await self.close()

    async def close(self) -> None:
        """Clean up resources"""
        try:
            await self.cleanup_verification_tasks()
            
            if self.session and not self.session.closed:
                await self.session.close()
                self.session = None
                
            logger.info("Payment verifier shutdown completed")
        except Exception as e:
            logger.error(f"Error during shutdown: {str(e)}", exc_info=True)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
