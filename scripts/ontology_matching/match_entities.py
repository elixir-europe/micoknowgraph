#!/usr/bin/env python3
"""Match competency-question entities to ontology classes.

Step 1  Read the entities (first column of a CSV, one entity per row, no header).
Step 2  Fuzzy-search each entity in
          - BioPortal (online; needs a free API key: BIOPORTAL_API_KEY in the environment or in <repo>/.env), and
          - the ontology files stored in data/ontology_matching/ontologies/ (offline).
        Both sources are scored with the same similarity measure (0-1), so their results can be ranked together.
Step 3  Write one row per entity:  entity, match_1, ..., match_10
        Each match reads "ID, name, ontology": the class ID, its label, and the ontology abbreviation,
        e.g. "TO:0020093, nitrogen content, TO".
        A class found by both sources appears once. Only matches at or above --min-score are kept,
        so many entities get fewer than 10 matches, and some get none.
        BioPortal is searched only in the ontologies listed in BIOPORTAL_ONTOLOGIES (from the project sheet).

Usage (from the repository root):
    cp .env.example .env                               # then put your key in .env (repository root, git-ignored)
                                                       # key: https://bioportal.bioontology.org/account
    python3 scripts/ontology_matching/match_entities.py
    python3 scripts/ontology_matching/match_entities.py --entities my_entities.csv --output my_matches.csv
    python3 scripts/ontology_matching/match_entities.py --no-bioportal   # local ontology files only
    python3 scripts/ontology_matching/match_entities.py --details data/ontology_matching/matches/ontology_matches_details.csv   # labels and scores, for review

Standard library only (Python 3.9+).
"""
import argparse
import csv
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # sibling modules, also under `python3 -I`
from bioportal import BioPortal                             # noqa: E402
from ontology_index import load_local_ontologies, normalise, ontology_of, to_curie  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "data" / "ontology_matching"                   # all data for this step lives here
DEFAULT_ENTITIES = DATA / "entities" / "test_entities.csv"
DEFAULT_OUTPUT = DATA / "matches" / "ontology_matches.csv"
DEFAULT_ONTOLOGY_DIR = DATA / "ontologies"
DEFAULT_CACHE = DATA / "cache" / "bioportal"

# BioPortal is searched only in these ontologies: the "short name" column of the "Task 2 - Ontologies"
# sheet in the MiCoKnowGraph coordination workbook. Edit the list to add or remove ontologies.
# BioPortal acronyms are case sensitive and a few differ from the sheet (noted below).
BIOPORTAL_ONTOLOGIES = [
    "AGRO", "ARO", "BERO", "BFO", "BIOLINK", "CHEAR", "CHEBI", "CL", "COB", "DC", "DCT", "DOID", "ECO",
    "ECOCORE", "ECTO", "EFO", "ENVO", "EXO", "FAO", "FOODON", "GENEPIO", "GO", "HHEAR", "IAO", "JERM",
    "MESH", "MICRONT", "MONDO", "NBO", "NCBITAXON", "NCIT", "OBI", "OBOE", "OHMI", "OMO", "OMP", "PATO",
    "PCO", "PECO", "PO", "PROVO", "QUDT", "RDFS", "SCHEMA", "SIO", "SNOMEDCT", "SO", "SWEET", "TIME",
    "TYPON", "UBERON", "VARIO",
    "GEOSPARQL",   # sheet: GeoSPARQL
    "MIXS",        # sheet: MIxS
    "OBOREL",      # sheet: OBOREL (Relations Ontology)
    "HP",          # sheet: HPO. BioPortal's "HPO" is the Hypertension Ontology; the Human Phenotype Ontology is "HP"
]
# On the sheet but not in BioPortal, so not searched online: OIO, OpenBiodiv-O, OWL, RDF-DataCube, SKOS,
# SOSA, KEGG, WHOW. SFWO, GloSiS and TO are not in BioPortal either, but are found through the local
# ontology files in data/ontology_matching/ontologies/.

# BioPortal acronyms whose terms use a different ID prefix (BioPortal's OBOREL holds RO_ terms)
OBO_PREFIX_ALIASES = {"OBOREL": "RO"}

# A perfect match to a related (not exact) synonym scores this much
RELATED_WEIGHT = 0.9

# Score multiplier when a word of the entity has no counterpart in the class name
MISSING_WORD_PENALTY = 0.9

# Words that carry no meaning on their own; ignored when looking up candidate terms by shared words
STOPWORDS = {"an", "and", "as", "at", "by", "e", "eg", "for", "from", "g", "in", "into", "is", "of",
             "on", "or", "the", "to", "via", "with"}


