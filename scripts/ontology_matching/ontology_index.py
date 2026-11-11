"""Read local ontology files (OWL RDF/XML, OBO, Turtle) into a searchable list of terms.

Adapted from search_terms.py in the microKG term-finder repository (github.com/scicrow/2026_biohackathon_p16_microKG).
Standard library only.

Each term is a dict:
    {"iri", "curie", "ontology", "label", "definition", "synonyms": [exact synonyms],
     "related": [related synonyms], "obsolete": bool, "source": "local"}
Narrow and broad synonyms are left out on purpose: they name a different concept
(OMP lists "acetate" as a narrow synonym of "carbon source utilization phenotype").
"""
import html
import re
import unicodedata
from pathlib import Path

OBO = "http://purl.obolibrary.org/obo/"

# Namespaces that are not OBO PURLs but still have a stable short prefix.
# (GloSIS prefixes are added at load time from the @prefix lines in its .ttl files.)
EXTRA_NAMESPACES = {"http://purl.org/sfwo/SFWO_": "SFWO:"}

# Which ontology a CURIE prefix belongs to, when it isn't simply the prefix itself.
PREFIX_TO_ONTOLOGY = {"glosis": "GLOSIS"}

# UK -> US spellings, so "fertiliser" matches "fertilizer"
SPELLING = [("sulph", "sulf"), ("fertilis", "fertiliz"), ("isation", "ization"), ("colour", "color"),
            ("haemo", "hemo"), ("aluminium", "aluminum"), ("oestr", "estr")]


# --------------------------------------------------------------------------- text helpers
def normalise(text):
    """Lower-case, drop punctuation, singularise plurals and use US spelling: 'Heavy-Metals' -> 'heavy metal'.
    NFKC turns sub/superscripts into plain characters first: 'CO₂' -> 'co2', 'Ca²⁺' -> 'ca2+'."""
    t = unicodedata.normalize("NFKC", text).lower().replace("_", " ").replace("-", " ").replace("/", " ")
    for uk, us in SPELLING:
        t = t.replace(uk, us)
    words = []
    for w in re.findall(r"[a-z0-9+]+", t):
        if len(w) > 3 and w.endswith("ies"):
            w = w[:-3] + "y"
        elif len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
            w = w[:-1]
        words.append(w)
    return " ".join(words)


def clean(text):
    """One line of plain text: HTML tags and entities removed, whitespace collapsed."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", text or ""))).strip()


# --------------------------------------------------------------------------- IDs
def to_curie(iri, namespaces=None, fallback_prefix=None):
    """Shorten an IRI to a CURIE.

    OBO PURL  http://purl.obolibrary.org/obo/ENVO_00001998 -> ENVO:00001998
    known namespace (SFWO, GloSIS)                          -> SFWO:0000001, glosis_cm:SoilDepth
    anything else                                           -> <fallback_prefix>:<last part of IRI>
    """
    if iri.startswith(OBO) and "_" in iri[len(OBO):]:
        prefix, local = iri[len(OBO):].split("_", 1)
        return f"{prefix}:{local}"
    # longest namespace first, so a specific namespace beats a general one
    for ns, prefix in sorted({**EXTRA_NAMESPACES, **(namespaces or {})}.items(), key=lambda x: -len(x[0])):
        if iri.startswith(ns) and len(iri) > len(ns):
            return prefix + iri[len(ns):]
    if fallback_prefix:
        return f"{fallback_prefix}:{re.split(r'[/#]', iri.rstrip('/#'))[-1]}"
    return iri


def ontology_of(curie):
    """Home ontology of a term from its CURIE prefix: 'ENVO:0000' -> 'ENVO', 'glosis_cm:X' -> 'GLOSIS'."""
    prefix = curie.split(":", 1)[0] if ":" in curie and not curie.startswith("http") else ""
    base = prefix.split("_", 1)[0].lower()          # glosis_cm -> glosis
    return PREFIX_TO_ONTOLOGY.get(base, prefix.upper())


# --------------------------------------------------------------------------- parsers
def parse_rdfxml(path):
    """OWLAPI/ROBOT RDF/XML. Top-level elements start at four spaces, so split there.
    (A naive <owl:Class>...</owl:Class> regex stops at nested anonymous classes and loses labels.)"""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    for chunk in re.split(r"\n    (?=<)", text):
        m = re.match(r'<owl:Class rdf:about="([^"]+)"', chunk)
        lab = re.search(r"\n        <rdfs:label[^>]*>(.*?)</rdfs:label>", chunk, re.S) if m else None
        if not lab:
            continue
        syn = lambda kind: [html.unescape(s).strip() for s in
                            re.findall(rf"<oboInOwl:has{kind}Synonym[^>]*>(.*?)</oboInOwl:", chunk, re.S)]
        yield {
            "iri": m.group(1),
            "label": html.unescape(lab.group(1)).strip(),
            "definition": clean(next(iter(re.findall(r"<obo:IAO_0000115[^>]*>(.*?)</obo:IAO_0000115>", chunk, re.S)), "")),
            "synonyms": syn("Exact"),
            "related": syn("Related"),
            "obsolete": bool(re.search(r"<owl:deprecated[^>]*>true<", chunk)),
        }


def parse_obo(path):
    """OBO flat file: one [Term] stanza per class."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    for block in text.split("\n[Term]\n")[1:]:
        block = block.split("\n[")[0]                       # stop at the next stanza
        i = re.search(r"^id: (\S+)", block, re.M)
        n = re.search(r"^name: (.*)", block, re.M)
        if not (i and n) or ":" not in i.group(1):
            continue
        yield {
            "iri": OBO + i.group(1).replace(":", "_", 1),
            "label": n.group(1).strip(),
            "definition": clean((re.search(r'^def: "(.*?)"(?: \[|$)', block, re.M) or [None, ""])[1]),
            "synonyms": re.findall(r'^synonym: "(.*?)" EXACT', block, re.M),
            "related": re.findall(r'^synonym: "(.*?)" RELATED', block, re.M),
            "obsolete": "is_obsolete: true" in block,
        }


