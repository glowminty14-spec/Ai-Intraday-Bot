import os
import time
import requests
import pandas as pd
import pandas_ta as ta
import yfinance as yf
from datetime import datetime, time as dtime

# ================= CONFIG =================
BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
CHAT_ID = os.environ.get("TG_CHAT_ID")

RVOL_LIMIT = 1.5
VWAP_TOLERANCE = 0.05
MAX_ALERTS_PER_DAY = 2        # Strict Limit for Quality
MIN_SCORE = 8                 # Only A+ Setups

# ================= EXPANDED WATCHLIST (NIFTY 50) =================
STOCKS = [
    # --- BANKS & FINANCE ---
    "HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS", "AXISBANK.NS", "KOTAKBANK.NS",
    "INDUSINDBK.NS", "BAJFINANCE.NS", "BAJAJFINSV.NS", "HDFCLIFE.NS", "SBILIFE.NS",
    
    # --- IT & TECH ---
    "TCS.NS", "INFY.NS", "HCLTECH.NS", "WIPRO.NS", "TECHM.NS", "LTIM.NS",
    
    # --- ENERGY & OIL ---
    "RELIANCE.NS", "ONGC.NS", "NTPC.NS", "POWERGRID.NS", "BPCL.NS", "COALINDIA.NS",
    
    # --- AUTO ---
    "TATAMOTORS.NS", "MARUTI.NS", "M&M.NS", "HEROMOTOCO.NS", "EICHERMOT.NS", "BAJAJ-AUTO.NS",
    
    # --- CONSUMER GOODS (FMCG) ---
    "ITC.NS", "HINDUNILVR.NS", "NESTLEIND.NS", "BRITANNIA.NS", "TATACONSUM.NS", "TITAN.NS",
    
    # --- METALS & COMMODITIES ---
    "TATASTEEL.NS", "HINDALCO.NS", "JSWSTEEL.NS", "ULTRACEMCO.NS", "GRASIM.NS",
    
    # --- PHARMA ---
    "SUNPHARMA.NS", "DRREDDY.NS", "CIPLA.NS", "DIVISLAB.NS", "APOLLOHOSP.NS",
    
    # --- OTHERS (Infra, Ports, Paints) ---
    "LT.NS", "ADANIENT.NS", "ADANIPORTS.NS", "ASIANPAINT.NS", "BHARTIARTL.NS"
]

# ================= TELEGRAM =================
def send_telegram(msg):
    if not BOT_TOKEN or not CHAT_ID:
        print(f"⚠️ Telegram Alert (No Token):\n{msg}")
        return
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": CHAT_ID, 
            "text": msg, 
            "parse_mode": "Markdown"
        }, timeout=5)
    except Exception as e:
        print(f"Telegram Error: {e}")

# ================= NIFTY DATA =================
def get_nifty_data():
    try:
        df = yf.download("^NSEI", period="5d", interval="5m", progress=False)
        if df.empty: return None, "NEUTRAL"
        
        df.ta.vwap(append=True)
        vwap_col = [c for c in df.columns if "VWAP" in c][0]
        curr = df.iloc[-1]
        
        bias = "BULLISH" if curr["Close"] > curr[vwap_col] else "BEARISH"
        return df, bias
    except:
        return None, "NEUTRAL"

