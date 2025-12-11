import csv
import io
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, Optional

import requests
from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from dotenv import load_dotenv

#Конфигурация
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")

if not TELEGRAM_BOT_TOKEN or not OPENWEATHER_API_KEY:
    raise ValueError("Укажите TELEGRAM_BOT_TOKEN и OPENWEATHER_API_KEY в .env")

#Логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

USER_DATA_FILE = "user_data.json"
user_data_storage: Dict[int, dict] = {}

#Утилиты 

def load_persistent_data():
    global user_data_storage
    if os.path.exists(USER_DATA_FILE):
        try:
            with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
                user_data_storage = {int(k): v for k, v in raw.items()}
        except Exception as e:
            logger.error(f"Ошибка загрузки данных: {e}")

def save_persistent_data():
    try:
        with open(USER_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(user_data_storage, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"Ошибка сохранения: {e}")

def get_default_city(user_id: int) -> Optional[str]:
    return user_data_storage.get(user_id, {}).get("default_city")

def set_default_city(user_id: int, city: str):
    if user_id not in user_data_storage:
        user_data_storage[user_id] = {}
    user_data_storage[user_id]["default_city"] = city
    save_persistent_data()

def add_to_history(user_id: int, city: str, temp: float, desc: str):
    if user_id not in user_data_storage:
        user_data_storage[user_id] = {}
    if "history" not in user_data_storage[user_id]:
        user_data_storage[user_id]["history"] = []
    user_data_storage[user_id]["history"].append({
        "city": city,
        "temp": temp,
        "desc": desc,
        "timestamp": datetime.now().isoformat()
    })
    save_persistent_data()

def get_user_history(user_id: int):
    return user_data_storage.get(user_id, {}).get("history", [])

def get_weather_now(city: str) -> Optional[dict]:
    url = "http://api.openweathermap.org/data/2.5/weather"
    params = {"q": city, "appid": OPENWEATHER_API_KEY, "units": "metric", "lang": "ru"}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return {
            "city": city,
            "temp": data["main"]["temp"],
            "feels_like": data["main"]["feels_like"],
            "desc": data["weather"][0]["description"],
            "humidity": data["main"]["humidity"],
            "wind_speed": data.get("wind", {}).get("speed", 0)
        }
    except Exception as e:
        logger.error(f"Ошибка погоды для {city}: {e}")
        return None

def get_5_day_forecast(city: str) -> Optional[list]:
    url = "http://api.openweathermap.org/data/2.5/forecast"
    params = {"q": city, "appid": OPENWEATHER_API_KEY, "units": "metric", "lang": "ru"}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        days = {}
        for item in data["list"]:
            date = datetime.fromtimestamp(item["dt"]).strftime("%Y-%m-%d")
            if date not in days:
                days[date] = {
                    "date": date,
                    "temp": item["main"]["temp"],
                    "desc": item["weather"][0]["description"]
                }
            if len(days) >= 5:
                break
        return list(days.values())[:5]
    except Exception as e:
        logger.error(f"Ошибка прогноза для {city}: {e}")
        return None

def get_yesterday_weather(user_id: int, city: str) -> Optional[dict]:
    history = get_user_history(user_id)
    yesterday = (datetime.now().date() - timedelta(days=1)).isoformat()
    for record in reversed(history):
        if record["city"] == city and record["timestamp"].startswith(yesterday):
            return record
    return None

def format_now_message(data: dict) -> str:
    city = data["city"]
    temp = data["temp"]
    feels = data["feels_like"]
    desc = data["desc"].capitalize()
    humidity = data["humidity"]
    wind = data["wind_speed"]
    advice = []
    t = temp
    if t >= 25:
        advice.append("Наденьте лёгкую одежду.")
    elif t >= 15:
        advice.append("Тёплая одежда не требуется, но возьмите лёгкую куртку.")
    elif t >= 5:
        advice.append("Рекомендуется куртка или пальто.")
    elif t >= -5:
        advice.append("Обязательно наденьте тёплую куртку, шапку и перчатки.")
    else:
        advice.append("Очень холодно! Теплое пальто, шапка, шарф, перчатки — обязательно.")
    if "дождь" in data["desc"] or "ливень" in data["desc"]:
        advice.append("Возьмите зонт и наденьте непромокаемую обувь.")
    elif "снег" in data["desc"]:
        advice.append("Наденьте непромокаемую обувь и тёплую одежду.")
    return (
        f"🌤 <b>{city}</b>\n"
        f"Температура: {temp:.1f}°C (ощущается как {feels:.1f}°C)\n"
        f"Описание: {desc}\n"
        f"Влажность: {humidity}%, Ветер: {wind} м/с\n\n"
        f"💡 <i>{' '.join(advice)}</i>"
    )

def format_forecast_message(city: str, days: list) -> str:
    lines = [f"📅 <b>Прогноз на 5 дней — {city}</b>"]
    for d in days:
        date = datetime.strptime(d["date"], "%Y-%m-%d").strftime("%d.%m")
        lines.append(f"• {date}: {d['temp']:.1f}°C, {d['desc'].capitalize()}")
    lines.append("\n💡 Одевайтесь по погоде!")
    return "\n".join(lines)

MAIN_MENU = [["🌤 Погода", "🔁 Сравнить погоду"], ["📊 Статистика", "📤 Экспорт CSV"], ["⚙️ Установить город"]]

def main_menu_markup():
    return ReplyKeyboardMarkup(MAIN_MENU, resize_keyboard=True)

#Обработчики 

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Привет! Выберите действие:", reply_markup=main_menu_markup())

async def send_weather_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, city: str):
    context.user_data["temp_city"] = city
    context.user_data["state"] = "choose_weather_type"
    kb = [["Сейчас", "Вчера", "На 5 дней"], ["← Назад"]]
    await update.message.reply_text(
        f"Город: {city}\nВыберите тип погоды:",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )

