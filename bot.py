import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone, date
from pathlib import Path
from zoneinfo import ZoneInfo
from openai import OpenAI

import swisseph as swe
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DEFAULT_SEND_TIME = os.getenv("DEFAULT_SEND_TIME", "08:30")
USER_TZ = os.getenv("USER_TZ", "Europe/Moscow")

BIRTH_YEAR = 1992
BIRTH_MONTH = 2
BIRTH_DAY = 5
BIRTH_HOUR = 2
BIRTH_MINUTE = 0
BIRTH_TZ = "Europe/Moscow"
BIRTH_LAT = 52.723056
BIRTH_LON = 41.453889

STATE_FILE = Path("state.json")

PLANETS = {
    "Sun": swe.SUN,
    "Moon": swe.MOON,
    "Mercury": swe.MERCURY,
    "Venus": swe.VENUS,
    "Mars": swe.MARS,
    "Jupiter": swe.JUPITER,
    "Saturn": swe.SATURN,
}

SIGNS_RU = [
    "Овен", "Телец", "Близнецы", "Рак", "Лев", "Дева",
    "Весы", "Скорпион", "Стрелец", "Козерог", "Водолей", "Рыбы"
]

ASPECTS = [
    ("соединение", 0, 5.0),
    ("секстиль", 60, 4.0),
    ("квадрат", 90, 4.0),
    ("тригон", 120, 4.0),
    ("оппозиция", 180, 5.0),
]


@dataclass
class TransitHit:
    transit_planet: str
    natal_planet: str
    aspect_name: str
    orb: float


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {
        "chat_id": None,
        "send_time": DEFAULT_SEND_TIME,
        "user_tz": USER_TZ,
    }


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def normalize_angle(angle: float) -> float:
    return angle % 360.0


def angle_distance(a: float, b: float) -> float:
    diff = abs((a - b) % 360.0)
    return min(diff, 360.0 - diff)


def zodiac_sign_name(longitude: float) -> str:
    idx = int(longitude // 30) % 12
    return SIGNS_RU[idx]


def local_birth_to_utc() -> datetime:
    local_dt = datetime(
        BIRTH_YEAR,
        BIRTH_MONTH,
        BIRTH_DAY,
        BIRTH_HOUR,
        BIRTH_MINUTE,
        tzinfo=ZoneInfo(BIRTH_TZ),
    )
    return local_dt.astimezone(timezone.utc)


def julday_from_utc(dt_utc: datetime) -> float:
    hour = dt_utc.hour + dt_utc.minute / 60 + dt_utc.second / 3600
    return swe.julday(dt_utc.year, dt_utc.month, dt_utc.day, hour)


def get_planet_longitude(jd_ut: float, planet_code: int) -> float:
    result, _ = swe.calc_ut(jd_ut, planet_code)
    return normalize_angle(result[0])


def get_natal_chart() -> dict:
    birth_utc = local_birth_to_utc()
    jd_ut = julday_from_utc(birth_utc)

    natal = {}
    for name, code in PLANETS.items():
        natal[name] = get_planet_longitude(jd_ut, code)

    _, ascmc = swe.houses(jd_ut, BIRTH_LAT, BIRTH_LON, b"P")
    natal["Asc"] = normalize_angle(ascmc[0])
    return natal


def get_transits_for_date(target_date: date, user_tz_name: str) -> dict:
    tz = ZoneInfo(user_tz_name)
    dt_local = datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        12, 0, 0,
        tzinfo=tz,
    )
    dt_utc = dt_local.astimezone(timezone.utc)
    jd_ut = julday_from_utc(dt_utc)

    transits = {}
    for name, code in PLANETS.items():
        transits[name] = get_planet_longitude(jd_ut, code)
    return transits


def find_strongest_transit_for_date(target_date: date, user_tz_name: str) -> TransitHit | None:
    natal = get_natal_chart()
    transits = get_transits_for_date(target_date, user_tz_name)
    candidates = []

    for t_name, t_lon in transits.items():
        for n_name in ["Sun", "Moon", "Mercury", "Venus", "Mars", "Asc"]:
            n_lon = natal[n_name]
            dist = angle_distance(t_lon, n_lon)

            for aspect_name, aspect_angle, orb_limit in ASPECTS:
                orb = abs(dist - aspect_angle)
                if orb <= orb_limit:
                    candidates.append(
                        TransitHit(
                            transit_planet=t_name,
                            natal_planet=n_name,
                            aspect_name=aspect_name,
                            orb=round(orb, 2),
                        )
                    )

    if not candidates:
        return None

    candidates.sort(key=lambda x: x.orb)
    return candidates[0]


