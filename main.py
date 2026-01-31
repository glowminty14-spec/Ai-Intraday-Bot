import os
import time
import json
import requests
import pandas as pd
import pandas_ta as ta
import yfinance as yf
import pytz
from datetime import datetime, time as dtime, timedelta

# ================= CONFIG =================
BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
CHAT_ID = os.environ.get("TG_CHAT_ID")
TRADES_FILE = "trades.json"

# STRATEGY SETTINGS
RVOL_LIMIT = 1.5           # Volume must be 1.5x average
MIN_SCORE = 7.5            # High quality threshold
MAX_ALERTS_PER_DAY = 5     # Money management limit

# TIME ZONES (IST)
IST = pytz.timezone('Asia/Kolkata')
START_TRADING = dtime(9, 15)  # Market Open
LUNCH_START = dtime(11, 30)   # Optional: Avoid low volume hours
LUNCH_END = dtime(13, 00)     
STOP_TRADING = dtime(15, 30)  # Market Close (Graceful Exit)

# ================= WATCHLIST (NIFTY 75) =================
STOCKS = [
    # --- BANKING & FINANCE ---
    "HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS", "AXISBANK.NS", "KOTAKBANK.NS",
    "INDUSINDBK.NS", "BAJFINANCE.NS", "BAJAJFINSV.NS", "CHOLAFIN.NS", "SHRIRAMFIN.NS",
    "CANBK.NS", "BANKBARODA.NS", "PFC.NS", "REC.NS", "JIOFIN.NS",

    # --- TECHNOLOGY (IT) ---
    "TCS.NS", "INFY.NS", "HCLTECH.NS", "WIPRO.NS", "TECHM.NS", "LTIM.NS", 
    "PERSISTENT.NS", "COFORGE.NS", "KPITTECH.NS",

    # --- DEFENSE & PSU ---
    "HAL.NS", "BEL.NS", "MAZDOCK.NS", "COCHINSHIP.NS", "BHEL.NS", "NTPC.NS", 
    "POWERGRID.NS", "ONGC.NS", "COALINDIA.NS", "BPCL.NS",

    # --- CONSUMER ---
    "TITAN.NS", "TRENT.NS", "ZOMATO.NS", "DMART.NS", "ITC.NS", "HINDUNILVR.NS", 
    "NESTLEIND.NS", "BRITANNIA.NS", "TATACONSUM.NS", "ASIANPAINT.NS", "VARUN.NS",

    # --- AUTO ---
    "TATAMOTORS.NS", "MARUTI.NS", "M&M.NS", "HEROMOTOCO.NS", "EICHERMOT.NS", 
    "BAJAJ-AUTO.NS", "TVSMOTOR.NS", "MOTHERSON.NS",

    # --- METALS ---
    "TATASTEEL.NS", "JSWSTEEL.NS", "HINDALCO.NS", "VEDANTA.NS", "NMDC.NS", 
    "JINDALSTEL.NS",

    # --- PHARMA ---
    "SUNPHARMA.NS", "DRREDDY.NS", "CIPLA.NS", "DIVISLAB.NS", "APOLLOHOSP.NS", 
    "LUPIN.NS", "AUROPHARMA.NS",

    # --- OTHERS ---
    "RELIANCE.NS", "LT.NS", "ADANIENT.NS", "ADANIPORTS.NS", "DLF.NS", 
    "SIEMENS.NS", "ABB.NS", "HAVELLS.NS", "POLYCAB.NS", "VBL.NS", "INDIGO.NS"
]

# ================= HELPER FUNCTIONS =================
def get_ist_time():
    """Returns current IST time"""
    return datetime.now(IST)

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
    if not BOT_TOKEN or not CHAT_ID: 
        print(f"⚠️ Telegram Token Missing. Msg: {msg}")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=5
        )
    except Exception as e:
        print(f"Telegram Error: {e}")

# ================= DATA ENGINE (OPTIMIZED) =================
def fetch_bulk_data(tickers):
    """Downloads data for ALL tickers + Nifty in one request"""
    try:
        # Download Nifty + Stocks together
        # group_by='ticker' ensures data is organized as data['RELIANCE.NS']['Close']
        all_tickers = tickers + ["^NSEI"]
        data = yf.download(all_tickers, period="5d", interval="5m", group_by='ticker', progress=False, threads=True)
        return data
    except Exception as e:
        print(f"❌ Data Download Error: {e}")
        return pd.DataFrame()

# ================= TRADING LOGIC =================
def update_positions(trades, data):
    updated = False
    
    for t in trades:
        if t["status"] != "OPEN": continue
        
        ticker = t["symbol"] + ".NS"
        # Check if we have data for this ticker
        if ticker not in data.columns.levels[0]: continue

        try:
            df = data[ticker]
            # Get latest complete candle
            if df.empty: continue
            last = df.iloc[-1]
            
            # CHECK EXIT CONDITIONS
            if last["Low"] <= t["sl"]:
                t["status"] = "LOSS"
                t["exit_price"] = t["sl"]
                t["exit_date"] = get_ist_time().strftime("%Y-%m-%d %H:%M")
                updated = True
                
                msg = f"""
🛑 **STOP LOSS HIT**
🔻 **{t['symbol']}** exited at {t['sl']}
⚠️ Win Rate: {get_win_rate(trades)}
"""
                send_telegram(msg)
            
            elif last["High"] >= t["target"]:
                t["status"] = "WIN"
                t["exit_price"] = t["target"]
                t["exit_date"] = get_ist_time().strftime("%Y-%m-%d %H:%M")
                updated = True
                
                msg = f"""
✅ **TARGET HIT!**
🚀 **{t['symbol']}** exited at {t['target']}
🏆 Win Rate: {get_win_rate(trades)}
"""
                send_telegram(msg)
                
        except Exception as e:
            print(f"Error updating {ticker}: {e}")
            continue
        
    return trades, updated

