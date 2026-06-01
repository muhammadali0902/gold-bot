#!/usr/bin/env python3
import logging
import asyncio
import aiohttp
import json
import os
from datetime import datetime, timedelta, timezone
import zoneinfo
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
    PreCheckoutQueryHandler
)

BOT_TOKEN = "8620122819:AAGwp7yCX5s816zZs17kM8rpDhGWpxAexX4"
TWELVE_DATA_KEY = "b7e3d63b149644698d40763661942f9d"
CHANNEL = "@gold_signaluz"
ADMIN_IDS = [5398690867]  # Bot egasi — bepul premium
FREE_SIGNALS = 3
PREMIUM_STARS = 500  # 500 Stars = ~$10
DATA_FILE = "users.json"

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ── USER DATA ──────────────────────────────────────────────────────────────────
def load_users():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {}

def save_users(users):
    with open(DATA_FILE, "w") as f:
        json.dump(users, f)

def get_user(user_id: int):
    users = load_users()
    uid = str(user_id)
    if uid not in users:
        users[uid] = {"signals_used": 0, "premium": False, "premium_until": None}
        save_users(users)
    return users[uid]

def update_user(user_id: int, data: dict):
    users = load_users()
    uid = str(user_id)
    if uid not in users:
        users[uid] = {"signals_used": 0, "premium": False, "premium_until": None}
    users[uid].update(data)
    save_users(users)

def is_premium(user_id: int) -> bool:
    if user_id in ADMIN_IDS:
        return True  # Admin har doim premium
    user = get_user(user_id)
    if not user["premium"]:
        return False
    if user["premium_until"]:
        until = datetime.fromisoformat(user["premium_until"])
        if datetime.now() > until:
            update_user(user_id, {"premium": False, "premium_until": None})
            return False
    return True

def can_use_signal(user_id: int) -> bool:
    if user_id in ADMIN_IDS:
        return True  # Admin cheksiz signal
    if is_premium(user_id):
        return True
    user = get_user(user_id)
    return user["signals_used"] < FREE_SIGNALS

def use_signal(user_id: int):
    if not is_premium(user_id):
        user = get_user(user_id)
        update_user(user_id, {"signals_used": user["signals_used"] + 1})

def signals_left(user_id: int) -> int:
    if is_premium(user_id):
        return 999
    user = get_user(user_id)
    return max(0, FREE_SIGNALS - user["signals_used"])

# ── API ────────────────────────────────────────────────────────────────────────
async def check_subscription(user_id: int, bot) -> bool:
    try:
        member = await bot.get_chat_member(CHANNEL, user_id)
        return member.status in ["member", "administrator", "creator"]
    except:
        return False

async def get_gold_price():
    url = f"https://api.twelvedata.com/price?symbol=XAU/USD&apikey={TWELVE_DATA_KEY}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                data = await r.json()
                if "price" in data:
                    price = float(data["price"])
                    return {
                        "price": price,
                        "bid": price - 0.15,
                        "ask": price + 0.15,
                        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    }
    except Exception as e:
        logger.error(f"Narx olishda xato: {e}")
    return None

async def get_intraday_data():
    url = (f"https://api.twelvedata.com/time_series"
           f"?symbol=XAU/USD&interval=5min&outputsize=20"
           f"&apikey={TWELVE_DATA_KEY}")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                data = await r.json()
                values = data.get("values", [])
                closes = [float(v["close"]) for v in values]
                return closes
    except Exception as e:
        logger.error(f"Intraday data xato: {e}")
    return []

# ── TAHLIL ─────────────────────────────────────────────────────────────────────
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


def calc_macd(prices, fast=12, slow=26, signal=9):
    if len(prices) < slow:
        return 0, 0, 0
    ema_fast = calc_ema(prices, fast)
    ema_slow = calc_ema(prices, slow)
    macd_line = ema_fast - ema_slow
    # Signal line (EMA of MACD)
    macd_values = []
    for i in range(len(prices) - slow + 1):
        ef = calc_ema(prices[i:i+slow], fast)
        es = calc_ema(prices[i:i+slow], slow)
        macd_values.append(ef - es)
    if len(macd_values) >= signal:
        signal_line = calc_ema(macd_values, signal)
    else:
        signal_line = macd_line
    histogram = macd_line - signal_line
    return round(macd_line, 4), round(signal_line, 4), round(histogram, 4)

