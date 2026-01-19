import os
import time
import json
import requests
import pandas as pd
import pandas_ta as ta
import yfinance as yf
from datetime import datetime, time as dtime, timedelta

# ================= CONFIG =================
BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
CHAT_ID = os.environ.get("TG_CHAT_ID")
TRADES_FILE = "trades.json"

# STRATEGY SETTINGS
RVOL_LIMIT = 1.5           # Volume must be 1.5x average
VWAP_TOLERANCE = 0.05      # Price must be > 0.05% above VWAP
MAX_ALERTS_PER_DAY = 3     # Strict money management
MIN_SCORE = 7.5            # High quality threshold

# TIME ZONES (IST)
START_TRADING = dtime(9, 30)  # Avoid first 15 mins noise
LUNCH_START = dtime(11, 0)    # Stop trading (Midday Chop)
LUNCH_END = dtime(13, 30)     # Resume trading
STOP_TRADING = dtime(15, 0)   # End day

# ================= EXPANDED WATCHLIST (NIFTY 50) =================
# ================= WATCHLIST (NIFTY 75 - HIGH LIQUIDITY) =================
STOCKS = [
    # --- BANKING & FINANCE ---
    "HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS", "AXISBANK.NS", "KOTAKBANK.NS",
    "INDUSINDBK.NS", "BAJFINANCE.NS", "BAJAJFINSV.NS", "CHOLAFIN.NS", "SHRIRAMFIN.NS",
    "CANBK.NS", "BANKBARODA.NS", "PFC.NS", "REC.NS", "JIOFIN.NS",

    # --- TECHNOLOGY (IT) ---
    "TCS.NS", "INFY.NS", "HCLTECH.NS", "WIPRO.NS", "TECHM.NS", "LTIM.NS", 
    "PERSISTENT.NS", "COFORGE.NS", "KPITTECH.NS",

    # --- DEFENSE & PUBLIC SECTOR (PSU) ---
    "HAL.NS", "BEL.NS", "MAZDOCK.NS", "COCHINSHIP.NS", "BHEL.NS", "NTPC.NS", 
    "POWERGRID.NS", "ONGC.NS", "COALINDIA.NS", "BPCL.NS",

    # --- CONSUMER & RETAIL ---
    "TITAN.NS", "TRENT.NS", "ZOMATO.NS", "DMART.NS", "ITC.NS", "HINDUNILVR.NS", 
    "NESTLEIND.NS", "BRITANNIA.NS", "TATACONSUM.NS", "ASIANPAINT.NS", "VARUN.NS",

    # --- AUTO & MOTORS ---
    "TATAMOTORS.NS", "MARUTI.NS", "M&M.NS", "HEROMOTOCO.NS", "EICHERMOT.NS", 
    "BAJAJ-AUTO.NS", "TVSMOTOR.NS", "MOTHERSON.NS",

    # --- METALS & COMMODITIES ---
    "TATASTEEL.NS", "JSWSTEEL.NS", "HINDALCO.NS", "VEDANTA.NS", "NMDC.NS", 
    "JINDALSTEL.NS",

    # --- PHARMA ---
    "SUNPHARMA.NS", "DRREDDY.NS", "CIPLA.NS", "DIVISLAB.NS", "APOLLOHOSP.NS", 
    "LUPIN.NS", "AUROPHARMA.NS",

    # --- INDUSTRIAL & OTHERS ---
    "RELIANCE.NS", "LT.NS", "ADANIENT.NS", "ADANIPORTS.NS", "DLF.NS", 
    "SIEMENS.NS", "ABB.NS", "HAVELLS.NS", "POLYCAB.NS", "VBL.NS", "INDIGO.NS"
]

# ================= HELPER FUNCTIONS =================
def get_ist_time():
    """Converts Server Time (UTC) to Indian Standard Time (IST)"""
    utc_now = datetime.utcnow()
    ist_now = utc_now + timedelta(hours=5, minutes=30)
    return ist_now

