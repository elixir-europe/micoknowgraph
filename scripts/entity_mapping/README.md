# Entity → concept mapping

Draft for the coordination sheet tab **"Entities to Classes/Concept Mapping"**, 11 November 2026; curated assignments for the last unresolved entities added 12 November.

Built from the sheet's own entity list (A2:B600, 553 non-empty rows), Thomas Crow's BioPortal matcher output (PR #2, copied to `data/entity_mapping/inputs/details.csv`), the project's ontology tiers (the "core / mapped / extension" tiers proposed in the Ontologies sheet discussion) and, where those left a gap, the EBI OLS4 API.

## Files

| File | What it is |
|---|---|
| `data/entity_mapping/entity_kinds.tsv` | One row per sheet entity (553). Columns: `entity, canonical_name, cqs, kind, map, rationale`. The `kind` decides whether the entity gets an ontology class at all. |
| `data/entity_mapping/entities_to_concepts.tsv` | Long format, one row per entity–concept pair (1193 rows). Up to 5 candidate concepts per mappable entity, re-ranked by the project's ontology preference; one empty row per non-mappable entity saying where it lives in the schema instead. |
| `data/entity_mapping/inputs/` | Inputs and caches: `details.csv`, `unmatched_entities.csv` (Thomas's output), `sheet_entities.json` (the sheet range), `ols_cache.json` (every OLS response used, so every added IRI is verifiable offline), `build_report.json` (counts and lists from the last run), `spot_check.json`. |
| `scripts/entity_mapping/build_mapping.py` | The script (stdlib + PyYAML). `--help` works; `--offline` reruns from the caches; `--spot-check N` verifies N random kept matches against OLS. |
| `scripts/entity_mapping/entity_kinds_curated.yaml` | The hand-curated kind per entity, plus repaired names, OLS query hints, hand-flagged wrong matches and, for entities no matcher finds, hand-assigned concepts (`assign:`, looked up on OLS at build time) or a `no_class:` verdict. This is the file to edit when a call is disputed. |

Run from the repository root. Needs Python 3.9+ and PyYAML (`pip install pyyaml`); no other dependency:

```
python3 -I scripts/entity_mapping/build_mapping.py            # uses OLS cache, fetches only what is missing
python3 -I scripts/entity_mapping/build_mapping.py --offline  # no network at all
python3 -I scripts/entity_mapping/build_mapping.py --spot-check 15
```

## Column order the sheet expects

`entities_to_concepts.tsv` mirrors the tab plus a few working columns. Paste in this order:

```
entity | canonical_name | cqs | kind | map | preferred_ontology | rank_adjusted | concept_id | ontology | name | definition | rating_thomas | source | tier | note | status | contributors | reviewers
```

`status` is "Not Started" on every row; `contributors` and `reviewers` are empty. `entity` is the sheet's spelling with whitespace collapsed (the sheet's Proper Case is kept so rows line up); `canonical_name` is the repaired or expanded name (e.g. `Blakpc` → `blaKPC`, `Tmao` → `TMAO (trimethylamine N-oxide)`, `Soil Pollutants: Lead` → `Lead`). `cqs` is normalised to `CQ 19` style.

## The kind rule

Every entity gets exactly one `kind`. Kinds in the first group are things the graph names with an ontology class (`map = yes`); kinds in the second group are things the schema records on Observations, Procedures, Associations or Study metadata, so they get no concept row (`map = no`).

**map = yes:** taxon, taxon-group, host, chemical, drug-or-therapy, gene, gene-product, pathway-or-process, mobile-element, disease-or-phenotype, anatomy-or-body-site, sample-material, environment, event-or-exposure, food-or-diet, ecological-role, cell-state, time-or-period, study-design.

**map = no:** measurement (Observation.observed_property), statistic (Association p-value / effect size / direction), method-or-procedure (Procedure), data-artifact (dataset, PROV), identifier-scheme (alternate_ids / xrefs), meta (schema- or query-level notions such as Study, Biomarker, Final Product), junk-fragment.

Decisions worth knowing about:

- **Broken fragments** from line breaks in the source cells were repaired where the intent is obvious and then classified as the repaired entity: `Soil Pollutants: Lead` → chemical *Lead*; `Organic Hydrocarbons (E.G. Petrol` → chemical *Organic hydrocarbons (e.g. petrol, oil)*; `Polyfluoroalkyl Substances (Pfas) Humidity` → chemical *Perfluoroalkyl substances (PFAS)* (Humidity has its own row). Two are unrepairable and stay `junk-fragment`: `Oil)` (tail of the hydrocarbons cell) and `Organic Carbon Availability. Season` (two entities merged; both exist separately). `rationale` says so on each.
- **Generic schema classes** (`Taxon`, `Microbial Taxon`, `Host`, `Host Organism`, `Host Species`, `Body Site`, `Metastatic Site`) keep their kind but are flagged `generic` in the curated file: Thomas's matches are kept, nothing is fetched from OLS, and the note says the values come from the preferred ontology.
- **Genes** have no OBO class. Resistance genes map to ARO (CARD) families (`blaKPC` → ARO:3000059 KPC beta-lactamase, `tetM` → ARO:3000186 tet(M)). Other gene symbols (cbbL/cbbS, rbcL/rbcS, psbA, psbB, ndh, gpx, sod) are hand-assigned the GO term for what the gene product does (`source = curated`, `match function-of-product`: cbbL/rbcL → GO:0016984 ribulose-bisphosphate carboxylase activity, sod → GO:0004784 superoxide dismutase activity …) with the KEGG KO as a secondary id in the note; the type class is SO:0000704 *gene* and the GO term belongs on a `has_function` edge, not `is_a`. Gene *categories* (circadian rhythm, stress-response, virulence gene) are mapped `broader` to the GO process (GO:0007623, GO:0006950) or, for virulence, MeSH D037521 Virulence Factors, since GO:0009405 pathogenesis is obsolete and ARO has no virulence class.
- **Resistance phenotypes**: antimicrobial ones (biocide, fluoroquinolone) are disease-or-phenotype with ARO preferred; abiotic ones (UV, desiccation, heavy metal) are pathway-or-process on the GO "response to …" term until OMP is adopted (open decision 2 on the tiers page).
- **Ecosystem processes** (carbon cycling, sulfur cycling, plant decomposition) prefer ENVO over GO; **interactions** (competition, mutualism, parasitism, grooming, social contact) are ecological-role, with a note that the schema models them as RO/NBO predicates.
- `qPCR Data`, `Transcriptomic Data`, `Behavioural Data` are all data-artifact (the assay itself is the Procedure).
- **Duplicate rows**: when Thomas's matcher and the OLS fill return the same id (Cattle NCBITaxon:9913, Stool UBERON:0001988, Yoghurt FOODON:00001014 …) the table keeps one row with both sources (`ols-manual+bioportal`) and the note "also found by …", so `(entity, concept_id)` is unique.

## Ontology preference per kind

Thomas's matches are re-ranked so that the preferred ontology for the kind comes first (in list order), then any other core-tier ontology, then tier-3 extensions, then the mapped tier, then everything else. Hand- or heuristically-flagged wrong matches sink to the bottom. The same list drives the OLS lookups. Per-entity overrides live in the curated file (`prefer:`).

| kind | preferred, in order | notes |
|---|---|---|
| taxon, host | NCBITaxon | NCBI has renamed several phyla (Pseudomonadota, Bacillota, Actinomycetota…); the sheet's names are kept, the note says the new name |
| taxon-group | NCBITaxon, ECOCORE, PCO | trophic groups (phototroph, heterotroph) → ECOCORE; communities → PCO:1000004 |
| chemical | ChEBI, FOODON | microplastic and NPK fertiliser override to ENVO / AGRO (no ChEBI class) |
| drug-or-therapy | ChEBI, ARO, NCIT | antibiotics → ChEBI; therapies (anti-PD-1, CAR-T, prophylaxis) → NCIT |
| gene | ARO, SO | ARO for resistance genes; SO:0000704 only for the generic "Gene" |
| gene-product | ARO, GO, ChEBI | ARO for resistance enzymes and pumps (AcrAB-TolC, MexAB-OprM), GO molecular function otherwise |
| pathway-or-process | GO, ENVO | GO biological process / molecular function / cellular component |
| mobile-element | SO | Inc groups have no SO class; they fall back to SO:0001235 replicon |
| disease-or-phenotype | MONDO, HP, PATO | MONDO over DOID / NCIT / MeSH / SNOMED / EFO; clinical states without a MONDO class (relapse, remission) keep NCIT |
| anatomy-or-body-site | UBERON | |
| sample-material | UBERON, OBI | feces, blood plasma, urine → UBERON; FFPE / frozen specimen → OBI |
| environment | ENVO | |
| event-or-exposure | ENVO, ECTO | stimuli recorded as the ENVO/ChEBI thing per ADR-0005; ECTO only when ENVO has nothing |
| food-or-diet | FOODON | diets come from ONS, which FOODON imports (noted) |
| ecological-role | ECOCORE, PCO, PATO | behaviour roles (grooming, social contact) override to NBO |
| cell-state | PATO, GO, CL | host cell types (Treg) override to CL |
| time-or-period | ENVO, NCIT | |
| study-design | OBI, AGRO, PECO | greenhouse / growth-chamber / field studies → PECO or AGRO |

Lower tier, kept only as cross-references (`tier = mapped`): DOID, NCIT, EFO, MeSH, SNOMED CT, GTDB, KEGG, HP, BERO, UMLS, HMDB. `tier = extension` is tier 3 of the tiers page plus CL and PR; `tier = other` is anything outside the tiers (SWEET, CHEAR, SIO, MIxS, SFWO, IDO, ONS…), informational only.

## Counts

| | |
|---|---|
| Entities in the sheet (non-empty column A) | 553 |
| `map = yes` / `map = no` | 363 / 190 |
| Rows in `entities_to_concepts.tsv` | 1193 (997 concept rows + 196 placeholder rows: 190 `map = no`, 2 no class exists, 4 generic) |
| Entities Thomas matched | 316, of which 247 are `map = yes` (his 69 matches for measurements, methods, identifier schemes are not carried into the concept table) |
| Rows with a Thomas match kept | 796 (source `bioportal`, `local`, `bioportal+local`); 20 further rows where his match and the OLS fill are the same id are merged into one row (source `ols-manual+bioportal`) |
| Rows added from OLS (`source = ols-manual`) | 174, each IRI verified by a term lookup (responses in `inputs/ols_cache.json`); 85 of them are now the rank-1 candidate for their entity |
| Entities whose rank-1 changed by re-ranking Thomas's own matches | 114 (37 of them MeSH/SNOMED → ChEBI/MONDO) |
| Hand-assigned concepts (`source = curated`) | 27 rows for 22 entities, each id looked up on OLS at build time (label and definition are the ontology's own) |
| Thomas matches flagged `WRONG?` in the note | 88 rows (30 by hand in the curated file, the rest by the definition/word-overlap heuristics); none is rank 1 |
| `map = yes` entities with no concept at all | 2 with no class in any ontology + 4 generic (see below) |
| Spot check | 15 random kept Thomas matches, all resolve on OLS with the same label (`downloads/spot_check.json`) |

Per kind: measurement 115, chemical 69, taxon 44, pathway-or-process 41, gene 29, meta 24, environment 23, ecological-role 19, host 18, method-or-procedure 16, disease-or-phenotype 16, statistic 13, drug-or-therapy 13, gene-product 12, data-artifact 11, mobile-element 11, taxon-group 11, cell-state 10, event-or-exposure 10, identifier-scheme 9, food-or-diet 9, sample-material 8, study-design 8, time-or-period 7, anatomy-or-body-site 5, junk-fragment 2.

Hand-assigned on 12 November after checking OLS by hand (`assign:` in the curated file; the note on each row gives the match type and the alternatives): the twelve gene symbols and categories (above), ESBL → ARO:3000001 beta-lactamase (ARO has no extended-spectrum grouping class; the families CTX-M, SHV, TEM are listed in the note), Halophile → MICRO:0001367 halophile (the only OBO organism-level class; OMP:0005016 halophilic growth is the phenotype alternative), Non-Pathogen → IDO:0000528 pathogen as a *negation* (no ontology has a non-pathogen class), Gut Taxa → PCO:1000004 microbial community located in UBERON:0001555 digestive tract (two rows), Pot Trial → PECO:0007248 greenhouse study (no pot-trial design class in PECO, AGRO or OBI), Relaxase → ICEO:0000036 relaxase (the Integrative and Conjugative Element Ontology is the only one with the class; GO:0004520 DNA endonuclease activity as the broader GO function, InterPro IPR005094 in the note), Carbon concentrating mechanism → GO:0031470 carboxysome (*component*; GO has no CCM process, GO:0015977 carbon fixation is the broader process), Plant decomposition → ECOCORE:00000186 litter decomposition (ENVO and GO have no decomposition process), Cloud dissipation → ENVO:02500003 atmospheric process (*broader*; ENVO has cloud formation but no inverse, a term request is the real fix), Th17/Treg imbalance → HP:0025830 abnormal Th17 T cell proportion + HP:0020113 decreased regulatory T cell proportion (two *component* rows; the ratio is an observed property, HP:0031392 is the single parent).

No class in any OLS ontology (`no_class:` in the curated file; placeholder row proposes a local micokg class): Keystone Species, Resource Partitioning.

Nothing is left unresolved.

## Problems found in Thomas's output

His identifiers are sound (the spot check found no bad IRI) but the ranking is lexical, so the following need care:

- **Rank 1 is almost always MeSH / SNOMED CT / NCIT** (657 of his 1122 rows are from those three) even when ChEBI, NCBITaxon or MONDO is in the list. The re-ranking fixes 114 rank-1s without touching his data.
- **Lexical artefacts at rank 1**: NCIT "Lead" = a position of leadership; "Cog" → Congo; "Arg" → Arginine (ARG is antibiotic resistance gene); "Ros" → ROS1 wt Allele; "Npk" → TAC1 wt Allele; "Mag" → MAG Gene; "Ko" → alpha-D-Ko (a sugar); "Consortium" → an organisation; "Pigments" → Pigmentation; "Growth Chamber" → Drosophila egg chamber growth; "Functional Potential" → Motor-potential; "Host Organism" → GO host cellular component; "Carbonyl Compounds" → Iron Carbonyl Compounds; "Iron" → a GloSiS cementation code.
- **Right family, wrong term**: AcrAB-TolC and MexAB-OprM matched to the *inhibitors* of the pumps; Anti-PD-1 therapy → Anti-PD-L1 therapy; Calvin cycle → Photosynthesis; Carbonic anhydrase → Carbonic Anhydrase I; Type IV secretion system → the secretion *process* rather than the complex; Non-alcoholic fatty liver disease → *alcoholic* fatty liver disease (rating 0.82); Heavy metal resistance → an obsolete GO term.
- **Bogus identifier**: `NCBITAXON:T016` for Human is not an NCBI taxon id (Homo sapiens is NCBITaxon:9606).
- **Buffalo, Goat, Sheep, Swine, Hen** matched to FOODON food-source products, and **Maize, Soybean, Tomato, Potatoes, Wheat** to GloSiS crop codes, with no NCBITaxon in the list; the OLS fills add the species.
- **Missing core ontologies for common things**: Acetic acid, Cadmium, Mercury, Nitrogen, Phosphorus, Iron, Zinc had no ChEBI; Freshwater no ENVO; Stool no UBERON; Obesity, IBD, T2D no MONDO. These are now filled from OLS.
- 238 entities unmatched; 116 of them are `map = yes` and 89 of those are now resolved from OLS (the rest are the 24 unresolved and 3 generic classes).

## Caveats

- The NCBI phylum names in the sheet (Proteobacteria, Actinobacteria, Cyanobacteria…) are kept as written; the note gives the current NCBI name. Pick one spelling for the graph.
- Some fills are deliberately parent classes with a note (Secondary bile acid → bile acid; Asian/Saharan dust input → dust; Inc replicons → replicon; Bioaerosols → aerosol). They are placeholders for a term request, not final answers.
- OLS `exact=true` also matches synonyms; the note says whether the hit was on the label, a synonym, or a non-exact search ("check").
