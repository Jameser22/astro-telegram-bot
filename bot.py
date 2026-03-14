import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import swisseph as swe
from geopy.geocoders import Nominatim
from openai import OpenAI
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
from timezonefinder import TimezoneFinder

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5")
DEFAULT_SEND_TIME = os.getenv("DEFAULT_SEND_TIME", "08:30")
USER_TZ = os.getenv("USER_TZ", "Europe/Moscow")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

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

ONBOARD_NAME, ONBOARD_DATE, ONBOARD_CITY, ONBOARD_TIME = range(4)

DEFAULT_PROFILE = {
    "name": "",
    "birth_date": "",
    "birth_time": "",
    "birth_city": "",
    "birth_lat": 52.723056,
    "birth_lon": 41.453889,
    "birth_tz": "Europe/Moscow",
    "is_complete": False,
}

geolocator = Nominatim(user_agent="astro_telegram_bot")
tf = TimezoneFinder()


@dataclass
class TransitHit:
    transit_planet: str
    natal_planet: str
    aspect_name: str
    orb: float


def default_state() -> dict:
    return {
        "chat_id": None,
        "send_time": DEFAULT_SEND_TIME,
        "user_tz": USER_TZ,
        "profile": DEFAULT_PROFILE.copy(),
    }


def load_state() -> dict:
    if not STATE_FILE.exists():
        return default_state()

    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to read state.json, using defaults")
        return default_state()

    merged = default_state()
    merged.update({k: v for k, v in state.items() if k != "profile"})

    profile = DEFAULT_PROFILE.copy()
    profile.update(state.get("profile", {}))
    merged["profile"] = profile
    return merged


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


def parse_birth_local_dt(profile: dict) -> datetime:
    dt_str = f"{profile['birth_date']} {profile['birth_time']}"
    naive_dt = datetime.strptime(dt_str, "%d.%m.%Y %H:%M")
    return naive_dt.replace(tzinfo=ZoneInfo(profile["birth_tz"]))


def local_birth_to_utc(profile: dict) -> datetime:
    local_dt = parse_birth_local_dt(profile)
    return local_dt.astimezone(timezone.utc)


def julday_from_utc(dt_utc: datetime) -> float:
    hour = dt_utc.hour + dt_utc.minute / 60 + dt_utc.second / 3600
    return swe.julday(dt_utc.year, dt_utc.month, dt_utc.day, hour)


def get_planet_longitude(jd_ut: float, planet_code: int) -> float:
    result, _ = swe.calc_ut(jd_ut, planet_code)
    return normalize_angle(result[0])


def get_natal_chart(profile: dict) -> dict:
    birth_utc = local_birth_to_utc(profile)
    jd_ut = julday_from_utc(birth_utc)

    natal = {}
    for name, code in PLANETS.items():
        natal[name] = get_planet_longitude(jd_ut, code)

    _, ascmc = swe.houses(jd_ut, profile["birth_lat"], profile["birth_lon"], b"P")
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


def find_strongest_transit_for_date(
    target_date: date,
    user_tz_name: str,
    profile: dict,
) -> TransitHit | None:
    natal = get_natal_chart(profile)
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
        return "Фон бодрее обычного. Хорошо расширяться, знакомиться и делать шаг вперёд."
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
        return "Сильный день для порядка, бюджета, рутины и практических задач."
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


def build_ai_name_story(name: str) -> str:
    if not client:
        return (
            f"{name} — красивое и выразительное имя. "
            "В нём слышатся мягкость, характер и внутренняя глубина. "
            "У таких имён обычно сильная личная энергия и запоминающееся звучание.\n\n"
            "Спасибо за веру в Павла Юрьевича. "
            "Теперь давай соберём твои данные рождения и перейдём к прогнозу."
        )

    prompt = f"""
Напиши красивый, тёплый, интересный текст на русском языке про имя "{name}".

Что нужно:
- краткая характеристика имени
- небольшая историческая или культурная справка
- стиль живой, уважительный, красивый
- 140-220 слов
- без списков
- в конце обязательно добавь фразу благодарности за веру в Павла Юрьевича
- не выдумывай слишком фантастические факты; если точная история имени неочевидна, пиши аккуратно и общо
"""

    try:
        response = client.responses.create(
            model=OPENAI_MODEL,
            input=prompt,
        )
        text = (response.output_text or "").strip()
        if text:
            return text
    except Exception:
        logger.exception("OpenAI name story error")

    return (
        f"{name} — красивое и выразительное имя. "
        "В нём слышатся мягкость, характер и внутренняя глубина. "
        "У таких имён обычно сильная личная энергия и запоминающееся звучание.\n\n"
        "Спасибо за веру в Павла Юрьевича. "
        "Теперь давай соберём твои данные рождения и перейдём к прогнозу."
    )