def calc_bollinger(prices, period=20, std_dev=2):
    if len(prices) < period:
        return 0, 0, 0
    recent = prices[:period]
    middle = sum(recent) / period
    variance = sum((p - middle) ** 2 for p in recent) / period
    std = variance ** 0.5
    upper = round(middle + std_dev * std, 2)
    lower = round(middle - std_dev * std, 2)
    return round(upper, 2), round(middle, 2), round(lower, 2)

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
        signal, conf, reason = "🟢 BUY", min(95, 60 + score * 5), f"EMA + RSI {rsi} + Trend yuqoriga"
    elif score <= -4:
        signal, conf, reason = "🔴 SELL", min(95, 60 + abs(score) * 5), f"EMA + RSI {rsi} + Trend pastga"
    elif score > 0:
        signal, conf, reason = "🟡 WEAK BUY", 45, f"Kuchsiz ko'tarilish, RSI {rsi}"
    elif score < 0:
        signal, conf, reason = "🟠 WEAK SELL", 45, f"Kuchsiz tushish, RSI {rsi}"
    else:
        signal, conf, reason = "⏳ WAIT", 50, "Aniq signal yo'q"
    # MACD
    macd_line, signal_line, histogram = calc_macd(prices)
    if histogram > 0: score += 1
    else: score -= 1

    # Bollinger Bands
    bb_upper, bb_middle, bb_lower = calc_bollinger(prices)
    if current < bb_lower: score += 1   # Pastki banddan chiqdi — BUY
    elif current > bb_upper: score -= 1  # Yuqori banddan chiqdi — SELL

    # Qayta hisoblash score asosida
    if spread > 8:
        signal, conf, reason = "⚠️ WAIT", 40, "Bozor juda o'zgaruvchan"
    elif score >= 5:
        signal, conf, reason = "🟢 BUY", min(95, 60 + score * 4), f"EMA+MACD+RSI {rsi} — Kuchli signal"
    elif score <= -5:
        signal, conf, reason = "🔴 SELL", min(95, 60 + abs(score) * 4), f"EMA+MACD+RSI {rsi} — Kuchli signal"
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
            "high5": round(high5, 2), "low5": round(low5, 2),
            "macd": macd_line, "macd_signal": signal_line, "histogram": histogram,
            "bb_upper": bb_upper, "bb_middle": bb_middle, "bb_lower": bb_lower}

def format_signal_message(price_data, analysis, user_id):
    now = datetime.now(zoneinfo.ZoneInfo("Asia/Tashkent")).strftime("%d.%m.%Y %H:%M")
    bars = "█" * (analysis["confidence"] // 10) + "░" * (10 - analysis["confidence"] // 10)
    trend_arrow = "📈" if analysis["trend"] == "UP" else "📉"
    left = signals_left(user_id)
    plan = "👑 PREMIUM" if is_premium(user_id) else f"🆓 Bepul ({left} ta qoldi)"
    return (
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🥇 *XAUUSD (OLTIN) SIGNAL*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"💰 *Narx:* `{price_data['price']:.2f}` USD\n"
        f"🕐 *Vaqt:* {now}\n"
        f"📋 *Reja:* {plan}\n\n"
        f"┌─────────────────────\n"
        f"│ {analysis['signal']}\n"
        f"│ Ishonch: {bars} {analysis['confidence']}%\n"
        f"│ Sabab: _{analysis['reason']}_\n"
        f"└─────────────────────\n\n"
        f"📊 *Indikatorlar:*\n"
        f"  • EMA 9:   `{analysis['ema9']}`\n"
        f"  • EMA 21:  `{analysis['ema21']}`\n"
        f"  • RSI:     `{analysis['rsi']}`\n"
        f"  • MACD:    `{analysis.get('macd', 0)}`\n"
        f"  • Signal:  `{analysis.get('macd_signal', 0)}`\n"
        f"  • BB yuqori: `{analysis.get('bb_upper', 0)}`\n"
        f"  • BB pastki: `{analysis.get('bb_lower', 0)}`\n"
        f"  • Trend:   {trend_arrow} {analysis['trend']}\n\n"
        f"🎯 *Darajalar:*\n"
        f"  • Stop Loss: `{analysis['sl']}`\n"
        f"  • Take P1:   `{analysis['tp1']}`\n"
        f"  • Take P2:   `{analysis['tp2']}`\n\n"
        f"⚠️ _Risk managementni unutmang!_"
    )

# ── BUYRUQLAR ──────────────────────────────────────────────────────────────────
async def not_subscribed_msg(update):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Kanalga obuna bo'lish", url="https://t.me/gold_signaluz")],
        [InlineKeyboardButton("✅ Obuna bo'ldim", callback_data="check_sub")],
    ])
    await update.message.reply_text(
        "⚠️ *Botdan foydalanish uchun kanalga obuna bo'ling!*\n\n"
        f"📢 Kanal: {CHANNEL}",
        parse_mode="Markdown", reply_markup=kb)

