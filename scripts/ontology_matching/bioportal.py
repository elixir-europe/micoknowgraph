"""Minimal client for the BioPortal REST API (https://data.bioontology.org/documentation).

Needs a free API key (BioPortal account settings page). The key is sent in the
Authorization header, never in the URL, so it does not end up in logs or cache files.

Responses are cached as JSON files, so rerunning the matcher does not repeat the requests.
Standard library only.
"""
import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://data.bioontology.org"


class BioPortal:
    def __init__(self, api_key, cache_dir=None, delay=0.1, timeout=60):
        self.api_key = api_key
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.delay = delay              # pause between live requests (BioPortal allows ~15/s per key)
        self.timeout = timeout

    # ------------------------------------------------------------------ HTTP + cache
    def _get(self, path, params):
        """GET a BioPortal endpoint as JSON, from the cache when possible. Returns None on failure."""
        url = f"{API}{path}?{urllib.parse.urlencode(params)}"
        cache_file = None
        if self.cache_dir:
            cache_file = self.cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ".json")
            if cache_file.exists():
                return json.loads(cache_file.read_text(encoding="utf-8"))

        req = urllib.request.Request(url, headers={"Authorization": f"apikey token={self.api_key}",
                                                   "Accept": "application/json"})
        for attempt in range(4):                         # retry on rate limit / server errors
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    data = json.load(r)
                break
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                if e.code == 401:
                    sys.exit("error: BioPortal rejected the API key (HTTP 401). Check BIOPORTAL_API_KEY.")
                print(f"warning: BioPortal {path} failed for {params.get('q', '')!r}: HTTP {e.code}", file=sys.stderr)
                return None
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                if attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                print(f"warning: BioPortal {path} failed for {params.get('q', '')!r}: {e}", file=sys.stderr)
                return None
        time.sleep(self.delay)

        if cache_file:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(data), encoding="utf-8")
        return data

    # ------------------------------------------------------------------ endpoints
    def search(self, query, ontologies=None, pagesize=50):
        """Search class labels and synonyms across BioPortal (or only the listed ontology acronyms).

        Returns [{"iri", "label", "definition", "synonyms", "acronym", "notation", "obsolete"}], in BioPortal's rank order.
        """
        # Obsolete classes are excluded by BioPortal's default (also_search_obsolete=false);
        # `include` cannot ask for the obsolete flag, so it is only checked if BioPortal sends it.
        params = {"q": query, "pagesize": pagesize, "include": "prefLabel,synonym,definition,notation",
                  "display_context": "false"}
        if ontologies:
            params["ontologies"] = ",".join(ontologies)
        data = self._get("/search", params) or {}
        out = []
        for r in data.get("collection", []):
            # the ontology acronym is the last part of links.ontology: .../ontologies/CHEBI -> CHEBI
            acronym = r.get("links", {}).get("ontology", "").rstrip("/").rsplit("/", 1)[-1]
            out.append({
                "iri": r.get("@id", ""),
                "label": r.get("prefLabel") or "",
                "definition": (r.get("definition") or [""])[0],
                "synonyms": r.get("synonym") or [],
                "acronym": acronym,
                "notation": r.get("notation") or "",
                "obsolete": bool(r.get("obsolete")),
            })
        return out

    def ontology_names(self):
        """{acronym: full name} for every ontology in BioPortal (one request, cached)."""
        data = self._get("/ontologies", {"display_links": "false", "display_context": "false"}) or []
        return {o["acronym"]: o["name"] for o in data if o.get("acronym") and o.get("name")}