def build_ai_forecast(
    target_date: date,
    profile: dict,
    natal_sun_sign: str,
    natal_moon_sign: str,
    today_moon_sign: str,
    hit: TransitHit | None,
) -> str:
    if not client:
        return ""

    if hit is None:
        transit_text = "Сильного точного транзита сегодня нет."
    else:
        transit_text = (
            f"Главный транзит дня: {hit.transit_planet} "
            f"{hit.aspect_name} к {hit.natal_planet}, орб {hit.orb}°."
        )

    prompt = f"""
Ты астрологический помощник. Напиши короткий, тёплый, персональный прогноз на день на русском языке.

Данные пользователя:
- Имя: {profile["name"]}
- Дата рождения: {profile["birth_date"]}
- Время рождения: {profile["birth_time"]}
- Город рождения: {profile["birth_city"]}
- Натальное Солнце: {natal_sun_sign}
- Натальная Луна: {natal_moon_sign}
- Луна сегодня: {today_moon_sign}
- Дата прогноза: {target_date.strftime('%d.%m.%Y')}
- {transit_text}

Требования:
- 120–180 слов
- Без мистического пафоса и без запугивания
- Тон: спокойный, умный, поддерживающий
- Можно обращаться по имени
- Структура:
  1) общий фон дня
  2) на что обратить внимание
  3) один практический совет
- Не используй списки
- Не пиши дисклеймеров
"""

    try:
        response = client.responses.create(
            model=OPENAI_MODEL,
            input=prompt,
        )
        return (response.output_text or "").strip()
    except Exception:
        logger.exception("OpenAI forecast error")
        return ""


def build_daily_message(user_tz_name: str, profile: dict, target_date: date | None = None) -> str:
    tz = ZoneInfo(user_tz_name)
    now_local = datetime.now(tz)
    d = target_date or now_local.date()

    natal = get_natal_chart(profile)
    transits = get_transits_for_date(d, user_tz_name)
    hit = find_strongest_transit_for_date(d, user_tz_name, profile)

    natal_sun_sign = zodiac_sign_name(natal["Sun"])
    natal_moon_sign = zodiac_sign_name(natal["Moon"])
    today_moon_sign = zodiac_sign_name(transits["Moon"])

    header = f"Прогноз на {d.strftime('%d.%m.%Y')}\n\n"

    if hit is None:
        accent = "🔭 Акцент дня: мягкий фон, без сильного транзитного удара.\n"
    else:
        accent = (
            f"🔭 Акцент дня: {hit.transit_planet} {hit.aspect_name} "
            f"к {hit.natal_planet} (орб {hit.orb}°).\n"
        )

    ai_text = build_ai_forecast(
        target_date=d,
        profile=profile,
        natal_sun_sign=natal_sun_sign,
        natal_moon_sign=natal_moon_sign,
        today_moon_sign=today_moon_sign,
        hit=hit,
    )
    if ai_text:
        return header + accent + "\n" + ai_text

    intro = (
        f"☀️ База: Солнце в {natal_sun_sign}, Луна в {natal_moon_sign}.\n"
        f"🌙 Сегодня Луна в {today_moon_sign}.\n\n"
    )

    body = (
        f"Общий смысл: {day_theme_text(hit)}\n\n"
        f"⚡ Энергия: {energy_text(hit, today_moon_sign)}\n\n"
        f"💼 Работа: {work_text(hit, today_moon_sign)}\n\n"
        f"❤️ Отношения: {relationship_text(hit, today_moon_sign)}"
    )

    return header + intro + accent + "\n" + body


def build_week_message(user_tz_name: str, profile: dict) -> str:
    tz = ZoneInfo(user_tz_name)
    start_day = datetime.now(tz).date()

    lines = ["Прогноз на 7 дней\n"]
    for i in range(7):
        d = start_day + timedelta(days=i)
        hit = find_strongest_transit_for_date(d, user_tz_name, profile)
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


def geocode_city(city_query: str) -> tuple[float, float, str, str] | None:
    try:
        location = geolocator.geocode(city_query, language="ru", exactly_one=True, timeout=20)
    except Exception:
        logger.exception("Geocoding failed")
        return None

    if not location:
        return None

    lat = float(location.latitude)
    lon = float(location.longitude)

    tz_name = tf.timezone_at(lat=lat, lng=lon)
    if not tz_name:
        tz_name = USER_TZ

    city_name = location.address or city_query
    return lat, lon, tz_name, city_name