# --------------------------------------------------------------------------- Step 1: entities
def read_entities(path):
    """First column of the CSV, stripped, blanks and repeats removed, original order kept."""
    seen, out = set(), []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            text = row[0].strip() if row else ""
            if text and text not in seen:
                seen.add(text)
                out.append(text)
    return out


def query_variants(entity):
    """Search strings for one entity. Besides the full text, try the parts of composite entities:
        'Soil Pollutants: Lead'                    -> also 'Lead'
        'Polycyclic Aromatic Hydrocarbons (Pahs)'  -> also 'Polycyclic Aromatic Hydrocarbons' and 'Pahs'
    """
    variants = [entity]
    if ":" in entity:
        variants.append(entity.rsplit(":", 1)[1])
    no_paren = re.sub(r"\([^)]*\)?", " ", entity)              # also handles a missing ')'
    variants.append(no_paren)
    variants += re.findall(r"\(([^)]+)\)?", entity)
    clean = [" ".join(v.split()).strip(" .,;") for v in variants]
    return [v for v in dict.fromkeys(clean) if v]               # drop empties and repeats


# --------------------------------------------------------------------------- Step 2: similarity
def content_words(text_n):
    """Words of a normalised string minus stopwords (all words if nothing else is left)."""
    words = text_n.split()
    return [w for w in words if w not in STOPWORDS] or words


def words_match(a, b):
    """Two words count as the same if equal, or (for longer words) nearly equal: catches typos
    ('electric' ~ 'electrical') but not different words sharing a stem ('carbonyl' vs 'carbon')."""
    return a == b or (min(len(a), len(b)) >= 6 and SequenceMatcher(None, a, b).ratio() >= 0.88)


def similarity(query_n, name_n):
    """0-1 similarity of two normalised strings. 1.0 = same after normalising.

    Average of two parts, so a match needs both similar spelling AND the same words:
      character part  better of plain and word-order-independent character similarity
                      ('nitrogen soil' ~ 'soil nitrogen')
      word part       share of words on either side that have a match on the other side
                      ('cl concentration' vs 'zinc concentration' = 2 of 4 words -> 0.5)
    If a word of the query is missing from the name, the name is probably broader or different
    ('o3 concentration' vs 'concentration'), so the score is reduced by MISSING_WORD_PENALTY.
    Extra words in the name ('growth chamber' vs 'growth chamber study') are not penalised further.
    """
    if not query_n or not name_n:
        return 0.0
    if query_n == name_n:
        return 1.0
    sm = SequenceMatcher(None, query_n, name_n)
    if sm.real_quick_ratio() < 0.6:                            # cheap upper bound: skip clear non-matches
        return 0.0
    qw, nw = content_words(query_n), content_words(name_n)
    q_found = sum(any(words_match(q, n) for n in nw) for q in qw)
    n_found = sum(any(words_match(n, q) for q in qw) for n in nw)
    word_part = (q_found + n_found) / (len(qw) + len(nw))
    if word_part == 0:
        return 0.0
    char_part = max(sm.ratio(), SequenceMatcher(None, " ".join(sorted(qw)), " ".join(sorted(nw))).ratio())
    score = (char_part + word_part) / 2
    return score if q_found == len(qw) else score * MISSING_WORD_PENALTY


def best_name_score(variants_n, label, synonyms, related=()):
    """Best score of any query variant against the label, an exact synonym, or a related synonym
    (related synonyms count for RELATED_WEIGHT of a full match). Returns (score, matched_name)."""
    best = (0.0, "")
    names = [(n, 1.0) for n in [label] + list(synonyms)] + [(n, RELATED_WEIGHT) for n in related]
    for name, weight in names:
        name_n = normalise(name)
        for q in variants_n:
            s = similarity(q, name_n) * weight
            if s > best[0]:
                best = (s, name)
    return best


class LocalSearch:
    """Fuzzy search over the local ontology files.
    A word index finds the terms that share at least one word with the query; only those are scored."""

    def __init__(self, terms):
        self.terms = [t for t in terms if not t["obsolete"]]
        self.by_word = {}
        for i, t in enumerate(self.terms):
            for name in [t["label"]] + t["synonyms"] + t["related"]:
                for w in set(normalise(name).split()) - STOPWORDS:
                    self.by_word.setdefault(w, set()).add(i)

    def search(self, variants):
        variants_n = [normalise(v) for v in variants]
        candidates = set()
        for q in variants_n:
            for w in set(q.split()) - STOPWORDS:
                candidates |= self.by_word.get(w, set())
        hits = []
        for i in candidates:
            t = self.terms[i]
            score, matched = best_name_score(variants_n, t["label"], t["synonyms"], t["related"])
            if score > 0:
                hits.append({**t, "score": score, "matched": matched})
        return hits


