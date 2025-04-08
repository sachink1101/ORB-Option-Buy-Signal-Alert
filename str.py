import requests
import datetime as dt
import time
import webbrowser
import threading
import logging
from fyers_api import fyersModel
from fyers_api import accessToken
from flask import Flask, request
from telegram import Bot
from apscheduler.schedulers.background import BackgroundScheduler

# === LOGGING CONFIG ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === FYERS CONFIG ===
client_id = "HI0GEEHW17-100"
secret_key = "4DI0JB13FH"
redirect_uri = "http://127.0.0.1:5000"
grant_type = "authorization_code"

# === TELEGRAM CONFIG ===
TELEGRAM_TOKEN = "7734973822:AAF4bD4SxEqJ4CKaJzakF4gRD4vIZp8bS_8"
TELEGRAM_CHAT_ID = "@tradin_capital"

# === STRATEGY CONFIG ===
lot_size = 50
stop_loss_points = 25
target_points = 50
index_symbol = "NSE:NIFTY50-INDEX"

# === INIT ===
app = Flask(__name__)
session = None
fyers = None
bot = Bot(token=TELEGRAM_TOKEN)

# === AUTH ===
@app.route('/')
def get_auth_code():
    global session, fyers
    try:
        auth_code = request.args.get("auth_code")
        if not auth_code:
            logger.error("No auth_code received")
            return "❌ No auth_code provided."
        session.set_token(auth_code)
        response = session.generate_token()
        if "access_token" not in response:
            logger.error(f"Token generation failed: {response}")
            return "❌ Token generation failed."
        access_token_val = response["access_token"]
        fyers = fyersModel.FyersModel(client_id=client_id, token=access_token_val, log_path="")
        logger.info("Authentication successful")
        return "✅ Auth done. Close this tab."
    except Exception as e:
        logger.error(f"Auth error: {e}")
        return f"❌ Auth error: {e}"

def start_auth_flowCUL():
    global session
    try:
        session = accessToken.SessionModel(
            client_id=client_id,
            secret_key=secret_key,
            redirect_uri=redirect_uri,
            response_type="code",
            grant_type=grant_type
        )
        auth_url = session.generate_authcode()
        webbrowser.open(auth_url)
        threading.Thread(target=lambda: app.run(port=5000, debug=False, use_reloader=False)).start()
        logger.info("Auth flow started")
    except Exception as e:
        logger.error(f"Error starting auth flow: {e}")

# === UTILITIES ===
def get_strike_price():
    try:
        res = fyers.quotes({"symbols": index_symbol})
        if "d" not in res or not res["d"]:
            logger.error(f"Invalid quote response: {res}")
            return None, None
        ltp = res["d"][0]["v"]["lp"]
        return ltp, round(ltp / 50) * 50
    except Exception as e:
        logger.error(f"Error getting strike price: {e}")
        return None, None

def get_option_price(symbol):
    try:
        data = fyers.quotes({"symbols": symbol})
        if "d" not in data or not data["d"]:
            logger.error(f"Invalid option quote response: {data}")
            return None
        return data["d"][0]["v"]["lp"]
    except Exception as e:
        logger.error(f"Error getting option price: {e}")
        return None

def send_alert(message):
    try:
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
        logger.info("Telegram alert sent")
    except Exception as e:
        logger.error(f"Telegram error: {e}")

def format_expiry_code():
    today = dt.datetime.now()
    expiry = today + dt.timedelta((3 - today.weekday()) % 7)  # Next Wednesday
    return expiry.strftime("%d") + expiry.strftime("%b").upper() + expiry.strftime("%y")

def get_opening_range():
    today = dt.datetime.now().strftime("%Y-%m-%d")
    payload = {
        "symbol": index_symbol,
        "resolution": "5",
        "date_format": "1",
        "range_from": today,
        "range_to": today,
        "cont_flag": "1"
    }
    try:
        candles = fyers.history(payload)["candles"]
        if len(candles) < 3:
            logger.error(f"Not enough candles: {len(candles)}")
            return None, None
        opening_candles = candles[:3]
        highs = [c[2] for c in opening_candles]
        lows = [c[3] for c in opening_candles]
        return max(highs), min(lows)
    except Exception as e:
        logger.error(f"Error getting opening range: {e}")
        return None, None

def track_sl_target(option_symbol, entry_price):
    sl = entry_price - stop_loss_points
    target = entry_price + target_points
    logger.info(f"Tracking SL/Target | Entry: ₹{entry_price:.2f} | SL: ₹{sl:.2f} | Target: ₹{target:.2f}")
    while True:
        ltp = get_option_price(option_symbol)
        if ltp is None:
            time.sleep(10)
            continue

        if ltp <= sl:
            send_alert(f"🛑 SL Hit for {option_symbol} | LTP: ₹{ltp:.2f}")
            break
        elif ltp >= target:
            send_alert(f"✅ Target Hit for {option_symbol} | LTP: ₹{ltp:.2f}")
            break

        logger.info(f"{dt.datetime.now().strftime('%H:%M:%S')} → Tracking {option_symbol} | ₹{ltp:.2f}")
        time.sleep(30)

def monitor_breakout():
    logger.info("Fetching opening range...")
    high, low = get_opening_range()
    if high is None or low is None:
        logger.error("Failed to fetch opening range, exiting monitor_breakout")
        return
    logger.info(f"Opening High: {high} | Low: {low}")

    while True:
        spot, strike = get_strike_price()
        if spot is None or strike is None:
            logger.error("Failed to get strike price, retrying...")
            time.sleep(30)
            continue
        expiry_code = format_expiry_code()
        ce_symbol = f"NSE:NIFTY{expiry_code}{strike}CE"
        pe_symbol = f"NSE:NIFTY{expiry_code}{strike}PE"

        if spot > high:
            ce_price = get_option_price(ce_symbol)
            if ce_price:
                msg = (f"🚨 CALL BREAKOUT 🚨\nSpot: ₹{spot:.2f} | Strike: {strike}CE\n"
                       f"LTP: ₹{ce_price:.2f} | SL: ₹{ce_price - stop_loss_points:.2f} | "
                       f"Target: ₹{ce_price + target_points:.2f}")
                send_alert(msg)
                track_sl_target(ce_symbol, ce_price)
                break

        elif spot < low:
            pe_price = get_option_price(pe_symbol)
            if pe_price:
                msg = (f"🚨 PUT BREAKOUT 🚨\nSpot: ₹{spot:.2f} | Strike: {strike}PE\n"
                       f"LTP: ₹{pe_price:.2f} | SL: ₹{pe_price - stop_loss_points:.2f} | "
                       f"Target: ₹{pe_price + target_points:.2f}")
                send_alert(msg)
                track_sl_target(pe_symbol, pe_price)
                break

        logger.info(f"{dt.datetime.now().strftime('%H:%M:%S')} → No breakout yet. Spot: ₹{spot:.2f}")
        time.sleep(30)

# === DAILY SCHEDULE ===
def schedule_daily_strategy():
    logger.info("Starting authentication...")
    start_auth_flowCUL()  # ✅ Fixed name

    def wait_for_auth_and_run():
        global fyers
        while fyers is None:
            logger.info("Waiting for authentication...")
            time.sleep(2)
        monitor_breakout()

    threading.Thread(target=wait_for_auth_and_run).start()

# === MAIN ===
if __name__ == "__main__":
    scheduler = BackgroundScheduler()
    scheduler.add_job(schedule_daily_strategy, 'cron', hour=9, minute=0)
    scheduler.start()

    logger.info("Scheduler started. Waiting for 9:00 AM daily trigger...")
    while True:
        time.sleep(60)
