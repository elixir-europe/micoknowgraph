# Data

Document datasets used or produced by the project, including their sources,
versions, licenses, access conditions, and relevant provenance.

Do not commit sensitive, restricted, or large datasets without checking their
access conditions and agreeing on an appropriate storage location. Small,
shareable test fixtures belong under [tests/](../tests/).

## Layout

Data is grouped by pipeline step, mirroring [scripts/](../scripts/) and [tests/](../tests/):
the data for `scripts/ontology_matching/` is in `data/ontology_matching/`.

### `ontology_matching/`

| Path                                     | What it is                                                                                                                                                       |
| ---------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `entities/test_entities.csv`           | 553 entities from the "Entities from Competency Questions" sheet of the MiCoKnowGraph coordination workbook (column "Entity in the CQs"), one per row, no header |
| `ontologies/`                          | Local ontology files searched by `match_entities.py`: OMP, PTO (TO), PECO, ECOCORE, RO, SFWO and GloSIS (Turtle modules)                                       |
| `matches/ontology_matches.csv`         | Output: one row per entity, up to 10 matches as `ID, name, ontology`                                                                                           |
| `matches/ontology_matches_details.csv` | Same matches in long format, with score and source, for review                                                                                                   |
| `cache/`                               | BioPortal API responses (git-ignored, regenerated automatically)                                                                                                 |

## Development Notes

The following ontologies aren't in BioPortal: 

OIO, OpenBiodiv-O, OWL, RDF-DataCube, SKOS, SOSA, KEGG and WHOW. 

SFWO, GloSiS and TO aren't in BioPortal either, but the local directory covers them:

`micoknowgraph/data/ontology_matching/ontologies`.
