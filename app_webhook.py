"""
Umnico Auto Greeting Bot (Webhook version)
===========================================
Автоматически отправляет приветствие (голосовое, видео, фото или документ)
всем новым клиентам через webhooks от Umnico.

Запуск:
    pip install flask requests python-dotenv
    python app.py
"""

import logging
import os
import time
import requests
from typing import Optional
from dotenv import load_dotenv
from flask import Flask, request, jsonify

load_dotenv()

# ─────────────────────────────────────────
#  НАСТРОЙКИ
# ─────────────────────────────────────────
UMNICO_LOGIN = os.getenv("UMNICO_LOGIN", "")  # Логин от Umnico
UMNICO_PASSWORD = os.getenv("UMNICO_PASSWORD", "")  # Пароль от Umnico
GREETING_FILE = os.getenv("GREETING_FILE", "Салем_1.ogg")  # Файл для отправки
FILE_TYPE = os.getenv("FILE_TYPE", "audio")  # Тип файла: audio, video, photo, doc
LOG_FILE = os.getenv("LOG_FILE", "bot.log")  # файл для логов
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "5000"))  # Порт для webhook сервера
BASE_URL = "https://api.umnico.com/v1.3"
TARGET_SA_ID = int(os.getenv("TARGET_SA_ID", "108954"))  # ID интеграции
# ─────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

_seen_customers: set = set()
_access_token: str = ""
_refresh_token: str = ""
_token_expires: int = 0


def get_access_token() -> str:
    """
    Получает или обновляет access token через OAuth авторизацию.
    """
    global _access_token, _refresh_token, _token_expires

    current_time = int(time.time())

    # Если токен еще валиден, возвращаем его
    if _access_token and current_time < _token_expires - 60:
        return _access_token

    # Если есть refresh token, пытаемся обновить
    if _refresh_token and current_time < _token_expires:
        try:
            logger.info("🔄 Обновление access token...")
            r = requests.post(
                f"{BASE_URL}/auth/tokens",
                headers={"Authorization": _refresh_token},
                timeout=10,
            )
            if r.status_code == 200:
                data = r.json()
                _access_token = data["accessToken"]["token"]
                _token_expires = data["accessToken"]["exp"]
                if "refreshToken" in data:
                    _refresh_token = data["refreshToken"]["token"]
                logger.info("✅ Access token обновлен")
                return _access_token
        except Exception as e:
            logger.warning(f"⚠️ Не удалось обновить токен: {e}")

    # Авторизация по логину и паролю
    try:
        logger.info("🔐 Авторизация по логину и паролю...")
        r = requests.post(
            f"{BASE_URL}/auth/login",
            json={"login": UMNICO_LOGIN, "pass": UMNICO_PASSWORD},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            _access_token = data["accessToken"]["token"]
            _token_expires = data["accessToken"]["exp"]
            _refresh_token = data["refreshToken"]["token"]
            logger.info("✅ Авторизация успешна")
            return _access_token
        else:
            logger.error(f"❌ Ошибка авторизации {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.error(f"❌ Ошибка при авторизации: {e}")

    return ""


def hdrs():
    token = get_access_token()
    return {
        "Authorization": token,
        "Content-Type": "application/json",
    }


def hdrs_base():
    token = get_access_token()
    return {
        "Authorization": token,
    }


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


def send_greeting(lead: dict) -> bool:
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
            logger.info(f"✅ Приветствие отправлено → {name} (чат {lead_id})")
            return True
        logger.error(
            f"❌ Ошибка отправки {r.status_code} в чат {lead_id}: {r.text[:300]}"
        )
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Сетевая ошибка при отправке в чат {lead_id}: {e}")
    return False


@app.route("/webhook", methods=["POST"])
def webhook():
    """
    Обработчик webhook событий от Umnico
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({"status": "error", "message": "No data"}), 400

        event_type = data.get("event")

        # Обрабатываем событие "Новое обращение"
        if event_type == "new_lead":
            lead = data.get("lead", {})
            lead_id = lead.get("id")
            customer = lead.get("customer", {})
            customer_id = customer.get("id")
            sa_id = lead.get("socialAccount", {}).get("id")

            if not customer_id or not sa_id:
                logger.warning(f"⚠️ Webhook: неполные данные в событии")
                return jsonify({"status": "ok"}), 200

            # Проверяем интеграцию
            if sa_id != TARGET_SA_ID:
                logger.debug(f"⏭️ Webhook: пропускаем интеграцию {sa_id}")
                return jsonify({"status": "ok"}), 200

            # Проверяем, не обрабатывали ли мы этого клиента
            customer_id_str = str(customer_id)
            if customer_id_str in _seen_customers:
                logger.debug(f"⏭️ Webhook: клиент {customer_id} уже обработан")
                return jsonify({"status": "ok"}), 200

            # Проверяем, первое ли это обращение клиента
            if not is_first_contact_in_integration(customer_id, sa_id):
                logger.info(
                    f"⏭️ Webhook: клиент {customer.get('name', '')} (id={customer_id}) уже писал ранее"
                )
                _seen_customers.add(customer_id_str)
                return jsonify({"status": "ok"}), 200

            # Отправляем приветствие
            name = customer.get("name", "")
            logger.info(
                f"🆕 Webhook: новый клиент (первый контакт): {name} (customer_id={customer_id}, lead_id={lead_id})"
            )
            send_greeting(lead)
            _seen_customers.add(customer_id_str)

            return jsonify({"status": "ok"}), 200

        # Другие события игнорируем
        return jsonify({"status": "ok"}), 200

    except Exception as e:
        logger.error(f"❌ Ошибка обработки webhook: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    """
    Проверка здоровья сервера
    """
    return jsonify({"status": "ok", "service": "umnico-greeting-bot"}), 200


if __name__ == "__main__":
    if not UMNICO_LOGIN or not UMNICO_PASSWORD:
        logger.error("❌ Укажите UMNICO_LOGIN и UMNICO_PASSWORD в файле .env!")
        exit(1)
    if not os.path.exists(GREETING_FILE):
        logger.error(f"❌ Файл не найден: {GREETING_FILE}")
        exit(1)

    # Авторизуемся при запуске
    get_access_token()

    logger.info("🚀 Бот запущен (Webhook режим)")
    logger.info(f"📁 Файл для отправки: {GREETING_FILE} (тип: {FILE_TYPE})")
    logger.info(f"🎯 Интеграция ID: {TARGET_SA_ID}")
    logger.info(f"🌐 Webhook сервер запускается на порту {WEBHOOK_PORT}")

    # Запускаем Flask сервер
    app.run(host="0.0.0.0", port=WEBHOOK_PORT, debug=False)
