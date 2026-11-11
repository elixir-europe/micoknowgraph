# Scripts

Place Python scripts used to design and build the knowledge graph here.
Organize them into subdirectories when the project needs them.

For each script, document its purpose, dependencies, invocation, expected
inputs and outputs, and any relevant data sources or assumptions. Add
corresponding tests under [tests/](../tests/).

## Layout

Scripts are grouped by step in subfolders, each with its own README.

| Folder | Purpose |
| --- | --- |
| [ontology_matching/](ontology_matching/) | Match competency-question entities to ontology classes (BioPortal and local ontology files) |

Add a new subfolder per step, with matching folders under [tests/](../tests/) and [data/](../data/)  (data for a step lives in `data/<step>/`).
