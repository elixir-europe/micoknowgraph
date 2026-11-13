"""Проверить обнаружение поврежденных таблиц на небольших независимых примерах."""

import contextlib
import copy
import csv
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/entity_mapping/validate_mapping.py"
SPEC = importlib.util.spec_from_file_location("mapping_validation", SCRIPT)
validation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validation)


class MappingValidationTests(unittest.TestCase):
    """Проверить контракты без сети, YAML и реального снимка проекта."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sheet = {"values": [["Alpha", "CQ1, CQ 2"], ["Measure", "CQ3"], ["Unknown", "CQ4"]]}
        self.kinds = [
            dict(entity="Alpha", canonical_name="Alpha", cqs="CQ 1, CQ 2", kind="chemical", map="yes", rationale=""),
            dict(entity="Measure", canonical_name="Measure", cqs="CQ 3", kind="measurement", map="no", rationale=""),
            dict(entity="Unknown", canonical_name="Unknown", cqs="CQ 4", kind="gene", map="yes", rationale=""),
        ]
        self.concepts = []
        for row in self.kinds:
            item = dict.fromkeys(validation.CONCEPT_COLUMNS, "")
            item.update({key: row[key] for key in ["entity", "canonical_name", "cqs", "kind", "map"]})
            item.update(note="Причина отсутствия понятия.", status="Not Started")
            self.concepts.append(item)
        self.concepts[0].update(rank_adjusted="1", concept_id="TEST:1", ontology="TEST", name="Synthetic concept", source="local", tier="other", note="")

    def write_inputs(self):
        """Сохранить независимый пример во временный каталог.

        Returns
        -------
        list of pathlib.Path
            Пути снимка и двух TSV.
        """
        paths = [self.root / name for name in ["sheet.json", "kinds.tsv", "concepts.tsv"]]
        paths[0].write_text(json.dumps(self.sheet), encoding="utf-8")
        for path, columns, rows in [(paths[1], validation.KIND_COLUMNS, self.kinds), (paths[2], validation.CONCEPT_COLUMNS, self.concepts)]:
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=columns, delimiter="\t", lineterminator="\n")
                writer.writeheader()
                writer.writerows(rows)
        return paths

    def check(self):
        """Проверить текущую версию независимого примера.

        Returns
        -------
        dict
            Ошибки, предупреждения и метрики.
        """
        return validation.check_mapping(*self.write_inputs())

    def assert_error(self, code):
        """Убедиться в обнаружении заданного нарушения.

        Parameters
        ----------
        code : str
            Ожидаемый код ошибки.
        """
        self.assertIn(code, {item["code"] for item in self.check()["errors"]})

    def test_valid_mapping_uses_source_size(self):
        result = self.check()
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["warnings"], [])
        self.assertEqual(result["metrics"]["source_entities"], len(self.sheet["values"]))

    def test_source_growth_requires_both_tables(self):
        self.sheet["values"].append(["Added", "CQ5"])
        result = self.check()
        missing = [item for item in result["errors"] if item["code"] == "missing-entity"]
        self.assertEqual({item["table"] for item in missing if item["entity"] == "Added"}, {"kinds", "concepts"})

    def test_duplicate_source_after_whitespace_normalization(self):
        self.sheet["values"].append(["  Alpha\n", "CQ9"])
        self.assert_error("duplicate-source-entity")

    def test_duplicate_kind_entity(self):
        self.kinds.append(copy.deepcopy(self.kinds[0]))
        self.assert_error("duplicate-kind-entity")

    def test_unknown_kind_and_kind_map_mismatch(self):
        for kind, mapping, code in [("invented", "yes", "unknown-kind"), (" chemical ", "yes", "unknown-kind"), ("chemical", "no", "kind-map"), ("measurement", "yes", "kind-map")]:
            with self.subTest(kind=kind, mapping=mapping):
                self.kinds[0].update(kind=kind, map=mapping)
                self.concepts[0].update(kind=kind, map=mapping)
                self.assert_error(code)

    def test_whitespace_canonical_name_is_missing(self):
        self.kinds[0]["canonical_name"] = "   "
        self.concepts[0]["canonical_name"] = "   "
        self.assert_error("canonical-name")

    def test_candidate_requires_source_and_every_row_requires_status(self):
        self.concepts[0]["source"] = "  "
        self.concepts[1]["status"] = ""
        codes = {item["code"] for item in self.check()["errors"]}
        self.assertTrue({"candidate-source", "status"}.issubset(codes))

    def test_entity_not_in_source(self):
        self.sheet["values"].pop()
        self.assert_error("unexpected-entity")

    def test_missing_concept_entity(self):
        self.concepts.pop()
        self.assert_error("missing-entity")

    def test_headers_and_row_width_are_checked(self):
        for which, change in [(1, "header"), (2, "extra"), (1, "missing")]:
            with self.subTest(table=which, change=change):
                paths = self.write_inputs()
                lines = paths[which].read_text(encoding="utf-8").splitlines()
                if change == "header":
                    lines[0] = lines[0].replace("entity", "unexpected", 1)
                elif change == "extra":
                    lines[1] += "\textra"
                else:
                    lines[1] = lines[1].rsplit("\t", 1)[0]
                paths[which].write_text("\n".join(lines) + "\n", encoding="utf-8")
                codes = {item["code"] for item in validation.check_mapping(*paths)["errors"]}
                self.assertIn("columns" if change == "header" else "row-width", codes)

    def test_common_fields_must_agree(self):
        original = copy.deepcopy(self.concepts[0])
        for field, value in [("canonical_name", "Other"), ("kind", "host"), ("map", "no"), ("cqs", "CQ 99")]:
            with self.subTest(field=field):
                self.concepts[0] = dict(original, **{field: value})
                self.assert_error("inconsistent-classification")

    def test_non_mappable_entity_cannot_have_candidate(self):
        self.concepts[1].update(rank_adjusted="1", concept_id="TEST:2", ontology="TEST", name="Synthetic", tier="other")
        self.assert_error("non-mappable-rows")

    def test_candidate_cannot_mix_with_placeholder(self):
        placeholder = dict(self.concepts[0], **dict.fromkeys(validation.EMPTY_CANDIDATE_FIELDS, ""))
        placeholder["note"] = "Отдельное пояснение."
        self.concepts.append(placeholder)
        self.assert_error("placeholder-layout")

    def test_placeholder_requires_empty_candidate_fields_and_reason(self):
        self.concepts[2].update(ontology="TEST", note="")
        codes = {item["code"] for item in self.check()["errors"]}
        self.assertTrue({"placeholder-fields", "placeholder-note"}.issubset(codes))

    def test_identifier_format_without_scientific_validation(self):
        for cid in ["TEST:", "not a curie", "https://example.org/id"]:
            with self.subTest(cid=cid):
                self.concepts[0]["concept_id"] = cid
                self.assert_error("concept-id")
        self.concepts[0].update(concept_id="NCBITaxon:T016", ontology="NCBITAXON", note="WRONG? retained for review")
        self.assertEqual(self.check()["errors"], [])

    def test_candidate_fields_and_tier(self):
        original = copy.deepcopy(self.concepts[0])
        for field, value, code in [("ontology", "", "candidate-fields"), ("name", "", "candidate-fields"), ("tier", "invented", "tier")]:
            with self.subTest(field=field):
                self.concepts[0] = dict(original, **{field: value})
                self.assert_error(code)

    def test_ranks_must_be_positive_and_consecutive(self):
        for rank in ["0", "-1", "1.5", "3"]:
            with self.subTest(rank=rank):
                self.concepts[0]["rank_adjusted"] = rank
                self.assert_error("rank-sequence" if rank == "3" else "rank")

    def test_distinct_candidates_cannot_share_rank(self):
        self.concepts.append(dict(self.concepts[0], concept_id="TEST:2"))
        self.assert_error("rank-sequence")

    def test_broken_csv_quotes_are_reported(self):
        paths = self.write_inputs()
        with paths[2].open("a", encoding="utf-8") as handle:
            handle.write('"unterminated\n')
        errors = validation.check_mapping(*paths)["errors"]
        self.assertIn("read-error", {item["code"] for item in errors})

    def test_too_many_candidates(self):
        for rank in range(2, 7):
            self.concepts.append(dict(self.concepts[0], rank_adjusted=str(rank), concept_id=f"TEST:{rank}"))
        self.assert_error("candidate-limit")

    def test_duplicate_pair_retains_sources_as_warning(self):
        self.concepts.append(dict(self.concepts[0], rank_adjusted="2", source="bioportal"))
        result = self.check()
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["warnings"][0]["sources"], ["local", "bioportal"])
        self.assertEqual(result["warnings"][0]["ranks"], ["1", "2"])

    def test_cli_strict_duplicates_and_reports(self):
        self.concepts.append(dict(self.concepts[0], rank_adjusted="2", source="bioportal"))
        sheet, kinds, concepts = self.write_inputs()
        report, summary = self.root / "report.json", self.root / "report.md"
        args = ["--sheet", str(sheet), "--kinds", str(kinds), "--concepts", str(concepts), "--report", str(report), "--summary", str(summary)]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(validation.main(args), 0)
            self.assertEqual(validation.main(args + ["--strict-duplicates"]), 1)
        self.assertEqual(json.loads(report.read_text(encoding="utf-8"))["metrics"]["duplicate_concept_pairs"], 1)
        self.assertIn("duplicate-concept-pair", summary.read_text(encoding="utf-8"))

    def test_bad_source_and_missing_table_are_reported(self):
        paths = self.write_inputs()
        paths[0].write_text('{"values": null}', encoding="utf-8")
        paths[1].unlink()
        codes = {item["code"] for item in validation.check_mapping(*paths)["errors"]}
        self.assertTrue({"sheet-read-error", "empty-source", "read-error"}.issubset(codes))


if __name__ == "__main__":
    unittest.main(verbosity=2)
