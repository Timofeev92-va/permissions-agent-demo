#!/usr/bin/env python3
"""
Извлекает данные из документов инженеров и сохраняет в JSON базу знаний.
Схема: pdftoppm/sips -> tesseract OCR -> структурированный JSON.
"""

import json
import os
import re
import subprocess
import tempfile
import pypdf
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent
PERMISSIONS_DIR = BASE_DIR / "permissions"
PERMISSIONS_DIR.mkdir(exist_ok=True)

ENGINEERS = {
    "employee_a": BASE_DIR / "sample_data" / "employee_a",
    "employee_b": BASE_DIR / "sample_data" / "employee_b",
}

# Сроки действия по типу документа (из Excel-справочника)
VALIDITY_YEARS = {
    "пром": 5,
    "промышленная безопасность": 5,
    "электробезопасность": 1,
    "гнвп": 2,
    "первая помощь": 3,
    "охрана труда": 1,
    "медкомиссия": 1,
    "мед": 1,
    "bosiet": 4,
    "huet": 4,
    "пожар": 1,
    "птм": 1,
    "высота": 3,
    "гвс": 3,
    "сосуды": 1,
    "эб": 1,
    "электробез": 1,
}


def pdf_to_text_pypdf(pdf_path: Path) -> str:
    """Пробует извлечь текст из PDF напрямую."""
    try:
        reader = pypdf.PdfReader(str(pdf_path))
        texts = []
        for page in reader.pages:
            t = page.extract_text()
            if t and t.strip():
                texts.append(t.strip())
        return "\n".join(texts)
    except Exception:
        return ""