def energy_text(hit: TransitHit | None, moon_sign: str) -> str:
    if hit is None:
        return "Ровный фон. Береги силы и не разбрасывайся."
    if hit.transit_planet == "Mars":
        if hit.aspect_name in ("квадрат", "оппозиция"):
            return "Энергии много, но она может идти в раздражение. Нужен физический выход."
        return "Сильный заряд на действие. Хорошо двигаться и закрывать зависшие задачи."
    if hit.transit_planet == "Saturn":
        return "Энергия может ощущаться плотной и тяжёлой. Темп лучше держать спокойный."
    if hit.transit_planet == "Jupiter":
        return "Фон бодрее обычного. Хорошо расширяться, ехать, знакомиться, делать шаг вперёд."
    if hit.transit_planet == "Moon":
        return "Энергия зависит от настроения. Не принимай решения на пике эмоций."
    if moon_sign in ("Овен", "Лев", "Стрелец"):
        return "Огонь дня подталкивает к инициативе. Не сиди слишком пассивно."
    if moon_sign in ("Рак", "Рыбы", "Скорпион"):
        return "Энергия тонкая и чувствительная. Береги нервную систему."
    return "Лучше всего сработает умеренный, собранный ритм."


def work_text(hit: TransitHit | None, moon_sign: str) -> str:
    if hit is None:
        return "Работай через приоритеты: одно главное дело и минимум суеты."
    if hit.transit_planet == "Mercury":
        if hit.aspect_name in ("квадрат", "оппозиция"):
            return "Перепроверяй детали, письма и договорённости. Не спеши с выводами."
        return "Отличный день для планирования, текстов, документов, переговоров и обучения."
    if hit.transit_planet == "Saturn":
        return "Лучше не распыляться. Хороши дисциплина, структура и доведение до конца."
    if hit.transit_planet == "Jupiter":
        return "Полезно думать шире: контакты, идеи роста, новые возможности."
    if hit.transit_planet == "Mars":
        return "Лучше делать, чем долго обсуждать. Закрывай то, что давно висит."
    if moon_sign in ("Дева", "Козерог"):
        return "Сильный день для порядка, таблиц, бюджета, рутины и практических задач."
    if moon_sign == "Близнецы":
        return "Хороши созвоны, переписка и быстрые переключения."
    return "Иди от простого к сложному и не перегружай день лишним."


def relationship_text(hit: TransitHit | None, moon_sign: str) -> str:
    if hit is None:
        return "Спокойное общение даст больше, чем давление и ожидания."
    if hit.transit_planet == "Venus":
        if hit.aspect_name in ("квадрат", "оппозиция"):
            return "Не идеализируй и не трать лишние эмоции. Лучше мягкость и честность."
        return "Хороший день для тёплого контакта, симпатии, свидания и примирения."
    if hit.transit_planet == "Moon":
        return "Чувствительность выше обычного. Лучше говорить бережно и без резкости."
    if hit.transit_planet == "Mars":
        return "Легко вспыхнуть. Полезно сначала выдохнуть, потом отвечать."
    if moon_sign in ("Весы", "Телец"):
        return "Лучше работает мягкость, красота, внимание к тону и атмосфере."
    if moon_sign in ("Скорпион", "Рак", "Рыбы"):
        return "Нужны тепло, безопасность и аккуратность с чувствами."
    return "Искренний короткий разговор лучше, чем недосказанность."


def day_theme_text(hit: TransitHit | None) -> str:
    if hit is None:
        return "День без жёсткого акцента: лучше не форсировать события."
    templates = {
        ("Sun", "тригон", "Sun"): "Сегодня легче проявляться, принимать решения и держать курс.",
        ("Sun", "квадрат", "Sun"): "Не дави на себя. Лучше меньше рывков, больше осознанности.",
        ("Moon", "соединение", "Moon"): "Эмоции ярче обычного. Сначала почувствуй, потом решай.",
        ("Mercury", "соединение", "Mercury"): "Сильный день для мыслей, текстов, планирования и учёбы.",
        ("Mercury", "квадрат", "Mercury"): "Перепроверяй детали: сегодня возможна путаница.",
        ("Venus", "тригон", "Venus"): "Подходящий день для симпатии, вкуса, денег и мягкого общения.",
        ("Venus", "квадрат", "Venus"): "Не идеализируй людей и не трать лишнего из настроения.",
        ("Mars", "соединение", "Mars"): "Много энергии. Лучше направить её в действие, а не в конфликт.",
        ("Mars", "квадрат", "Mars"): "Раздражение может вспыхивать быстро. Нужен физический выход.",
        ("Jupiter", "тригон", "Sun"): "Хороший день для роста, уверенности и расширения контактов.",
        ("Saturn", "квадрат", "Sun"): "День дисциплины: меньше обещаний, больше конкретики.",
    }
    return templates.get(
        (hit.transit_planet, hit.aspect_name, hit.natal_planet),
        "День требует внимательности к своему состоянию и темпу."
    )