def bioportal_hits(client, variants, namespaces, ontologies):
    """Search BioPortal, within the given ontology acronyms only, for each query variant
    and score the results like local ones."""
    variants_n = [normalise(v) for v in variants]
    # An ontology can contain classes imported from others (ENVO holds PR and UO terms). Those are
    # reported under their home ontology, so keep them only if that ontology is on the list too.
    allowed = {OBO_PREFIX_ALIASES.get(o, o).upper() for o in ontologies} if ontologies else None   # None = no limit
    hits = []
    for v in variants:
        for r in client.search(v, ontologies=ontologies):
            if r["obsolete"] or not r["iri"]:
                continue
            curie = to_curie(r["iri"], namespaces)        # same ID scheme as for local terms
            if curie != r["iri"]:
                ontology, from_acronym = ontology_of(curie), False   # OBO term: home ontology, not importer
                if allowed and ontology not in allowed:
                    continue
            else:                                          # other IRIs: ACRONYM:last-part-of-IRI
                curie = to_curie(r["iri"], fallback_prefix=r["acronym"])
                ontology, from_acronym = r["acronym"], True
            score, matched = best_name_score(variants_n, r["label"], r["synonyms"])
            hits.append({"iri": r["iri"], "curie": curie, "ontology": ontology, "label": r["label"],
                         "source": "bioportal", "score": score, "matched": matched,
                         "from_acronym": from_acronym})
    return hits


# --------------------------------------------------------------------------- Step 3: combine and rank
def combine(hits, min_score, max_matches, max_per_ontology):
    """Merge hits for one entity: one entry per ID (same class from BioPortal and local files = one match),
    keep scores >= min_score, best first, at most max_per_ontology per ontology and max_matches overall."""
    merged = {}
    for h in hits:
        if h["score"] < min_score:
            continue
        key = h["curie"].upper()                              # ID, so copies of a class under other IRIs merge too
        m = merged.get(key)
        if m is None:
            merged[key] = {**h, "sources": {h["source"]}}
        else:
            m["sources"].add(h["source"])
            if h["score"] > m["score"]:
                m.update(score=h["score"], matched=h["matched"])
            if h["source"] == "local":                        # local files give the most reliable ID/ontology
                m.update(curie=h["curie"], ontology=h["ontology"], from_acronym=False)

    # Rank: score, then found by both sources, then label over synonym, then shorter label
    ranked = sorted(merged.values(), key=lambda m: (-round(m["score"], 2), -len(m["sources"]),
                                                    m["matched"] != m["label"], len(m["label"])))
    out, per_ontology = [], {}
    for m in ranked:
        if per_ontology.get(m["ontology"], 0) >= max_per_ontology:
            continue
        per_ontology[m["ontology"]] = per_ontology.get(m["ontology"], 0) + 1
        out.append(m)
        if len(out) == max_matches:
            break
    return out


def shown(path):
    """Path as shown in messages: relative to the repository when inside it, so output does not depend on where it is cloned."""
    try:
        return str(Path(path).resolve().relative_to(REPO))
    except ValueError:
        return str(path)