def pdf_to_text_ocr(pdf_path: Path) -> str:
    """Конвертирует первую страницу PDF в изображение и применяет OCR."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Для больших файлов снижаем разрешение
        size_mb = pdf_path.stat().st_size / 1_000_000
        dpi = "72" if size_mb > 3 else "120"
        out_prefix = os.path.join(tmpdir, "page")
        result = subprocess.run(
            ["pdftoppm", "-r", dpi, "-f", "1", "-l", "1", "-png", str(pdf_path), out_prefix],
            capture_output=True, timeout=120
        )
        if result.returncode != 0:
            return ""

        # Найти сгенерированный файл
        png_files = sorted(Path(tmpdir).glob("*.png"))
        if not png_files:
            return ""

        # OCR через tesseract (русский + английский)
        ocr_result = subprocess.run(
            ["tesseract", str(png_files[0]), "stdout", "-l", "rus+eng", "--psm", "6"],
            capture_output=True, text=True, timeout=60
        )
        return ocr_result.stdout if ocr_result.returncode == 0 else ""


def image_to_text_ocr(img_path: Path) -> str:
    """Применяет OCR к изображению напрямую."""
    result = subprocess.run(
        ["tesseract", str(img_path), "stdout", "-l", "rus+eng", "--psm", "6"],
        capture_output=True, text=True, timeout=60
    )
    return result.stdout if result.returncode == 0 else ""


def extract_text(file_path: Path) -> str:
    """Извлекает текст из PDF или изображения."""
    ext = file_path.suffix.lower()
    if ext == ".pdf":
        text = pdf_to_text_pypdf(file_path)
        if len(text.strip()) < 30:
            text = pdf_to_text_ocr(file_path)
        return text
    elif ext in (".jpg", ".jpeg", ".png"):
        return image_to_text_ocr(file_path)
    return ""


def parse_date(s: str) -> str | None:
    """Пытается извлечь дату из строки, возвращает YYYY-MM-DD или None."""
    if not s:
        return None
    patterns = [
        r"(\d{4})-(\d{2})-(\d{2})",
        r"(\d{2})\.(\d{2})\.(\d{4})",
        r"(\d{2})/(\d{2})/(\d{4})",
        r"(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+(\d{4})",
    ]
    months_ru = {
        "января": "01", "февраля": "02", "марта": "03", "апреля": "04",
        "мая": "05", "июня": "06", "июля": "07", "августа": "08",
        "сентября": "09", "октября": "10", "ноября": "11", "декабря": "12"
    }
    for pat in patterns:
        m = re.search(pat, s, re.IGNORECASE)
        if m:
            groups = m.groups()
            if len(groups) == 3:
                if groups[1] in months_ru:
                    d, mon, y = groups
                    return f"{y}-{months_ru[mon]}-{int(d):02d}"
                elif len(groups[0]) == 4:
                    return f"{groups[0]}-{groups[1]}-{groups[2]}"
                else:
                    return f"{groups[2]}-{groups[1]}-{groups[0]}"
    return None


def guess_doc_type(filename: str, text: str) -> str:
    """Определяет тип документа по имени файла и тексту."""
    combined = (filename + " " + text[:500]).lower()
    if "диплом" in combined:
        return "Диплом"
    if "гнвп" in combined or "контроль скважин" in combined:
        return "ГНВП / Контроль скважины"
    if "первая помощь" in combined or "оказание первой" in combined:
        return "Первая помощь"
    if "мед" in combined and ("осмотр" in combined or "комиссия" in combined or "заключение" in combined):
        return "Медицинский осмотр"
    if "работы на высоте" in combined or "высота" in combined:
        return "Работы на высоте"
    if "электробез" in combined or "электро" in combined and "безопасность" in combined:
        return "Электробезопасность"
    if "пожар" in combined or "птм" in combined or "противопожар" in combined:
        return "Пожарная безопасность / ПТМ"
    if "охрана труда" in combined or " от " in combined and "протокол" in combined:
        return "Охрана труда"
    if "сосуды" in combined and "давление" in combined:
        return "Сосуды под давлением"
    if "пром" in combined and ("а1" in combined or "б2" in combined or "б9" in combined):
        cats = re.findall(r"[аб]\s*[\d.]+", combined.replace("б.", "б").replace("а.", "а"))
        cats_clean = list(dict.fromkeys(c.replace(" ", "").upper() for c in cats))
        label = ", ".join(cats_clean[:6]) if cats_clean else ""
        return f"Промышленная безопасность{(' ' + label) if label else ''}"
    if "промышленная безопасность" in combined or "ростехнадзор" in combined:
        return "Промышленная безопасность"
    if "эб" in combined or "электробезопасность" in combined:
        return "Электробезопасность (ЭБ)"
    if "bosiet" in combined or "huet" in combined or "вертолет" in combined or "покидание" in combined:
        return "BOSIET / HUET"
    if "гвс" in combined or "газовоздушн" in combined:
        return "Контроль ГВС"
    if "аттестация" in combined:
        return "Аттестация (уточнить)"
    return "Неизвестно"


def guess_validity(doc_type: str) -> int | None:
    """Возвращает срок действия в годах по типу документа."""
    dt = doc_type.lower()
    for key, years in VALIDITY_YEARS.items():
        if key in dt:
            return years
    return None


def extract_dates_from_filename(filename: str):
    """Пытается вытащить дату выдачи прямо из имени файла."""
    # Форматы: 20.06.25, 28.03.2024, 2025-09-02
    patterns = [
        r"(\d{2})\.(\d{2})\.(\d{2})\b",   # 20.06.25
        r"(\d{2})\.(\d{2})\.(\d{4})",      # 28.03.2024
        r"(\d{4})-(\d{2})-(\d{2})",        # 2025-09-02
        r"(\d{2})\.(\d{2})\.(\d{4})г",     # 05.02.2024г
    ]
    for pat in patterns:
        m = re.search(pat, filename)
        if m:
            groups = m.groups()
            if len(groups[0]) == 4:
                return f"{groups[0]}-{groups[1]}-{groups[2]}"
            elif len(groups[2]) == 2:
                year = "20" + groups[2]
                return f"{year}-{groups[1]}-{groups[0]}"
            else:
                return f"{groups[2]}-{groups[1]}-{groups[0]}"
    return None


def process_file(file_path: Path, engineer: str) -> dict:
    """Обрабатывает один файл, возвращает запись базы знаний."""
    print(f"  {file_path.name}")

    # Сначала проверяем дату в имени файла (быстро и без OCR)
    date_from_name = extract_dates_from_filename(file_path.name)

    # Извлекаем текст (OCR только если нет даты в имени или нужна доп. инфо)
    text = extract_text(file_path)

    doc_type = guess_doc_type(file_path.name, text)
    issue_date = date_from_name or parse_date(text)
    validity_years = guess_validity(doc_type)

    expiry_date = None
    if issue_date and validity_years:
        try:
            dt = datetime.strptime(issue_date, "%Y-%m-%d")
            exp = dt.replace(year=dt.year + validity_years)
            expiry_date = exp.strftime("%Y-%m-%d")
        except Exception:
            pass

    today = datetime.today()
    if expiry_date:
        exp_dt = datetime.strptime(expiry_date, "%Y-%m-%d")
        days_left = (exp_dt - today).days
        if days_left < 0:
            status = "ПРОСРОЧЕН"
        elif days_left <= 90:
            status = f"ИСТЕКАЕТ ЧЕРЕЗ {days_left} дн."
        else:
            status = "OK"
    elif doc_type == "Диплом":
        status = "OK (бессрочно)"
    else:
        status = "ДАТА НЕИЗВЕСТНА"

    return {
        "file": file_path.name,
        "engineer": engineer,
        "doc_type": doc_type,
        "issue_date": issue_date,
        "expiry_date": expiry_date,
        "validity_years": validity_years,
        "status": status,
        "text_snippet": text[:300].strip() if text else "",
    }


def main():
    print(f"Начало: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    all_data = {}

    for engineer_name, folder in ENGINEERS.items():
        if not folder.exists():
            print(f"Папка не найдена: {folder}")
            continue

        print(f"\n=== {engineer_name.upper()} ===")
        records = []
        files = sorted(p for p in folder.iterdir() if not p.name.startswith("."))

        for f in files:
            if f.suffix.lower() not in (".pdf", ".jpg", ".jpeg", ".png"):
                continue
            record = process_file(f, engineer_name)
            records.append(record)
            print(f"    -> [{record['status']}] {record['doc_type']} | выдан: {record['issue_date']} | истекает: {record['expiry_date']}")

        all_data[engineer_name] = records
        out_path = PERMISSIONS_DIR / f"{engineer_name}.json"
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(records, fh, ensure_ascii=False, indent=2)
        print(f"  Сохранено: {out_path}")

    combined_path = PERMISSIONS_DIR / "all_docs.json"
    with open(combined_path, "w", encoding="utf-8") as fh:
        json.dump(all_data, fh, ensure_ascii=False, indent=2)
    print(f"\nГотово. Файлы в {PERMISSIONS_DIR}/")


if __name__ == "__main__":
    main()
