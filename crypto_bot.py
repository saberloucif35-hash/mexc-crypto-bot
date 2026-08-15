from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import os
from threading import Lock, Thread
import time
import ccxt
from flask import Flask
import pandas as pd
import requests

# ==========================================
# 0. خادم ويب وهمي لإبقاء الخدمة تعمل 24/7 (على Render)
# ==========================================
app = Flask(__name__)


@app.route('/')
def home():
    return '🤖 Trading Bot is active and scanning MEXC 24/7!'


def run_web_server():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)


def keep_alive():
    t = Thread(target=run_web_server)
    t.daemon = True
    t.start()


# ==========================================
# 1. إعدادات التلجرام الخاص بك
# ==========================================
TELEGRAM_TOKEN = '8264898059:AAGIiseY7WFsx3Q77GrNC9ZFT9qXlxUP6TU'
CHAT_ID = '5088377890'

# ذاكرة وقفل حماية لمنع التضارب بين الخيوط
sent_signals = set()
signals_lock = Lock()


def send_telegram_msg(message):
    url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
    payload = {
        'chat_id': CHAT_ID,
        'text': message,
        'parse_mode': 'Markdown',
        'disable_web_page_preview': True,
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f'❌ خطأ في إرسال رسالة التلجرام: {e}')


# ==========================================
# 2. الربط مع منصة MEXC وجلب كافة عملات USDT
# ==========================================
exchange = ccxt.mexc({'enableRateLimit': True})


def get_all_mexc_pairs():
    try:
        tickers = exchange.fetch_tickers()
        all_usdt_pairs = []
        for symbol, ticker in tickers.items():
            if symbol.endswith('/USDT') and not any(
                x in symbol
                for x in [
                    '3L',
                    '3S',
                    '4L',
                    '4S',
                    '5L',
                    '5S',
                    'BULL',
                    'BEAR',
                ]
            ):
                quote_volume = ticker.get('quoteVolume', 0)
                if quote_volume and quote_volume > 50000:
                    all_usdt_pairs.append(symbol)
        return all_usdt_pairs
    except Exception as e:
        print(f'❌ خطأ في جلب كافة عملات MEXC: {e}')
        return []


