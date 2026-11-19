"""Локальный тест цепочки Avito -> BUNKER на ВЫМЫШЛЕННЫХ данных.

Ничего не отправляет в Авито и Make. Хранилище — словарь в памяти, повторяющий
Make Data Store с ключом-строкой (key) и полями записи.

Проверяет:
  1. получение входящего сообщения (формат вебхука Avito Messenger API v3);
  2. формирование черновика ответа (заглушка вместо BUNKER Sales Manager:
     берёт только цены из prices_from_site.json, ничего не выдумывает);
  3. сохранение 4 типов записей с непересекающимися ключами;
  4. защиту от повторной обработки того же message_id.
"""
import json
import pathlib
import re

HERE = pathlib.Path(__file__).parent
PRICES = json.loads((HERE / "prices_from_site.json").read_text(encoding="utf-8"))

# --- Ключи: префикс типа гарантирует, что client/incoming/ai_result/price не перезапишут друг друга
def key_client(user_id):            return f"client:avito:{user_id}"
def key_incoming(chat_id, msg_id):  return f"incoming:avito:{chat_id}:{msg_id}"
def key_ai_result(chat_id, msg_id): return f"ai_result:avito:{chat_id}:{msg_id}"

# Единая структура записи Data Store (все поля текстовые/дата, лишние пустые)
FIELDS = ["type", "key", "source", "chat_id", "message_id", "user_id", "item_id",
          "client_name", "text", "reply_draft", "status", "service", "price_text",
          "created_at", "updated_at"]

def record(**kw):
    unknown = set(kw) - set(FIELDS)
    assert not unknown, f"поля вне структуры: {unknown}"
    return {f: kw.get(f, "") for f in FIELDS}


class DataStore(dict):
    def add(self, rec, overwrite=False):
        k = rec["key"]
        if k in self and not overwrite:
            raise KeyError(f"ключ уже занят: {k}")
        self[k] = rec


KEYWORDS = {
    "бронеплён": ["зоны-риска-седан", "зоны-риска-кроссовер", "полный-кузов-седан"],
    "ppf": ["зоны-риска-седан", "зоны-риска-кроссовер", "полный-кузов-седан"],
    "тонир": ["задняя-полусфера", "атермальная-передняя-полусфера", "одно-стекло"],
    "такси": ["solaris-rio-polo-rapid-octavia-optima", "только-гост-серый-жёлтые-полосы"],
    "винил": ["полная-оклейка-винилом-седан", "хамелеон"],
}

def draft_reply(text):
    """Заглушка для теста. В бою ответ формирует BUNKER Sales Manager в Make."""
    t = text.lower()
    lines = []
    for kw, slugs in KEYWORDS.items():
        if kw in t:
            for s in slugs:
                p = next((x for x in PRICES if x["key"] == f"price:site:{s}"), None)
                if p:
                    lines.append(f"• {p['service']}: {p['price_text']}")
    if "плёнк" in t and "прода" in t or "купить плёнк" in t:
        return None, "needs_owner"  # цен и остатков на продажу плёнок нет — не выдумываем
    if not lines:
        return None, "needs_owner"
    return ("Здравствуйте! Ориентиры по цене:\n" + "\n".join(lines) +
            "\nТочную стоимость назовём после осмотра. Когда удобно приехать?"), "draft"


def process_webhook(store, payload, now):
    v = payload["payload"]["value"]
    chat_id, msg_id, user_id = v["chat_id"], v["id"], v["author_id"]
    if v.get("type") != "text":
        return "skipped_non_text"
    if key_ai_result(chat_id, msg_id) in store:
        return "duplicate_skipped"
    text = v["content"]["text"]          # реальный текст входящего — именно он идёт в Sales Manager
    if key_client(user_id) not in store:
        store.add(record(type="client", key=key_client(user_id), source="avito",
                         user_id=str(user_id), chat_id=chat_id, created_at=now))
    store.add(record(type="incoming", key=key_incoming(chat_id, msg_id), source="avito",
                     chat_id=chat_id, message_id=msg_id, user_id=str(user_id),
                     item_id=str(v.get("item_id", "")), text=text, created_at=now))
    reply, status = draft_reply(text)
    store.add(record(type="ai_result", key=key_ai_result(chat_id, msg_id), source="avito",
                     chat_id=chat_id, message_id=msg_id, text=text,
                     reply_draft=reply or "", status=status, created_at=now))
    return status


def fake_webhook(chat_id, msg_id, user_id, text, mtype="text"):
    return {"id": "test", "version": "v3.0.0", "timestamp": 1700000000,
            "payload": {"type": "message", "value": {
                "id": msg_id, "chat_id": chat_id, "user_id": 999000001,
                "author_id": user_id, "created": 1700000000, "type": mtype,
                "chat_type": "u2i", "item_id": 111000111,
                "content": {"text": text} if mtype == "text" else {}}}}


if __name__ == "__main__":
    store = DataStore()
    for p in PRICES:
        store.add(record(type="price", key=p["key"], source="site",
                         service=p["service"], price_text=p["price_text"]))
    n_price = len(store)
    now = "2026-11-16T22:00:00+03:00"
    results = {}
    tests = [
        ("ТЕСТ-1 PPF", fake_webhook("u2i-TEST-A", "msg-TEST-1", 900001, "Сколько стоит бронеплёнка на зоны риска?")),
        ("ТЕСТ-2 тонировка", fake_webhook("u2i-TEST-B", "msg-TEST-2", 900002, "Хочу тонировку задней полусферы")),
        ("ТЕСТ-3 повтор того же сообщения", fake_webhook("u2i-TEST-A", "msg-TEST-1", 900001, "Сколько стоит бронеплёнка на зоны риска?")),
        ("ТЕСТ-4 продажа плёнки", fake_webhook("u2i-TEST-C", "msg-TEST-3", 900003, "Продаёте плёнку рулоном? Сколько стоит метр?")),
        ("ТЕСТ-5 фото без текста", fake_webhook("u2i-TEST-C", "msg-TEST-4", 900003, "", mtype="image")),
        ("ТЕСТ-6 второе сообщение того же клиента", fake_webhook("u2i-TEST-A", "msg-TEST-5", 900001, "А винил хамелеон?")),
    ]
    for name, wh in tests:
        results[name] = process_webhook(store, wh, now)

    by_type = {}
    for r in store.values():
        by_type[r["type"]] = by_type.get(r["type"], 0) + 1
    checks = {
        "price-записи не тронуты": sum(1 for r in store.values() if r["type"] == "price") == n_price,
        "повтор не обработан": results["ТЕСТ-3 повтор того же сообщения"] == "duplicate_skipped",
        "клиент A записан один раз": sum(1 for k in store if k == key_client(900001)) == 1,
        "в incoming реальный текст": store[key_incoming("u2i-TEST-A", "msg-TEST-1")]["text"].startswith("Сколько стоит"),
        "продажа плёнки -> владельцу, без выдуманной цены": results["ТЕСТ-4 продажа плёнки"] == "needs_owner",
        "ключи разных типов не пересекаются": all(k.split(":")[0] == r["type"] for k, r in store.items()),
        "все ключи уникальны": len(store) == len(set(store)),
    }
    print(json.dumps({"results": results, "records_by_type": by_type, "checks": checks},
                     ensure_ascii=False, indent=1))
    print("\nЧерновик ТЕСТ-1:\n" + store[key_ai_result("u2i-TEST-A", "msg-TEST-1")]["reply_draft"])
    assert all(checks.values()), "есть проваленные проверки"
    print("\nВСЕ ПРОВЕРКИ ПРОЙДЕНЫ")