def read_env_key(path, name="BIOPORTAL_API_KEY"):
    """Value of NAME from a .env file (lines like NAME=value; spaces and quotes allowed). None if absent."""
    p = Path(path)
    if not p.is_file():
        return None
    for line in p.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip().removeprefix("export ").strip() == name:
            return value.strip().strip("'\"") or None
    return None


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--entities", default=str(DEFAULT_ENTITIES), help="input CSV, entity in column 1 (default: %(default)s)")
    ap.add_argument("--output", default=str(DEFAULT_OUTPUT), help="output CSV (default: %(default)s)")
    ap.add_argument("--ontology-dir", default=str(DEFAULT_ONTOLOGY_DIR), help="local ontology files (default: %(default)s)")
    ap.add_argument("--max-matches", type=int, default=10, help="most matches per entity (default: 10)")
    ap.add_argument("--max-per-ontology", type=int, default=2, help="most matches per entity from one ontology (default: 2)")
    ap.add_argument("--min-score", type=float, default=0.8, help="similarity cut-off, 0-1 (default: 0.8)")
    ap.add_argument("--api-key", help="BioPortal API key (default: $BIOPORTAL_API_KEY, then the .env file)")
    ap.add_argument("--env-file", help="file holding BIOPORTAL_API_KEY=... (default: .env in the repository root)")
    ap.add_argument("--bioportal-ontologies", default=",".join(BIOPORTAL_ONTOLOGIES),
                    help="BioPortal acronyms to search, comma-separated (default: BIOPORTAL_ONTOLOGIES in this script)")
    ap.add_argument("--workers", type=int, default=4, help="parallel BioPortal requests (default: 4)")
    ap.add_argument("--no-bioportal", action="store_true", help="search the local ontology files only")
    ap.add_argument("--cache-dir", default=str(DEFAULT_CACHE), help="where BioPortal responses are cached (default: %(default)s)")
    ap.add_argument("--details", help="also write a long-format CSV with label, score and source of every match")
    args = ap.parse_args()

    # Step 1: entities
    entities = read_entities(args.entities)
    print(f"Step 1: {len(entities)} entities from {shown(args.entities)}", file=sys.stderr)

    # Local ontology files
    terms, namespaces = load_local_ontologies(args.ontology_dir)
    local = LocalSearch(terms)
    bioportal_names = {}
    print(f"        {len(terms)} terms loaded from {shown(args.ontology_dir)}", file=sys.stderr)

    # BioPortal (optional)
    client = None
    if not args.no_bioportal:
        env_file = Path(args.env_file) if args.env_file else REPO / ".env"
        api_key = args.api_key or os.environ.get("BIOPORTAL_API_KEY") or read_env_key(env_file)
        if api_key:
            client = BioPortal(api_key, cache_dir=args.cache_dir)
            bioportal_names = client.ontology_names()          # only used to check which acronyms BioPortal has
        else:
            print(f"warning: no BioPortal API key found. Add BIOPORTAL_API_KEY=... to {shown(env_file)} "
                  "(see .env.example), set it as an environment variable, or use --api-key. "
                  "Searching local files only.", file=sys.stderr)
    # BioPortal rejects a whole search if one acronym is unknown, so only send acronyms it has
    bp_ontologies = [x.strip() for x in args.bioportal_ontologies.split(",") if x.strip()]
    if client:
        unknown = [o for o in bp_ontologies if o not in bioportal_names]
        if unknown:
            print(f"warning: not in BioPortal, skipped: {', '.join(unknown)}", file=sys.stderr)
        bp_ontologies = [o for o in bp_ontologies if o in bioportal_names]
        if not bp_ontologies:
            sys.exit("error: none of the BioPortal ontologies exist (acronyms are case sensitive)")

    # Step 2: search one entity in both sources and keep the best matches
    def match_one(entity):
        variants = query_variants(entity)
        hits = local.search(variants)
        if client:
            hits += bioportal_hits(client, variants, namespaces, bp_ontologies)
        return combine(hits, args.min_score, args.max_matches, args.max_per_ontology)

    # BioPortal requests are slow (~1 s each), so entities are searched in parallel; map() keeps input order
    with ThreadPoolExecutor(max_workers=args.workers if client else 1) as pool:
        results = []
        for n, matches in enumerate(pool.map(match_one, entities), 1):
            results.append(matches)
            if n % 50 == 0:
                print(f"        {n}/{len(entities)} entities searched", file=sys.stderr)

    # Step 3: one row per entity; "ID, name, ontology" per match
    rows, details, n_none = [], [], 0
    for entity, matches in zip(entities, results):
        n_none += not matches
        rows.append([entity] + [f"{m['curie']}, {m['label']}, {m['ontology']}" for m in matches])
        for rank, m in enumerate(matches, 1):
            details.append([entity, rank, m["curie"], m["ontology"], m["label"],
                            m["matched"], f"{m['score']:.2f}", "+".join(sorted(m["sources"]))])

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["entity"] + [f"match_{i}" for i in range(1, args.max_matches + 1)])
        w.writerows(rows)
    print(f"Step 3: wrote {shown(args.output)} ({len(rows) - n_none} entities with matches, {n_none} without)",
          file=sys.stderr)

    if args.details:
        with open(args.details, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["entity", "rank", "id", "ontology", "label", "matched_name", "score", "source"])
            w.writerows(details)
        print(f"        wrote {shown(args.details)}", file=sys.stderr)


if __name__ == "__main__":
    main()
