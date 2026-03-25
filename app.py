"""
Umnico Auto Greeting Bot
=========================
Автоматически отправляет приветствие (голосовое, видео, фото или документ)
всем новым клиентам в указанной интеграции со статусом "Первичный".

Запуск:
    pip install requests python-dotenv
    python app.py
"""

import logging
import os
import time
import requests
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────
#  НАСТРОЙКИ
# ─────────────────────────────────────────
UMNICO_TOKEN = os.getenv(
    "UMNICO_TOKEN", "ВАШ_API_ТОКЕН"
)  # Umnico → Настройки → API Public
GREETING_FILE = os.getenv("GREETING_FILE", "Салем_1.ogg")  # Файл для отправки
FILE_TYPE = os.getenv("FILE_TYPE", "audio")  # Тип файла: audio, video, photo, doc
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "10"))  # секунд между проверками
LOG_FILE = os.getenv("LOG_FILE", "bot.log")  # файл для логов
BASE_URL = "https://api.umnico.com/v1.3"
TARGET_SA_ID = int(os.getenv("TARGET_SA_ID", "108954"))  # ID интеграции
TARGET_STATUS_ID = int(os.getenv("TARGET_STATUS_ID", "958299"))  # ID статуса
# ─────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

_seen_customers: set = set()
_initialized = False


def hdrs():
    return {
        "Authorization": f"bearer {UMNICO_TOKEN}",
        "Content-Type": "application/json",
    }


def hdrs_base():
    return {
        "Authorization": f"bearer {UMNICO_TOKEN}",
    }


def get_active_leads() -> list:
    try:
        r = requests.get(f"{BASE_URL}/leads/active", headers=hdrs(), timeout=10)
        if r.status_code == 200:
            data = r.json()
            return data if isinstance(data, list) else (data.get("data") or [])
        logger.error(f"❌ Ошибка получения лидов {r.status_code}: {r.text[:200]}")
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Сетевая ошибка при получении лидов: {e}")
    return []


def get_source_real_id(lead_id: int) -> Optional[str]:
    try:
        r = requests.get(
            f"{BASE_URL}/messaging/{lead_id}/sources", headers=hdrs(), timeout=10
        )
        if r.status_code == 200:
            sources = r.json()
            if sources:
                return str(sources[0].get("realId") or sources[0].get("id", ""))
        logger.warning(
            f"⚠️ Не удалось получить source для лида {lead_id}: {r.status_code}"
        )
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Сетевая ошибка при получении source для лида {lead_id}: {e}")
    return None


def upload_file(source_real_id: str) -> Optional[dict]:
    try:
        with open(GREETING_FILE, "rb") as f:
            r = requests.post(
                f"{BASE_URL}/messaging/upload",
                headers=hdrs_base(),
                data={"source": source_real_id},
                files={
                    "media": (os.path.basename(GREETING_FILE), f, f"{FILE_TYPE}/ogg")
                },
                timeout=30,
            )
        if r.status_code == 200:
            return r.json()
        logger.error(f"❌ Ошибка загрузки файла {r.status_code}: {r.text[:300]}")
    except FileNotFoundError:
        logger.error(f"❌ Файл не найден: {GREETING_FILE}")
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Сетевая ошибка при загрузке файла: {e}")
    return None


def is_first_contact_in_integration(customer_id: int, sa_id: int) -> bool:
    """
    Проверяет, является ли это первое обращение клиента в данной интеграции.
    Возвращает True, если у клиента только одно обращение в этой интеграции.
    """
    try:
        # Получаем все обращения клиента
        r = requests.get(
            f"{BASE_URL}/leads/all",
            headers=hdrs(),
            params={"customer": customer_id, "sa": sa_id, "limit": 200},
            timeout=10,
        )
        if r.status_code == 200:
            leads = r.json()
            # Если у клиента только одно обращение в этой интеграции - это первый контакт
            lead_count = len(leads) if isinstance(leads, list) else 0
            logger.debug(
                f"Клиент {customer_id} имеет {lead_count} обращений в интеграции {sa_id}"
            )
            return lead_count == 1
        logger.warning(
            f"⚠️ Не удалось получить обращения клиента {customer_id}: {r.status_code}"
        )
    except requests.exceptions.RequestException as e:
        logger.error(
            f"❌ Сетевая ошибка при проверке истории клиента {customer_id}: {e}"
        )
    return False