async def premium_required_msg(message, user_id):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("👑 Premium olish (500 ⭐)", callback_data="buy_premium")],
    ])
    await message.reply_text(
        "⚠️ *Bepul signallar tugadi!*\n\n"
        f"Siz {FREE_SIGNALS} ta bepul signal ishlatdingiz.\n\n"
        "👑 *Premium — 500 Telegram Stars (~$10/oy)*\n"
        "✅ Cheksiz signal\n"
        "✅ Har 15 daqiqada avtomatik signal\n"
        "✅ SL/TP aniq ko'rsatiladi\n\n"
        "Premium olish uchun tugmani bosing! 👇",
        parse_mode="Markdown", reply_markup=kb)

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.effective_user.first_name
    if not await check_subscription(user_id, ctx.bot):
        await not_subscribed_msg(update)
        return
    get_user(user_id)
    left = signals_left(user_id)
    plan = "👑 PREMIUM" if is_premium(user_id) else f"🆓 Bepul ({left} ta signal qoldi)"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Signal olish", callback_data="signal")],
        [InlineKeyboardButton("💰 Joriy narx", callback_data="price")],
        [InlineKeyboardButton("👑 Premium olish", callback_data="buy_premium")],
        [InlineKeyboardButton("📋 Mening rejam", callback_data="my_plan")],
        [InlineKeyboardButton("🌍 Sessiyalar", callback_data="session")],
    ])
    await update.message.reply_text(
        f"🥇 *XAUUSD Trading Signal Bot*\n\n"
        f"Salom, {name}! 👋\n\n"
        f"📋 Sizning rejangiz: *{plan}*\n\n"
        f"• /signal — signal olish\n"
        f"• /price — joriy narx\n"
        f"• /premium — premium olish\n"
        f"• Narx yuboring: `4545` — tahlil",
        parse_mode="Markdown", reply_markup=kb)

async def cmd_signal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await check_subscription(user_id, ctx.bot):
        await not_subscribed_msg(update)
        return
    if not can_use_signal(user_id):
        msg = update.message or update.callback_query.message
        await premium_required_msg(msg, user_id)
        return
    msg = update.message or update.callback_query.message
    wait = await msg.reply_text("⏳ Tahlil qilinmoqda...")
    try:
        price_data = await get_gold_price()
        prices = await get_intraday_data()
        if not price_data:
            await wait.edit_text("❌ Narx olib bo'lmadi. Keyinroq urinib ko'ring.")
            return
        if not prices:
            prices = [price_data["price"]] * 14
        analysis = analyze_signal(prices, price_data["price"])
        use_signal(user_id)
        text = format_signal_message(price_data, analysis, user_id)
        left = signals_left(user_id)
        kb_buttons = [[InlineKeyboardButton("🔄 Yangilash", callback_data="signal"),
                       InlineKeyboardButton("💰 Narx", callback_data="price")]]
        if not is_premium(user_id) and left == 0:
            kb_buttons.append([InlineKeyboardButton("👑 Premium olish", callback_data="buy_premium")])
        kb = InlineKeyboardMarkup(kb_buttons)
        await wait.edit_text(text, parse_mode="Markdown", reply_markup=kb)
    except Exception as e:
        logger.error(f"Signal xato: {e}")
        await wait.edit_text("❌ Xato yuz berdi.")

async def cmd_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await check_subscription(user_id, ctx.bot):
        await not_subscribed_msg(update)
        return
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