def build_daily_message(user_tz_name: str, target_date: date | None = None) -> str:
    tz = ZoneInfo(user_tz_name)
    now_local = datetime.now(tz)
    d = target_date or now_local.date()

    natal = get_natal_chart()
    transits = get_transits_for_date(d, user_tz_name)
    hit = find_strongest_transit_for_date(d, user_tz_name)

    natal_sun_sign = zodiac_sign_name(natal["Sun"])
    natal_moon_sign = zodiac_sign_name(natal["Moon"])
    today_moon_sign = zodiac_sign_name(transits["Moon"])

    header = f"Прогноз на {d.strftime('%d.%m.%Y')}\n\n"
    intro = (
        f"☀️ База: Солнце в {natal_sun_sign}, Луна в {natal_moon_sign}.\n"
        f"🌙 Сегодня Луна в {today_moon_sign}.\n\n"
    )

    if hit is None:
        accent = "🔭 Акцент дня: мягкий фон, без сильного транзитного удара.\n\n"
    else:
        accent = (
            f"🔭 Акцент дня: {hit.transit_planet} {hit.aspect_name} "
            f"к {hit.natal_planet} (орб {hit.orb}°).\n\n"
        )

    body = (
        f"Общий смысл: {day_theme_text(hit)}\n\n"
        f"⚡ Энергия: {energy_text(hit, today_moon_sign)}\n\n"
        f"💼 Работа: {work_text(hit, today_moon_sign)}\n\n"
        f"❤️ Отношения: {relationship_text(hit, today_moon_sign)}"
    )

    return header + intro + accent + body


def day_short_summary(user_tz_name: str, target_date: date) -> str:
    transits = get_transits_for_date(target_date, user_tz_name)
    hit = find_strongest_transit_for_date(target_date, user_tz_name)
    moon_sign = zodiac_sign_name(transits["Moon"])

    if hit is None:
        return f"{target_date.strftime('%d.%m')}: спокойный день, Луна в {moon_sign}."
    return (
        f"{target_date.strftime('%d.%m')}: "
        f"{hit.transit_planet} {hit.aspect_name} к {hit.natal_planet}, "
        f"Луна в {moon_sign}."
    )


def build_week_message(user_tz_name: str) -> str:
    tz = ZoneInfo(user_tz_name)
    start_day = datetime.now(tz).date()

    lines = ["Прогноз на 7 дней\n"]
    for i in range(7):
        d = start_day + timedelta(days=i)
        hit = find_strongest_transit_for_date(d, user_tz_name)
        transits = get_transits_for_date(d, user_tz_name)
        moon_sign = zodiac_sign_name(transits["Moon"])

        if hit is None:
            theme = "спокойный ритм"
            tip = "держи фокус на одном главном деле"
        else:
            if hit.transit_planet == "Mercury":
                theme = "мысли, планы, общение"
                tip = "перепроверь детали и записывай идеи"
            elif hit.transit_planet == "Venus":
                theme = "отношения, комфорт, деньги"
                tip = "делай ставку на мягкость и вкус"
            elif hit.transit_planet == "Mars":
                theme = "действие, напор, энергия"
                tip = "лучше двигаться, чем копить напряжение"
            elif hit.transit_planet == "Jupiter":
                theme = "рост и расширение"
                tip = "смотри шире и используй возможности"
            elif hit.transit_planet == "Saturn":
                theme = "дисциплина и структура"
                tip = "не распыляйся и доводи до конца"
            elif hit.transit_planet == "Moon":
                theme = "эмоции и внутренний фон"
                tip = "не решай всё на настроении"
            else:
                theme = "внимательность к своему ритму"
                tip = "не форсируй события"

        lines.append(
            f"{d.strftime('%d.%m')} — {theme}; Луна в {moon_sign}; совет: {tip}."
        )

    return "\n".join(lines)


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔮 Прогноз на сегодня", callback_data="today")],
        [InlineKeyboardButton("🗓 Прогноз на неделю", callback_data="week")],
        [InlineKeyboardButton("🧾 Мои данные", callback_data="whoami")],
        [InlineKeyboardButton("⏰ Сменить время", callback_data="time_menu")],
    ])


