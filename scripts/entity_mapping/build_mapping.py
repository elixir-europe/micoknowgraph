#!/usr/bin/env python3
"""Build the local entity -> ontology-concept mapping for the sheet tab
"Entities to Classes/Concept Mapping".

Inputs (all local, so the script reruns offline):
  data/entity_mapping/inputs/sheet_entities.json   cached A2:B600 of the sheet tab
  data/entity_mapping/inputs/details.csv           Thomas Crow's BioPortal matcher output
                                    (entity, rank, id, ontology, name, definition,
                                    rating, matched_text, source)
  entity_kinds_curated.yaml         hand-curated kind per entity, plus hand-assigned
                                    concepts (`assign:`) for entities no matcher finds
  data/entity_mapping/inputs/ols_cache.json        cached OLS responses (created on first online run)

Outputs (in <out>):
  entity_kinds.tsv                  one row per entity: kind and map yes/no
  entities_to_concepts.tsv          long format, one row per entity-concept pair

Only the standard library and PyYAML are used. Run it with `python3 -I` from
outside the downloads directory, e.g.

  python3 -I scripts/entity_mapping/build_mapping.py
  python3 -I scripts/entity_mapping/build_mapping.py --offline       # no OLS calls
  python3 -I scripts/entity_mapping/build_mapping.py --spot-check 15 # verify kept matches
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, OrderedDict
from pathlib import Path

import yaml

# --------------------------------------------------------------------------
# Kinds and ontology preference
# --------------------------------------------------------------------------

# Kinds that get an ontology class in the graph (map = yes), with the ordered
# list of ontologies to prefer. Thomas's `ontology` column spelling is used
# (upper case). Per-entity `prefer` in the curated file overrides the list.
KIND_PREFS: "OrderedDict[str, list[str]]" = OrderedDict([
    ("taxon",                 ["NCBITAXON"]),
    ("taxon-group",           ["NCBITAXON", "ECOCORE", "PCO"]),
    ("host",                  ["NCBITAXON"]),
    ("chemical",              ["CHEBI", "FOODON"]),
    ("drug-or-therapy",       ["CHEBI", "ARO", "NCIT"]),
    ("gene",                  ["ARO", "SO"]),
    ("gene-product",          ["ARO", "GO", "CHEBI"]),
    ("pathway-or-process",    ["GO", "ENVO"]),
    ("mobile-element",        ["SO"]),
    ("disease-or-phenotype",  ["MONDO", "HP", "PATO"]),
    ("anatomy-or-body-site",  ["UBERON"]),
    ("sample-material",       ["UBERON", "OBI"]),
    ("environment",           ["ENVO"]),
    ("event-or-exposure",     ["ENVO", "ECTO"]),
    ("food-or-diet",          ["FOODON"]),
    ("ecological-role",       ["ECOCORE", "PCO", "PATO"]),
    ("cell-state",            ["PATO", "GO", "CL"]),
    ("time-or-period",        ["ENVO", "NCIT"]),
    ("study-design",          ["OBI", "AGRO", "PECO"]),
])

# Kinds that live on Observations, Procedures, Associations or Study records
# in the schema rather than in the concept table (map = no).
NO_MAP_KINDS: "OrderedDict[str, str]" = OrderedDict([
    ("measurement",        "goes on Observation.observed_property, not a concept"),
    ("statistic",          "goes on Association (p_value, effect_size, direction), not a concept"),
    ("method-or-procedure", "goes on Procedure (assay, protocol, tool), not a concept"),
    ("data-artifact",      "a dataset or file; Study / PROV derived_from, not a concept"),
    ("identifier-scheme",  "an identifier namespace (alternate_ids / xrefs), not a concept"),
    ("meta",               "schema-level or query-level notion, not a concept"),
    ("junk-fragment",      "broken fragment of a multi-line sheet cell"),
])

KINDS = list(KIND_PREFS) + list(NO_MAP_KINDS)

# Tiers from docs/ontology-tiers.md. Extension = tier 3 plus OBO ontologies the
# tiers page has not placed yet (CL, PR). Mapped = tier 2 (accepted on input,
# translated to the core ID). Everything else is "other".
TIER_CORE = {"NCBITAXON", "ENVO", "UBERON", "MONDO", "CHEBI", "GO", "ARO", "SO",
             "PATO", "OBI", "ECO", "FOODON"}
TIER_EXTENSION = {"PO", "OMP", "GLOSIS", "AGRO", "ECTO", "NBO", "OBA", "PCO", "SFWO",
                  "OHMI", "ECOCORE", "PECO", "CL", "PR", "GENEPIO"}
TIER_MAPPED = {"DOID", "NCIT", "EFO", "MESH", "SNOMEDCT", "GTDB", "KEGG", "HP", "BERO",
               "UMLS", "HMDB", "MICRONT", "SILVA"}

# OLS4 ontology ids for the ontologies we may fill from.
OLS_IDS = {
    "NCBITAXON": "ncbitaxon", "CHEBI": "chebi", "ENVO": "envo", "UBERON": "uberon",
    "MONDO": "mondo", "GO": "go", "ARO": "aro", "SO": "so", "PATO": "pato", "OBI": "obi",
    "FOODON": "foodon", "ECTO": "ecto", "AGRO": "agro", "PECO": "peco", "PCO": "pco",
    "ECOCORE": "ecocore", "OMP": "omp", "NBO": "nbo", "OBA": "oba", "CL": "cl",
    "HP": "hp", "NCIT": "ncit", "EFO": "efo", "DOID": "doid", "PR": "pr",
    "MICRO": "micro", "IDO": "ido", "MESH": "mesh", "ICEO": "iceo",
}

# Proper CURIE prefix spelling for the output.
CURIE_PREFIX = {"NCBITAXON": "NCBITaxon"}

OLS_BASE = "https://www.ebi.ac.uk/ols4/api"
USER_AGENT = "micokg-mapping/0.1 (BioHackathon 2026 project 16; stdlib urllib)"

CONCEPT_COLUMNS = [
    "entity", "canonical_name", "cqs", "kind", "map", "preferred_ontology",
    "rank_adjusted", "concept_id", "ontology", "name", "definition", "rating_thomas",
    "source", "tier", "note", "status", "contributors", "reviewers",
]
KIND_COLUMNS = ["entity", "canonical_name", "cqs", "kind", "map", "rationale"]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def norm_cqs(s: str) -> str:
    """'CQ19, CQ 2' -> 'CQ 19, CQ 2' (keeps order, strips)."""
    s = clean(s)
    if not s:
        return ""
    parts = [p.strip() for p in s.split(",") if p.strip()]
    out = []
    for p in parts:
        m = re.fullmatch(r"(CQ|QC)\s*(\d+)", p, re.I)
        out.append(f"{m.group(1).upper()} {m.group(2)}" if m else p)
    return ", ".join(out)


def tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", s.lower()) if len(t) >= 3}


def tier_of(ontology: str) -> str:
    o = ontology.upper()
    if o in TIER_CORE:
        return "core"
    if o in TIER_EXTENSION:
        return "extension"
    if o in TIER_MAPPED:
        return "mapped"
    return "other"


def norm_id(concept_id: str, ontology: str) -> str:
    """NCBITAXON:976 -> NCBITaxon:976 ; EFO:EFO_0004601 -> EFO:0004601."""
    cid = clean(concept_id)
    if ":" in cid:
        prefix, local = cid.split(":", 1)
        if local.upper().startswith(prefix.upper() + "_"):
            local = local[len(prefix) + 1:]
        prefix = CURIE_PREFIX.get(prefix.upper(), prefix)
        return f"{prefix}:{local}"
    return cid


def one_line(s: str) -> str:
    return re.sub(r"[\t\r\n]+", " ", s or "").strip()


# --------------------------------------------------------------------------
# OLS client with a JSON cache
# --------------------------------------------------------------------------

class OLS:
    def __init__(self, cache_path: Path, offline: bool, sleep: float):
        self.cache_path = cache_path
        self.offline = offline
        self.sleep = sleep
        self.cache: dict = {}
        self.live_calls = 0
        if cache_path.exists():
            self.cache = json.loads(cache_path.read_text(encoding="utf-8"))

    def save(self) -> None:
        self.cache_path.write_text(json.dumps(self.cache, indent=0, sort_keys=True),
                                   encoding="utf-8")

    def get(self, url: str):
        if url in self.cache:
            return self.cache[url]
        if self.offline:
            return None
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            data = {"_error": e.code}
        except (urllib.error.URLError, TimeoutError) as e:  # keep going offline-ish
            print(f"  OLS unreachable ({e}); continuing without", file=sys.stderr)
            self.offline = True
            return None
        self.live_calls += 1
        self.cache[url] = data
        if self.live_calls % 25 == 0:
            self.save()
        time.sleep(self.sleep)
        return data

    def search(self, q: str, ontology: str, exact: bool, rows: int = 10) -> list[dict]:
        params = {
            "q": q, "ontology": ontology, "exact": "true" if exact else "false",
            "rows": rows,
            "fieldList": "iri,obo_id,short_form,label,description,ontology_name,synonym",
        }
        data = self.get(f"{OLS_BASE}/search?{urllib.parse.urlencode(params)}")
        if not data or "response" not in data:
            return []
        return [d for d in data["response"]["docs"] if d.get("ontology_name") == ontology]

    def term(self, ontology: str, iri: str) -> dict | None:
        enc = urllib.parse.quote(urllib.parse.quote(iri, safe=""), safe="")
        data = self.get(f"{OLS_BASE}/ontologies/{ontology}/terms/{enc}")
        if not data or "_error" in data or "label" not in data:
            return None
        return data

    def term_by_id(self, ontology: str, curie: str) -> dict | None:
        """Look a term up by its CURIE (used for hand-assigned concepts)."""
        prefix, local = curie.split(":", 1)
        # OLS spells some prefixes in lower case (mesh:D037521) and accepts
        # the short form (GO_0016984) too; try the variants in turn.
        variants = [{"obo_id": curie}, {"obo_id": f"{prefix.lower()}:{local}"},
                    {"short_form": f"{prefix}_{local}"}]
        for params in variants:
            q = urllib.parse.urlencode(params)
            data = self.get(f"{OLS_BASE}/ontologies/{ontology}/terms?{q}")
            terms = (data or {}).get("_embedded", {}).get("terms", []) if isinstance(data, dict) else []
            for t in terms:
                if "label" in t and not t.get("is_obsolete"):
                    return t
        return None


def doc_curie(doc: dict) -> str:
    cid = doc.get("obo_id") or doc.get("short_form", "").replace("_", ":", 1)
    return norm_id(cid, doc.get("ontology_name", "").upper())


def is_native(doc: dict, ontology: str) -> bool:
    """True when the term belongs to the ontology itself, not to one it imports
    (an OLS search in FOODON also returns the ONS 'diet' it imports)."""
    prefix = doc_curie(doc).split(":", 1)[0].upper()
    return prefix == ontology.upper()


def usable(docs: list[dict], ontology: str, wrong: set[str], native_only: bool) -> list[dict]:
    out = []
    for d in docs:
        if d.get("is_obsolete") or (d.get("label") or "").lower().startswith("obsolete"):
            continue
        if doc_curie(d) in wrong:
            continue
        if native_only and not is_native(d, ontology):
            continue
        out.append(d)
    return out


def pick_doc(docs: list[dict], query: str, mode: str) -> tuple[dict | None, str]:
    """mode 'label': a label equal to the query; mode 'synonym': an exact
    synonym (or, failing that, the first exact-search hit)."""
    ql = query.lower()
    if mode == "label":
        for d in docs:
            if (d.get("label") or "").lower() == ql:
                return d, "exact label"
        return None, ""
    for d in docs:
        syns = [s.lower() for s in d.get("synonym", [])]
        if ql in syns:
            return d, "exact synonym"
    return (docs[0], "exact search, first hit") if docs else (None, "")


def fuzzy_ok(query: str, label: str) -> bool:
    """Guard for non-exact hits: the label must share most words with the
    query. 'Sunset Syndrome' for 'sunset' or 'eggshell chorion gene
    amplification' for 'gene amplification' must not pass."""
    ql, ll = query.lower(), (label or "").lower()
    if ql == ll:
        return True
    qt, lt = tokens(ql), tokens(ll)
    if not qt or not lt:
        return False
    if len(qt) >= 2 and (ql in ll or ll in ql) and len(lt) <= len(qt) + 1:
        return True
    return len(qt & lt) / len(qt | lt) >= 0.67