def load_trades():
    if not os.path.exists(TRADES_FILE): return []
    try:
        with open(TRADES_FILE, "r") as f: return json.load(f)
    except: return []

def save_trades(trades):
    with open(TRADES_FILE, "w") as f: json.dump(trades, f, indent=4)

def get_win_rate(trades):
    closed = [t for t in trades if t["status"] in ["WIN", "LOSS"]]
    if not closed: return "0%"
    wins = len([t for t in closed if t["status"] == "WIN"])
    return f"{round((wins / len(closed)) * 100)}%"

def send_telegram(msg):
    if not BOT_TOKEN or not CHAT_ID: return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=5
        )
    except: pass

# ================= DATA ENGINE =================
def get_nifty_data():
    try:
        df = yf.download("^NSEI", period="5d", interval="5m", progress=False)
        if df.empty: return None, "NEUTRAL"
        df.ta.vwap(append=True)
        vwap_col = [c for c in df.columns if "VWAP" in c][0]
        # Use simple bias check
        bias = "BULLISH" if df.iloc[-1]["Close"] > df.iloc[-1][vwap_col] else "BEARISH"
        return df, bias
    except: return None, "NEUTRAL"

# ================= TRADE MANAGER =================
def check_positions(trades):
    updated = False
    for t in trades:
        if t["status"] != "OPEN": continue

        try:
            ticker = t["symbol"] + ".NS"
            df = yf.download(ticker, period="1d", interval="5m", progress=False)
            if df.empty: continue
            
            last = df.iloc[-1] # Most recent closed candle

            # CHECK SL (Conservative)
            if last["Low"] <= t["sl"]:
                t["status"] = "LOSS"
                t["exit_price"] = t["sl"]
                t["exit_date"] = get_ist_time().strftime("%Y-%m-%d")
                updated = True
                
                msg = f"""
🛑 **STOP HIT**
🔻 **{t['symbol']}** hit {t['sl']}
⚠️ Win Rate: {get_win_rate(trades)}
"""
                send_telegram(msg)

            # CHECK TARGET
            elif last["High"] >= t["target"]:
                t["status"] = "WIN"
                t["exit_price"] = t["target"]
                t["exit_date"] = get_ist_time().strftime("%Y-%m-%d")
                updated = True
                
                msg = f"""
✅ **TARGET HIT!**
💎 **{t['symbol']}** hit {t['target']}
🏆 Win Rate: {get_win_rate(trades)}
"""
                send_telegram(msg)

        except: continue
            
    return trades, updated

# ================= ANALYSIS ENGINE =================
def analyze_stock(ticker, nifty_df, market_bias):
    try:
        df = yf.download(ticker, period="5d", interval="5m", progress=False)
        if df.empty or len(df) < 50: return None
        if isinstance(df.columns, pd.MultiIndex): df = df.xs(ticker, level=1, axis=1)

        # Indicators
        df.ta.vwap(append=True)
        vwap_col = [c for c in df.columns if "VWAP" in c][0]
        df["EMA9"] = ta.ema(df["Close"], 9)
        df["EMA21"] = ta.ema(df["Close"], 21)
        df["RVOL"] = df["Volume"] / ta.sma(df["Volume"], 20)
        df["ATR"] = ta.atr(df["High"], df["Low"], df["Close"], 14)

        curr = df.iloc[-1]

        # FILTERS
        if market_bias != "BULLISH": return None
        if curr["Close"] <= curr[vwap_col]: return None
        if curr["RVOL"] < RVOL_LIMIT: return None
        
        atr_pct = (curr["ATR"] / curr["Close"]) * 100
        if atr_pct < 0.2 or atr_pct > 2.5: return None

        # SCORING
        score = 0
        if curr["Close"] > curr[vwap_col]: score += 2.0
        if curr["EMA9"] > curr["EMA21"]: score += 2.0
        if curr["RVOL"] > 2.5: score += 2.0
        elif curr["RVOL"] > 1.5: score += 1.0
        
        # RS Check
        stock_ret = (curr["Close"] / df["Close"].iloc[-6]) - 1
        nifty_ret = (nifty_df["Close"].iloc[-1] / nifty_df["Close"].iloc[-6]) - 1
        if stock_ret > nifty_ret: score += 1.5

        if score < MIN_SCORE: return None

        entry = curr["Close"]
        sl = entry - (2.0 * curr["ATR"])
        target = entry + (entry - sl) * 2

        return {
            "symbol": ticker.replace(".NS", ""),
            "entry": round(entry, 2),
            "sl": round(sl, 2),
            "target": round(target, 2),
            "score": score,
            "date": get_ist_time().strftime("%Y-%m-%d")
        }
    except: return None

