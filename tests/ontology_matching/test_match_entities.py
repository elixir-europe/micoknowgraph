"""Tests for scripts/ontology_matching/match_entities.py. Offline: BioPortal is replaced by a fake client.

Run from the repository root:
    python3 -m unittest discover -s tests -t . -v
"""
import csv
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "ontology_matching"))

import match_entities as me                      # noqa: E402
from ontology_index import load_local_ontologies, normalise, ontology_of, to_curie  # noqa: E402

OBO = "http://purl.obolibrary.org/obo/"


class FakeBioPortal:
    """Stands in for bioportal.BioPortal: returns canned search results, makes no network calls."""

    def __init__(self, results):
        self.results = results
        self.queries = []
        self.scopes = []

    def search(self, query, ontologies=None, pagesize=50):
        self.queries.append(query)
        self.scopes.append(ontologies)
        return self.results.get(query, [])


def bp(iri, label, acronym, synonyms=(), notation="", obsolete=False):
    """One BioPortal search result in the shape bioportal.BioPortal.search returns."""
    return {"iri": iri, "label": label, "synonyms": list(synonyms), "acronym": acronym,
            "notation": notation, "obsolete": obsolete}


class TestText(unittest.TestCase):
    def test_normalise(self):
        self.assertEqual(normalise("Heavy-Metals"), "heavy metal")
        self.assertEqual(normalise("Fertiliser"), "fertilizer")
        self.assertEqual(normalise("CO₂ Concentration"), "co2 concentration")

    def test_query_variants(self):
        self.assertEqual(me.query_variants("Soil Pollutants: Lead"), ["Soil Pollutants: Lead", "Lead"])
        self.assertEqual(me.query_variants("Polycyclic Aromatic Hydrocarbons (Pahs)"),
                         ["Polycyclic Aromatic Hydrocarbons (Pahs)", "Polycyclic Aromatic Hydrocarbons", "Pahs"])
        self.assertEqual(me.query_variants("Organic Hydrocarbons (E.G. Petrol"),
                         ["Organic Hydrocarbons (E.G. Petrol", "Organic Hydrocarbons", "E.G. Petrol"])

    def test_similarity(self):
        s = lambda a, b: me.similarity(normalise(a), normalise(b))
        self.assertEqual(s("Nitrogen Content", "nitrogen content"), 1.0)
        self.assertGreaterEqual(s("Electric Conductivity", "Electrical conductivity"), 0.8)
        self.assertGreaterEqual(s("Growth Chamber", "growth chamber study"), 0.8)       # narrower name: ok
        self.assertLess(s("Cl⁻ Concentration", "zinc concentration"), 0.8)             # different word
        self.assertLess(s("O₃ Concentration", "concentration of"), 0.8)                # missing word
        self.assertLess(s("Carbonyl Compounds", "one-carbon compound"), 0.8)           # shared stem only


class TestEnvFile(unittest.TestCase):
    def test_read_env_key(self):
        cases = {"BIOPORTAL_API_KEY=abc\n": "abc", "BIOPORTAL_API_KEY = 'abc'\n": "abc",
                 "# comment\nexport BIOPORTAL_API_KEY=\"abc\"\n": "abc", "OTHER=1\n": None, "BIOPORTAL_API_KEY=\n": None}
        with tempfile.TemporaryDirectory() as d:
            for text, expected in cases.items():
                path = Path(d) / ".env"
                path.write_text(text, encoding="utf-8")
                self.assertEqual(me.read_env_key(path), expected, text)
            self.assertIsNone(me.read_env_key(Path(d) / "missing.env"))

    def test_paths_shown_relative_to_repo(self):
        self.assertEqual(me.shown(REPO / "data" / "x.csv"), str(Path("data") / "x.csv"))

    def test_example_env_file_exists_and_is_not_ignored(self):
        text = (REPO / ".env.example").read_text(encoding="utf-8")
        self.assertIn("BIOPORTAL_API_KEY=", text)
        self.assertNotIn("=" + "abc", text)


class TestIds(unittest.TestCase):
    def test_curies(self):
        self.assertEqual(to_curie(OBO + "ENVO_00001998"), "ENVO:00001998")
        self.assertEqual(to_curie("http://purl.org/sfwo/SFWO_0000001"), "SFWO:0000001")
        self.assertEqual(to_curie("http://w3id.org/glosis/model/common/Texture",
                                  {"http://w3id.org/glosis/model/common/": "glosis_cm:"}), "glosis_cm:Texture")
        self.assertEqual(to_curie("http://purl.bioontology.org/ontology/MESH/D015032", fallback_prefix="MESH"),
                         "MESH:D015032")

    def test_home_ontology(self):
        self.assertEqual(ontology_of("NCBITaxon:562"), "NCBITAXON")
        self.assertEqual(ontology_of("glosis_cm:Texture"), "GLOSIS")