def format_profile(profile: dict) -> str:
    return (
        "🌌 Твои данные:\n"
        f"- имя: {profile['name'] or 'не указано'}\n"
        f"- дата: {profile['birth_date'] or 'не указана'}\n"
        f"- время: {profile['birth_time'] or 'не указано'}\n"
        f"- город: {profile['birth_city'] or 'не указан'}\n"
        f"- часовой пояс: {profile['birth_tz']}"
    )


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔮 Прогноз на сегодня", callback_data="today")],
        [InlineKeyboardButton("🗓 Прогноз на неделю", callback_data="week")],
        [InlineKeyboardButton("🧾 Мои данные", callback_data="whoami")],
        [InlineKeyboardButton("✏️ Изменить данные", callback_data="edit_profile")],
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


def lets_go_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Летс гоу 🤝", callback_data="lets_go")]
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    state = load_state()
    state["chat_id"] = update.effective_chat.id
    save_state(state)

    text = (
        "Привет ✨\n\n"
        "Сначала давай познакомимся.\n"
        "Напиши своё имя так, как тебе приятно, чтобы к тебе обращались.\n\n"
        "Например: Настя или Анастасия"
    )
    await update.message.reply_text(text)
    return ONBOARD_NAME


async def onboard_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    if len(name) < 2:
        await update.message.reply_text("Напиши имя чуть подробнее. Например: Настя")
        return ONBOARD_NAME

    context.user_data["name"] = name
    text = build_ai_name_story(name)

    await update.message.reply_text(text)
    await update.message.reply_text(
        "Теперь введи дату рождения в формате ДД.ММ.ГГГГ\n\nНапример: 05.02.1992"
    )
    return ONBOARD_DATE


async def onboard_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    try:
        parsed = datetime.strptime(text, "%d.%m.%Y")
        if parsed.year < 1900 or parsed > datetime.now():
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "Не получилось распознать дату.\nВведи в формате ДД.ММ.ГГГГ\nНапример: 05.02.1992"
        )
        return ONBOARD_DATE

    context.user_data["birth_date"] = text
    await update.message.reply_text(
        "Теперь введи город рождения.\n\nНапример: Тамбов, Россия"
    )
    return ONBOARD_CITY


async def onboard_city(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    city_query = update.message.text.strip()
    result = geocode_city(city_query)

    if not result:
        await update.message.reply_text(
            "Не смог найти этот город.\nПопробуй точнее, например: Тамбов, Россия"
        )
        return ONBOARD_CITY

    lat, lon, tz_name, city_name = result

    try:
        ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz_name = USER_TZ

    context.user_data["birth_city"] = city_name
    context.user_data["birth_lat"] = lat
    context.user_data["birth_lon"] = lon
    context.user_data["birth_tz"] = tz_name

    await update.message.reply_text(
        "Теперь введи время рождения в формате ЧЧ:ММ\n\nНапример: 02:00"
    )
    return ONBOARD_TIME


async def onboard_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    try:
        hh, mm = text.split(":")
        hh, mm = int(hh), int(mm)
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "Не получилось распознать время.\nВведи в формате ЧЧ:ММ\nНапример: 02:00"
        )
        return ONBOARD_TIME

    context.user_data["birth_time"] = f"{hh:02d}:{mm:02d}"

    summary = (
        "Почти всё готово ✨\n\n"
        f"Имя: {context.user_data['name']}\n"
        f"Дата рождения: {context.user_data['birth_date']}\n"
        f"Город: {context.user_data['birth_city']}\n"
        f"Время: {context.user_data['birth_time']}\n\n"
        "Если всё верно, нажимай кнопку ниже."
    )

    await update.message.reply_text(summary, reply_markup=lets_go_menu())
    return ConversationHandler.WAITING


async def lets_go_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    state = load_state()
    state["chat_id"] = query.message.chat.id
    state["profile"] = {
        "name": context.user_data.get("name", ""),
        "birth_date": context.user_data.get("birth_date", ""),
        "birth_time": context.user_data.get("birth_time", ""),
        "birth_city": context.user_data.get("birth_city", ""),
        "birth_lat": context.user_data.get("birth_lat", DEFAULT_PROFILE["birth_lat"]),
        "birth_lon": context.user_data.get("birth_lon", DEFAULT_PROFILE["birth_lon"]),
        "birth_tz": context.user_data.get("birth_tz", USER_TZ),
        "is_complete": True,
    }
    save_state(state)

    await reschedule_daily_job(context.application)

    context.user_data.clear()

    forecast = build_daily_message(state["user_tz"], state["profile"])
    await query.message.edit_text(
        "Готово 🤝 Твой профиль сохранён.\n\nСразу даю первый прогноз:\n\n" + forecast,
        reply_markup=main_menu(),
    )
    return ConversationHandler.END


