import json
from pathlib import Path

path = Path(__file__).with_name("questionnaires.json")
text = path.read_text(encoding="utf-8")
decoder = json.JSONDecoder()
objects = []
position = 0
while position < len(text):
    start = text.find("{", position)
    if start < 0:
        break
    try:
        value, end = decoder.raw_decode(text[start:])
    except json.JSONDecodeError:
        position = start + 1
        continue
    if isinstance(value, dict) and isinstance(value.get("questions"), list):
        objects.append(value)
    position = start + end

if not objects:
    raise SystemExit("Не найдено ни одного блока с questions")

# Prefer the first occurrence of each questionnaire; nested JSON fragments are ignored.
selected = []
seen = set()
for item in objects:
    questions = item["questions"]
    title = item.get("test_title", "")
    description = item.get("description", "")
    if "14 утверждений" in description or "HADS" in title:
        questionnaire_id = "hads"
    elif "BAI" in title or "тревоги Бека" in item.get("test_name", ""):
        questionnaire_id = "bai"
    elif "BDI" in title or "депрессии Бека" in title:
        questionnaire_id = "bdi"
    else:
        continue
    if questionnaire_id in seen:
        continue
    seen.add(questionnaire_id)
    selected.append((questionnaire_id, item))

result = []
for questionnaire_id, source in selected:
    global_options = source.get("options", [])
    items = []
    for question in source.get("questions", []):
        options = question.get("options", global_options)
        normalized_options = []
        for option in options:
            value = option.get("score", option.get("points"))
            if value is None:
                continue
            normalized_options.append({"value": int(value), "label": option.get("text", "")})
        normalized = {
            "id": str(question.get("id")),
            "text": question.get("text", ""),
            "options": normalized_options,
        }
        if question.get("scale"):
            normalized["domain"] = question["scale"]
        if question.get("category"):
            normalized["category"] = question["category"]
        if question.get("id") == 9 and questionnaire_id == "bdi":
            normalized["sensitive"] = True
        items.append(normalized)

    if questionnaire_id == "hads":
        scoring = {
            "type": "domains",
            "domains": {
                "A": {"title": "Тревога", "bands": [{"max": 7, "label": "Норма"}, {"max": 10, "label": "Пограничный уровень"}, {"max": 21, "label": "Клинически значимый уровень"}]},
                "D": {"title": "Депрессия", "bands": [{"max": 7, "label": "Норма"}, {"max": 10, "label": "Пограничный уровень"}, {"max": 21, "label": "Клинически значимый уровень"}]},
            },
        }
        title = "HADS: госпитальная шкала тревоги и депрессии"
    elif questionnaire_id == "bai":
        scoring = {"type": "sum", "bands": [{"max": 21, "label": "Низкая тревожность"}, {"max": 35, "label": "Средняя тревожность"}, {"max": 63, "label": "Высокая тревожность"}]}
        title = "Шкала тревоги Бека"
    else:
        scoring = {"type": "sum", "bands": [{"max": 13, "label": "Минимальные проявления"}, {"max": 19, "label": "Лёгкие проявления"}, {"max": 28, "label": "Умеренные проявления"}, {"max": 63, "label": "Выраженные проявления"}]}
        title = "Шкала депрессии Бека"
    result.append({"id": questionnaire_id, "title": title, "description": source.get("description", ""), "available": bool(items), "items": items, "scoring": scoring})

expected = {"hads": 14, "bai": 21, "bdi": 21}
missing = [key for key, count in expected.items() if not any(item["id"] == key and len(item["items"]) == count for item in result)]
if missing:
    raise SystemExit("Не хватает корректных опросников: " + ", ".join(missing))

path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print({item["id"]: len(item["items"]) for item in result})
