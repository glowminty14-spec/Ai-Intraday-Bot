import os
import time
import json
import requests
import pandas as pd
import pandas_ta as ta
import yfinance as yf
from datetime import datetime, time as dtime

# ================= CONFIG =================
BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
CHAT_ID = os.environ.get("TG_CHAT_ID")
TRADES_FILE = "trades.json"

# STRATEGY SETTINGS
RVOL_LIMIT = 1.5           # Volume must be 1.5x average
VWAP_TOLERANCE = 0.05      # Price must be > 0.05% above VWAP
MAX_ALERTS_PER_DAY = 3     # strict money management
MIN_SCORE = 7.5            # High quality threshold

# TIME ZONES (IST)
START_TRADING = dtime(9, 30)  # Avoid first 15 mins noise
LUNCH_START = dtime(11, 0)    # Stop trading (Midday Chop)
LUNCH_END = dtime(13, 30)     # Resume trading
STOP_TRADING = dtime(15, 0)   # End day

# EXPANDED NIFTY 50 WATCHLIST
STOCKS = [
    "HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS", "AXISBANK.NS", "KOTAKBANK.NS",
    "RELIANCE.NS", "TCS.NS", "INFY.NS", "HCLTECH.NS", "WIPRO.NS",
    "ITC.NS", "HINDUNILVR.NS", "TITAN.NS", "LT.NS", "BHARTIARTL.NS",
    "TATAMOTORS.NS", "MARUTI.NS", "M&M.NS", "SUNPHARMA.NS", "ULTRACEMCO.NS",
    "ADANIENT.NS", "BAJFINANCE.NS", "ASIANPAINT.NS", "JSWSTEEL.NS", "POWERGRID.NS"
]

# ================= LEDGER SYSTEM =================
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

# ================= TELEGRAM =================
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
        bias = "BULLISH" if df.iloc[-1]["Close"] > df.iloc[-1][vwap_col] else "BEARISH"
        return df, bias
    except: return None, "NEUTRAL"

# ================= TRADE MANAGER (FIXED) =================
def check_positions(trades):
    updated = False
    for t in trades:
        if t["status"] != "OPEN": continue

        try:
            # FIX #1: Check ONLY the last candle, not the whole day
            # This prevents "Fake Wins" where Target hits after SL
            ticker = t["symbol"] + ".NS"
            df = yf.download(ticker, period="1d", interval="5m", progress=False)
            if df.empty: continue
            
            last = df.iloc[-1] # The most recent closed 5-min candle

            # CHECK SL FIRST (Conservative approach)
            if last["Low"] <= t["sl"]:
                t["status"] = "LOSS"
                t["exit_price"] = t["sl"]
                t["exit_date"] = datetime.now().strftime("%Y-%m-%d")
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
                t["exit_date"] = datetime.now().strftime("%Y-%m-%d")
                updated = True
                
                msg = f"""
✅ **TARGET HIT!**
💎 **{t['symbol']}** hit {t['target']}
🏆 Win Rate: {get_win_rate(trades)}
"""
                send_telegram(msg)

        except: continue
            
    return trades, updated

# ================= ANALYSIS (FIXED SCORING) =================
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

        # 1. HARD FILTERS (Must Pass)
        if market_bias != "BULLISH": return None
        if curr["Close"] <= curr[vwap_col]: return None
        if curr["RVOL"] < RVOL_LIMIT: return None
        
        atr_pct = (curr["ATR"] / curr["Close"]) * 100
        if atr_pct < 0.2 or atr_pct > 2.5: return None

        # 2. FIX #2: SCORING FROM ZERO
        score = 0
        
        # Trend Points (Max 4)
        if curr["Close"] > curr[vwap_col]: score += 2.0
        if curr["EMA9"] > curr["EMA21"]: score += 2.0
        
        # Momentum Points (Max 3)
        if curr["RVOL"] > 2.5: score += 2.0
        elif curr["RVOL"] > 1.5: score += 1.0
        
        # Structure Points (Max 3)
        # Check relative strength vs Nifty
        stock_ret = (curr["Close"] / df["Close"].iloc[-6]) - 1
        nifty_ret = (nifty_df["Close"].iloc[-1] / nifty_df["Close"].iloc[-6]) - 1
        if stock_ret > nifty_ret: score += 1.5
        if stock_ret > (nifty_ret * 1.5): score += 1.5

        # Final Cutoff
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
            "date": datetime.now().strftime("%Y-%m-%d")
        }
    except: return None

# ================= MAIN RUNNER =================
if __name__ == "__main__":
    print(f"🦅 Institutional Bot Active @ {datetime.now().strftime('%H:%M')}")
    
    trades = load_trades()
    sent_today = [t["symbol"] for t in trades if t["date"] == datetime.now().strftime("%Y-%m-%d")]
    
    # Bot runs for 2 hours max
    end_time = time.time() + (120 * 60)
    
    last_nifty_update = None
    nifty_df = None
    market_bias = "NEUTRAL"

    while time.time() < end_time:
        now = datetime.now().time()

        # FIX #3: TIME DISCIPLINE
        if now < START_TRADING:
            print("⏳ Market Opening Noise... Waiting.")
            time.sleep(60)
            continue
            
        if LUNCH_START < now < LUNCH_END:
            print("💤 Midday Chop (Lunch). Sleeping...")
            time.sleep(300)
            continue
            
        if now > STOP_TRADING:
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

        print("💤 Sleeping 5 mins...")
        time.sleep(300)