def ols_lookup(ols: OLS, prefer: list[str], queries: list[str],
               wrong: set[str] | None = None, exact_only: bool = False) -> dict | None:
    """Passes over the preferred ontologies, in order: exact label on the
    ontology's own terms, exact synonym on its own terms, the same two on
    terms it imports, then a guarded non-exact search (own terms, then
    imported). Returns the term plus the ontology it really belongs to."""
    wrong = wrong or set()
    ontos = [o for o in prefer if o in OLS_IDS]
    # (exact search?, native terms only?, how to pick)
    passes = [(True, True, "label"), (True, True, "synonym"),
              (True, False, "label"), (True, False, "synonym")]
    if not exact_only:
        passes += [(False, True, "fuzzy"), (False, False, "fuzzy")]
    for exact, native_only, mode in passes:
        for o in ontos:
            for q in queries:
                docs = usable(ols.search(q, OLS_IDS[o], exact=exact), o, wrong, native_only)
                if exact:
                    doc, how = pick_doc(docs, q, mode)
                else:
                    doc, how = None, ""
                    for d in docs:
                        if fuzzy_ok(q, d.get("label", "")):
                            doc, how = d, "non-exact search; check"
                            break
                if not doc:
                    continue
                term = ols.term(OLS_IDS[o], doc["iri"])
                if term is None:  # IRI did not resolve
                    continue
                desc = term.get("description") or doc.get("description") or [""]
                cid = doc_curie(doc)
                real = cid.split(":", 1)[0].upper()
                if real != o:
                    how += f"; imported into {o}, native prefix {real}"
                return {
                    "concept_id": cid, "ontology": real, "searched_in": o,
                    "name": term.get("label", doc.get("label", "")),
                    "definition": one_line(desc[0] if desc else ""),
                    "how": f"{how} for '{q}'", "iri": doc["iri"],
                }
    return None


