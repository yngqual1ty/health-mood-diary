"""Download current Russian clinical guidelines (cr.minzdrav.gov.ru) as Markdown for the RAG index.

Usage: python fetch_guidelines.py [code ...]   (default: depression and anxiety guidelines)
"""

import json
import re
import sys
import urllib.request
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

API = "https://apicr.minzdrav.gov.ru/api.ashx"
VIEW_URL = "https://cr.minzdrav.gov.ru/view-cr/{id}"
OUT_DIR = Path(__file__).resolve().parent / "guidelines"

DEFAULT_CODES = {
    301: "Депрессивный эпизод, рекуррентное депрессивное расстройство",
    457: "Генерализованное тревожное расстройство",
    456: "Паническое расстройство",
    455: "Тревожно-фобические расстройства",
}
# Bibliography, authors, keyword lists, ICD coding tables, image-only algorithms and
# questionnaire scales only add retrieval noise for treatment questions.
SKIP_SECTIONS = {
    "doc_whole", "doc_title", "doc_key_words", "doc_crat_info_1_4",
    "doc_bible", "doc_a1", "doc_b", "doc_g",
}


def request(op: str, payload: dict | None = None, **params) -> dict:
    query = "&".join([f"op={op}", *(f"{k}={v}" for k, v in params.items())])
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{API}?{query}", data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as response:
        return json.loads(response.read())


def latest_versions(codes: list[int]) -> dict[int, dict]:
    listing = request("GetJsonClinrecsFilterV2", {
        "filters": [{"fieldName": "status", "filterType": 1, "filterValueType": 2, "value1": 0, "value2": "", "values": []}],
        "sortOption": {"fieldName": "publishdate", "sortType": 2},
        "currentPage": 1,
        "pageSize": 5000,
    })
    found: dict[int, dict] = {}
    for item in listing["Data"]:
        code = int(item["Code"])
        if code in codes and (code not in found or item["Version"] > found[code]["Version"]):
            found[code] = item
    return found


class MarkdownConverter(HTMLParser):
    BLOCKS = {"p", "div", "ul", "ol", "table", "tbody", "thead", "br"}

    def __init__(self, heading_offset: int):
        super().__init__(convert_charrefs=True)
        self.heading_offset = heading_offset
        self.lines: list[str] = []
        self.current: list[str] = []
        self.prefix = ""
        self.cells: list[str] | None = None

    def flush(self):
        text = re.sub(r"\s+", " ", "".join(self.current)).strip()
        self.current = []
        if text.startswith("#") and not self.prefix:
            # In guideline texts a leading "#" marks off-label use, not a heading.
            text = "\\" + text
        if text:
            self.lines.append(self.prefix + text)
        self.prefix = ""

    def handle_starttag(self, tag, attrs):
        if re.fullmatch(r"h[1-6]", tag):
            self.flush()
            self.lines.append("")
            self.prefix = "#" * min(6, int(tag[1]) + self.heading_offset) + " "
        elif tag == "li":
            self.flush()
            self.prefix = "- "
        elif tag == "tr":
            self.flush()
            self.cells = []
        elif tag in ("td", "th") and self.cells is not None:
            self.current = []
        elif tag in self.BLOCKS:
            self.flush()

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cells is not None:
            self.cells.append(re.sub(r"\s+", " ", "".join(self.current)).strip())
            self.current = []
        elif tag == "tr" and self.cells is not None:
            if any(self.cells):
                self.lines.append("| " + " | ".join(self.cells) + " |")
            self.cells = None
        elif re.fullmatch(r"h[1-6]", tag):
            self.flush()
            self.lines.append("")
        elif tag in self.BLOCKS or tag == "li":
            self.flush()

    def handle_data(self, data):
        self.current.append(data)

    def markdown(self) -> str:
        self.flush()
        # Blank lines separate paragraphs for the RAG chunker.
        return "\n\n".join(line for line in self.lines if line.strip())


def section_markdown(section: dict) -> str:
    title = section["title"].strip()
    level = 3 if re.match(r"^\d+\.\d+", title) else 2
    converter = MarkdownConverter(heading_offset=level - 1)
    converter.feed(section.get("content") or "")
    body = converter.markdown()
    return f"{'#' * level} {title}\n\n{body}" if body or level == 2 else ""


def fetch(code: int, meta: dict) -> Path:
    cr_id = meta["CodeVersion"]
    data = request("GetClinrec2", id=cr_id, ssid="")
    header = [
        f"# {data['name']}",
        (
            f"Клинические рекомендации Минздрава России, КР{code}, версия {data['version']}. "
            f"МКБ-10: {data['mkb']}. Возраст: {'взрослые' if data.get('adult') else ''}"
            f"{' дети' if data.get('child') else ''}. Статус: {data.get('apply_status')}. "
            f"Опубликовано: {data['publish_date'][:10]}. Источник: {VIEW_URL.format(id=cr_id)} "
            f"(загружено {date.today().isoformat()})."
        ),
    ]
    sections = [
        section_markdown(s) for s in data["obj"]["sections"] if s["id"] not in SKIP_SECTIONS
    ]
    path = OUT_DIR / f"kr{code}_{data['version']}.md"
    path.write_text("\n\n".join([*header, *filter(None, sections)]) + "\n", encoding="utf-8")
    return path


def main():
    codes = [int(arg) for arg in sys.argv[1:]] or list(DEFAULT_CODES)
    OUT_DIR.mkdir(exist_ok=True)
    versions = latest_versions(codes)
    for code in codes:
        if code not in versions:
            print(f"КР{code}: не найдены в рубрикаторе")
            continue
        path = fetch(code, versions[code])
        print(f"КР{code} ({versions[code]['Name']}): {path.name}, {path.stat().st_size // 1024} КБ")


if __name__ == "__main__":
    main()