def send_voice(lead: dict) -> bool:
    lead_id = lead["id"]
    user_id = lead["userId"]

    source_real_id = get_source_real_id(lead_id)
    if not source_real_id:
        return False

    attachment = upload_file(source_real_id)
    if not attachment:
        return False

    payload = {
        "message": {"text": "", "attachment": attachment},
        "source": source_real_id,
        "userId": user_id,
    }
    try:
        r = requests.post(
            f"{BASE_URL}/messaging/{lead_id}/send",
            headers=hdrs(),
            json=payload,
            timeout=15,
        )
        if r.status_code in (200, 201):
            name = lead.get("customer", {}).get("name", "")
            logger.info(f"✅ Голосовое отправлено → {name} (чат {lead_id})")
            return True
        logger.error(
            f"❌ Ошибка отправки {r.status_code} в чат {lead_id}: {r.text[:300]}"
        )
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Сетевая ошибка при отправке в чат {lead_id}: {e}")
    return False


def polling_loop():
    global _initialized, _seen_customers
    logger.info(f"🔄 Polling запущен (каждые {POLL_INTERVAL} сек)")

    try:
        while True:
            leads = get_active_leads()

            if not _initialized:
                _seen_customers = {
                    str(l.get("customer", {}).get("id"))
                    for l in leads
                    if l.get("customer", {}).get("id")
                }
                _initialized = True
                logger.info(
                    f"📋 Существующих клиентов: {len(_seen_customers)} — пропускаем"
                )
            else:
                for lead in leads:
                    lead_id = str(lead["id"])
                    customer = lead.get("customer", {})
                    customer_id = str(customer.get("id", ""))

                    if not customer_id:
                        logger.warning(
                            f"⚠️ Обращение {lead_id} без customer.id — пропускаем"
                        )
                        continue

                    if customer_id in _seen_customers:
                        continue

                    sa_id = (lead.get("socialAccount") or {}).get("id")
                    status_id = lead.get("statusId")

                    # Только новые клиенты из нужной интеграции с нужным статусом
                    if (
                        not sa_id
                        or sa_id != TARGET_SA_ID
                        or status_id != TARGET_STATUS_ID
                    ):
                        _seen_customers.add(customer_id)
                        continue

                    # ВАЖНО: Проверяем, является ли это первое обращение клиента в интеграции
                    if not is_first_contact_in_integration(int(customer_id), sa_id):
                        logger.info(
                            f"⏭️ Клиент {customer.get('name', '')} (id={customer_id}) уже писал ранее в интеграции {sa_id} — пропускаем"
                        )
                        _seen_customers.add(customer_id)
                        continue

                    # ВАЖНО: Проверяем, является ли это первое обращение клиента в интеграции
                    if not is_first_contact_in_integration(
                        int(customer_id), int(sa_id)
                    ):
                        logger.info(
                            f"⏭️ Клиент {customer.get('name', '')} (id={customer_id}) уже писал ранее в интеграции {sa_id} — пропускаем"
                        )
                        _seen_customers.add(customer_id)
                        continue

                    name = customer.get("name", "")
                    logger.info(
                        f"🆕 Новый клиент (первый контакт): {name} (customer_id={customer_id}, lead_id={lead_id})"
                    )
                    send_voice(lead)
                    _seen_customers.add(customer_id)

            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        logger.info("\n⏹️ Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
        raise


if __name__ == "__main__":
    if "ВАШ_API_ТОКЕН" in UMNICO_TOKEN:
        logger.error("❌ Укажите UMNICO_TOKEN в файле .env!")
        exit(1)
    if not os.path.exists(GREETING_FILE):
        logger.error(f"❌ Файл не найден: {GREETING_FILE}")
        exit(1)
    logger.info("🚀 Бот запущен")
    logger.info(f"📁 Файл для отправки: {GREETING_FILE} (тип: {FILE_TYPE})")
    logger.info(f"🎯 Интеграция ID: {TARGET_SA_ID}, Статус ID: {TARGET_STATUS_ID}")
    polling_loop()
