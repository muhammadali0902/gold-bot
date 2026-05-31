#!/usr/bin/env python3
import logging
import asyncio
import aiohttp
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

BOT_TOKEN = "AAGwp7yCX5s816zZs17kM8rpDhGWpxAexX4"
ALPHA_VANTAGE_KEY = "SEEPKRCHUFNR075N"

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

async def get_gold_price():
    url = (f"https://www.alphavantage.co/query?function=CURRENCY_EXCHANGE_RATE"
           f"&from_currency=XAU&to_currency=USD&apikey={ALPHA_VANTAGE_KEY}")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                data = await r.json()
                rate = data.get("Realtime Currency Exchange Rate", {})
                if rate:
                    return {
                        "price": float(rate["5. Exchange Rate"]),
                        "bid": float(rate.get("8. Bid Price", rate["5. Exchange Rate"])),
                        "ask": float(rate.get("9. Ask Price", rate["5. Exchange Rate"])),
                        "time": rate.get("6. Last Refreshed", "N/A"),
                    }
    except Exception as e:
        logger.error(f"Narx olishda xato: {e}")
    return None

async def get_intraday_data():
    url = (f"https://www.alphavantage.co/query?function=FX_INTRADAY"
           f"&from_symbol=XAU&to_symbol=USD&interval=5min&outputsize=compact"
           f"&apikey={ALPHA_VANTAGE_KEY}")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                data = await r.json()
                series = data.get("Time Series FX (5min)", {})
                closes = [float(v["4. close"]) for v in list(series.values())[:20]]
                return closes
    except Exception as e:
        logger.error(f"Intraday data xato: {e}")
    return []

def calc_ema(prices, period):
    if len(prices) < period:
        return sum(prices) / len(prices)
    k = 2 / (period + 1)
    ema = prices[-period]
    for p in prices[-period + 1:]:
        ema = p * k + ema * (1 - k)
    return ema

def calc_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, period + 1):
        d = prices[i] - prices[i - 1]
        (gains if d > 0 else losses).append(abs(d))
    ag = sum(gains) / period if gains else 0
    al = sum(losses) / period if losses else 1e-9
    return round(100 - 100 / (1 + ag / al), 2)

def analyze_signal(prices, current):
    if len(prices) < 14:
        return {"signal": "⏳ WAIT", "confidence": 0, "reason": "Ma'lumot yetarli emas",
                "ema9": current, "ema21": current, "rsi": 50, "trend": "—",
                "sl": current - 5, "tp1": current + 5, "tp2": current + 10,
                "high5": current, "low5": current}

    ema9 = calc_ema(prices, min(9, len(prices)))
    ema21 = calc_ema(prices, min(21, len(prices)))
    rsi = calc_rsi(prices)
    recent = prices[:5]
    high5 = max(recent)
    low5 = min(recent)
    spread = high5 - low5
    trend = "UP" if prices[0] > prices[4] else "DOWN"
    score = 0

    if ema9 > ema21: score += 2
    else: score -= 2
    if rsi < 35: score += 2
    elif rsi > 65: score -= 2
    elif rsi < 50: score += 1
    else: score -= 1
    if trend == "UP": score += 1
    else: score -= 1
    if current > ema9: score += 1
    else: score -= 1

    atr = spread / 5 if spread > 0 else 2

    if spread > 8:
        signal, conf, reason = "⚠️ WAIT", 40, "Bozor juda o'zgaruvchan"
    elif score >= 4:
        signal, conf, reason = "🟢 BUY", min(95, 60 + score * 5), f"EMA cross + RSI {rsi} + Trend yuqoriga"
    elif score <= -4:
        signal, conf, reason = "🔴 SELL", min(95, 60 + abs(score) * 5), f"EMA cross + RSI {rsi} + Trend pastga"
    elif score > 0:
        signal, conf, reason = "🟡 WEAK BUY", 45, f"Kuchsiz ko'tarilish, RSI {rsi}"
    elif score < 0:
        signal, conf, reason = "🟠 WEAK SELL", 45, f"Kuchsiz tushish, RSI {rsi}"
    else:
        signal, conf, reason = "⏳ WAIT", 50, "Aniq signal yo'q"

    sl = round(low5 - atr * 1.5, 2) if "BUY" in signal else round(high5 + atr * 1.5, 2)
    tp1 = round(current + atr * 2, 2) if "BUY" in signal else round(current - atr * 2, 2)
    tp2 = round(current + atr * 4, 2) if "BUY" in signal else round(current - atr * 4, 2)

    return {"signal": signal, "confidence": conf, "reason": reason,
            "ema9": round(ema9, 2), "ema21": round(ema21, 2), "rsi": rsi,
            "trend": trend, "sl": sl, "tp1": tp1, "tp2": tp2,
            "high5": round(high5, 2), "low5": round(low5, 2)}

