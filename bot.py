import asyncio
import json
import logging
import os
import random
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import swisseph as swe
from geopy.geocoders import Nominatim
from openai import OpenAI
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
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
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
DEFAULT_SEND_TIME = os.getenv("DEFAULT_SEND_TIME", "08:30")
USER_TZ = os.getenv("USER_TZ", "Europe/Moscow")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

STATE_FILE = Path("state.json")
NAMES_DB_FILE = Path("names_db.json")

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

EXAMPLE_NAMES = [
    "Анна", "Мария", "София", "Анастасия", "Ева", "Полина", "Виктория",
    "Илья", "Артём", "Михаил", "Егор", "Дмитрий", "Максим", "Матвей",
    "Алиса", "Дарья", "Ксения", "Ольга", "Юлия", "Екатерина",
    "Александр", "Алексей", "Кирилл", "Роман", "Никита", "Андрей"
]

EXAMPLE_BIRTHDATES = [
    "03.01.1987", "14.01.1991", "27.01.1994",
    "05.02.1992", "18.02.1989", "24.02.1996",
    "01.03.1990", "12.03.1988", "29.03.1995",
    "04.04.1993", "16.04.1987", "30.04.1998",
    "07.05.1991", "21.05.1986", "28.05.1994",
    "02.06.1989", "11.06.1997", "26.06.1992",
    "03.07.1993", "17.07.1988", "29.07.1996",
    "05.08.1990", "13.08.1995", "22.08.1987",
    "01.09.1994", "14.09.1991", "25.09.1986",
    "06.10.1992", "18.10.1998", "30.10.1993",
    "04.11.1989", "12.11.1996", "27.11.1991",
    "03.12.1988", "15.12.1994", "28.12.1990",
    "09.01.1985", "20.01.1993", "31.01.1997",
    "02.02.1984", "11.02.1990", "26.02.1998",
    "08.03.1987", "19.03.1992", "31.03.1986",
    "06.04.1991", "22.04.1997", "29.04.1985",
    "10.05.1993", "19.05.1988", "31.05.1996",
    "04.06.1991", "15.06.1985", "30.06.1994",
    "08.07.1986", "21.07.1991", "31.07.1998",
    "09.08.1984", "17.08.1992", "28.08.1997",
    "05.09.1989", "19.09.1993", "30.09.1987",
    "08.10.1991", "22.10.1985", "31.10.1994",
    "06.11.1990", "18.11.1986", "29.11.1997",
    "07.12.1991", "19.12.1985", "31.12.1993",
    "13.01.1988", "25.01.1995", "07.02.1986",
    "15.02.1993", "28.02.1987", "10.03.1991",
    "23.03.1989", "05.04.1996", "18.04.1992",
    "27.04.1988", "09.05.1995", "24.05.1990",
    "06.06.1987", "18.06.1993", "27.06.1985",
    "09.07.1994", "24.07.1989", "31.08.1991",
    "07.09.1996", "16.09.1988", "29.10.1990",
    "11.11.1992", "24.11.1987", "09.12.1995"
]