# ================= MAIN RUNNER =================
if __name__ == "__main__":
    ist_now = get_ist_time()
    print(f"🦅 Bot Active @ {ist_now.strftime('%H:%M')} IST")
    
    trades = load_trades()
    sent_today = [t["symbol"] for t in trades if t["date"] == ist_now.strftime("%Y-%m-%d")]
    
    end_time = time.time() + (120 * 60) # Run for 2 hours
    
    last_nifty_update = None
    nifty_df = None
    market_bias = "NEUTRAL"

    while time.time() < end_time:
        now_ist = get_ist_time().time()

        # TIME DISCIPLINE (Using IST)
        if now_ist < START_TRADING:
            print(f"⏳ Market Opening Noise ({now_ist.strftime('%H:%M')}). Waiting...")
            time.sleep(60)
            continue
            
        if LUNCH_START < now_ist < LUNCH_END:
            print(f"💤 Midday Chop ({now_ist.strftime('%H:%M')}). Sleeping...")
            time.sleep(300)
            continue
            
        if now_ist > STOP_TRADING:
            print("🌙 Market Closing. Bye.")
            break

        # 1. Manage Ledger
        print("📋 Updating Ledger...")
        trades, updated = check_positions(trades)
        if updated: save_trades(trades)

        # 2. Refresh Market View
        if not last_nifty_update or (datetime.now() - last_nifty_update).seconds > 900:
            nifty_df, market_bias = get_nifty_data()
            last_nifty_update = datetime.now()
            print(f"   Market Bias: {market_bias}")

        # 3. Scan
        if market_bias == "BULLISH":
            print(f"🔎 Scanning... (Sent: {len(sent_today)}/{MAX_ALERTS_PER_DAY})")
            
            for ticker in STOCKS:
                clean_sym = ticker.replace(".NS", "")
                if clean_sym in sent_today: continue
                print(f"   👉 Checking {clean_sym}...")  # <--- ADD THIS
                if any(t["symbol"] == clean_sym and t["status"] == "OPEN" for t in trades): continue
                if len(sent_today) >= MAX_ALERTS_PER_DAY: break

                signal = analyze_stock(ticker, nifty_df, market_bias)
                if signal:
                    # Save Trade
                    new_trade = {
                        "symbol": signal["symbol"],
                        "entry": signal["entry"],
                        "target": signal["target"],
                        "sl": signal["sl"],
                        "date": signal["date"],
                        "status": "OPEN"
                    }
                    trades.append(new_trade)
                    save_trades(trades)
                    
                    # Alert
                    msg = f"""
🚨 **NEW TRADE**
💎 **{signal['symbol']}** (Score: {signal['score']})
🟢 Entry: {signal['entry']}
🛑 Stop: {signal['sl']}
🎯 Target: {signal['target']}
"""
                    send_telegram(msg)
                    sent_today.append(clean_sym)
                    print(f"✅ Logged: {clean_sym}")

        print("💤 Sleeping 3 mins...")
        time.sleep(180)
