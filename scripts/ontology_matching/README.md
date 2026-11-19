# ontology_matching: match CQ entities to ontology classes

Fuzzy-matches each entity in a CSV against BioPortal and the ontology files in
[data/ontology_matching/ontologies/](../../data/ontology_matching/ontologies/), and writes the best matches per entity.

```sh
# one-off, from the repository root: create .env from the template and put your key in it
cp .env.example .env        # then edit .env; it is git-ignored (key: https://bioportal.bioontology.org/account)

python3 scripts/ontology_matching/match_entities.py                                  # data/ontology_matching/entities/test_entities.csv -> data/ontology_matching/matches/ontology_matches.csv
python3 scripts/ontology_matching/match_entities.py --entities other.csv --output other_matches.csv
python3 scripts/ontology_matching/match_entities.py --no-bioportal                   # offline: local ontology files only
python3 scripts/ontology_matching/match_entities.py --details data/ontology_matching/matches/ontology_matches_details.csv   # label, score and source per match
```

- **Input:** a CSV with one entity per row in the first column and no header. Example: `data/ontology_matching/entities/test_entities.csv`
- **Output:** one row per entity: `entity, match_1 … match_10`. Each match reads
  `ID, name, ontology`: the class ID, its label and the ontology abbreviation,
  e.g. `TO:0020093, nitrogen content, TO`.
- **How matching works:** the entity text (and its parts, e.g. `Lead` from
  `Soil Pollutants: Lead`) is compared with each class label and synonym. The
  score (0–1) needs both similar spelling and the same words. Only scores at or
  above `--min-score` (0.8) are kept, so many entities get fewer than 10 matches.
- **No duplicates:** a class found by both BioPortal and the local files, or
  imported into several BioPortal ontologies, appears once, under its home
  ontology (e.g. `CHEBI:…` found inside ENVO is listed as ChEBI).
- **Ranking:** best score first, then classes found by both sources, then
  label over synonym, shorter label, and finally label and ID alphabetically,
  so the output is the same on every run. At most 10 matches per entity
  (`--max-matches`) and 2 per ontology (`--max-per-ontology`).
- **Ties:** matches with the same score as the last one allowed are all kept,
  even if that goes over a limit: up to 5 extra per ontology and up to 20 extra
  overall, so one ontology cannot crowd out the others. If more tied
  matches exist, the script prints `warning: <entity>: n terms excluded as many
  extra matches of equal score were found`. The matches CSV gets extra
  `match_N` columns when an entity has more than 10 matches.
- **Which ontologies BioPortal searches:** only those in `BIOPORTAL_ONTOLOGIES`
  at the top of `match_entities.py`. It is the "short name" column of the
  "Task 2 - Ontologies" sheet, written out as a list; edit it to add or remove
  ontologies, or override it for one run with `--bioportal-ontologies CHEBI,ENVO`.
  Sheet entries BioPortal doesn't have (OWL, SKOS, KEGG ...) are not searched
  online; SFWO, GloSiS and TO are covered by the local files.
- **Speed:** a first run makes about 1,400 BioPortal requests (4 in parallel).
  Responses are cached in `data/ontology_matching/cache/bioportal/` (git-ignored), so reruns take
  seconds.
- **Dependencies:** Python 3.9+, standard library only.

Matches are suggestions for curation, not final mappings: check each one's
definition before using it.

Helper modules: `ontology_index.py` (parses OWL/OBO/Turtle files) and
`bioportal.py` (BioPortal API client with caching).