EXAMPLE_CITIES = [
    "Москва, Россия",
    "Санкт-Петербург, Россия",
    "Новосибирск, Россия",
    "Екатеринбург, Россия",
    "Казань, Россия",
    "Нижний Новгород, Россия",
    "Самара, Россия",
    "Омск, Россия",
    "Ростов-на-Дону, Россия",
    "Уфа, Россия",
    "Красноярск, Россия",
    "Пермь, Россия",
    "Воронеж, Россия",
    "Волгоград, Россия",
    "Краснодар, Россия",
    "Саратов, Россия",
    "Тюмень, Россия",
    "Тольятти, Россия",
    "Ижевск, Россия",
    "Барнаул, Россия",
    "Ульяновск, Россия",
    "Иркутск, Россия",
    "Хабаровск, Россия",
    "Ярославль, Россия",
    "Владивосток, Россия",
    "Тамбов, Россия",
    "Белгород, Россия",
    "Калининград, Россия",
    "Ставрополь, Россия",
    "Челябинск, Россия",
    "Оренбург, Россия",
    "Курск, Россия",
    "Тула, Россия",
    "Липецк, Россия",
    "Рязань, Россия",
    "Тверь, Россия",
    "Пенза, Россия",
    "Киров, Россия",
    "Астрахань, Россия",
    "Сочи, Россия",
    "Архангельск, Россия",
    "Мурманск, Россия",
    "Вологда, Россия",
    "Смоленск, Россия",
    "Брянск, Россия",
    "Иваново, Россия",
    "Кострома, Россия",
    "Калуга, Россия",
    "Владимир, Россия",
    "Севастополь, Россия",
    "Минск, Беларусь",
    "Гродно, Беларусь",
    "Гомель, Беларусь",
    "Витебск, Беларусь",
    "Брест, Беларусь",
    "Алматы, Казахстан",
    "Астана, Казахстан",
    "Шымкент, Казахстан",
    "Караганда, Казахстан",
    "Павлодар, Казахстан",
    "Бишкек, Кыргызстан",
    "Ош, Кыргызстан",
    "Ташкент, Узбекистан",
    "Самарканд, Узбекистан",
    "Душанбе, Таджикистан",
    "Баку, Азербайджан",
    "Гянджа, Азербайджан",
    "Тбилиси, Грузия",
    "Батуми, Грузия",
    "Кутаиси, Грузия",
    "Ереван, Армения",
    "Гюмри, Армения",
    "Рига, Латвия",
    "Даугавпилс, Латвия",
    "Вильнюс, Литва",
    "Каунас, Литва",
    "Таллин, Эстония",
    "Тарту, Эстония",
    "Киев, Украина",
    "Одесса, Украина",
    "Львов, Украина",
    "Днепр, Украина",
    "Харьков, Украина",
    "Кишинёв, Молдова",
    "Бельцы, Молдова"
]

TODAY_HISTORY_FACTS = [
    "📜 Исторический факт: одна и та же дата часто хранит память сразу о нескольких событиях — от открытий и премьер до важных решений.",
    "📜 Исторический факт: многие большие перемены в истории начинались в самый обычный день, который поначалу ничем не выделялся.",
    "📜 Исторический факт: почти у каждой даты есть свой характер — где-то это день открытий, где-то день неожиданных поворотов.",
    "📜 Исторический факт: в мировой истории одна дата может объединять и рождение выдающегося человека, и событие, повлиявшее на эпоху.",
    "📜 Исторический факт: культурная память дат складывается из встреч, писем, премьер, открытий и решений, которые позже стали важными."
]

NAME_ALIASES = {
    "настя": "анастасия",
    "аня": "анна",
    "маша": "мария",
    "соня": "софия",
    "даша": "дарья",
    "катя": "екатерина",
    "катерина": "екатерина",
    "вика": "виктория",
    "поля": "полина",
    "ксюша": "ксения",
    "лена": "елена",
    "ира": "ирина",
    "наташа": "наталья",
    "таня": "татьяна",
    "света": "светлана",
    "надя": "надежда",
    "люба": "любовь",
    "люд": "людмила",
    "люда": "людмила",
    "юля": "юлия",
    "алена": "алёна",
    "алёна": "алёна",
    "леша": "алексей",
    "алёша": "алексей",
    "дима": "дмитрий",
    "митя": "дмитрий",
    "илюша": "илья",
    "артем": "артём",
    "тёма": "артём",
    "тема": "артём",
    "миша": "михаил",
    "макс": "максим",
    "тима": "тимофей",
    "матвейка": "матвей",
    "андрюша": "андрей",
    "сережа": "сергей",
    "серёжа": "сергей",
    "вова": "владимир",
    "паша": "павел",
    "женя": "евгений",
    "рома": "роман",
    "кирил": "кирилл",
    "ден": "денис",
    "антончик": "антон",
    "игорек": "игорь",
    "игорёк": "игорь",
    "юра": "юрий",
    "степа": "степан",
    "стёпа": "степан",
    "влад": "владислав",
    "жора": "георгий",
    "гоша": "георгий",
    "костя": "константин"
}

