import requests
import datetime as dt
import time
import webbrowser
import threading
import logging
import os
import json
from fyers_api import fyersModel
from fyers_api import accessToken
from flask import Flask, request
from telegram import Bot
from apscheduler.schedulers.background import BackgroundScheduler
from pytz import timezone

# === CONFIG ===
client_id = "HI0GEEHW17-100"
secret_key = "4DI0JB13FH"
redirect_uri = "http://127.0.0.1:5000"
grant_type = "authorization_code"
TELEGRAM_TOKEN = "7734973822:AAF4bD4SxEqJ4CKaJzakF4gRD4vIZp8bS_8"
TELEGRAM_CHAT_ID = "@tradin_capital"
TOKEN_FILE = "tokens.json"
lot_size = 50
stop_loss_points = 25
target_points = 50
index_symbol = "NSE:NIFTY50-INDEX"

# === INIT ===
app = Flask(__name__)
session = None
fyers = None
bot = Bot(token=TELEGRAM_TOKEN)

# === LOGGING CONFIG ===
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === AUTH HANDLING ===
def save_tokens(access_token, refresh_token):
    with open(TOKEN_FILE, "w") as f:
        json.dump({"access_token": access_token, "refresh_token": refresh_token}, f)

def load_tokens():
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r") as f:
            return json.load(f)
    return None

def refresh_access_token():
    tokens = load_tokens()
    if not tokens or "refresh_token" not in tokens:
        logger.warning("No refresh token found. Please authenticate manually once.")
        start_auth_flowCUL()
        return None

    refresh_token_val = tokens["refresh_token"]
    response = requests.post(
        url="https://api.fyers.in/api/v2/token",
        json={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "secret_key": secret_key,
            "refresh_token": refresh_token_val
        },
        headers={"Content-Type": "application/json"}
    ).json()

    if "access_token" in response:
        logger.info("Access token refreshed successfully.")
        save_tokens(response["access_token"], response["refresh_token"])
        return response["access_token"]
    else:
        logger.error(f"Token refresh failed: {response}")
        start_auth_flowCUL()
        return None

@app.route('/')
def get_auth_code():
    global session, fyers
    try:
        auth_code = request.args.get("auth_code")
        if not auth_code:
            return "No auth_code provided."
        session.set_token(auth_code)
        response = session.generate_token()
        if "access_token" not in response:
            return "Token generation failed."
        access_token_val = response["access_token"]
        refresh_token_val = response["refresh_token"]
        save_tokens(access_token_val, refresh_token_val)
        fyers = fyersModel.FyersModel(client_id=client_id, token=access_token_val, log_path="")
        return "✅ Auth done. You can close this tab."
    except Exception as e:
        logger.error(f"Auth error: {e}")
        return f"Auth error: {e}"

def start_auth_flowCUL():
    global session
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

def init_fyers():
    global fyers
    access_token = refresh_access_token()
    if access_token:
        fyers = fyersModel.FyersModel(client_id=client_id, token=access_token, log_path="")

# === UTILITIES ===
def get_strike_price():
    try:
        res = fyers.quotes({"symbols": index_symbol})
        ltp = res["d"][0]["v"]["lp"]
        return ltp, round(ltp / 50) * 50
    except:
        return None, None

def get_option_price(symbol):
    try:
        data = fyers.quotes({"symbols": symbol})
        return data["d"][0]["v"]["lp"]
    except:
        return None

def send_alert(message):
    try:
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
    except Exception as e:
        logger.error(f"Telegram error: {e}")

def format_expiry_code():
    today = dt.datetime.now()
    expiry = today + dt.timedelta((3 - today.weekday()) % 7)
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
    candles = fyers.history(payload).get("candles", [])
    if len(candles) < 3:
        return None, None
    opening_candles = candles[:3]
    highs = [c[2] for c in opening_candles]
    lows = [c[3] for c in opening_candles]
    return max(highs), min(lows)

def track_sl_target(option_symbol, entry_price):
    sl = entry_price - stop_loss_points
    target = entry_price + target_points
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
        logger.error("Failed to fetch opening range")
        return
    logger.info(f"Opening High: {high} | Low: {low}")
    while True:
        spot, strike = get_strike_price()
        if not spot:
            time.sleep(30)
            continue
        expiry_code = format_expiry_code()
        ce_symbol = f"NSE:NIFTY{expiry_code}{strike}CE"
        pe_symbol = f"NSE:NIFTY{expiry_code}{strike}PE"
        if spot > high:
            ce_price = get_option_price(ce_symbol)
            if ce_price:
                send_alert(f"🚨 CALL BREAKOUT 🚨\nSpot: ₹{spot:.2f} | Strike: {strike}CE\nLTP: ₹{ce_price:.2f}")
                track_sl_target(ce_symbol, ce_price)
                break
        elif spot < low:
            pe_price = get_option_price(pe_symbol)
            if pe_price:
                send_alert(f"🚨 PUT BREAKOUT 🚨\nSpot: ₹{spot:.2f} | Strike: {strike}PE\nLTP: ₹{pe_price:.2f}")
                track_sl_target(pe_symbol, pe_price)
                break
        logger.info(f"{dt.datetime.now().strftime('%H:%M:%S')} → Spot: ₹{spot:.2f}, No breakout yet.")
        time.sleep(30)

# === DAILY SCHEDULE ===
def schedule_daily_strategy():
    init_fyers()
    def wait_for_auth_and_run():
        global fyers
        while fyers is None:
            logger.info("Waiting for authentication...")
            time.sleep(2)
        monitor_breakout()
    threading.Thread(target=wait_for_auth_and_run).start()

# === MAIN ===
if __name__ == "__main__":
    scheduler = BackgroundScheduler(timezone=timezone("Asia/Kolkata"))
    scheduler.add_job(schedule_daily_strategy, 'cron', hour=9, minute=0)
    scheduler.start()
    logger.info("Scheduler started. Waiting for 9:00 AM IST daily trigger...")
    while True:
        time.sleep(60)