# ================= ANALYSIS =================
def analyze_stock(ticker, nifty_df, market_bias):
    try:
        df = yf.download(ticker, period="5d", interval="5m", progress=False)
        if df.empty or len(df) < 50: return None

        if isinstance(df.columns, pd.MultiIndex):
            df = df.xs(ticker, level=1, axis=1)

        df.ta.vwap(append=True)
        vwap_col = [c for c in df.columns if "VWAP" in c][0]
        df["EMA9"] = ta.ema(df["Close"], 9)
        df["EMA21"] = ta.ema(df["Close"], 21)
        df["Vol_SMA"] = ta.sma(df["Volume"], 20)
        df["RVOL"] = df["Volume"] / df["Vol_SMA"]
        df["ATR"] = ta.atr(df["High"], df["Low"], df["Close"], 14)

        curr = df.iloc[-1]

        # 1. Volatility Filter
        atr_pct = (curr["ATR"] / curr["Close"]) * 100
        if atr_pct < 0.2 or atr_pct > 2.5: return None

        # 2. Market Bias Filter
        if market_bias != "BULLISH": return None

        # 3. Relative Strength
        stock_ret = (curr["Close"] / df["Close"].iloc[-6]) - 1
        nifty_ret = (nifty_df["Close"].iloc[-1] / nifty_df["Close"].iloc[-6]) - 1
        if stock_ret <= nifty_ret: return None

        # 4. The Setup
        if not (curr["Close"] > curr[vwap_col] * (1 + VWAP_TOLERANCE/100) and 
                curr["EMA9"] > curr["EMA21"]):
            return None

        # 5. Volume Check
        if curr["RVOL"] < RVOL_LIMIT: return None

        # ================= SCORING =================
        score = 0
        score += 2 if curr["EMA9"] > curr["EMA21"] > curr[vwap_col] else 1
        score += 2 if stock_ret > (nifty_ret * 1.5) else 1
        score += 2 if curr["RVOL"] > 2.5 else (1 if curr["RVOL"] > 1.8 else 0)
        
        candle_range = curr["High"] - curr["Low"]
        close_pos = curr["Close"] - curr["Low"]
        if candle_range > 0 and (close_pos / candle_range) > 0.8:
            score += 2

        score += 2 
        
        if score < MIN_SCORE: return None

        entry = curr["Close"]
        sl = entry - (2.0 * curr["ATR"])
        target = entry + (entry - sl) * 2

        return {
            "symbol": ticker.replace(".NS", ""),
            "entry": round(entry, 2),
            "sl": round(sl, 2),
            "target": round(target, 2),
            "rvol": round(curr["RVOL"], 1),
            "score": score
        }

    except:
        return None

# ================= RUNNER =================
if __name__ == "__main__":
    print(f"🦅 Institutional Bot Started @ {datetime.now().strftime('%H:%M')}")
    
    sent_alerts = []
    last_nifty_update = None
    nifty_df = None
    market_bias = "NEUTRAL"
    
    # Run for 2 hours (Max GitHub Action limit is 6 hrs, we keep it short)
    end_time = time.time() + (120 * 60) 

    while time.time() < end_time:
        
        # 1. Refresh Nifty (Every 15 mins)
        if not last_nifty_update or (datetime.now() - last_nifty_update).seconds > 900:
            nifty_df, market_bias = get_nifty_data()
            last_nifty_update = datetime.now()
            print(f"   Market Bias: {market_bias}")
        
        if nifty_df is None or market_bias == "NEUTRAL":
            print("   Market Neutral. Waiting...")
            time.sleep(300)
            continue

        print(f"🔎 Scanning... {len(STOCKS)} Stocks")
        candidates = []

        for ticker in STOCKS:
            clean_symbol = ticker.replace(".NS", "")
            if clean_symbol in sent_alerts: continue

            signal = analyze_stock(ticker, nifty_df, market_bias)
            if signal:
                candidates.append(signal)

        # Sort by Score & Send Top Picks
        candidates.sort(key=lambda x: x["score"], reverse=True)
        
        for s in candidates[:MAX_ALERTS_PER_DAY - len(sent_alerts)]:
            msg = f"""
🚨 **INSTITUTIONAL PICK**
💎 **{s['symbol']}** | Score: {s['score']}/10
🌊 RVOL: **{s['rvol']}x** | 📊 Market: {market_bias}

🟢 **ENTRY:** {s['entry']}
🛑 **STOP:** {s['sl']}
🎯 **TARGET:** {s['target']}
"""
            send_telegram(msg)
            print(f"✅ Alert Sent: {s['symbol']}")
            sent_alerts.append(s["symbol"])

        if len(sent_alerts) >= MAX_ALERTS_PER_DAY:
            print("🏁 Daily Target Reached.")
            break
            
        time.sleep(300) # Sleep 5 mins
