#!/usr/bin/env python3
"""Проверить структуру и покрытие локальных таблиц сопоставления сущностей."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path


KIND_COLUMNS = ["entity", "canonical_name", "cqs", "kind", "map", "rationale"]
CONCEPT_COLUMNS = [
    "entity", "canonical_name", "cqs", "kind", "map", "preferred_ontology",
    "rank_adjusted", "concept_id", "ontology", "name", "definition", "rating_thomas",
    "source", "tier", "note", "status", "contributors", "reviewers",
]
CURIE = re.compile(r"[A-Za-z][A-Za-z0-9._-]*:[^\s:\"<>]+")
TIERS = {"core", "extension", "mapped", "other"}
MAPPABLE_KINDS = {
    "taxon", "taxon-group", "host", "chemical", "drug-or-therapy", "gene",
    "gene-product", "pathway-or-process", "mobile-element", "disease-or-phenotype",
    "anatomy-or-body-site", "sample-material", "environment", "event-or-exposure",
    "food-or-diet", "ecological-role", "cell-state", "time-or-period", "study-design",
}
NON_MAPPABLE_KINDS = {
    "measurement", "statistic", "method-or-procedure", "data-artifact",
    "identifier-scheme", "meta", "junk-fragment",
}
EMPTY_CANDIDATE_FIELDS = [
    "rank_adjusted", "concept_id", "ontology", "name", "definition",
    "rating_thomas", "source", "tier",
]


def _clean(value):
    """Свернуть пробельные символы по правилу исходной сборки.

    Parameters
    ----------
    value : str
        Исходное текстовое значение.

    Returns
    -------
    str
        Значение без краевых и повторных пробелов.
    """
    return re.sub(r"\s+", " ", value).strip()


def _cqs(value):
    """Нормализовать запись CQ, сохранив исходный порядок и прочие значения.

    Parameters
    ----------
    value : str
        Список CQ из исходного снимка.

    Returns
    -------
    str
        Нормализованное значение по контракту исходной сборки.
    """
    parts = []
    for part in _clean(value).split(","):
        part = part.strip()
        if part:
            match = re.fullmatch(r"(CQ|QC)\s*(\d+)", part, re.I)
            parts.append(f"{match[1].upper()} {match[2]}" if match else part)
    return ", ".join(parts)


def _issue(items, code, message, path, row=None, **details):
    """Добавить машиночитаемое замечание с положением в источнике.

    Parameters
    ----------
    items : list of dict
        Изменяемый список замечаний.
    code, message : str
        Код и пояснение обнаруженного нарушения.
    path : str or pathlib.Path
        Путь проверяемого источника.
    row : int, optional
        Номер строки, если применим.
    **details : dict
        Дополнительные поля диагностической записи.
    """
    items.append({"code": code, "message": message, "file": str(path), "row": row, **details})


def _read_tsv(path, columns, errors):
    """Прочитать TSV, обнаружив ошибки заголовка и ширины каждой записи.

    Parameters
    ----------
    path : str or pathlib.Path
        Путь таблицы в UTF-8.
    columns : list of str
        Точный порядок ожидаемых столбцов.
    errors : list of dict
        Изменяемый список ошибок чтения.

    Returns
    -------
    list of tuple
        Номер строки и словарь полей для каждой корректно прочитанной записи.
    """
    rows = []
    try:
        with Path(path).open(encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle, delimiter="\t", strict=True)
            if next(reader, None) != columns:
                _issue(errors, "columns", "Заголовок отличается от согласованного порядка столбцов.", path, 1)
                return rows
            for values in reader:
                line = reader.line_num
                if len(values) != len(columns):
                    _issue(errors, "row-width", "Число значений не соответствует заголовку.", path, line)
                    continue
                rows.append((line, dict(zip(columns, values))))
    except (OSError, UnicodeError, csv.Error) as exc:
        _issue(errors, "read-error", str(exc), path)
    return rows


def check_mapping(sheet_path, kinds_path, concepts_path):
    """Проверить таблицы, не изменяя входные файлы и не обращаясь к сети.

    Parameters
    ----------
    sheet_path : str or pathlib.Path
        Снимок исходного списка сущностей в JSON.
    kinds_path : str or pathlib.Path
        Таблица классификации сущностей.
    concepts_path : str or pathlib.Path
        Таблица кандидатов понятий.

    Returns
    -------
    dict
        JSON-совместимые errors, warnings и metrics. Повторные пары являются
        предупреждениями, поскольку могут сохранять разные источники доказательств.
    """
    errors, warnings, sheet = [], [], {}
    source_rows = 0
    try:
        values = json.loads(Path(sheet_path).read_text(encoding="utf-8"))["values"]
        if not isinstance(values, list):
            raise ValueError("Поле values должно быть списком строк.")
        for number, row in enumerate(values, 1):
            if not isinstance(row, list) or any(not isinstance(v, str) for v in row[:2]):
                _issue(errors, "sheet-row", "Ожидается строка с текстовыми значениями.", sheet_path, number)
                continue
            entity = _clean(row[0]) if row else ""
            if not entity:
                continue
            source_rows += 1
            if entity in sheet:
                _issue(errors, "duplicate-source-entity", "Сущность повторяется после нормализации пробелов.", sheet_path, number, entity=entity)
            else:
                sheet[entity] = _cqs(row[1] if len(row) > 1 else "")
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        _issue(errors, "sheet-read-error", str(exc), sheet_path)
    if not sheet:
        _issue(errors, "empty-source", "В снимке нет непустых сущностей.", sheet_path)

    kind_rows = _read_tsv(kinds_path, KIND_COLUMNS, errors)
    concept_rows = _read_tsv(concepts_path, CONCEPT_COLUMNS, errors)
    kinds, groups = {}, defaultdict(list)
    for path, rows, label in [(kinds_path, kind_rows, "kinds"), (concepts_path, concept_rows, "concepts")]:
        for line, row in rows:
            entity = row["entity"]
            if not entity or entity != _clean(entity):
                _issue(errors, "entity-normalization", "Имя сущности пусто или содержит лишние пробелы.", path, line, entity=entity)
            if entity not in sheet:
                _issue(errors, "unexpected-entity", "Сущности нет в исходном снимке.", path, line, entity=entity)
            elif row["cqs"] != sheet[entity]:
                _issue(errors, "cqs", "CQ не совпадает с нормализованным исходным значением.", path, line, entity=entity)
            if not row["kind"].strip() or row["map"] not in {"yes", "no"}:
                _issue(errors, "classification", "Нужны непустой kind и map со значением yes или no.", path, line, entity=entity)
            if row["kind"] not in MAPPABLE_KINDS | NON_MAPPABLE_KINDS:
                _issue(errors, "unknown-kind", "Классификация содержит неизвестный kind.", path, line, entity=entity)
            elif row["map"] != ("yes" if row["kind"] in MAPPABLE_KINDS else "no"):
                _issue(errors, "kind-map", "Значение map не соответствует согласованной группе kind.", path, line, entity=entity)
            if not row["canonical_name"].strip() and row["kind"] != "junk-fragment":
                _issue(errors, "canonical-name", "Отсутствует каноническое имя обычной сущности.", path, line, entity=entity)
            if label == "kinds":
                if entity in kinds:
                    _issue(errors, "duplicate-kind-entity", "В классификации допустима одна строка на сущность.", path, line, entity=entity)
                kinds.setdefault(entity, row)
            else:
                if not row["status"].strip():
                    _issue(errors, "status", "Статус строки должен быть указан явно.", path, line, entity=entity)
                groups[entity].append((line, row))
    for label, path, actual in [("kinds", kinds_path, kinds), ("concepts", concepts_path, groups)]:
        for entity in sorted(set(sheet) - set(actual)):
            _issue(errors, "missing-entity", "Сущность снимка отсутствует в таблице.", path, entity=entity, table=label)

    pairs = defaultdict(list)
    for entity, rows in groups.items():
        for line, row in rows:
            expected = kinds.get(entity)
            if expected and any(row[key] != expected[key] for key in ["canonical_name", "cqs", "kind", "map"]):
                _issue(errors, "inconsistent-classification", "Общие поля расходятся между двумя таблицами.", concepts_path, line, entity=entity)
        mappable = kinds.get(entity, rows[0][1])["map"] == "yes"
        candidates = [(line, row) for line, row in rows if row["concept_id"]]
        placeholders = [(line, row) for line, row in rows if not row["concept_id"]]
        if not mappable and (candidates or len(placeholders) != 1):
            _issue(errors, "non-mappable-rows", "Для map=no нужна ровно одна строка без понятия.", concepts_path, entity=entity)
        if mappable and ((candidates and placeholders) or (not candidates and len(placeholders) != 1)):
            _issue(errors, "placeholder-layout", "Нужны кандидаты либо одна явная строка без совпадения.", concepts_path, entity=entity)
        for line, row in placeholders:
            if any(row[key] for key in EMPTY_CANDIDATE_FIELDS):
                _issue(errors, "placeholder-fields", "В строке без понятия заполнены поля кандидата.", concepts_path, line, entity=entity)
            if not row["note"].strip():
                _issue(errors, "placeholder-note", "Причина отсутствия понятия должна быть указана явно.", concepts_path, line, entity=entity)
        ranks = []
        for line, row in candidates:
            cid = row["concept_id"]
            if not CURIE.fullmatch(cid) or "://" in cid:
                _issue(errors, "concept-id", "Ожидается CURIE без пустого префикса или локального имени.", concepts_path, line, entity=entity, concept_id=cid)
            if not row["ontology"].strip() or not row["name"].strip():
                _issue(errors, "candidate-fields", "У кандидата нужны ontology и name.", concepts_path, line, entity=entity)
            if not row["source"].strip():
                _issue(errors, "candidate-source", "У кандидата должен быть указан источник.", concepts_path, line, entity=entity)
            if row["tier"] not in TIERS:
                _issue(errors, "tier", "Неизвестный структурный tier.", concepts_path, line, entity=entity)
            if not re.fullmatch(r"[1-9][0-9]*", row["rank_adjusted"]):
                _issue(errors, "rank", "Ранг должен быть положительным целым числом.", concepts_path, line, entity=entity)
            else:
                ranks.append(int(row["rank_adjusted"]))
            pairs[(entity, cid)].append((line, row))
        if len(candidates) > 5:
            _issue(errors, "candidate-limit", "Число кандидатов превышает согласованный предел 5.", concepts_path, entity=entity)
        if len(ranks) == len(candidates) and sorted(ranks) != list(range(1, len(candidates) + 1)):
            _issue(errors, "rank-sequence", "Ранги должны быть уникальны и последовательны от 1.", concepts_path, entity=entity)

    duplicates = {pair: rows for pair, rows in pairs.items() if len(rows) > 1}
    for (entity, cid), rows in sorted(duplicates.items()):
        _issue(warnings, "duplicate-concept-pair", "Пара сущность/понятие повторяется; фактические источники и ранги сохранены для проверки.", concepts_path, entity=entity, concept_id=cid, ranks=[r["rank_adjusted"] for _, r in rows], sources=[r["source"] for _, r in rows], rows=[line for line, _ in rows])
    metrics = {
        "source_entities": source_rows, "unique_source_entities": len(sheet),
        "kind_rows": len(kind_rows), "concept_rows": len(concept_rows),
        "mappable_entities": sum(row["map"] == "yes" for row in kinds.values()),
        "non_mappable_entities": sum(row["map"] == "no" for row in kinds.values()),
        "concept_candidates": sum(bool(row["concept_id"]) for _, row in concept_rows),
        "placeholder_rows": sum(not row["concept_id"] for _, row in concept_rows),
        "duplicate_concept_pairs": len(duplicates),
        "duplicated_concept_rows": sum(len(rows) - 1 for rows in duplicates.values()),
    }
    return {"errors": errors, "warnings": warnings, "metrics": metrics}


def _summary(result):
    """Подготовить краткий Markdown-отчет, сохранив все замечания.

    Parameters
    ----------
    result : dict
        Результат структурной проверки.

    Returns
    -------
    str
        Сводка метрик, ошибок и предупреждений.
    """
    lines = ["# Проверка таблиц сопоставления", "", "Проверка структуры не подтверждает биологическую корректность понятий.", ""]
    lines += [f"- {key}: {value}" for key, value in result["metrics"].items()]
    for name, title in [("errors", "Ошибки"), ("warnings", "Предупреждения")]:
        lines += ["", f"## {title}: {len(result[name])}", ""]
        for item in result[name]:
            details = ", ".join(str(item[key]) for key in ["entity", "concept_id", "ranks", "sources"] if key in item)
            lines.append(f"- `{item['code']}`: {item['message']} {details}".rstrip())
    return "\n".join(lines) + "\n"


def main(argv=None):
    """Выполнить проверку и при запросе сохранить JSON и Markdown.

    Parameters
    ----------
    argv : list of str, optional
        Аргументы командной строки; по умолчанию используются аргументы процесса.

    Returns
    -------
    int
        Ноль при отсутствии ошибок; единица при ошибках или строгих дубликатах.
    """
    root = Path(__file__).resolve().parents[2] / "data/entity_mapping"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet", type=Path, default=root / "inputs/sheet_entities.json")
    parser.add_argument("--kinds", type=Path, default=root / "entity_kinds.tsv")
    parser.add_argument("--concepts", type=Path, default=root / "entities_to_concepts.tsv")
    parser.add_argument("--report", type=Path, help="Сохранить полный JSON-отчет.")
    parser.add_argument("--summary", type=Path, help="Сохранить краткий Markdown-отчет.")
    parser.add_argument("--strict-duplicates", action="store_true", help="Считать повторные пары ошибкой завершения.")
    args = parser.parse_args(argv)
    result = check_mapping(args.sheet, args.kinds, args.concepts)
    print(_summary(result), end="")
    for path, content in [(args.report, json.dumps(result, ensure_ascii=False, indent=2) + "\n"), (args.summary, _summary(result))]:
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    return int(bool(result["errors"] or (args.strict_duplicates and result["metrics"]["duplicate_concept_pairs"])))


if __name__ == "__main__":
    raise SystemExit(main())