# --------------------------------------------------------------------------
# Wrong-match heuristics
# --------------------------------------------------------------------------

GENE_KINDS = {"gene", "gene-product"}


def suspicious(entity: str, canonical: str, kind: str, m: dict) -> str:
    name = m["name"]
    nl = name.lower()
    defn = (m.get("definition") or "").lower()
    if nl.startswith("obsolete"):
        return "obsolete term"
    if kind not in GENE_KINDS and re.search(r"\b(gene|allele)\b", nl):
        return "gene/allele name for a non-gene entity"
    if "inhibitor" in nl and "inhibitor" not in entity.lower() + canonical.lower():
        return "inhibitor of the entity, not the entity"
    for kw in ("leadership", "a country", "an organization", "an organisation"):
        if kw in defn:
            return f"definition says '{kw}'"
    et = tokens(entity) | tokens(canonical)
    nt = tokens(name)
    if et and nt and not any(a.startswith(b) or b.startswith(a) for a in et for b in nt):
        return "no word in common with the entity"
    return ""


# --------------------------------------------------------------------------
# Main build
# --------------------------------------------------------------------------

def load_sheet(path: Path) -> list[tuple[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for row in data["values"]:
        a = row[0] if row else ""
        b = row[1] if len(row) > 1 else ""
        if not clean(a):
            continue
        rows.append((clean(a), norm_cqs(b)))
    return rows


def load_thomas(path: Path) -> dict[str, list[dict]]:
    by_entity: dict[str, list[dict]] = {}
    with path.open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            key = clean(r["entity"]).lower()
            r = {k: one_line(v) for k, v in r.items()}
            try:
                r["rating_f"] = float(r.get("rating") or 0)
            except ValueError:
                r["rating_f"] = 0.0
            try:
                r["rank_i"] = int(r.get("rank") or 999)
            except ValueError:
                r["rank_i"] = 999
            by_entity.setdefault(key, []).append(r)
    for v in by_entity.values():
        v.sort(key=lambda r: r["rank_i"])
    return by_entity


def as_list(x) -> list[str]:
    if x is None:
        return []
    return [str(x)] if isinstance(x, str) else [str(i) for i in x]


def build(args) -> dict:
    downloads = Path(args.downloads).expanduser()
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    sheet = load_sheet(downloads / "sheet_entities.json")
    thomas = load_thomas(downloads / "details.csv")
    curated_raw = yaml.safe_load(Path(args.curated).expanduser().read_text(encoding="utf-8"))
    curated = {clean(k): (v or {}) for k, v in curated_raw.items()}

    missing = [e for e, _ in sheet if e not in curated]
    extra = sorted(set(curated) - {e for e, _ in sheet})
    if missing:
        sys.exit(f"ERROR: {len(missing)} sheet entities lack a curated kind: {missing}")
    if extra:
        print(f"WARNING: curated entries not in the sheet: {extra}", file=sys.stderr)
    bad = {e: c["kind"] for e, c in curated.items() if c.get("kind") not in KINDS}
    if bad:
        sys.exit(f"ERROR: unknown kinds: {bad}")

    ols = OLS(downloads / "ols_cache.json", offline=args.offline, sleep=args.ols_sleep)

    kind_rows, concept_rows = [], []
    stats = Counter()
    per_kind = Counter()
    rerank_changes, ols_filled, unresolved, wrong_top = [], [], [], []
    curated_assigned, no_class = [], []
    thomas_keys_used = set()

    for entity, cqs in sheet:
        c = curated[entity]
        kind = c["kind"]
        canonical = c.get("canonical", entity)
        canonical = entity if canonical is None else str(canonical)
        mappable = kind in KIND_PREFS
        prefer = [p.upper() for p in as_list(c.get("prefer"))] or KIND_PREFS.get(kind, [])
        rationale = c.get("rationale", "") or ""
        per_kind[kind] += 1

        kind_rows.append({
            "entity": entity, "canonical_name": canonical, "cqs": cqs, "kind": kind,
            "map": "yes" if mappable else "no", "rationale": rationale,
        })

        base = {
            "entity": entity, "canonical_name": canonical, "cqs": cqs, "kind": kind,
            "map": "yes" if mappable else "no",
            "preferred_ontology": " > ".join(prefer) if mappable else "",
            "status": "Not Started", "contributors": "", "reviewers": "",
        }

        if not mappable:
            concept_rows.append({**base, "rank_adjusted": "", "concept_id": "", "ontology": "",
                                 "name": "", "definition": "", "rating_thomas": "",
                                 "source": "", "tier": "",
                                 "note": f"{kind}: {NO_MAP_KINDS[kind]}"
                                         + (f"; {rationale}" if rationale else "")})
            stats["rows_map_no"] += 1
            continue

        stats["entities_map_yes"] += 1
        matches = thomas.get(entity.lower()) or thomas.get(canonical.lower()) or []
        if matches:
            thomas_keys_used.add(entity.lower())
            stats["entities_with_thomas_match"] += 1
        wrong = {norm_id(k, ""): v for k, v in (c.get("wrong") or {}).items()}

        scored = []
        for m in matches:
            onto = m["ontology"].upper()
            cid = norm_id(m["id"], onto)
            reason = wrong.get(cid) or wrong.get(m["id"]) or suspicious(entity, canonical, kind, m)
            if onto in prefer:
                score = prefer.index(onto)
            else:
                score = {"core": 20, "extension": 30, "mapped": 40, "other": 50}[tier_of(onto)]
            if reason:
                score += 100
            scored.append((score, -m["rating_f"], m["rank_i"], m, cid, onto, reason))
        scored.sort(key=lambda t: t[:3])
        if c.get("generic"):  # a wrong match adds nothing to a schema-level class
            scored = [s for s in scored if not s[6]]

        # "Covered" means Thomas already found a non-wrong match in a preferred
        # ontology that is core or extension tier; a preferred-but-mapped hit
        # (e.g. NCIT for a time period) still triggers an OLS look for the
        # core term.
        has_preferred = any(s[5] in prefer and not s[6]
                            and tier_of(s[5]) in ("core", "extension") for s in scored)
        candidates = []  # (score, order, row)

        # Hand-assigned concepts (curated `assign:`) go first. Each id is
        # looked up on OLS so the label and definition are the ontology's own
        # and the id is verified; an entry OLS cannot resolve must carry a name.
        assigns = c.get("assign") or []
        for i, a in enumerate(assigns):
            cid = norm_id(str(a["id"]), "")
            onto = cid.split(":", 1)[0].upper()
            term = ols.term_by_id(OLS_IDS[onto], cid) if onto in OLS_IDS else None
            if term is None and not a.get("name"):
                sys.exit(f"ERROR: {entity}: assigned {cid} not found on OLS and no name given")
            desc = (term or {}).get("description") or [""]
            how = a.get("match", "exact")
            note_bits = [f"curated {how} match"
                         + (" (verified by OLS term lookup)" if term else " (not on OLS; name given by hand)")]
            if a.get("note"):
                note_bits.append(str(a["note"]))
            if a.get("secondary"):
                note_bits.append(f"secondary id: {a['secondary']}")
            candidates.append((-10 + i, -1, {
                **base, "concept_id": cid, "ontology": onto,
                "name": (term or {}).get("label") or a.get("name", ""),
                "definition": one_line(desc[0] if desc else "") or one_line(str(a.get("definition", ""))),
                "rating_thomas": "", "source": "curated", "tier": tier_of(onto),
                "note": "; ".join(note_bits),
            }))
            stats["curated_assigned"] += 1
        if assigns:
            curated_assigned.append((entity, [norm_id(str(a["id"]), "") for a in assigns]))

        if not c.get("generic") and not has_preferred and not assigns:
            queries = as_list(c.get("query")) or []
            for q in (canonical, entity):
                if q and q not in queries:
                    queries.append(q)
            hit = ols_lookup(ols, prefer, queries, set(wrong), bool(c.get("exact_only")))
            if hit:
                onto = hit["ontology"]
                if onto in prefer:
                    score = prefer.index(onto)
                elif hit["searched_in"] in prefer:  # imported term found via a preferred ontology
                    score = 25
                else:
                    score = {"core": 20, "extension": 30, "mapped": 40, "other": 50}[tier_of(onto)]
                candidates.append((score, -1, {
                    **base, "concept_id": hit["concept_id"], "ontology": onto,
                    "name": hit["name"], "definition": hit["definition"], "rating_thomas": "",
                    "source": "ols-manual", "tier": tier_of(onto),
                    "note": "filled from OLS (" + hit["how"] + ")"
                            + ("; Thomas had no match" if not matches
                               else "; none of Thomas's matches is in a preferred core ontology"),
                }))
                ols_filled.append((entity, hit["concept_id"], hit["name"]))
                stats["ols_filled"] += 1

        for order, (score, _, _, m, cid, onto, reason) in enumerate(scored):
            note_bits = []
            if reason:
                note_bits.append(f"WRONG? {reason}")
            if m["rating_f"] < 0.9:
                note_bits.append(f"low matcher rating {m['rating']}")
            if tier_of(onto) == "mapped":
                note_bits.append("tier-2: keep as xref, map to the core ID")
            elif tier_of(onto) == "other":
                note_bits.append("not in any tier; informational")
            candidates.append((score, order, {
                **base, "concept_id": cid, "ontology": onto, "name": m["name"],
                "definition": m["definition"], "rating_thomas": m["rating"],
                "source": m["source"], "tier": tier_of(onto), "note": "; ".join(note_bits),
            }))

        candidates.sort(key=lambda t: (t[0], t[1]))

        # One row per concept: when Thomas's matcher and the OLS fill (or a
        # curated assignment) found the same id, keep the best-ranked row and
        # record the other source on it.
        by_id: dict[str, tuple] = {}
        deduped = []
        for cand in candidates:
            cid = cand[2]["concept_id"]
            if cid in by_id:
                kept = by_id[cid][2]
                extra_src = cand[2]["source"]
                if extra_src not in kept["source"].split("+"):
                    kept["source"] = kept["source"] + "+" + extra_src
                    kept["note"] = (kept["note"] + "; " if kept["note"] else "") + f"also found by {extra_src}"
                if not kept["rating_thomas"] and cand[2]["rating_thomas"]:
                    kept["rating_thomas"] = cand[2]["rating_thomas"]
                stats["duplicate_rows_merged"] += 1
                continue
            by_id[cid] = cand
            deduped.append(cand)
        rows_for_entity = [row for _, _, row in deduped[:5]]

        if not rows_for_entity:
            why = "generic schema class; values come from the preferred ontology" if c.get("generic") \
                else "no match found"
            if kind == "gene" and not c.get("generic"):
                why = "no ontology class: gene symbol, use ARO/CARD or UniProt/NCBI Gene identifier"
            if c.get("no_class"):
                why = ("no class in any OLS ontology (checked 2026-11-12); candidate for a local "
                       f"micokg class: {c['no_class']}")
            concept_rows.append({**base, "rank_adjusted": "", "concept_id": "", "ontology": "",
                                 "name": "", "definition": "", "rating_thomas": "",
                                 "source": "", "tier": "",
                                 "note": why + (f"; {rationale}" if rationale else "")})
            if c.get("generic"):
                stats["generic_unmapped"] += 1
            elif c.get("no_class"):
                stats["no_class"] += 1
                no_class.append(entity)
            else:
                stats["unresolved"] += 1
                unresolved.append(entity)
            continue

        for i, r in enumerate(rows_for_entity, 1):
            r["rank_adjusted"] = str(i)
            if i == 1 and rationale and not r["note"].endswith(rationale):
                r["note"] = (r["note"] + "; " if r["note"] else "") + rationale
            concept_rows.append(r)

        top = rows_for_entity[0]
        if matches:
            thomas_top = norm_id(matches[0]["id"], matches[0]["ontology"])
            if "ols-manual" in top["source"].split("+"):
                stats["top1_is_ols_fill"] += 1
            elif top["concept_id"] != thomas_top:
                stats["top1_changed_by_rerank"] += 1
                rerank_changes.append((entity, thomas_top, top["concept_id"]))
        if top["note"].startswith("WRONG?"):
            wrong_top.append((entity, top["concept_id"], top["name"]))
            stats["top1_still_flagged"] += 1

    ols.save()

    unused = sorted(set(thomas) - thomas_keys_used - {e.lower() for e, _ in sheet})
    stats["entities"] = len(sheet)
    stats["rows_concepts"] = len(concept_rows)
    stats["thomas_entities_not_in_sheet"] = len(unused)
    stats["ols_live_calls"] = ols.live_calls

    with (out / "entity_kinds.tsv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=KIND_COLUMNS, delimiter="\t", lineterminator="\n")
        w.writeheader()
        w.writerows(kind_rows)
    with (out / "entities_to_concepts.tsv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CONCEPT_COLUMNS, delimiter="\t", lineterminator="\n")
        w.writeheader()
        for r in concept_rows:
            w.writerow({k: one_line(str(r.get(k, ""))) for k in CONCEPT_COLUMNS})

    report = {
        "stats": dict(stats),
        "per_kind": dict(per_kind.most_common()),
        "rerank_top1_changes": rerank_changes,
        "ols_filled": ols_filled,
        "curated_assigned": curated_assigned,
        "no_class": no_class,
        "unresolved": unresolved,
        "top1_still_flagged": wrong_top,
        "thomas_entities_not_in_sheet": unused,
    }
    (downloads / "build_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return report


# --------------------------------------------------------------------------
# Spot check of kept Thomas matches against OLS labels
# --------------------------------------------------------------------------

def spot_check(args) -> None:
    downloads = Path(args.downloads).expanduser()
    out = Path(args.out).expanduser()
    ols = OLS(downloads / "ols_cache.json", offline=args.offline, sleep=args.ols_sleep)
    rows = []
    with (out / "entities_to_concepts.tsv").open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            if r["source"] != "ols-manual" and r["concept_id"] and r["ontology"] in OLS_IDS:
                rows.append(r)
    rnd = random.Random(args.seed)
    sample = rnd.sample(rows, min(args.spot_check, len(rows)))
    results = []
    for r in sample:
        prefix, local = r["concept_id"].split(":", 1)
        iri = f"http://purl.obolibrary.org/obo/{prefix}_{local}"
        if prefix == "EFO":
            iri = f"http://www.ebi.ac.uk/efo/EFO_{local}"
        term = ols.term(OLS_IDS[r["ontology"]], iri)
        ok = bool(term) and (term.get("label", "").lower() == r["name"].lower())
        results.append({"entity": r["entity"], "concept_id": r["concept_id"],
                        "thomas_name": r["name"],
                        "ols_label": term.get("label") if term else None,
                        "resolves": bool(term), "label_matches": ok})
        print(f"{'OK ' if ok else 'XX '} {r['entity']!r} {r['concept_id']} "
              f"thomas={r['name']!r} ols={(term or {}).get('label')!r}")
    ols.save()
    (downloads / "spot_check.json").write_text(json.dumps(results, indent=1), encoding="utf-8")


def main() -> None:
    here = Path(__file__).resolve().parent
    default_root = Path(__file__).resolve().parents[2] / "data" / "entity_mapping"
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--downloads", default=str(default_root / "inputs"),
                   help="directory with sheet_entities.json, details.csv and the OLS cache")
    p.add_argument("--curated", default=str(here / "entity_kinds_curated.yaml"),
                   help="hand-curated kinds YAML")
    p.add_argument("--out", default=str(default_root), help="output directory for the TSVs")
    p.add_argument("--offline", action="store_true",
                   help="never call OLS; use only the cached responses")
    p.add_argument("--ols-sleep", type=float, default=0.1, help="pause between live OLS calls")
    p.add_argument("--spot-check", type=int, metavar="N", default=0,
                   help="instead of building, verify N random kept matches against OLS")
    p.add_argument("--seed", type=int, default=2026, help="random seed for --spot-check")
    args = p.parse_args()

    if args.spot_check:
        spot_check(args)
        return
    report = build(args)
    print(json.dumps(report["stats"], indent=1))
    print("per kind:", json.dumps(report["per_kind"], indent=1))
    print(f"re-ranked top-1 changes: {len(report['rerank_top1_changes'])}")
    print(f"filled from OLS: {len(report['ols_filled'])}")
    print(f"unresolved (map=yes, nothing found): {len(report['unresolved'])}: {report['unresolved']}")
    print(f"top-1 still flagged WRONG?: {report['top1_still_flagged']}")
    print(f"Thomas entities not in the sheet: {report['thomas_entities_not_in_sheet']}")


if __name__ == "__main__":
    main()