# ==========================================
# 3. خوارزمية تطبيق الشروط الأربعة بالحرف
# ==========================================
def analyze_symbol(symbol):
    try:
        # جلب بيانات فريم الـ 4 ساعات לרصد الـ Test Pump والتجميع
        ohlcv_4h = exchange.fetch_ohlcv(symbol, timeframe='4h', limit=80)
        if len(ohlcv_4h) < 60:
            return

        df_4h = pd.DataFrame(
            ohlcv_4h,
            columns=['time', 'open', 'high', 'low', 'close', 'volume'],
        )
        df_4h['vol_ma'] = df_4h['volume'].rolling(20).mean()

        # -------------------------------------------------------------
        # Step 1: البحث عن Test Pump سابق (صعود قوي بفوليوم عالي)
        # -------------------------------------------------------------
        # البحث في النافذة السابقة (استثناء آخر 10 شمعات لمراقبة التجميع)
        pump_window = df_4h.iloc[-70:-10]
        pump_candidates = pump_window[
            (
                (pump_window['high'] - pump_window['low']) / pump_window['low']
                >= 0.25
            )
            & (pump_window['volume'] > pump_window['vol_ma'] * 2.5)
        ]

        if pump_candidates.empty:
            return

        pump_idx = pump_candidates.index[-1]
        pump_candle = df_4h.loc[pump_idx]

        # -------------------------------------------------------------
        # Step 2: مرحلة الهبوط والتجميع (Accumulation)
        # -------------------------------------------------------------
        # الشموع المحصورة بعد الـ Test Pump وحتى ما قبل الشمعة الحالية
        accum_df = df_4h.loc[pump_idx + 1 : df_4h.index[-2]]

        # شرط ألا يقل عدد شمعات التجميع عن 8 شمعات (أي 32 ساعة تجميع على الأقل)
        if len(accum_df) < 8:
            return

        resistance_level = accum_df['high'].max()
        support_level = accum_df['low'].min()

        # التأكد من أن نطاق التجميع ضيق (Range <= 25%) لمنع التذبذب العريض
        range_pct = ((resistance_level - support_level) / support_level) * 100
        if range_pct > 25.0:
            return

        # -------------------------------------------------------------
        # Step 3: انخفاض الفوليوم أثناء التجميع (Low Volume)
        # -------------------------------------------------------------
        avg_accum_vol = accum_df['volume'].mean()
        # فوليوم التجميع يجب أن يكون أقل من 35% من فوليوم شمعة الـ Test Pump
        if avg_accum_vol >= (pump_candle['volume'] * 0.35):
            return

        # -------------------------------------------------------------
        # Step 4: الاختراق على فريم أصغر (15M / 1H)
        # -------------------------------------------------------------
        ohlcv_15m = exchange.fetch_ohlcv(symbol, timeframe='15m', limit=30)
        if len(ohlcv_15m) < 20:
            return

        df_15m = pd.DataFrame(
            ohlcv_15m,
            columns=['time', 'open', 'high', 'low', 'close', 'volume'],
        )
        df_15m['vol_ma'] = df_15m['volume'].rolling(20).mean()

        last_closed_15m = df_15m.iloc[-2]
        candle_timestamp = last_closed_15m['time']

        # منع التكرار برقم فريد
        signal_id = f'{symbol}_{candle_timestamp}'
        with signals_lock:
            if signal_id in sent_signals:
                return

        # شرط الاختراق: إغلاق فوق سقف التجميع + ارتياح الفوليوم
        is_breakout = last_closed_15m['close'] > resistance_level
        is_volume_surge = last_closed_15m['volume'] > (
            df_15m['vol_ma'].iloc[-2] * 2.2
        )

        if is_breakout and is_volume_surge:
            entry_price = last_closed_15m['close']

            # حساب وقف الخسارة أسفل قاع التجميع مباشرة (لحماية القاع)
            stop_loss = support_level * 0.98

            # حساب الأهداف حسب المعادلة الحسابية للنسب
            tp1 = entry_price * 1.5
            tp2 = entry_price * 3.0
            tp3 = entry_price * 11.0
            tp4 = entry_price * 31.0

            clean_symbol = symbol.replace('/', '')
            chart_url = f'https://www.tradingview.com/chart/?symbol=MEXC:{clean_symbol}'

            msg = (
                f'🚨 **توصية اختراق تجميعي (MEXC)!**\n\n'
                f'🪙 **العملة:** `{symbol}`\n'
                f'🎯 **سعر الدخول:** `{entry_price:.6f}`\n'
                f'🛑 **وقف الخسارة (SL):** `{stop_loss:.6f}`\n\n'
                f'📌 **مستويات التجميع المكسورة:**\n'
                f'🔹 **سقف التجميع:** `{resistance_level:.6f}`\n'
                f'🔹 **قاع التجميع:** `{support_level:.6f}`\n\n'
                f'📌 **أهداف جني الأرباح (Take Profit):**\n'
                f'1️⃣ **الهدف الأول (+50%):** `{tp1:.6f}`\n'
                f'2️⃣ **الهدف الثاني (+200%):** `{tp2:.6f}`\n'
                f'3️⃣ **هدف الانفجار (+1000%):** `{tp3:.6f}`\n'
                f'4️⃣ **الهدف الأقصى (+3000%):** `{tp4:.6f}`\n\n'
                f'⚠️ **تنبيه:** عند تحقق الهدف الأول (+50%)، نقل وقف الخسارة لسعر الدخول فوراً.\n\n'
                f'📈 [فتح التشارت على TradingView]({chart_url})\n'
                f"⏱ **التوقيت:** {datetime.now().strftime('%H:%M:%S')}"
            )

            send_telegram_msg(msg)

            with signals_lock:
                sent_signals.add(signal_id)

            print(f'✅ تم إرسال توصية نموذجية: {symbol}')

    except Exception as e:
        print(f'⚠️ خطأ أثناء فحص {symbol}: {e}')


# ==========================================
# 4. التشغيل الرئيسي
# ==========================================
if __name__ == '__main__':
    keep_alive()

    send_telegram_msg(
        '🤖 **تم تشغيل البوت المطور بنجاح!**\nيقوم البوت الآن بمراقبة جميع'
        ' عملات MEXC واقتناص الاختراقات بعد التجميع...'
    )
    print('البوت يعمل الآن ويراقب السوق...')

    while True:
        symbols = get_all_mexc_pairs()
        print(f'⚡ جاري فحص {len(symbols)} عملة بالتوازي...')

        with ThreadPoolExecutor(max_workers=5) as executor:
            executor.map(analyze_symbol, symbols)

        print('اكتملت دورة الفحص. الانتظار 5 دقائق...')
        time.sleep(300)
