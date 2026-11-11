# Tests

Place tests for the Python scripts here, mirroring the script organization
where practical. Prefer small, non-sensitive fixtures that can be shared in
the repository. Document how to run the tests when a test suite is added.

## Running the tests

```sh
python3 -m unittest discover -s tests -t . -v
```

The tests run offline: BioPortal is replaced by a fake client.

Tests mirror the script folders: `tests/ontology_matching/` tests `scripts/ontology_matching/`.
Each test folder needs an `__init__.py` so `discover` finds it.