async def handle_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    user_id = update.effective_user.id

    if text == "🌤 Погода":
        default = get_default_city(user_id)
        if default:
            kb = [["Город по умолчанию", "Новый город"], ["← Назад"]]
            await update.message.reply_text(
                f"Ваш город по умолчанию: {default}\nВыберите:",
                reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
            )
            context.user_data["state"] = "choose_city_source"
        else:
            await update.message.reply_text("Введите город:")
            context.user_data["state"] = "enter_city"

    elif text == "⚙️ Установить город":
        await update.message.reply_text("Введите город для установки по умолчанию:")
        context.user_data["state"] = "set_default_city"

    elif text == "🔁 Сравнить погоду":
        await update.message.reply_text("Введите два города через пробел (например: Москва Сочи):")
        context.user_data["state"] = "compare_cities"

    elif text == "📊 Статистика":
        await show_stats(update, context)

    elif text == "📤 Экспорт CSV":
        await export_csv(update, context)

    else:
        await unknown(update, context)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id
    state = context.user_data.get("state")

    #Установка города по умолчанию
    if state == "set_default_city":
        if get_weather_now(text):
            set_default_city(user_id, text)
            await update.message.reply_text(f"✅ Город по умолчанию: {text}")
        else:
            await update.message.reply_text("❌ Город не найден. Попробуйте снова.")
        context.user_data.clear()
        await update.message.reply_text("Выберите действие:", reply_markup=main_menu_markup())
        return

    # --- Ввод города вручную ---
    if state == "enter_city":
        data = get_weather_now(text)
        if data:
            await send_weather_menu(update, context, data["city"])
        else:
            await update.message.reply_text("❌ Город не найден. Попробуйте снова.")
        return

    #Выбор источника города
    if state == "choose_city_source":
        if text == "← Назад":
            context.user_data.clear()
            await update.message.reply_text("Выберите действие:", reply_markup=main_menu_markup())
            return
        elif text == "Город по умолчанию":
            default = get_default_city(user_id)
            if default:
                await send_weather_menu(update, context, default)
            else:
                await update.message.reply_text("❌ Город по умолчанию не установлен.")
                context.user_data.clear()
                await update.message.reply_text("Выберите действие:", reply_markup=main_menu_markup())
            return
        elif text == "Новый город":
            await update.message.reply_text("Введите город:")
            context.user_data["state"] = "enter_city"
            return
        else:
            await update.message.reply_text("Выберите из меню.")
            return

    #Выбор типа погоды
    if state == "choose_weather_type":
        city = context.user_data.get("temp_city")
        if not city:
            await update.message.reply_text("❌ Ошибка: город не задан.")
            context.user_data.clear()
            await update.message.reply_text("Выберите действие:", reply_markup=main_menu_markup())
            return

        if text == "← Назад":
            context.user_data.clear()
            await update.message.reply_text("Выберите действие:", reply_markup=main_menu_markup())
            return

        if text == "Сейчас":
            data = get_weather_now(city)
            if data:
                msg = format_now_message(data)
                await update.message.reply_html(msg)
                add_to_history(user_id, data["city"], data["temp"], data["desc"])
            else:
                await update.message.reply_text("❌ Не удалось получить погоду.")

        elif text == "Вчера":
            record = get_yesterday_weather(user_id, city)
            if record:
                fake_data = {
                    "city": city,
                    "temp": record["temp"],
                    "feels_like": record["temp"],
                    "desc": record["desc"],
                    "humidity": 0,
                    "wind_speed": 0
                }
                msg = f"📅 <b>Вчерашняя погода — {city}</b>\n{format_now_message(fake_data)}"
                await update.message.reply_html(msg)
            else:
                await update.message.reply_text(
                    "📂 Вчерашняя погода не найдена в архиве.\n"
                    "Запрашивайте погоду ежедневно, чтобы она сохранялась!"
                )

        elif text == "На 5 дней":
            forecast = get_5_day_forecast(city)
            if forecast:
                msg = format_forecast_message(city, forecast)
                await update.message.reply_html(msg)
            else:
                await update.message.reply_text("❌ Не удалось получить прогноз.")
        else:
            await update.message.reply_text("Выберите из меню.")
            return

        context.user_data.clear()
        await update.message.reply_text("Выберите действие:", reply_markup=main_menu_markup())
        return

    #Сравнение городов
    if state == "compare_cities":
        cities = text.split()
        if len(cities) != 2:
            await update.message.reply_text("Введите ровно два города через пробел.")
            return
        c1, c2 = cities
        d1, d2 = get_weather_now(c1), get_weather_now(c2)
        if not d1 or not d2:
            await update.message.reply_text("❌ Один из городов не найден.")
        else:
            diff = d1["temp"] - d2["temp"]
            msg = (
                f"🌡 <b>{c1}</b>: {d1['temp']:.1f}°C ({d1['desc']})\n"
                f"🌡 <b>{c2}</b>: {d2['temp']:.1f}°C ({d2['desc']})\n"
                f"Разница: <b>{diff:+.1f}°C</b>"
            )
            await update.message.reply_html(msg)
        context.user_data.clear()
        await update.message.reply_text("Выберите действие:", reply_markup=main_menu_markup())
        return

    #Главное меню
    await handle_main_menu(update, context, text)