def format_signal_message(price_data, analysis):
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    bars = "█" * (analysis["confidence"] // 10) + "░" * (10 - analysis["confidence"] // 10)
    trend_arrow = "📈" if analysis["trend"] == "UP" else "📉"
    return (
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🥇 *XAUUSD (OLTIN) SIGNAL*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"💰 *Narx:* `{price_data['price']:.2f}` USD\n"
        f"🕐 *Vaqt:* {now}\n\n"
        f"┌─────────────────────\n"
        f"│ {analysis['signal']}\n"
        f"│ Ishonch: {bars} {analysis['confidence']}%\n"
        f"│ Sabab: _{analysis['reason']}_\n"
        f"└─────────────────────\n\n"
        f"📊 *Indikatorlar:*\n"
        f"  • EMA 9:  `{analysis['ema9']}`\n"
        f"  • EMA 21: `{analysis['ema21']}`\n"
        f"  • RSI:    `{analysis['rsi']}`\n"
        f"  • Trend:  {trend_arrow} {analysis['trend']}\n\n"
        f"🎯 *Darajalar:*\n"
        f"  • Stop Loss: `{analysis['sl']}`\n"
        f"  • Take P1:   `{analysis['tp1']}`\n"
        f"  • Take P2:   `{analysis['tp2']}`\n\n"
        f"⚠️ _Risk managementni unutmang!_"
    )

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Signal olish", callback_data="signal")],
        [InlineKeyboardButton("💰 Joriy narx", callback_data="price")],
        [InlineKeyboardButton("🔔 Obuna bo'lish", callback_data="subscribe")],
    ])
    await update.message.reply_text(
        "🥇 *XAUUSD Trading Signal Bot*\n\n"
        "Salom! Men oltin bozori uchun signal beraman.\n\n"
        "• /signal — signal olish\n"
        "• /price — joriy narx\n"
        "• /subscribe — avtomatik signal\n"
        "• Narx yuboring: `4545` — tahlil qilaman",
        parse_mode="Markdown", reply_markup=kb,
    )

async def cmd_signal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message or update.callback_query.message
    wait = await msg.reply_text("⏳ Tahlil qilinmoqda...")
    try:
        price_data = await get_gold_price()
        prices = await get_intraday_data()
        if not price_data:
            await wait.edit_text("❌ API dan narx olib bo'lmadi. Keyinroq urinib ko'ring.")
            return
        if not prices:
            prices = [price_data["price"]] * 14
        analysis = analyze_signal(prices, price_data["price"])
        text = format_signal_message(price_data, analysis)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Yangilash", callback_data="signal"),
             InlineKeyboardButton("💰 Narx", callback_data="price")],
        ])
        await wait.edit_text(text, parse_mode="Markdown", reply_markup=kb)
    except Exception as e:
        logger.error(f"Signal xato: {e}")
        await wait.edit_text("❌ Xato yuz berdi. Qaytadan urinib ko'ring.")

async def cmd_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message or update.callback_query.message
    wait = await msg.reply_text("⏳ Narx olinmoqda...")
    price_data = await get_gold_price()
    if price_data:
        text = (f"🥇 *XAUUSD Joriy Narx*\n\n"
                f"💰 Narx: `{price_data['price']:.2f}`\n"
                f"📤 Ask:  `{price_data['ask']:.2f}`\n"
                f"📥 Bid:  `{price_data['bid']:.2f}`\n"
                f"🕐 Vaqt: {price_data['time']}")
    else:
        text = "❌ Narxni olib bo'lmadi."
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("📊 Signal olish", callback_data="signal")]])
    await wait.edit_text(text, parse_mode="Markdown", reply_markup=kb)

async def handle_manual_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        price = float(update.message.text.strip().replace(",", "."))
    except ValueError:
        await update.message.reply_text("❓ Narx yuboring (masalan: `4545.60`)", parse_mode="Markdown")
        return
    if not (1000 < price < 10000):
        await update.message.reply_text("⚠️ XAUUSD narxi 1000–10000 oralig'ida bo'lishi kerak.")
        return
    wait = await update.message.reply_text("⏳ Tahlil qilinmoqda...")
    prices = await get_intraday_data()
    if not prices:
        prices = [price] * 14
    analysis = analyze_signal(prices, price)
    price_data = {"price": price, "ask": price + 0.3, "bid": price - 0.3}
    text = format_signal_message(price_data, analysis)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Real narx bilan", callback_data="signal")]])
    await wait.edit_text(text, parse_mode="Markdown", reply_markup=kb)

async def auto_signal_job(ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = ctx.job.data
    try:
        price_data = await get_gold_price()
        prices = await get_intraday_data()
        if not price_data or not prices:
            return
        analysis = analyze_signal(prices, price_data["price"])
        if "WAIT" in analysis["signal"] or "WEAK" in analysis["signal"]:
            return
        text = "🔔 *AVTOMATIK SIGNAL*\n\n" + format_signal_message(price_data, analysis)
        await ctx.bot.send_message(chat_id, text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Auto signal xato: {e}")

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "signal":
        await cmd_signal(update, ctx)
    elif q.data == "price":
        await cmd_price(update, ctx)
    elif q.data == "subscribe":
        chat_id = update.effective_chat.id
        for job in ctx.job_queue.get_jobs_by_name(f"auto_{chat_id}"):
            job.schedule_removal()
        ctx.job_queue.run_repeating(auto_signal_job, interval=1800, first=10,
                                    data=chat_id, name=f"auto_{chat_id}")
        await q.message.reply_text("✅ Har 30 daqiqada signal olasiz!\nBekor qilish: /unsubscribe")

async def cmd_subscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    for job in ctx.job_queue.get_jobs_by_name(f"auto_{chat_id}"):
        job.schedule_removal()
    ctx.job_queue.run_repeating(auto_signal_job, interval=1800, first=10,
                                data=chat_id, name=f"auto_{chat_id}")
    await update.message.reply_text("✅ Obuna bo'ldingiz! Har 30 daqiqada signal keladi.\nBekor qilish: /unsubscribe")

async def cmd_unsubscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    for job in ctx.job_queue.get_jobs_by_name(f"auto_{chat_id}"):
        job.schedule_removal()
    await update.message.reply_text("❌ Obuna bekor qilindi.")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("signal", cmd_signal))
    app.add_handler(CommandHandler("price", cmd_price))
    app.add_handler(CommandHandler("subscribe", cmd_subscribe))
    app.add_handler(CommandHandler("unsubscribe", cmd_unsubscribe))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_manual_price))
    logger.info("Bot ishga tushdi ✅")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