async def cmd_premium(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_premium(user_id):
        user = get_user(user_id)
        until = user.get("premium_until", "Noma'lum")
        await update.message.reply_text(
            f"👑 *Siz allaqachon Premium foydalanuvchisiz!*\n\n"
            f"📅 Muddati: {until[:10] if until else 'Cheksiz'}",
            parse_mode="Markdown")
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐ 500 Stars bilan to'lash", callback_data="buy_premium")],
    ])
    await update.message.reply_text(
        "👑 *Premium Reja — 500 Telegram Stars (~$10/oy)*\n\n"
        "✅ Cheksiz signal\n"
        "✅ Har 15 daqiqada avtomatik signal\n"
        "✅ SL/TP aniq ko'rsatiladi\n"
        "✅ Kanal obunasi bilan birga\n\n"
        "👇 Tugmani bosib to'lang:",
        parse_mode="Markdown", reply_markup=kb)

async def handle_manual_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await check_subscription(user_id, ctx.bot):
        await not_subscribed_msg(update)
        return
    if not can_use_signal(user_id):
        await premium_required_msg(update.message, user_id)
        return
    try:
        price = float(update.message.text.strip().replace(",", "."))
    except ValueError:
        return
    if not (1000 < price < 10000):
        await update.message.reply_text("⚠️ XAUUSD narxi 1000-10000 oralig'ida bo'lishi kerak.")
        return
    wait = await update.message.reply_text("⏳ Tahlil qilinmoqda...")
    prices = await get_intraday_data()
    if not prices:
        prices = [price] * 14
    analysis = analyze_signal(prices, price)
    price_data = {"price": price, "ask": price + 0.3, "bid": price - 0.3}
    use_signal(user_id)
    text = format_signal_message(price_data, analysis, user_id)
    await wait.edit_text(text, parse_mode="Markdown")

