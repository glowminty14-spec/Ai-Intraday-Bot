import os
import time
import json
import requests
import pandas as pd
import pandas_ta as ta
import yfinance as yf
import pytz
from datetime import datetime, time as dtime

# ================= CONFIG (PROFESSIONAL GRADE) =================
BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
CHAT_ID = os.environ.get("TG_CHAT_ID")
TRADES_FILE = "trades.json"

# STRATEGY SETTINGS
MIN_SCORE = 5.5            # FIXED: Allows high-quality setups, not just "unicorns"
MAX_ALERTS_PER_DAY = 5     
MAX_VWAP_DIST = 1.2        
MIN_NIFTY_MOVE = 0.30      # Trend Day Filter (0.3% - 0.35%)

# TIME ZONES (IST)
IST = pytz.timezone('Asia/Kolkata')
START_TRADING = dtime(9, 20)  
STOP_NEW_TRADES = dtime(11, 00) # Momentum is best before 11 AM
STOP_TRADING = dtime(15, 30)  

# ================= WATCHLIST (NIFTY 75) =================
STOCKS = [
    "HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS", "AXISBANK.NS", "KOTAKBANK.NS",
    "INDUSINDBK.NS", "BAJFINANCE.NS", "BAJAJFINSV.NS", "CHOLAFIN.NS", "SHRIRAMFIN.NS",
    "CANBK.NS", "BANKBARODA.NS", "PFC.NS", "REC.NS", "JIOFIN.NS",
    "TCS.NS", "INFY.NS", "HCLTECH.NS", "WIPRO.NS", "TECHM.NS", "LTIM.NS", 
    "PERSISTENT.NS", "COFORGE.NS", "KPITTECH.NS",
    "HAL.NS", "BEL.NS", "MAZDOCK.NS", "COCHINSHIP.NS", "BHEL.NS", "NTPC.NS", 
    "POWERGRID.NS", "ONGC.NS", "COALINDIA.NS", "BPCL.NS",
    "TITAN.NS", "TRENT.NS", "ZOMATO.NS", "DMART.NS", "ITC.NS", "HINDUNILVR.NS", 
    "NESTLEIND.NS", "BRITANNIA.NS", "TATACONSUM.NS", "ASIANPAINT.NS", "VARUN.NS",
    "TATAMOTORS.NS", "MARUTI.NS", "M&M.NS", "HEROMOTOCO.NS", "EICHERMOT.NS", 
    "BAJAJ-AUTO.NS", "TVSMOTOR.NS", "MOTHERSON.NS",
    "TATASTEEL.NS", "JSWSTEEL.NS", "HINDALCO.NS", "VEDANTA.NS", "NMDC.NS", 
    "JINDALSTEL.NS",
    "SUNPHARMA.NS", "DRREDDY.NS", "CIPLA.NS", "DIVISLAB.NS", "APOLLOHOSP.NS", 
    "LUPIN.NS", "AUROPHARMA.NS",
    "RELIANCE.NS", "LT.NS", "ADANIENT.NS", "ADANIPORTS.NS", "DLF.NS", 
    "SIEMENS.NS", "ABB.NS", "HAVELLS.NS", "POLYCAB.NS", "VBL.NS", "INDIGO.NS"
]

# ================= CORE FUNCTIONS =================
def get_ist_time():
    return datetime.now(IST)

def load_trades():
    if not os.path.exists(TRADES_FILE): return []
    try:
        with open(TRADES_FILE, "r") as f: return json.load(f)
    except: return []

def save_trades(trades):
    with open(TRADES_FILE, "w") as f: json.dump(trades, f, indent=4)

def send_telegram(msg):
    if not BOT_TOKEN or not CHAT_ID: return
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                      json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=5)
    except: pass

def fetch_bulk_data(tickers):
    try:
        all_tickers = list(set(tickers + ["^NSEI"]))
        # Increased to 3d to ensure we have a clean 'Previous Close' for gap detection
        return yf.download(all_tickers, period="3d", interval="5m", group_by='ticker', progress=False, threads=True)
    except Exception as e:
        print(f"Error: {e}")
        return pd.DataFrame()