def time_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("07:00", callback_data="settime_07:00"),
            InlineKeyboardButton("08:30", callback_data="settime_08:30"),
            InlineKeyboardButton("10:00", callback_data="settime_10:00"),
        ],
        [
            InlineKeyboardButton("12:00", callback_data="settime_12:00"),
            InlineKeyboardButton("18:00", callback_data="settime_18:00"),
            InlineKeyboardButton("21:00", callback_data="settime_21:00"),
        ],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_main")],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = load_state()
    state["chat_id"] = update.effective_chat.id
    save_state(state)

    await reschedule_daily_job(context.application)

    text = (
        "Привет. Я твой персональный астробот.\n\n"
        "Я могу каждый день присылать короткий прогноз и показывать обзор недели."
    )
    await update.message.reply_text(text, reply_markup=main_menu())


async def today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = load_state()
    await update.message.reply_text(
        build_daily_message(state["user_tz"]),
        reply_markup=main_menu(),
    )


async def week(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = load_state()
    await update.message.reply_text(
        build_week_message(state["user_tz"]),
        reply_markup=main_menu(),
    )


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = load_state()
    text = (
        "🧾 Текущие настройки:\n"
        f"- время отправки: {state['send_time']}\n"
        f"- часовой пояс: {state['user_tz']}\n\n"
        "🌌 Натальные данные:\n"
        "- 05.02.1992\n"
        "- 02:00\n"
        "- Тамбов"
    )
    await update.message.reply_text(text, reply_markup=main_menu())


async def settime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(
            "Используй так: /settime 08:30",
            reply_markup=main_menu(),
        )
        return

    raw = context.args[0].strip()

    try:
        hh, mm = raw.split(":")
        hh, mm = int(hh), int(mm)
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "Формат времени: /settime 08:30",
            reply_markup=main_menu(),
        )
        return

    state = load_state()
    state["send_time"] = f"{hh:02d}:{mm:02d}"
    state["chat_id"] = update.effective_chat.id
    save_state(state)

    await reschedule_daily_job(context.application)
    await update.message.reply_text(
        f"Готово. Теперь я буду писать каждый день в {state['send_time']}.",
        reply_markup=main_menu(),
    )


async def daily_push(context: ContextTypes.DEFAULT_TYPE) -> None:
    state = load_state()
    chat_id = state.get("chat_id")
    if not chat_id:
        return

    await context.bot.send_message(
        chat_id=chat_id,
        text=build_daily_message(state["user_tz"]),
        reply_markup=main_menu(),
    )


async def reschedule_daily_job(application) -> None:
    state = load_state()

    for job in application.job_queue.get_jobs_by_name("daily_horoscope"):
        job.schedule_removal()

    hh, mm = map(int, state["send_time"].split(":"))
    tz = ZoneInfo(state["user_tz"])

    application.job_queue.run_daily(
        daily_push,
        time=time(hour=hh, minute=mm, tzinfo=tz),
        name="daily_horoscope",
    )


async def post_init(application) -> None:
    state = load_state()
    if not state.get("send_time"):
        state["send_time"] = DEFAULT_SEND_TIME
        save_state(state)

    await reschedule_daily_job(application)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    state = load_state()

    if query.data == "today":
        await query.message.edit_text(
            build_daily_message(state["user_tz"]),
            reply_markup=main_menu(),
        )
        return

    if query.data == "week":
        await query.message.edit_text(
            build_week_message(state["user_tz"]),
            reply_markup=main_menu(),
        )
        return

    if query.data == "whoami":
        text = (
            "🧾 Текущие настройки:\n"
            f"- время отправки: {state['send_time']}\n"
            f"- часовой пояс: {state['user_tz']}\n\n"
            "🌌 Натальные данные:\n"
            "- 05.02.1992\n"
            "- 02:00\n"
            "- Тамбов"
        )
        await query.message.edit_text(text, reply_markup=main_menu())
        return

    if query.data == "time_menu":
        await query.message.edit_text(
            "Выбери удобное время ежедневной отправки:",
            reply_markup=time_menu(),
        )
        return

    if query.data.startswith("settime_"):
        new_time = query.data.replace("settime_", "")
        state["send_time"] = new_time
        state["chat_id"] = query.message.chat.id
        save_state(state)

        await reschedule_daily_job(context.application)

        await query.message.edit_text(
            f"⏰ Готово. Теперь я буду писать каждый день в {new_time}.",
            reply_markup=main_menu(),
        )
        return

    if query.data == "back_main":
        await query.message.edit_text(
            "Главное меню:",
            reply_markup=main_menu(),
        )
        return


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN в переменных окружения")

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("today", today))
    app.add_handler(CommandHandler("week", week))
    app.add_handler(CommandHandler("whoami", whoami))
    app.add_handler(CommandHandler("settime", settime))
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