async def cancel_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Ок, остановились. Чтобы начать заново, отправь /start")
    return ConversationHandler.END


async def today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = load_state()
    if not state["profile"]["is_complete"]:
        await update.message.reply_text("Сначала пройди знакомство через /start")
        return

    await update.message.reply_text(
        build_daily_message(state["user_tz"], state["profile"]),
        reply_markup=main_menu(),
    )


async def week(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = load_state()
    if not state["profile"]["is_complete"]:
        await update.message.reply_text("Сначала пройди знакомство через /start")
        return

    await update.message.reply_text(
        build_week_message(state["user_tz"], state["profile"]),
        reply_markup=main_menu(),
    )


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = load_state()
    mode = "AI-прогнозы включены" if OPENAI_API_KEY else "AI-прогнозы выключены"
    text = (
        "🧾 Текущие настройки:\n"
        f"- время отправки: {state['send_time']}\n"
        f"- часовой пояс для ежедневной отправки: {state['user_tz']}\n"
        f"- режим: {mode}\n\n"
        f"{format_profile(state['profile'])}"
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
    if not chat_id or not state["profile"]["is_complete"]:
        return

    await context.bot.send_message(
        chat_id=chat_id,
        text=build_daily_message(state["user_tz"], state["profile"]),
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


async def edit_profile_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(
            "Давай обновим данные.\n\nСначала напиши имя, как тебе приятно, чтобы к тебе обращались."
        )
    else:
        await update.message.reply_text(
            "Давай обновим данные.\n\nСначала напиши имя, как тебе приятно, чтобы к тебе обращались."
        )
    return ONBOARD_NAME


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    state = load_state()

    if query.data == "today":
        if not state["profile"]["is_complete"]:
            await query.message.edit_text("Сначала пройди знакомство через /start")
            return
        await query.message.edit_text(
            build_daily_message(state["user_tz"], state["profile"]),
            reply_markup=main_menu(),
        )
        return

    if query.data == "week":
        if not state["profile"]["is_complete"]:
            await query.message.edit_text("Сначала пройди знакомство через /start")
            return
        await query.message.edit_text(
            build_week_message(state["user_tz"], state["profile"]),
            reply_markup=main_menu(),
        )
        return

    if query.data == "whoami":
        mode = "AI-прогнозы включены" if OPENAI_API_KEY else "AI-прогнозы выключены"
        text = (
            "🧾 Текущие настройки:\n"
            f"- время отправки: {state['send_time']}\n"
            f"- часовой пояс для ежедневной отправки: {state['user_tz']}\n"
            f"- режим: {mode}\n\n"
            f"{format_profile(state['profile'])}"
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
        await query.message.edit_text("Главное меню:", reply_markup=main_menu())
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

    onboarding_conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ONBOARD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, onboard_name)],
            ONBOARD_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, onboard_date)],
            ONBOARD_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, onboard_city)],
            ONBOARD_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, onboard_time)],
            ConversationHandler.WAITING: [
                CallbackQueryHandler(lets_go_callback, pattern="^lets_go$")
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_onboarding)],
        per_message=False,
    )

    edit_profile_conv = ConversationHandler(
        entry_points=[
            CommandHandler("editprofile", edit_profile_start),
            CallbackQueryHandler(edit_profile_start, pattern="^edit_profile$"),
        ],
        states={
            ONBOARD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, onboard_name)],
            ONBOARD_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, onboard_date)],
            ONBOARD_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, onboard_city)],
            ONBOARD_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, onboard_time)],
            ConversationHandler.WAITING: [
                CallbackQueryHandler(lets_go_callback, pattern="^lets_go$")
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_onboarding)],
        per_message=False,
    )

    app.add_handler(onboarding_conv)
    app.add_handler(edit_profile_conv)
    app.add_handler(CommandHandler("today", today))
    app.add_handler(CommandHandler("week", week))
    app.add_handler(CommandHandler("whoami", whoami))
    app.add_handler(CommandHandler("settime", settime))
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()