"""
Umnico Auto Greeting Bot (Webhook version)
===========================================
Автоматически отправляет голосовое приветствие новым клиентам
через webhooks от Umnico (событие lead.created).

Запуск:
    pip install flask requests python-dotenv
    python app_webhook.py
"""

import json
import logging
import os
import time
from typing import Optional

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request

load_dotenv()

# ─────────────────────────────────────────
#  НАСТРОЙКИ
# ─────────────────────────────────────────
UMNICO_LOGIN = os.getenv("UMNICO_LOGIN", "")  # Логин от Umnico
UMNICO_PASSWORD = os.getenv("UMNICO_PASSWORD", "")  # Пароль от Umnico
LOG_FILE = os.getenv("LOG_FILE", "bot.log")  # Файл для логов
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "5000"))
BASE_URL = "https://api.umnico.com/v1.3"


def load_integrations() -> dict:
    """
    Загружает конфигурацию интеграций из .env.
    Формат: SA_ID1:файл1.ogg,SA_ID2:файл2.ogg
    """
    integrations_str = os.getenv("INTEGRATIONS", "")
    integrations = {}
    for pair in integrations_str.split(","):
        pair = pair.strip()
        if ":" in pair:
            integration_id, filename = pair.split(":", 1)
            integrations[int(integration_id.strip())] = filename.strip()
    return integrations


INTEGRATIONS = load_integrations()
# ─────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

_seen_customers: set = set()
_access_token: str = ""
_refresh_token: str = ""
_token_expires: int = 0


def get_access_token() -> str:
    """Получает или обновляет access token через OAuth авторизацию."""
    global _access_token, _refresh_token, _token_expires

    current_time = int(time.time())

    # Токен ещё валиден
    if _access_token and current_time < _token_expires - 60:
        return _access_token

    # Обновление через refresh token
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
                logger.info("✅ Access token обновлён")
                return _access_token
        except Exception as e:
            logger.warning(f"⚠️ Не удалось обновить токен: {e}")

    # Авторизация по логину и паролю
    try:
        logger.info("🔐 Авторизация...")
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


def hdrs() -> dict:
    return {"Authorization": get_access_token(), "Content-Type": "application/json"}


def hdrs_base() -> dict:
    return {"Authorization": get_access_token()}


def get_source_real_id(lead_id: int) -> Optional[str]:
    """Получает realId источника для данного лида."""
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


def upload_file(source_real_id: str, greeting_file: str) -> Optional[dict]:
    """Загружает аудиофайл в Umnico и возвращает объект вложения."""
    try:
        with open(greeting_file, "rb") as f:
            r = requests.post(
                f"{BASE_URL}/messaging/upload",
                headers=hdrs_base(),
                data={"source": source_real_id},
                files={"media": (os.path.basename(greeting_file), f, "audio/ogg")},
                timeout=30,
            )
        if r.status_code == 200:
            return r.json()
        logger.error(f"❌ Ошибка загрузки файла {r.status_code}: {r.text[:300]}")
    except FileNotFoundError:
        logger.error(f"❌ Файл не найден: {greeting_file}")
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Сетевая ошибка при загрузке файла: {e}")
    return None


def is_first_contact_in_integration(customer_id: int, sa_id: int) -> bool:
    """
    Возвращает True, если у клиента только одно обращение в данной интеграции.
    Используется для проверки, что клиент действительно новый.
    """
    try:
        r = requests.get(
            f"{BASE_URL}/leads/all",
            headers=hdrs(),
            params={"customer": customer_id, "sa": sa_id, "limit": 200},
            timeout=10,
        )
        if r.status_code == 200:
            leads = r.json()
            lead_count = len(leads) if isinstance(leads, list) else 0
            logger.debug(
                f"Клиент {customer_id}: {lead_count} обращений в интеграции {sa_id}"
            )
            return lead_count == 1
        logger.warning(
            f"⚠️ Не удалось получить обращения клиента {customer_id}: {r.status_code}"
        )
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Ошибка при проверке истории клиента {customer_id}: {e}")
    return False