class TestCombine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        terms, cls.namespaces = load_local_ontologies(REPO / "data" / "ontology_matching" / "ontologies")
        cls.local = me.LocalSearch(terms)

    def run_entity(self, entity, fake, **kw):
        variants = me.query_variants(entity)
        hits = self.local.search(variants) + me.bioportal_hits(fake, variants, self.namespaces, None)
        return me.combine(hits, kw.get("min_score", 0.8), kw.get("max_matches", 10), kw.get("max_per_ontology", 3))

    def test_same_class_from_both_sources_is_listed_once(self):
        # TO:0020093 "nitrogen content" is in the local PTO file; BioPortal returns it via TO and via an importer
        fake = FakeBioPortal({"Nitrogen Content": [
            bp(OBO + "TO_0020093", "nitrogen content", "PTO"),
            bp(OBO + "TO_0020093", "nitrogen content", "AGRO"),
        ]})
        matches = self.run_entity("Nitrogen Content", fake)
        ids = [m["curie"] for m in matches]
        self.assertEqual(ids.count("TO:0020093"), 1)
        top = matches[0]
        self.assertEqual((top["curie"], top["ontology"]), ("TO:0020093", "TO"))   # home ontology, not importer
        self.assertEqual(top["sources"], {"local", "bioportal"})

    def test_non_obo_bioportal_term_uses_acronym(self):
        fake = FakeBioPortal({"Stool": [bp("http://purl.bioontology.org/ontology/MESH/D005243", "Feces", "MESH",
                                           synonyms=["Stool"])]})
        matches = self.run_entity("Stool", fake)
        self.assertIn(("MESH:D005243", "MESH"), [(m["curie"], m["ontology"]) for m in matches])

    def test_limits_and_cutoff(self):
        many = [bp(f"http://example.org/X/{i}", "zinc", f"ONT{i}") for i in range(15)]
        many.append(bp("http://example.org/Y/1", "zinc finger protein", "Y"))       # too dissimilar
        many.append(bp("http://example.org/Z/1", "zinc", "Z", obsolete=True))       # obsolete
        matches = self.run_entity("Zinc", FakeBioPortal({"Zinc": many}))
        self.assertEqual(len(matches), 10)
        self.assertNotIn("Y", {m["ontology"] for m in matches})
        self.assertNotIn("Z", {m["ontology"] for m in matches})

    def test_bioportal_search_is_limited_to_the_ontology_list(self):
        fake = FakeBioPortal({})
        me.bioportal_hits(fake, ["Zinc"], self.namespaces, ["CHEBI", "ENVO"])
        self.assertEqual(fake.scopes, [["CHEBI", "ENVO"]])

    def test_imported_class_from_unlisted_ontology_dropped(self):
        # a UO term returned while searching ENVO: home ontology UO is not on the list
        fake = FakeBioPortal({"Zinc": [bp(OBO + "UO_0000001", "zinc", "ENVO"), bp(OBO + "CHEBI_27363", "zinc", "ENVO")]})
        hits = me.bioportal_hits(fake, ["Zinc"], self.namespaces, ["ENVO", "CHEBI"])
        self.assertEqual([h["curie"] for h in hits], ["CHEBI:27363"])

    def test_default_list_is_the_sheet_list(self):
        self.assertEqual(len(me.BIOPORTAL_ONTOLOGIES), len(set(me.BIOPORTAL_ONTOLOGIES)))
        for acronym in ("ENVO", "CHEBI", "GO", "NCBITAXON", "MIXS", "OBOREL"):
            self.assertIn(acronym, me.BIOPORTAL_ONTOLOGIES)
        self.assertNotIn("LOINC", me.BIOPORTAL_ONTOLOGIES)

    def test_same_id_under_two_iris_listed_once(self):
        fake = FakeBioPortal({"Zinc": [bp("http://purl.bioontology.org/ontology/MESH/D015032", "Zinc", "MESH"),
                                       bp("http://example.org/copy/MESH/D015032", "Zinc", "MESH")]})
        matches = self.run_entity("Zinc", fake)
        self.assertEqual([m["curie"] for m in matches].count("MESH:D015032"), 1)

    def test_max_per_ontology(self):
        same = [bp(f"http://example.org/X/{i}", "zinc", "ONT") for i in range(5)]
        matches = self.run_entity("Zinc", FakeBioPortal({"Zinc": same}), max_per_ontology=2)
        self.assertEqual(sum(m["ontology"] == "ONT" for m in matches), 2)


class TestCommandLine(unittest.TestCase):
    def test_local_only_run(self):
        with tempfile.TemporaryDirectory() as d:
            src, out = Path(d) / "entities.csv", Path(d) / "out.csv"
            src.write_text("Nitrogen Content\nSoil Pollutants: Lead\n\nNitrogen Content\n", encoding="utf-8")
            subprocess.run([sys.executable, "-I", str(REPO / "scripts" / "ontology_matching" / "match_entities.py"), "--no-bioportal",
                            "--entities", str(src), "--output", str(out)], check=True, capture_output=True)
            with out.open(encoding="utf-8") as f:
                rows = list(csv.reader(f))
        self.assertEqual(rows[0][:2], ["entity", "match_1"])
        self.assertEqual([r[0] for r in rows[1:]], ["Nitrogen Content", "Soil Pollutants: Lead"])   # blanks/repeats dropped
        self.assertEqual(rows[1][1], "TO:0020093, nitrogen content, TO")


if __name__ == "__main__":
    unittest.main()