# ================= ANALYSIS ENGINE =================
def analyze_market(data, trades, sent_today):
    if "^NSEI" not in data.columns.levels[0]: return trades
    
    # 1. FIX: NIFTY TREND DAY DETECTION
    nifty = data["^NSEI"].copy().dropna()
    n_today = nifty[nifty.index.date == nifty.index[-1].date()]
    if n_today.empty: return trades
    
    n_open = n_today["Open"].iloc[0]
    n_curr = n_today["Close"].iloc[-1]
    n_move = abs((n_curr - n_open) / n_open) * 100
    
    if n_move < MIN_NIFTY_MOVE:
        print(f"⚠️ Nifty Sideways ({n_move:.2f}%). No momentum trades.")
        return trades

    for ticker in STOCKS:
        clean_sym = ticker.replace(".NS", "")
        if clean_sym in sent_today or len(sent_today) >= MAX_ALERTS_PER_DAY: continue
        if any(t["symbol"] == clean_sym and t["status"] == "OPEN" for t in trades): continue
        if ticker not in data.columns.levels[0]: continue

        try:
            full_df = data[ticker].copy().dropna()
            # Split today's data from historical
            today_df = full_df[full_df.index.date == full_df.index[-1].date()]
            
            if len(today_df) < 4: continue # Wait for first 15-20 mins

            # 2. FIX: CORRECT ORB (Opening Range)
            orb_high = today_df["High"].iloc[:3].max()
            curr = today_df.iloc[-1]
            if curr["Close"] <= orb_high: continue

            # 3. FIX: GAP FILTER (Anti-Trap)
            prev_close = full_df[full_df.index.date < full_df.index[-1].date()]["Close"].iloc[-1]
            gap_pct = ((today_df["Open"].iloc[0] - prev_close) / prev_close) * 100
            if abs(gap_pct) > 2.5: continue # Skip huge gaps (mean reversion risk)

            # Indicators
            full_df.ta.vwap(append=True)
            vwap_col = [c for c in full_df.columns if "VWAP" in c][0]
            today_df = full_df[full_df.index.date == full_df.index[-1].date()] # Refresh with VWAP
            
            curr = today_df.iloc[-1]
            vwap_val = curr[vwap_col]
            
            # Anti-Chasing
            vwap_dist = (curr["Close"] - vwap_val) / vwap_val * 100
            if vwap_dist > MAX_VWAP_DIST or curr["Close"] < vwap_val: continue

            # 4. SCORING (Balanced 5.5 Threshold)
            score = 0
            ema9 = ta.ema(today_df["Close"], 9).iloc[-1]
            ema21 = ta.ema(today_df["Close"], 21).iloc[-1]
            if ema9 > ema21: score += 2.0
            
            # RVOL
            avg_vol = today_df["Volume"].rolling(10).mean().iloc[-1]
            if curr["Volume"] > 2.0 * avg_vol: score += 3.0
            elif curr["Volume"] > 1.2 * avg_vol: score += 1.5
            
            # Relative Strength vs Nifty
            stock_ret = (curr["Close"] / today_df["Open"].iloc[0]) - 1
            nifty_ret = (n_curr / n_open) - 1
            if stock_ret > (nifty_ret + 0.003): score += 2.0

            if score < MIN_SCORE: continue

            # Execution Logic
            atr = ta.atr(today_df["High"], today_df["Low"], today_df["Close"], 14).iloc[-1]
            entry = round(curr["Close"], 2)
            sl = round(entry - (1.5 * atr), 2)
            target = round(entry + (entry - sl) * 1.8, 2)

            trade = {
                "symbol": clean_sym, "entry": entry, "sl": sl, "target": target,
                "score": score, "date": get_ist_time().strftime("%Y-%m-%d"), "status": "OPEN"
            }
            
            trades.append(trade)
            sent_today.append(clean_sym)
            save_trades(trades)
            send_telegram(f"🚀 **VETTING PASSED**\n💎 **{clean_sym}** (Score: {score})\n\n🟢 Entry: {entry}\n🛑 SL: {sl}\n🎯 Target: {target}")

        except: continue
    return trades

# ================= RUNNER =================
if __name__ == "__main__":
    print(f"🦅 Bot Live - Logic Fixes Applied.")
    trades = load_trades()
    
    while True:
        now_ist = get_ist_time().time()
        if now_ist < START_TRADING:
            time.sleep(60); continue
        if now_ist > STOP_TRADING:
            break

        today_str = get_ist_time().strftime("%Y-%m-%d")
        sent_today = [t["symbol"] for t in trades if t["date"] == today_str]
        
        open_pos = [t["symbol"]+".NS" for t in trades if t["status"] == "OPEN"]
        data = fetch_bulk_data(list(set(STOCKS + open_pos)))

        if not data.empty:
            # Position Updates (Stop loss/Target checks)
            for t in trades:
                if t["status"] != "OPEN": continue
                sym = t["symbol"] + ".NS"
                if sym in data.columns.levels[0]:
                    px = data[sym].iloc[-1]
                    if px["Low"] <= t["sl"]:
                        t["status"] = "LOSS"
                        send_telegram(f"🛑 SL HIT: {t['symbol']}")
                    elif px["High"] >= t["target"]:
                        t["status"] = "WIN"
                        send_telegram(f"🎯 TARGET: {t['symbol']}")
            save_trades(trades)
            
            # Scanner
            if now_ist < STOP_NEW_TRADES:
                trades = analyze_market(data, trades, sent_today)

        time.sleep(300)
