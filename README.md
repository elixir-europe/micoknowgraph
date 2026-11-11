# MiCoKnowGraph: Designing a Microbiome Collaborative Knowledge Graph linking complex, multi-domain data

This repository s together scripts for designing a collaborative
microbiome knowledge graph across complex, multi-domain data.

## Repository structure

```text
.
├── data/                    # Data, one subfolder per step (same names as scripts/)
│   └── ontology_matching/
│       ├── entities/        #   entity lists extracted from the competency questions
│       ├── ontologies/      #   local ontology files (OMP, PTO, PECO, ECOCORE, RO, SFWO, GloSIS)
│       └── matches/         #   ontology matches written by the script
├── doc/                     # Design notes and project documentation
├── scripts/                 # Python scripts, one subfolder per step
│   └── ontology_matching/   #   match entities to ontology classes (BioPortal + local files)
├── tests/                   # Tests, mirroring the scripts folders
│   └── ontology_matching/
└── LICENSE
```

To match the entities to ontology classes, see
[scripts/ontology_matching/](scripts/ontology_matching/README.md).

## Getting started

Python 3 is needed to run future Python scripts. To create an isolated
environment from the repository root:

```sh
python3 -m venv .venv
```

Activate it with `source .venv/bin/activate` on macOS or Linux, or
`.venv\Scripts\activate` in Windows Command Prompt. There are currently no
project dependencies to install. Script-specific usage and dependencies
should be documented as scripts are added.

## BioPortal API key

`scripts/ontology_matching/match_entities.py` searches BioPortal, which needs a
free API key ([create an account](https://bioportal.bioontology.org/account) and
copy the key from the account page). From the repository root:

```sh
cp .env.example .env     # then open .env and replace the placeholder with your key
```

`.env` must be in the repository root (next to this README). It is git-ignored,
so the key is never committed. Alternatively, `export BIOPORTAL_API_KEY=...` in
your shell, or pass `--api-key`. Without a key the script still runs, searching
only the local ontology files.

## Contributing

Add scripts under [scripts/](scripts/), corresponding tests under
[tests/](tests/), and relevant design or data documentation under
[doc/](doc/) or [data/](data/). See the directory guides for details.

## License

This project is distributed under the [MIT License](LICENSE).