CITY_ALIASES = {
    "мск": "Москва, Россия",
    "москва": "Москва, Россия",
    "спб": "Санкт-Петербург, Россия",
    "питер": "Санкт-Петербург, Россия",
    "санкт петербург": "Санкт-Петербург, Россия",
    "санкт-петербург": "Санкт-Петербург, Россия",
    "екб": "Екатеринбург, Россия",
    "нск": "Новосибирск, Россия",
    "нижний": "Нижний Новгород, Россия",
    "ростов": "Ростов-на-Дону, Россия",
    "тамбов": "Тамбов, Россия",
    "минск": "Минск, Беларусь",
    "астана": "Астана, Казахстан",
    "алматы": "Алматы, Казахстан",
    "тбилиси": "Тбилиси, Грузия",
    "ереван": "Ереван, Армения",
    "рига": "Рига, Латвия",
    "вильнюс": "Вильнюс, Литва",
    "таллин": "Таллин, Эстония",
    "киев": "Киев, Украина",
    "одесса": "Одесса, Украина",
}

COUNTRY_HINTS = [
    "Россия",
    "Беларусь",
    "Казахстан",
    "Украина",
    "Грузия",
    "Армения",
    "Азербайджан",
    "Латвия",
    "Литва",
    "Эстония",
    "Кыргызстан",
    "Узбекистан",
    "Таджикистан",
    "Молдова",
]

geolocator = Nominatim(user_agent="astro_telegram_bot")
tf = TimezoneFinder()


@dataclass
class TransitHit:
    transit_planet: str
    natal_planet: str
    aspect_name: str
    orb: float


def load_names_db() -> dict:
    if not NAMES_DB_FILE.exists():
        logger.warning("names_db.json not found, local name database disabled")
        return {}
    try:
        return json.loads(NAMES_DB_FILE.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to read names_db.json")
        return {}


NAMES_DB = load_names_db()


async def safe_answer_callback(query) -> None:
    try:
        await query.answer()
    except Exception:
        logger.exception("Callback query answer failed")


def normalize_name(raw_name: str) -> str:
    cleaned = raw_name.strip().lower().replace("ё", "е")
    cleaned = " ".join(cleaned.split())
    return cleaned.split()[0] if cleaned else ""


def canonical_name(raw_name: str) -> str:
    key = normalize_name(raw_name)
    if key in NAMES_DB:
        return key
    return NAME_ALIASES.get(key, key)


def build_local_name_story(name: str) -> str | None:
    canonical = canonical_name(name)
    info = NAMES_DB.get(canonical)
    if not info:
        return None

    display_name = name.strip()
    return (
        f"{display_name} — {info['gender']} имя. {info['origin']} "
        f"{info['character']} {info['archetype']} {info['ending']}"
    )


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


def get_today_history_fact() -> str:
    return random.choice(TODAY_HISTORY_FACTS)


def build_history_today() -> str:
    today = date.today()
    mm = today.strftime("%m")
    dd = today.strftime("%d")
    url = f"https://api.wikimedia.org/feed/v1/wikipedia/ru/onthisday/events/{mm}/{dd}"

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "astro-telegram-bot/1.0"}
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))

        events = data.get("events", [])
        if not events:
            return (
                f"🤔 Что было в этот день в истории\n\n"
                f"Дата: {today.strftime('%d.%m')}\n\n"
                "Сегодня не удалось найти исторические события для этой даты."
            )

        picked = random.sample(events, k=min(3, len(events)))

        lines = [
            "🤔 Что было в этот день в истории",
            "",
            f"Дата: {today.strftime('%d.%m')}",
            ""
        ]

        for item in picked:
            year = item.get("year", "—")
            text = item.get("text", "").strip()
            if text:
                lines.append(f"• {year}: {text}")

        return "\n".join(lines)

    except Exception:
        logger.exception("Failed to load history events")
        return (
            f"🤔 Что было в этот день в истории\n\n"
            f"Дата: {today.strftime('%d.%m')}\n\n"
            "Сейчас не удалось загрузить реальные исторические события. Попробуй чуть позже."
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


def explain_aspect(hit: TransitHit | None) -> str:
    if hit is None:
        return "Это значит, что день идёт довольно ровно, без сильного напряжения и без резкого импульса."

    aspect_explanations = {
        "соединение": "Соединение означает, что энергия дня ощущается особенно сильно и прямо.",
        "секстиль": "Секстиль — это мягкая благоприятная возможность: день помогает, если самому включиться в процесс.",
        "тригон": "Тригон — это естественная поддержка: многое может идти легче и гармоничнее обычного.",
        "квадрат": "Квадрат даёт напряжение и внутренний вызов: день может подталкивать к росту через дискомфорт.",
        "оппозиция": "Оппозиция показывает противоречие или необходимость искать баланс между двумя полюсами."
    }

    planet_meanings = {
        "Sun": "Солнце связано с самочувствием, уверенностью и ощущением себя.",
        "Moon": "Луна связана с эмоциями, настроением и внутренним фоном.",
        "Mercury": "Меркурий отвечает за мысли, разговоры, документы и решения.",
        "Venus": "Венера связана с отношениями, симпатией, комфортом и деньгами.",
        "Mars": "Марс отвечает за действие, энергию, напор и конфликтность.",
        "Jupiter": "Юпитер усиливает рост, возможности, обучение и расширение.",
        "Saturn": "Сатурн связан с дисциплиной, ограничениями, ответственностью и структурой.",
        "Asc": "Асцендент связан с тем, как ты проявляешься вовне и как начинаешь действия."
    }

    aspect_text = aspect_explanations.get(hit.aspect_name, "Это важный аспект дня.")
    transit_text = planet_meanings.get(hit.transit_planet, "")
    natal_text = planet_meanings.get(hit.natal_planet, "")

    return f"{aspect_text} {transit_text} {natal_text}".strip()


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
        ("Saturn", "квадрат", "Sun"): "День дисциплины: меньше обещаний, больше конкретики."
    }
    return templates.get(
        (hit.transit_planet, hit.aspect_name, hit.natal_planet),
        "День требует внимательности к своему состоянию и темпу."
    )