def analyze_market(data, trades, sent_today):
    current_date = get_ist_time().strftime("%Y-%m-%d")
    
    # 1. CHECK MARKET BIAS (NIFTY)
    if "^NSEI" not in data.columns.levels[0]: 
        print("⚠️ Nifty data missing, skipping scan.")
        return trades

    nifty = data["^NSEI"].copy()
    if nifty.empty: return trades
    
    try:
        nifty.ta.vwap(append=True)
        # Find the VWAP column name (it changes dynamically)
        vwap_col_nifty = [c for c in nifty.columns if "VWAP" in c][0]
        market_bias = "BULLISH" if nifty["Close"].iloc[-1] > nifty[vwap_col_nifty].iloc[-1] else "BEARISH"
        print(f"📉 Market Bias: {market_bias}")
    except:
        return trades

    if market_bias != "BULLISH": 
        return trades # Strategy is Long-Only

    # 2. SCAN STOCKS
    for ticker in STOCKS:
        clean_sym = ticker.replace(".NS", "")
        
        # Skip filters
        if clean_sym in sent_today: continue
        if any(t["symbol"] == clean_sym and t["status"] == "OPEN" for t in trades): continue
        if len(sent_today) >= MAX_ALERTS_PER_DAY: break
        
        # Data Check
        if ticker not in data.columns.levels[0]: continue
        
        try:
            df = data[ticker].copy()
            if df.empty or len(df) < 50: continue
            
            # Clean Data
            df.dropna(inplace=True)

            # Indicators
            df.ta.vwap(append=True)
            vwap_col = [c for c in df.columns if "VWAP" in c][0]
            df["EMA9"] = ta.ema(df["Close"], 9)
            df["EMA21"] = ta.ema(df["Close"], 21)
            df["RVOL"] = df["Volume"] / ta.sma(df["Volume"], 20)
            df["ATR"] = ta.atr(df["High"], df["Low"], df["Close"], 14)

            curr = df.iloc[-1]

            # --- STRATEGY FILTERS ---
            if curr["Close"] <= curr[vwap_col]: continue
            if curr["RVOL"] < RVOL_LIMIT: continue
            
            atr_pct = (curr["ATR"] / curr["Close"]) * 100
            if atr_pct < 0.2 or atr_pct > 3.0: continue

            # --- SCORING SYSTEM ---
            score = 0
            if curr["Close"] > curr[vwap_col]: score += 2.0
            if curr["EMA9"] > curr["EMA21"]: score += 2.0
            if curr["RVOL"] > 2.5: score += 2.0
            elif curr["RVOL"] > 1.5: score += 1.0
            
            # Relative Strength (vs Nifty)
            stock_ret = (curr["Close"] / df["Close"].iloc[-6]) - 1
            nifty_ret = (nifty["Close"].iloc[-1] / nifty["Close"].iloc[-6]) - 1
            if stock_ret > nifty_ret: score += 1.5

            if score < MIN_SCORE: continue

            # --- EXECUTION ---
            entry = curr["Close"]
            sl = entry - (2.0 * curr["ATR"])
            target = entry + (entry - sl) * 2

            new_trade = {
                "symbol": clean_sym,
                "entry": round(entry, 2),
                "sl": round(sl, 2),
                "target": round(target, 2),
                "score": score,
                "date": current_date,
                "status": "OPEN"
            }
            
            trades.append(new_trade)
            sent_today.append(clean_sym)
            save_trades(trades) # Save immediately
            
            msg = f"""
🚨 **BUY ALERT**
💎 **{clean_sym}** (Score: {score})
🟢 Entry: {round(entry, 2)}
🛑 Stop: {round(sl, 2)}
🎯 Target: {round(target, 2)}
"""
            send_telegram(msg)
            print(f"✅ Alert Sent: {clean_sym}")

        except Exception as e:
            continue

    return trades

# ================= MAIN RUNNER =================
if __name__ == "__main__":
    print(f"🦅 Bot Active (Bulk Mode) - {get_ist_time().strftime('%H:%M')} IST")
    
    trades = load_trades()
    current_date = get_ist_time().strftime("%Y-%m-%d")
    sent_today = [t["symbol"] for t in trades if t["date"] == current_date]

    # Infinite Loop (Controlled by Time)
    while True:
        now_ist = get_ist_time().time()

        # 1. TIME GATES
        if now_ist < START_TRADING:
            print(f"⏳ Market not open. Waiting... ({now_ist.strftime('%H:%M')})")
            time.sleep(60)
            continue
            
        if LUNCH_START < now_ist < LUNCH_END:
            print(f"💤 Lunch Break ({now_ist.strftime('%H:%M')})...")
            time.sleep(300)
            continue
            
        if now_ist > STOP_TRADING:
            print("🌙 Market Closed. Exiting for GitHub to save data.")
            break

        print(f"\n🔄 Scanning Market... ({now_ist.strftime('%H:%M')})")
        
        # 2. BULK DATA FETCH
        # We fetch: Watchlist + Nifty + Any Currently Open Positions
        open_pos_tickers = [t["symbol"]+".NS" for t in trades if t["status"] == "OPEN"]
        scan_list = list(set(STOCKS + open_pos_tickers))
        
        market_data = fetch_bulk_data(scan_list)
        
        if not market_data.empty:
            # 3. MANAGE TRADES & SCAN
            trades, was_updated = update_positions(trades, market_data)
            if was_updated: save_trades(trades)
            
            trades = analyze_market(market_data, trades, sent_today)

        # 4. WAIT FOR NEXT CANDLE
        print("💤 Sleeping 5 minutes...")
        time.sleep(300)