def parse_ttl(path):
    """Turtle, as used by GloSIS: one subject per blank-line-separated block."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    prefixes = ttl_prefixes(text)
    for block in re.split(r"\n\s*\n", text):
        m = re.match(r"\s*(?:<([^>]+)>|(\S+):(\S+))\s", block)
        lab = re.search(r'(?:rdfs:label|skos:prefLabel)\s+"([^"]+)"', block)
        if not (m and lab):
            continue
        yield {
            "iri": m.group(1) or prefixes.get(m.group(2), "?:") + m.group(3),
            "label": lab.group(1),
            "definition": clean((re.search(r'(?:skos:definition|rdfs:comment)\s+"([^"]+)"', block) or [None, ""])[1]),
            "synonyms": re.findall(r'skos:altLabel\s+"([^"]+)"', block),
            "related": [],
            "obsolete": False,
        }


def ttl_prefixes(text):
    """@prefix name: <namespace> lines -> {name: namespace}."""
    return dict(re.findall(r"@prefix\s+(\S*):\s+<([^>]+)>", text))


# --------------------------------------------------------------------------- index
def load_local_ontologies(ontology_dir):
    """Parse every .owl/.obo/.ttl file under ontology_dir. Returns (terms, namespaces).

    `namespaces` maps namespace IRI -> CURIE prefix (from Turtle @prefix lines), and is reused
    to shorten BioPortal IRIs the same way, so the same term gets the same ID from both sources.
    """
    parsers = {".owl": parse_rdfxml, ".obo": parse_obo, ".ttl": parse_ttl}
    files = sorted(p for p in Path(ontology_dir).rglob("*") if p.suffix.lower() in parsers)

    # Collect GloSIS-style prefixes first (only project-specific ones; skip rdf:, owl:, xsd: ...)
    namespaces = {}
    for f in files:
        if f.suffix.lower() == ".ttl":
            for name, ns in ttl_prefixes(f.read_text(encoding="utf-8", errors="replace")).items():
                if name.lower().startswith("glosis"):
                    namespaces.setdefault(ns, f"{name}:")

    terms = {}
    for f in files:
        for t in parsers[f.suffix.lower()](f):
            if t["iri"] in terms:                           # same class imported into several files
                continue
            t["curie"] = to_curie(t["iri"], namespaces)
            t["ontology"] = ontology_of(t["curie"])
            t["source"] = "local"
            terms[t["iri"]] = t
    return list(terms.values()), namespaces


def ontology_titles(ontology_dir):
    """Ontology full names declared in the local files (dc:title), keyed by upper-case prefix, e.g. SFWO."""
    titles = {}
    for f in Path(ontology_dir).rglob("*.owl"):
        head = f.read_text(encoding="utf-8", errors="replace")[:20000]   # the ontology header is at the top
        m = re.search(r"<(?:dc|dcterms):title[^>]*>(.*?)</", head, re.S)
        if m:
            titles[f.stem.split("-")[0].upper()] = html.unescape(m.group(1)).strip()
    return titles