def send_greeting(lead: dict, greeting_file: str) -> bool:
    """Загружает файл и отправляет его как сообщение в диалог."""
    lead_id = lead["id"]
    # FIX: использовать .get() — userId может отсутствовать в новых обращениях
    user_id = lead.get("userId")

    source_real_id = get_source_real_id(lead_id)
    if not source_real_id:
        return False

    attachment = upload_file(source_real_id, greeting_file)
    if not attachment:
        return False

    payload: dict = {
        "message": {"text": "", "attachment": attachment},
        "source": source_real_id,
    }
    # FIX: не включаем userId в payload если он None (иначе API возвращает ошибку)
    if user_id:
        payload["userId"] = user_id

    try:
        r = requests.post(
            f"{BASE_URL}/messaging/{lead_id}/send",
            headers=hdrs(),
            json=payload,
            timeout=15,
        )
        if r.status_code in (200, 201):
            name = lead.get("customer", {}).get("name", "")
            logger.info(
                f"✅ Приветствие отправлено → {name} (лид {lead_id}, файл: {greeting_file})"
            )
            return True
        logger.error(
            f"❌ Ошибка отправки {r.status_code} в лид {lead_id}: {r.text[:300]}"
        )
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Сетевая ошибка при отправке в лид {lead_id}: {e}")
    return False


@app.route("/webhook", methods=["POST"])
def webhook():
    """Обработчик webhook событий от Umnico."""
    try:
        data = request.get_json()
        logger.info(f"📥 Webhook: {json.dumps(data, ensure_ascii=False)[:500]}")

        if not data:
            return jsonify({"status": "error", "message": "No data"}), 400

        event_type = data.get("type")
        logger.info(f"📋 Событие: {event_type}")

        # Обрабатываем только создание нового обращения
        if event_type == "lead.created":
            lead = data.get("lead", {})
            lead_id = lead.get("id")
            customer = lead.get("customer", {})
            customer_id = customer.get("id")
            sa_id = lead.get("socialAccount", {}).get("id")

            if not customer_id or not sa_id or not lead_id:
                logger.warning("⚠️ Неполные данные в событии")
                return jsonify({"status": "ok"}), 200

            # Интеграция не в списке — пропускаем
            if sa_id not in INTEGRATIONS:
                logger.debug(f"⏭️ Пропускаем интеграцию {sa_id}")
                return jsonify({"status": "ok"}), 200

            greeting_file = INTEGRATIONS[sa_id]

            # Клиент уже обработан в этой сессии
            customer_key = f"{sa_id}:{customer_id}"
            if customer_key in _seen_customers:
                logger.debug(f"⏭️ Клиент {customer_id} уже обработан")
                return jsonify({"status": "ok"}), 200

            # Проверяем через API что это первый контакт клиента
            if not is_first_contact_in_integration(customer_id, sa_id):
                logger.info(
                    f"⏭️ Клиент {customer.get('name', '')} (id={customer_id}) уже писал ранее"
                )
                _seen_customers.add(customer_key)
                return jsonify({"status": "ok"}), 200

            # Отправляем приветствие
            logger.info(
                f"🆕 Новый клиент: {customer.get('name', '')} "
                f"(customer_id={customer_id}, lead_id={lead_id}, sa={sa_id})"
            )
            send_greeting(lead, greeting_file)
            _seen_customers.add(customer_key)

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        logger.error(f"❌ Ошибка обработки webhook: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    """Проверка работоспособности сервера."""
    return jsonify({"status": "ok", "service": "umnico-greeting-bot"}), 200


if __name__ == "__main__":
    if not UMNICO_LOGIN or not UMNICO_PASSWORD:
        logger.error("❌ Укажите UMNICO_LOGIN и UMNICO_PASSWORD в файле .env!")
        exit(1)

    if not INTEGRATIONS:
        logger.error(
            "❌ Укажите INTEGRATIONS в файле .env!\n"
            "   Пример: INTEGRATIONS=110418:greeting.ogg"
        )
        exit(1)

    for sa_id, greeting_file in INTEGRATIONS.items():
        if not os.path.exists(greeting_file):
            logger.error(f"❌ Файл не найден: {greeting_file} (интеграция {sa_id})")
            exit(1)

    get_access_token()

    logger.info("🚀 Бот запущен (Webhook режим)")
    logger.info("🎯 Интеграции:")
    for sa_id, greeting_file in INTEGRATIONS.items():
        logger.info(f"   SA {sa_id} → {greeting_file}")
    logger.info(f"🌐 Webhook сервер на порту {WEBHOOK_PORT}")

    app.run(host="0.0.0.0", port=WEBHOOK_PORT, debug=False)
