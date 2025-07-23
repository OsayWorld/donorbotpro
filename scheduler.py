from apscheduler.schedulers.background import BackgroundScheduler
from telegram.ext import Updater
import notifications

scheduler = BackgroundScheduler()

def setup_scheduler(updater: Updater):
    # Example scheduled job - send monthly donation reminder
    scheduler.add_job(
        send_monthly_reminder,
        'cron',
        day=1,
        hour=12,
        args=[updater]
    )
    scheduler.start()

def send_monthly_reminder(updater: Updater):
    # In a real bot, you would get users from your database
    # This is just an example
    bot = updater.bot
    users = []  # Would be fetched from DB
    
    for user in users:
        try:
            bot.send_message(
                chat_id=user['chat_id'],
                text="🌱 It's a new month! Consider supporting our project with a donation /donate"
            )
        except Exception as e:
            print(f"Failed to send reminder to {user['chat_id']}: {e}")