def build_name_story(name: str) -> str:
    local_story = build_local_name_story(name)
    if local_story:
        return local_story

    clean_name = name.strip()
    if not clean_name:
        return "Имя не распознано. Давай попробуем ещё раз."

    if not client:
        return (
            f"{clean_name} — имя с индивидуальным характером и своим звучанием. "
            f"Оно оставляет личное впечатление и ощущается как имя со своей историей. "
            f"Теперь давай соберём твои данные рождения."
        )

    prompt = f"""
Ты пишешь красивый и интересный текст про имя человека.

Имя: {clean_name}

Задача:
- написать именно про это имя
- определить, мужское это имя или женское
- кратко и аккуратно объяснить происхождение имени
- описать характер и настроение имени
- добавить психологический архетип имени
- обращаться по имени естественно
- стиль: тёплый, живой, красивый, уважительный
- объём: 80–120 слов
- без списков
- не использовать универсальный шаблон
- не подменять имя другим
- если происхождение имени неочевидно, писать аккуратно и без выдумки
"""

    try:
        response = client.responses.create(
            model=OPENAI_MODEL,
            input=prompt
        )
        text = (response.output_text or "").strip()
        if not text:
            raise ValueError("Empty OpenAI response")
        if clean_name.lower() not in text.lower():
            raise ValueError("Name missing in response")
        return text
    except Exception:
        logger.exception("OpenAI name story error")
        return (
            f"{clean_name} — имя с заметным внутренним ритмом и своим особым настроением. "
            f"Оно может звучать мягко или ярко, но почти всегда оставляет личное впечатление. "
            f"Теперь давай соберём твои данные рождения."
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
- Не используй списки
"""

    try:
        response = client.responses.create(
            model=OPENAI_MODEL,
            input=prompt
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
        accent = (
            "🔭 Акцент дня: мягкий фон, без сильного транзитного удара.\n"
            f"Пояснение: {explain_aspect(hit)}\n"
        )
    else:
        accent = (
            f"🔭 Акцент дня: {hit.transit_planet} {hit.aspect_name} "
            f"к {hit.natal_planet} (орб {hit.orb}°).\n"
            f"Пояснение: {explain_aspect(hit)}\n"
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


def normalize_city_text(text: str) -> str:
    cleaned = text.strip().lower().replace("ё", "е")
    cleaned = " ".join(cleaned.split())
    return cleaned


def build_city_queries(city_query: str) -> list[str]:
    raw = city_query.strip()
    norm = normalize_city_text(raw)

    queries = []
    seen = set()

    def add(q: str) -> None:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            queries.append(q)

    add(raw)
    add(raw.replace(",", " "))
    add(raw.replace(",", ""))
    first_part = raw.split(",")[0].strip()
    add(first_part)

    if norm in CITY_ALIASES:
        add(CITY_ALIASES[norm])

    if "," not in raw and len(first_part.split()) <= 3:
        for country in COUNTRY_HINTS:
            add(f"{first_part}, {country}")

    if first_part and first_part != raw:
        for country in COUNTRY_HINTS:
            add(f"{first_part}, {country}")

    return queries


def geocode_city(city_query: str) -> tuple[float, float, str, str] | None:
    queries = build_city_queries(city_query)

    for q in queries:
        try:
            location = geolocator.geocode(
                q,
                language="ru",
                exactly_one=True,
                timeout=20,
                addressdetails=True,
            )
        except Exception:
            logger.exception("Geocoding failed for query: %s", q)
            continue

        if not location:
            continue

        lat = float(location.latitude)
        lon = float(location.longitude)

        tz_name = tf.timezone_at(lat=lat, lng=lon)
        if not tz_name:
            tz_name = USER_TZ

        city_name = location.address or q
        return lat, lon, tz_name, city_name

    return None


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
        [InlineKeyboardButton("🤔 Что было в этот день в истории", callback_data="history")],
        [InlineKeyboardButton("🧾 Мои данные", callback_data="whoami")],
        [InlineKeyboardButton("✏️ Изменить данные", callback_data="edit_profile")],
        [InlineKeyboardButton("⏰ Сменить время", callback_data="time_menu")]
    ])


def time_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("07:00", callback_data="settime_07:00"),
            InlineKeyboardButton("08:30", callback_data="settime_08:30"),
            InlineKeyboardButton("10:00", callback_data="settime_10:00")
        ],
        [
            InlineKeyboardButton("12:00", callback_data="settime_12:00"),
            InlineKeyboardButton("18:00", callback_data="settime_18:00"),
            InlineKeyboardButton("21:00", callback_data="settime_21:00")
        ],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_main")]
    ])


def review_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🙈 Кое-что исправить", callback_data="fix_profile")],
        [InlineKeyboardButton("Летс гоу 🤝", callback_data="lets_go")]
    ])


async def animate_waiting_message(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message_id: int,
    stop_event: asyncio.Event
) -> None:
    frames = ["⏳", "⌛", "⏳⌛", "⌛⏳"]
    idx = 0

    while not stop_event.is_set():
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=(
                    "🌌 Ууу, сейчас небо закрыто шелухой...\n"
                    "Мне нужно немного подумать над ответом\n\n"
                    f"{frames[idx % len(frames)]}"
                ),
            )
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception:
            logger.exception("Failed to update waiting animation")
        idx += 1
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=1.2)
        except asyncio.TimeoutError:
            pass


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    state = load_state()
    state["chat_id"] = update.effective_chat.id
    save_state(state)

    example_name = random.choice(EXAMPLE_NAMES)
    example_birthdate = random.choice(EXAMPLE_BIRTHDATES)
    example_city = random.choice(EXAMPLE_CITIES)

    context.user_data["example_birthdate"] = example_birthdate
    context.user_data["example_city"] = example_city

    text = (
        "Привет ✨\n\n"
        "Сначала давай познакомимся.\n"
        "Напиши своё имя так, как тебе приятно, чтобы к тебе обращались.\n\n"
        f"Например: {example_name}"
    )
    await update.message.reply_text(text)
    return ONBOARD_NAME


async def onboard_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    if len(name) < 2:
        await update.message.reply_text("Напиши имя чуть подробнее. Например: Анна")
        return ONBOARD_NAME

    context.user_data["name"] = name

    story_task = asyncio.create_task(asyncio.to_thread(build_name_story, name))

    waiting_message = None
    stop_event = asyncio.Event()
    animation_task = None

    try:
        done, _ = await asyncio.wait({story_task}, timeout=1.0)

        if story_task in done:
            story = story_task.result()
        else:
            waiting_message = await update.message.reply_text(
                "🌌 Ууу, сейчас небо закрыто шелухой...\n"
                "Мне нужно немного подумать над ответом\n\n"
                "⏳"
            )

            animation_task = asyncio.create_task(
                animate_waiting_message(
                    context=context,
                    chat_id=update.effective_chat.id,
                    message_id=waiting_message.message_id,
                    stop_event=stop_event,
                )
            )

            story = await story_task

    finally:
        stop_event.set()
        if animation_task:
            try:
                await animation_task
            except Exception:
                logger.exception("Animation task ended with error")

    if waiting_message:
        await waiting_message.edit_text(story)
    else:
        await update.message.reply_text(story)

    example_birthdate = context.user_data.get("example_birthdate", "05.02.1992")
    await update.message.reply_text(
        f"Теперь введи дату рождения в формате ДД.ММ.ГГГГ\n\nНапример: {example_birthdate}"
    )
    return ONBOARD_DATE


async def onboard_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    try:
        parsed = datetime.strptime(text, "%d.%m.%Y")
        if parsed.year < 1900 or parsed > datetime.now():
            raise ValueError
    except ValueError:
        example_birthdate = context.user_data.get("example_birthdate", "05.02.1992")
        await update.message.reply_text(
            f"Не получилось распознать дату.\n"
            f"Введи в формате ДД.ММ.ГГГГ\n"
            f"Например: {example_birthdate}"
        )
        return ONBOARD_DATE

    context.user_data["birth_date"] = text

    example_city = context.user_data.get("example_city", "Тамбов, Россия")
    await update.message.reply_text(
        f"Теперь введи город рождения.\n\nНапример: {example_city}"
    )
    return ONBOARD_CITY


async def onboard_city(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    city_query = update.message.text.strip()
    result = geocode_city(city_query)

    if not result:
        await update.message.reply_text(
            "Не смог найти этот город.\n"
            "Попробуй в одном из вариантов:\n"
            "• Тамбов\n"
            "• Тамбов, Россия\n"
            "• Москва\n"
            "• Алматы, Казахстан"
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
        "Если нужно, можешь поправить данные перед запуском."
    )

    await update.message.reply_text(summary, reply_markup=review_menu())
    return ConversationHandler.WAITING


async def lets_go_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await safe_answer_callback(query)

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
        "is_complete": True
    }
    save_state(state)

    await reschedule_daily_job(context.application)
    context.user_data.clear()

    forecast = build_daily_message(state["user_tz"], state["profile"])
    try:
        await query.message.edit_text(
            "Готово 🤝 Твой профиль сохранён.\n\nСразу даю первый прогноз:\n\n" + forecast,
            reply_markup=main_menu()
        )
    except Exception:
        logger.exception("Failed to edit lets_go message")
    return ConversationHandler.END


async def fix_profile_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await safe_answer_callback(query)
    try:
        await query.message.edit_text(
            "Окей, давай поправим данные.\n\nНапиши имя ещё раз так, как тебе приятно, чтобы к тебе обращались."
        )
    except Exception:
        logger.exception("Failed to edit fix_profile message")
    return ONBOARD_NAME


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
        reply_markup=main_menu()
    )


async def week(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = load_state()
    if not state["profile"]["is_complete"]:
        await update.message.reply_text("Сначала пройди знакомство через /start")
        return

    await update.message.reply_text(
        build_week_message(state["user_tz"], state["profile"]),
        reply_markup=main_menu()
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
            reply_markup=main_menu()
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
            reply_markup=main_menu()
        )
        return

    state = load_state()
    state["send_time"] = f"{hh:02d}:{mm:02d}"
    state["chat_id"] = update.effective_chat.id
    save_state(state)

    await reschedule_daily_job(context.application)
    await update.message.reply_text(
        f"Готово. Теперь я буду писать каждый день в {state['send_time']}.",
        reply_markup=main_menu()
    )


async def daily_push(context: ContextTypes.DEFAULT_TYPE) -> None:
    state = load_state()
    chat_id = state.get("chat_id")
    if not chat_id or not state["profile"]["is_complete"]:
        return

    await context.bot.send_message(
        chat_id=chat_id,
        text=build_daily_message(state["user_tz"], state["profile"]),
        reply_markup=main_menu()
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
        name="daily_horoscope"
    )


async def post_init(application) -> None:
    state = load_state()
    if not state.get("send_time"):
        state["send_time"] = DEFAULT_SEND_TIME
        save_state(state)

    await reschedule_daily_job(application)


async def edit_profile_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.callback_query:
        await safe_answer_callback(update.callback_query)
        try:
            await update.callback_query.message.edit_text(
                "Давай обновим данные.\n\nСначала напиши имя, как тебе приятно, чтобы к тебе обращались."
            )
        except Exception:
            logger.exception("Failed to edit edit_profile_start message")
    else:
        await update.message.reply_text(
            "Давай обновим данные.\n\nСначала напиши имя, как тебе приятно, чтобы к тебе обращались."
        )
    return ONBOARD_NAME


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await safe_answer_callback(query)

    state = load_state()

    if query.data == "today":
        if not state["profile"]["is_complete"]:
            try:
                await query.message.edit_text("Сначала пройди знакомство через /start")
            except Exception:
                logger.exception("Failed to edit today message")
            return
        try:
            await query.message.edit_text(
                build_daily_message(state["user_tz"], state["profile"]),
                reply_markup=main_menu()
            )
        except Exception:
            logger.exception("Failed to edit today result")
        return

    if query.data == "week":
        if not state["profile"]["is_complete"]:
            try:
                await query.message.edit_text("Сначала пройди знакомство через /start")
            except Exception:
                logger.exception("Failed to edit week message")
            return
        try:
            await query.message.edit_text(
                build_week_message(state["user_tz"], state["profile"]),
                reply_markup=main_menu()
            )
        except Exception:
            logger.exception("Failed to edit week result")
        return

    if query.data == "history":
        try:
            await query.message.edit_text(
                build_history_today(),
                reply_markup=main_menu()
            )
        except Exception:
            logger.exception("Failed to edit history result")
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
        try:
            await query.message.edit_text(text, reply_markup=main_menu())
        except Exception:
            logger.exception("Failed to edit whoami result")
        return

    if query.data == "time_menu":
        try:
            await query.message.edit_text(
                "Выбери удобное время ежедневной отправки:",
                reply_markup=time_menu()
            )
        except Exception:
            logger.exception("Failed to edit time_menu")
        return

    if query.data.startswith("settime_"):
        new_time = query.data.replace("settime_", "")
        state["send_time"] = new_time
        state["chat_id"] = query.message.chat.id
        save_state(state)

        await reschedule_daily_job(context.application)

        try:
            await query.message.edit_text(
                f"⏰ Готово. Теперь я буду писать каждый день в {new_time}.",
                reply_markup=main_menu()
            )
        except Exception:
            logger.exception("Failed to edit settime result")
        return

    if query.data == "back_main":
        try:
            await query.message.edit_text("Главное меню:", reply_markup=main_menu())
        except Exception:
            logger.exception("Failed to edit back_main")
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
                CallbackQueryHandler(lets_go_callback, pattern="^lets_go$"),
                CallbackQueryHandler(fix_profile_callback, pattern="^fix_profile$")
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel_onboarding)],
        per_message=False
    )

    edit_profile_conv = ConversationHandler(
        entry_points=[
            CommandHandler("editprofile", edit_profile_start),
            CallbackQueryHandler(edit_profile_start, pattern="^edit_profile$")
        ],
        states={
            ONBOARD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, onboard_name)],
            ONBOARD_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, onboard_date)],
            ONBOARD_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, onboard_city)],
            ONBOARD_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, onboard_time)],
            ConversationHandler.WAITING: [
                CallbackQueryHandler(lets_go_callback, pattern="^lets_go$"),
                CallbackQueryHandler(fix_profile_callback, pattern="^fix_profile$")
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel_onboarding)],
        per_message=False
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