async def auto_signal_job(ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = ctx.job.data
    if not is_premium(chat_id):
        return
    try:
        price_data = await get_gold_price()
        prices = await get_intraday_data()
        if not price_data or not prices:
            return
        analysis = analyze_signal(prices, price_data["price"])
        if "WAIT" in analysis["signal"] or "WEAK" in analysis["signal"]:
            return
        text = "🔔 *AVTOMATIK SIGNAL*\n\n" + format_signal_message(price_data, analysis, chat_id)
        await ctx.bot.send_message(chat_id, text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Auto signal xato: {e}")

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user_id = update.effective_user.id

    if q.data == "check_sub":
        if await check_subscription(user_id, ctx.bot):
            await q.message.reply_text("✅ Rahmat! Endi /start yuboring!", parse_mode="Markdown")
        else:
            await q.answer("❌ Hali obuna bo'lmadingiz!", show_alert=True)
        return

    if q.data == "my_plan":
        left = signals_left(user_id)
        plan = "👑 PREMIUM" if is_premium(user_id) else f"🆓 Bepul ({left} ta signal qoldi)"
        await q.message.reply_text(f"📋 *Sizning rejangiz:* {plan}", parse_mode="Markdown")
        return

    if q.data == "buy_premium":
        await ctx.bot.send_invoice(
            chat_id=user_id,
            title="👑 Gold Signal Premium",
            description="1 oylik premium obuna — cheksiz signal, avtomatik signal har 15 daqiqada",
            payload="premium_1month",
            currency="XTR",
            prices=[LabeledPrice("Premium 1 oy", PREMIUM_STARS)],
        )
        return

    if not await check_subscription(user_id, ctx.bot):
        await q.answer("❌ Avval kanalga obuna bo'ling!", show_alert=True)
        return

    if q.data == "session":
        await cmd_session(update, ctx)
        return
    if q.data == "signal":
        await cmd_signal(update, ctx)
    elif q.data == "price":
        await cmd_price(update, ctx)
    elif q.data == "subscribe":
        if not is_premium(user_id):
            await q.message.reply_text("⚠️ Avtomatik signal faqat Premium foydalanuvchilar uchun!\n\n/premium buyrug'ini yuboring.")
            return
        chat_id = update.effective_chat.id
        for job in ctx.job_queue.get_jobs_by_name(f"auto_{chat_id}"):
            job.schedule_removal()
        ctx.job_queue.run_repeating(auto_signal_job, interval=900, first=10,
                                    data=chat_id, name=f"auto_{chat_id}")
        await q.message.reply_text("✅ Har 15 daqiqada signal olasiz!")

async def precheckout(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def successful_payment(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    until = (datetime.now() + timedelta(days=30)).isoformat()
    update_user(user_id, {"premium": True, "premium_until": until})
    chat_id = update.effective_chat.id
    for job in ctx.job_queue.get_jobs_by_name(f"auto_{chat_id}"):
        job.schedule_removal()
    ctx.job_queue.run_repeating(auto_signal_job, interval=900, first=10,
                                data=chat_id, name=f"auto_{chat_id}")
    await update.message.reply_text(
        "🎉 *To'lov qabul qilindi! Premium faollashdi!*\n\n"
        "👑 Endi cheksiz signal olasiz!\n"
        "🔔 Avtomatik signal har 15 daqiqada keladi!\n\n"
        "Muddati: 30 kun",
        parse_mode="Markdown")

async def cmd_subscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_premium(user_id):
        await update.message.reply_text("⚠️ Avtomatik signal faqat Premium uchun!\n\n/premium yuboring.")
        return
    chat_id = update.effective_chat.id
    for job in ctx.job_queue.get_jobs_by_name(f"auto_{chat_id}"):
        job.schedule_removal()
    ctx.job_queue.run_repeating(auto_signal_job, interval=900, first=10,
                                data=chat_id, name=f"auto_{chat_id}")
    await update.message.reply_text("✅ Har 15 daqiqada avtomatik signal keladi!")

async def cmd_unsubscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    for job in ctx.job_queue.get_jobs_by_name(f"auto_{chat_id}"):
        job.schedule_removal()
    await update.message.reply_text("❌ Avtomatik signal o'chirildi.")


async def cmd_session(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Forex sessiyalari holati"""
    tz = zoneinfo.ZoneInfo("Asia/Tashkent")
    now = datetime.now(tz)
    hour = now.hour + now.minute / 60

    sessions = {
        "🇦🇺 Sidney":   (2, 11),
        "🇯🇵 Tokio":    (4, 13),
        "🇬🇧 London":   (13, 22),
        "🇺🇸 Nyu-York": (18, 27),  # 27 = 03:00 ertasi
    }

    lines = []
    active = []
    for name, (start, end) in sessions.items():
        h = hour if hour >= start else hour + 24
        is_open = start <= h < end
        status = "🟢 OCHIQ" if is_open else "🔴 YOPIQ"
        end_real = end if end <= 24 else end - 24
        lines.append(f"{name}: {status} ({start:02.0f}:00 — {end_real:02.0f}:00)")
        if is_open:
            active.append(name.split()[-1])

    # Overlap tekshirish
    london_open = 13 <= hour < 22
    ny_open = 18 <= hour < 27
    overlap = london_open and ny_open

    text = (
        f"🌍 *FOREX SESSIYALARI*\n"
        f"🕐 Hozir: *{now.strftime('%H:%M')}* (Toshkent)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
    )
    for line in lines:
        text += f"• {line}\n"

    text += "\n"
    if overlap:
        text += "⚡ *London + NY overlap — ENG FAOL VAQT!* 🔥\n"
        text += "💡 Hozir signal kuchi yuqori!\n"
    elif active:
        text += f"✅ *Faol sessiya:* {', '.join(active)}\n"
    else:
        text += "😴 *Hozir faol sessiya yo'q*\n"
        text += "💡 London sessiyasi: 13:00 dan\n"

    text += (
        f"\n━━━━━━━━━━━━━━━━━━━━\n"
        f"🥇 *XAUUSD uchun eng yaxshi vaqt:*\n"
        f"• London: 13:00 — 22:00\n"
        f"• London+NY: 18:00 — 22:00 🔥\n"
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Signal olish", callback_data="signal")],
        [InlineKeyboardButton("🔄 Yangilash", callback_data="session")],
    ])
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("session", cmd_session))
    app.add_handler(CommandHandler("signal", cmd_signal))
    app.add_handler(CommandHandler("price", cmd_price))
    app.add_handler(CommandHandler("premium", cmd_premium))
    app.add_handler(CommandHandler("subscribe", cmd_subscribe))
    app.add_handler(CommandHandler("unsubscribe", cmd_unsubscribe))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_manual_price))
    logger.info("Bot ishga tushdi ✅")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
