"""Проверить фактическую offline-сборку на сохраненных данных проекта."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import urlsplit

import yaml


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts/entity_mapping/build_mapping.py"
CURATED = ROOT / "scripts/entity_mapping/entity_kinds_curated.yaml"
DATA = ROOT / "data/entity_mapping"
OUTPUTS = ("entity_kinds.tsv", "entities_to_concepts.tsv")
INPUTS = ("sheet_entities.json", "details.csv", "ols_cache.json")

# Изолированный процесс исполняет настоящий CLI; любые DNS и сетевые соединения запрещены.
OFFLINE_BOOTSTRAP = """
import runpy
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

def deny_network(event, args):
    if event in {"socket.connect", "socket.getaddrinfo", "http.client.connect", "urllib.Request"}:
        raise RuntimeError("В offline-тесте запрещена сеть: " + event)

sys.addaudithook(deny_network)
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
"""


def fingerprint(paths):
    """Вычислить хеши исходных файлов перед и после сборки.

    Parameters
    ----------
    paths : Iterable[Path]
        Файлы, которые сборка не должна менять в checkout.

    Returns
    -------
    dict[Path, str]
        Соответствие пути и SHA-256 фактических байтов.
    """
    return {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def read_tsv(path):
    """Прочитать строки TSV с явной кодировкой UTF-8.

    Parameters
    ----------
    path : Path
        Путь проверяемой таблицы.

    Returns
    -------
    list[dict[str, str]]
        Строки таблицы с именами столбцов.
    """
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


class OfflineBuildTests(unittest.TestCase):
    """Проверить воспроизводимость CLI и обновление настоящего снимка сущностей."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="micokg-offline-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.workspace = Path(cls.temporary.name)
        originals = [BUILDER, CURATED, *(DATA / name for name in OUTPUTS), *sorted((DATA / "inputs").iterdir())]
        cls.original_hashes = fingerprint(path for path in originals if path.is_file())
        cls.downloads = cls.copy_inputs("baseline-inputs")
        cls.output = cls.workspace / "baseline-output"
        result = cls.run_builder(cls.downloads, cls.output)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def copy_inputs(cls, directory):
        """Скопировать входы: исходный builder записывает кеш и отчет рядом с ними.

        Parameters
        ----------
        directory : str
            Имя отдельного временного каталога.

        Returns
        -------
        Path
            Каталог безопасных копий входов.
        """
        target = cls.workspace / directory
        target.mkdir()
        for name in INPUTS:
            shutil.copyfile(DATA / "inputs" / name, target / name)
        return target

    @classmethod
    def run_builder(cls, downloads, output, curated=CURATED):
        """Запустить настоящий CLI с блокировкой сети и временными путями.

        Parameters
        ----------
        downloads : Path
            Копии снимка, результата matcher и кеша OLS.
        output : Path
            Каталог новых таблиц, расположенный вне checkout.
        curated : Path
            Проверяемая классификация сущностей.

        Returns
        -------
        subprocess.CompletedProcess
            Код завершения и диагностический вывод процесса.
        """
        return subprocess.run(
            [sys.executable, "-I", "-c", OFFLINE_BOOTSTRAP, str(BUILDER),
             "--offline", "--downloads", str(downloads), "--out", str(output),
             "--curated", str(curated)],
            cwd=cls.workspace, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", timeout=120, check=False,
        )

    @classmethod
    def tearDownClass(cls):
        if cls.original_hashes != fingerprint(cls.original_hashes):
            raise AssertionError("Offline-тест изменил исходные файлы checkout.")

    def test_offline_rebuild_matches_both_published_tables(self):
        """Обе опубликованные TSV воспроизводятся побайтово из настоящих входов."""
        for name in OUTPUTS:
            with self.subTest(table=name):
                self.assertEqual((DATA / name).read_bytes(), (self.output / name).read_bytes())

    def test_separate_isolated_rebuilds_are_identical(self):
        """Два независимых изолированных процесса создают одинаковые таблицы."""
        downloads = self.copy_inputs("repeat-inputs")
        output = self.workspace / "repeat-output"
        result = self.run_builder(downloads, output)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for name in OUTPUTS:
            with self.subTest(table=name):
                self.assertEqual((self.output / name).read_bytes(), (output / name).read_bytes())

    def test_offline_build_has_no_live_ols_calls(self):
        """Реальная offline-сборка не делает сетевых вызовов OLS."""
        report = json.loads((self.downloads / "build_report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["stats"]["ols_live_calls"], 0)

    def test_incomplete_cache_is_detected_without_network(self):
        """Неполный кеш выявляется до записи TSV или побайтовым сравнением."""
        with self.subTest(cache="empty"):
            downloads = self.copy_inputs("empty-cache-inputs")
            (downloads / "ols_cache.json").write_text("{}\n", encoding="utf-8")
            output = self.workspace / "empty-cache-output"
            result = self.run_builder(downloads, output)
            self.assertNotEqual(result.returncode, 0)
            self.assertRegex(result.stderr, r"assigned [^\n]+ not found on OLS and no name given")
            for name in OUTPUTS:
                self.assertFalse((output / name).exists())

        with self.subTest(cache="search-responses-omitted"):
            downloads = self.copy_inputs("missing-search-inputs")
            cache_path = downloads / "ols_cache.json"
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            retained = {url: value for url, value in cache.items()
                        if not urlsplit(url).path.endswith("/search")}
            self.assertLess(len(retained), len(cache))
            cache_path.write_text(json.dumps(retained), encoding="utf-8")
            output = self.workspace / "missing-search-output"
            result = self.run_builder(downloads, output)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual((DATA / "entity_kinds.tsv").read_bytes(),
                             (output / "entity_kinds.tsv").read_bytes())
            self.assertNotEqual((DATA / "entities_to_concepts.tsv").read_bytes(),
                                (output / "entities_to_concepts.tsv").read_bytes())

    def test_checkout_inputs_and_published_outputs_are_unchanged(self):
        """Кеш, снимок и опубликованные таблицы в checkout сохраняют исходные байты."""
        self.assertEqual(self.original_hashes, fingerprint(self.original_hashes))

    def test_new_curated_entity_is_supported_without_fixed_counts(self):
        """Добавление сущности в актуальный снимок не требует фиксированного числа строк."""
        downloads = self.copy_inputs("expanded-inputs")
        sheet_path = downloads / "sheet_entities.json"
        sheet = json.loads(sheet_path.read_text(encoding="utf-8"))
        entities = {" ".join(row[0].split()) for row in sheet["values"] if row and row[0].strip()}
        entity = "QA fixture measurement"
        while entity in entities:
            entity += " extra"
        sheet["values"].append([entity, "CQ 999"])
        sheet_path.write_text(json.dumps(sheet), encoding="utf-8")
        curated = yaml.safe_load(CURATED.read_text(encoding="utf-8"))
        curated[entity] = {"kind": "measurement", "rationale": "Синтетический пример проверки ПО."}
        curated_path = self.workspace / "expanded-curated.yaml"
        curated_path.write_text(yaml.safe_dump(curated, allow_unicode=True), encoding="utf-8")
        output = self.workspace / "expanded-output"
        result = self.run_builder(downloads, output, curated_path)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        kinds = read_tsv(output / "entity_kinds.tsv")
        concepts = read_tsv(output / "entities_to_concepts.tsv")
        self.assertEqual({row["entity"] for row in kinds}, entities | {entity})
        self.assertEqual(len(kinds), len(entities) + 1)
        added = [row for row in concepts if row["entity"] == entity]
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]["map"], "no")
        self.assertEqual(added[0]["concept_id"], "")

    def test_missing_curated_kind_fails_before_writing_tables(self):
        """Новая сущность без классификации завершается явной ошибкой до записи TSV."""
        downloads = self.copy_inputs("unclassified-inputs")
        sheet = json.loads((downloads / "sheet_entities.json").read_text(encoding="utf-8"))
        entity = next(" ".join(row[0].split()) for row in sheet["values"] if row and row[0].strip())
        curated = yaml.safe_load(CURATED.read_text(encoding="utf-8"))
        key = next(key for key in curated if " ".join(key.split()) == entity)
        del curated[key]
        curated_path = self.workspace / "unclassified-curated.yaml"
        curated_path.write_text(yaml.safe_dump(curated), encoding="utf-8")
        output = self.workspace / "unclassified-output"
        result = self.run_builder(downloads, output, curated_path)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("lack a curated kind", result.stderr)
        for name in OUTPUTS:
            self.assertFalse((output / name).exists())


if __name__ == "__main__":
    unittest.main()