async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    history = get_user_history(user_id)
    if not history:
        await update.message.reply_text("📊 История пуста.")
        return
    from collections import Counter
    cities = [h["city"] for h in history]
    most_common, count = Counter(cities).most_common(1)[0]
    msg = (
        f"📊 Статистика:\n"
        f"Всего запросов: {len(history)}\n"
        f"Самый частый город: {most_common} ({count} раз)\n"
        f"Первый: {history[0]['timestamp'][:10]}\n"
        f"Последний: {history[-1]['timestamp'][:10]}"
    )
    await update.message.reply_text(msg)

async def export_csv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    history = get_user_history(user_id)
    if not history:
        await update.message.reply_text("📭 Нет данных для экспорта.")
        return
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Город", "Температура (°C)", "Погода", "Дата и время"])
    for h in history:
        writer.writerow([h["city"], h["temp"], h["desc"], h["timestamp"]])
    csv_bytes = output.getvalue().encode("utf-8-sig")
    csv_buffer = io.BytesIO(csv_bytes)
    csv_buffer.name = "weather_history.csv"
    await update.message.reply_document(
        document=csv_buffer,
        filename="weather_history.csv",
        caption="📄 Ваша история погоды"
    )

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❓ Используйте меню или /start.")

#Основной обработчик

async def unified_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.startswith("/"):
        if text == "/start":
            await start(update, context)
        else:
            await unknown(update, context)
        return
    await handle_message(update, context)

#Запуск

def main():
    load_persistent_data()
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unified_handler))
    logger.info("✅ Бот запущен.")
    app.run_polling()

if __name__ == "__main__":
    main()