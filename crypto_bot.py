import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from threading import Thread
from flask import Flask
import ccxt
import pandas as pd
import requests

# ==========================================
# 0. خادم ويب وهمي لإبقاء الخدمة تعمل 24/7 (على Render)
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 Trading Bot is active and scanning MEXC 24/7!"

def run_web_server():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_web_server)
    t.daemon = True
    t.start()

# ==========================================
# 1. إعدادات التلجرام الخاص بك
# ==========================================
TELEGRAM_TOKEN = "8264898059:AAGIiseY7WFsx3Q77GrNC9ZFT9qXlxUP6TU"
CHAT_ID = "5088377890"

def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID, 
        "text": message, 
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"❌ خطأ في إرسال رسالة التلجرام: {e}")

# ==========================================
# 2. الربط مع منصة MEXC وجلب كافة عملات USDT
# ==========================================
exchange = ccxt.mexc({'enableRateLimit': True})

def get_all_mexc_pairs():
    try:
        tickers = exchange.fetch_tickers()
        all_usdt_pairs = []
        for symbol, ticker in tickers.items():
            # استبعاد عملات الرافعة المالية والعملات المنخفضة الفوليوم
            if symbol.endswith('/USDT') and not any(x in symbol for x in ['3L', '3S', '4L', '4S', '5L', '5S', 'BULL', 'BEAR']):
                quote_volume = ticker.get('quoteVolume', 0)
                if quote_volume and quote_volume > 50000:
                    all_usdt_pairs.append(symbol)
        return all_usdt_pairs
    except Exception as e:
        print(f"❌ خطأ في جلب كافة عملات MEXC: {e}")
        return []

# ==========================================
# 3. خوارزمية تطبيق الشروط الأربعة وحساب الأهداف
# ==========================================
def analyze_symbol(symbol):
    try:
        # جلب داتا فريم الـ 4 ساعات לרصد الـ Test Pump والتجميع
        ohlcv_4h = exchange.fetch_ohlcv(symbol, timeframe='4h', limit=70)
        if len(ohlcv_4h) < 60:
            return

        df_4h = pd.DataFrame(ohlcv_4h, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
        df_4h['vol_ma'] = df_4h['volume'].rolling(20).mean()

        # -------------------------------------------------------------
        # الشرط 1: الـ Test Pump (ارتفاع تجريبي >= 15% وفوليوم عالي)
        # -------------------------------------------------------------
        pump_window = df_4h.iloc[-60:-10]
        pump_candidates = pump_window[
            ((pump_window['high'] - pump_window['low']) / pump_window['low'] >= 0.15) &
            (pump_window['volume'] > pump_window['vol_ma'] * 2.0)
        ]

        if pump_candidates.empty:
            return  # لا يوجد Test Pump سابق

        pump_idx = pump_candidates.index[-1]
        pump_candle = df_4h.loc[pump_idx]

        # -------------------------------------------------------------
        # الشرط 2: مرحلة التجميع (Accumulation Zone)
        # -------------------------------------------------------------
        accum_df = df_4h.loc[pump_idx + 1 : df_4h.index[-2]]
        
        # اشتراط فترة تجميع لا تقل عن 5 شموع (20 ساعة)
        if len(accum_df) < 5:
            return

        # -------------------------------------------------------------
        # الشرط 3: الفوليوم منخفض جداً أثناء التجميع (Low Volume)
        # -------------------------------------------------------------
        avg_accum_vol = accum_df['volume'].mean()
        is_low_volume = avg_accum_vol < (pump_candle['volume'] * 0.4)

        if not is_low_volume:
            return

        # تحديد أسرار المستويات (المقاومة والدعم للتجميع)
        resistance_level = accum_df['high'].max()
        support_level = accum_df['low'].min()

        # -------------------------------------------------------------
        # الشرط 4: الاختراق على إطار زمني أصغر (15M Breakout)
        # -------------------------------------------------------------
        ohlcv_15m = exchange.fetch_ohlcv(symbol, timeframe='15m', limit=40)
        if len(ohlcv_15m) < 25:
            return

        df_15m = pd.DataFrame(ohlcv_15m, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
        df_15m['vol_ma'] = df_15m['volume'].rolling(20).mean()
        
        current_15m = df_15m.iloc[-1]

        # الشراء عند إغلاق شمعة 15 دقيقة أعلى المقاومة + ارتفاع في الفوليوم
        is_breakout = current_15m['close'] > resistance_level
        is_volume_surge = current_15m['volume'] > (df_15m['vol_ma'].iloc[-1] * 2.0)

        if is_breakout and is_volume_surge:
            entry_price = current_15m['close']
            
            # 📊 حساب الأهداف ووقف الخسارة تلقائياً:
            stop_loss = support_level * 0.97         # أسفل قاع التجميع بـ 3%
            tp1 = entry_price * 1.5                  # +50%
            tp2 = entry_price * 3.0                  # +200%
            tp3 = entry_price * 11.0                 # +1000%
            tp4 = entry_price * 31.0                 # +3000%

            clean_symbol = symbol.replace('/', '')
            chart_url = f"https://www.tradingview.com/chart/?symbol=MEXC:{clean_symbol}"
            
            msg = (
                f"🚨 **توصية صفقة انفجارية (MEXC)!**\n\n"
                f"🪙 **العملة:** `{symbol}`\n"
                f"🎯 **سعر الدخول:** `{entry_price:.6f}`\n"
                f"🛑 **وقف الخسارة (SL):** `{stop_loss:.6f}`\n\n"
                f"📌 **أهداف جني الأرباح (Take Profit):**\n"
                f"1️⃣ **الهدف الأول (+50%):** `{tp1:.6f}`\n"
                f"2️⃣ **الهدف الثاني (+200%):** `{tp2:.6f}`\n"
                f"3️⃣ **هدف الانفجار (+1000%):** `{tp3:.6f}`\n"
                f"4️⃣ **الهدف الأقصى (+3000%):** `{tp4:.6f}`\n\n"
                f"⚠️ **تنبيه:** عند تحقق الهدف الأول (+50%)، انقل وقف الخسارة لسعر الدخول تلقائياً.\n\n"
                f"📈 [فتح التشارت على TradingView]({chart_url})\n"
                f"⏱ **التوقيت:** {datetime.now().strftime('%H:%M:%S')}"
            )
            send_telegram_msg(msg)
            print(f"✅ تم إرسال توصية: {symbol}")

    except Exception as e:
        # طباعة الأخطاء الخفيفة دون إيقاف السيرفر
        print(f"⚠️ خطأ أثناء فحص {symbol}: {e}")

# ==========================================
# 4. التشغيل الرئيسي بالفحص المتوازي
# ==========================================
if __name__ == "__main__":
    keep_alive()
    
    send_telegram_msg("🤖 **تم تشغيل البوت المطور بنجاح!**\nيقوم البوت الآن بمراقبة جميع عملات MEXC وحساب الأهداف الانفجارية...")
    print("البوت يعمل الآن ويراقب السوق...")
    
    while True:
        symbols = get_all_mexc_pairs()
        print(f"⚡ جاري فحص {len(symbols)} عملة بالتوازي...")
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            executor.map(analyze_symbol, symbols)
            
        print("اكتملت دورة الفحص. الانتظار 5 دقائق...")
        time.sleep(300)
