import ccxt
import pandas as pd
import requests
import time
from datetime import datetime

# ==========================================
# 1. إعدادات التلجرام الخاصة بك
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
        print(f"خطأ في إرسال رسالة التلجرام: {e}")

# ==========================================
# 2. الربط مع منصة MEXC وجلب كافة العملات
# ==========================================
exchange = ccxt.mexc({'enableRateLimit': True})

def get_all_mexc_pairs():
    """جلب جميع أزواج USDT المتاحة على منصة MEXC بلا استثناء"""
    try:
        tickers = exchange.fetch_tickers()
        all_usdt_pairs = []
        
        for symbol, ticker in tickers.items():
            # فحص كافة أزواج USDT في السوق الفوري واستبعاد عملات الرافعة (3L/3S)
            if symbol.endswith('/USDT') and '3L' not in symbol and '3S' not in symbol:
                quote_volume = ticker.get('quoteVolume', 0)
                # شرط خفيف جداً: أن يكون حجم التداول اليومي أكثر من 50,000$
                if quote_volume and quote_volume > 50000:
                    all_usdt_pairs.append(symbol)
                    
        return all_usdt_pairs
    except Exception as e:
        print(f"خطأ في جلب كافة عملات MEXC: {e}")
        return []

# ==========================================
# 3. خوارزمية الفحص (Test Pump + Accumulation + Breakout)
# ==========================================
def analyze_symbol(symbol):
    try:
        # جلب أخر 60 شمعة على إطار 4 ساعات
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe='4h', limit=60)
        if len(ohlcv) < 60:
            return

        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
        df['vol_ma'] = df['volume'].rolling(20).mean()
        
        current = df.iloc[-1]
        prev_20 = df.iloc[-21:-1]
        older_candles = df.iloc[-50:-21]

        # الشروط الفنية:
        has_test_pump = any((older_candles['high'] - older_candles['open']) / older_candles['open'] >= 0.10)
        avg_accum_vol = prev_20['volume'].mean()
        is_low_volume = avg_accum_vol < df['vol_ma'].iloc[-2]
        resistance_level = prev_20['high'].max()
        is_breakout = current['close'] > resistance_level
        is_volume_surge = current['volume'] > (df['vol_ma'].iloc[-1] * 2.5)

        # إرسال التنبيه عند الانفجار
        if has_test_pump and is_low_volume and is_breakout and is_volume_surge:
            clean_symbol = symbol.replace('/', '')
            chart_url = f"https://www.tradingview.com/chart/?symbol=MEXC:{clean_symbol}"
            
            msg = (
                f"🚨 **فرصة اختراق على منصة MEXC!**\n\n"
                f"🪙 **العملة:** `{symbol}`\n"
                f"💰 **سعر الاختراق:** `{current['close']}`\n"
                f"📊 **مستوى المقاومة المكسور:** `{resistance_level:.4f}`\n"
                f"🔥 **الفوليوم الحالي:** 2.5x أعلى من المتوسط\n\n"
                f"📈 [فتح التشارت على TradingView]({chart_url})\n"
                f"⏱ **التوقيت:** {datetime.now().strftime('%H:%M:%S')}"
            )
            
            send_telegram_msg(msg)
            print(f"✅ تم إرسال تنبيه لعملة MEXC: {symbol}")

    except Exception as e:
        pass

# ==========================================
# 4. تشغيل البوت المستمر
# ==========================================
if __name__ == "__main__":
    send_telegram_msg("🤖 **تم تشغيل البوت بنجاح!**\nيقوم البوت الآن بمراقبة جميع عملات منصة MEXC...")
    print("البوت يعمل الآن على فحص كافة عملات MEXC...")
    
    while True:
        symbols = get_all_mexc_pairs()
        print(f"جاري فحص جميع عملات المنصة ({len(symbols)} عملة)...")
        
        for symbol in symbols:
            analyze_symbol(symbol)
            time.sleep(0.3) # فاصل زمني لتجنب الحظر من API المنصة
            
        print("اكتملت دورة الفحص الشاملة. الانتظار 10 دقائق للدورة القادمة...")
        time.sleep(600)