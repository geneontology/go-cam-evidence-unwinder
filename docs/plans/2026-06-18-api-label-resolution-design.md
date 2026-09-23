# OLS API Label Resolution for Non-GO/RO Terms Design

Branch: `nested-anatomy-extensions`.

## Problem

`term_label()` (`src/gocam_unwinder/gocam_ttl.py:1207-1233`) resolves a term URI to
a human-readable label using **only** the two locally-loaded ontologies:

- GO terms (`GO:`) via the ontobio GO ontology (`self.ontology.label(curie)`).
- RO / BFO terms (`RO:`, `BFO:`) via the RO rdflib graph (`rdfs:label`).
- Everything else falls back to the bare CURIE.

```python
def term_label(self, uri: URIRef) -> str:
    parsed_curies = curie_util.contract_uri(str(uri))
    if not parsed_curies:
        return str(uri)
    curie = parsed_curies[0]
    if curie.startswith("GO:"):
        label = self.ontology.label(curie)
        if label:
            return label
    if self.ro_ontology and (curie.startswith("RO:") or curie.startswith("BFO:")):
        for label in self.ro_ontology.objects(uri, rdflib.RDFS.label):
            return str(label)
    return str(curie)   # <-- anatomy/CHEBI/ECO/PR/... land here as raw CURIEs
```

Both report paths route every label through this one method:

- The criteria-failure report — `print_non_standard_annotation_failed_checks()`
  (`gocam_ttl.py:1235-1256`).
- The remainders report — `debug_non_standard.py` (every `builder.term_label(...)`
  call, e.g. lines 172-234).

So anatomy / cell-type terms that legitimately appear in annotation extensions —
`CL:` (cell type), `UBERON:`, `EMAPA:`, `WBbt:`, `FBbt:`, `ZFA:`, `MA:`, `PO:` —
and other non-GO/RO terms (`CHEBI:`, `ECO:`, `PR:`) show up in reports as opaque
CURIEs like `CL:0000540` instead of `neuron`. These ontologies are not loaded
locally and there is no general mechanism to resolve them.

## Solution: OLS4 REST fallback with a per-run in-memory cache

Add a final fallback step to `term_label()`: when the local ontologies do not
yield a label and API resolution is enabled, query the EBI **Ontology Lookup
Service v4** (OLS4) for the term's label, memoize the result (hit **and** miss) in
a per-builder dict, and return the label or fall back to the CURIE.

This was chosen over (B) the `oaklib` library — a heavy new dependency for a
single label lookup — and (C) downloading and parsing the anatomy ontologies
locally, which does not generalize to "any unresolved CURIE" and adds large file
maintenance. Approach A matches the stated intent (call an API, cache at runtime)
and adds **no new dependency**: `requests` 2.32.3 is already present in the env
transitively via ontobio.

### Decisions (settled during brainstorming)

| Decision | Choice |
|----------|--------|
| Which terms to resolve | **Any** unresolved CURIE (not just anatomy) |
| Activation | **Always-on**, with a `--no-label-api` opt-out flag |
| Cache | **In-memory only** (per process run); each ID queried at most once |
| API | EBI OLS4 REST (`https://www.ebi.ac.uk/ols4/api`) via `requests` |

### The OLS4 endpoint and label-selection heuristic

A single endpoint resolves **any** OBO PURL without per-ontology configuration —
`term_label()` already holds the full IRI (`str(uri)`):

```
GET https://www.ebi.ac.uk/ols4/api/terms?iri={iri}&size=20
```

The response is `_embedded.terms` — a list of term records (the same IRI may be
present in several ontologies). Live probes (2026-06-18):

| IRI | `_embedded.terms` | Chosen label |
|-----|-------------------|--------------|
| `…/CL_0000540` | 5 (cl `is_defining_ontology=true`; caro label is junk `CL_0000540`) | `neuron` |
| `…/EMAPA_16894` | 1 (defining) | `brain` |
| `…/WBbt_0006796` | 2 (defining) | `germ cell` |
| `…/UBERON_0000955` | 10 (no defining flag in top results) | `brain` (via prefix/real-label) |
| `…/CL_9999999999` (bogus) | 0 | — (miss → CURIE) |

`_select_label(terms, curie)` picks by priority, where a label is "real" iff it is
non-empty and not equal to the bare local ID fragment (e.g. not `"CL_0000540"`):

1. a term with `is_defining_ontology == true` and a real label, else
2. a term whose `ontology_name` equals the CURIE prefix lowercased
   (`CL:` → `cl`, `UBERON:` → `uberon`, …) and has a real label, else
3. the first term with any real label, else
4. `None` (→ caller returns the CURIE).

The UBERON case (no defining flag in the top results) is resolved by rule 2, and
the caro junk-label case (`CL_0000540`) by the real-label filter. Single-result
cases (EMAPA, WBbt) hit rule 1.

### Change 1: constructor arg + cache + session

`GoCamGraphBuilder.__init__` (`gocam_ttl.py:726`) gains a keyword arg and state:

```python
def __init__(self, ontology_path, ro_ontology_path=None, groups_yaml_path=None,
             resolve_labels_api=True):
    ...
    self.resolve_labels_api = resolve_labels_api
    self._api_label_cache = {}          # iri (str) -> label (str) | None  (negative-cached)
    self._api_session = None            # lazily-created requests.Session
    self._api_consecutive_failures = 0  # circuit-breaker counter
    self._api_disabled = False          # tripped after too many failures
```

Defaulting to `True` keeps resolution always-on, per the decision. `requests` is
imported at module top (already an available dependency).

### Change 2: `_fetch_label_from_ols(uri, curie)`

New private method. Performs one HTTP GET, returns the selected label or `None`.

```python
OLS4_TERMS_URL = "https://www.ebi.ac.uk/ols4/api/terms"
OLS4_TIMEOUT = 10           # seconds
OLS4_MAX_CONSECUTIVE_FAILURES = 5

def _fetch_label_from_ols(self, uri, curie):
    if self._api_disabled:
        return None
    if self._api_session is None:
        self._api_session = requests.Session()
        self._api_session.headers.update(
            {"User-Agent": "go-cam-evidence-unwinder/label-resolver"})
    try:
        resp = self._api_session.get(
            self.OLS4_TERMS_URL,
            params={"iri": str(uri), "size": 20},
            timeout=self.OLS4_TIMEOUT,
        )
        resp.raise_for_status()
        terms = resp.json().get("_embedded", {}).get("terms", [])
        label = self._select_label(terms, curie)
        self._api_consecutive_failures = 0   # reset on any successful response
        return label
    except (requests.RequestException, ValueError):
        # network error, non-2xx, or unparseable JSON -> treat as a miss
        self._api_consecutive_failures += 1
        if self._api_consecutive_failures >= self.OLS4_MAX_CONSECUTIVE_FAILURES:
            self._api_disabled = True   # offline / outage: stop trying for this run
        return None
```

`_select_label(terms, curie)` is a pure helper (no network) implementing the
4-rule priority above — making it independently unit-testable with canned JSON.

The circuit breaker (`_api_disabled` after 5 consecutive failures) keeps a fully
offline run from making one doomed HTTP call per unique term. A single transient
failure only negative-caches that one ID.

### Change 3: integrate into `term_label()`

Insert before the final `return str(curie)`:

```python
    # API fallback for non-GO/RO terms (anatomy, CHEBI, ECO, PR, ...)
    if self.resolve_labels_api:
        iri = str(uri)
        if iri not in self._api_label_cache:
            self._api_label_cache[iri] = self._fetch_label_from_ols(uri, curie)
        if self._api_label_cache[iri]:
            return self._api_label_cache[iri]

    # Fall back to CURIE
    return str(curie)
```

Caching keys on the full IRI string. Both hits and misses are stored, so any ID
is queried **at most once** per run (satisfying the runtime-cache requirement).
GO/RO resolution above is untouched — the API is a strict fallback, never
overrides a locally-found label, and the URI may even be a non-OBO host (gene
products, ComplexPortal): OLS simply returns 0 terms → negative-cached → CURIE.

### Change 4: CLI wiring (`--no-label-api`) in both entry points

`gocam_ttl.py` `__main__` (builder built at line 1282) and `debug_non_standard.py`
`main()` (builder built at line 125) each add:

```python
parser.add_argument("--no-label-api", action="store_true",
                    help="Disable OLS API fallback for resolving non-GO/RO term labels")
```

and pass `resolve_labels_api=not args.no_label_api` to `GoCamGraphBuilder(...)`.
(In `debug_non_standard.py` the arg parser is `ap`.)

### Change 5: keep the test suite hermetic

The shared `builder` fixture in `tests/conftest.py` is **session-scoped**. With the
API defaulting on, existing tests that pass anatomy terms through `term_label()`
would start making live, slow, flaky network calls. The fixture is therefore
changed to construct with the API **disabled**:

```python
return GoCamGraphBuilder(ONTOLOGY_FILE, RO_ONTOLOGY_FILE, GROUPS_YAML_FILE,
                         resolve_labels_api=False)
```

This preserves the current asserted behavior of all existing tests (anatomy terms
still resolve to CURIEs through the shared fixture) and keeps the suite offline.
The new API behavior is exercised by dedicated tests that construct their own
builder and mock the network — no live HTTP in CI.

## Out of Scope

- **Persistent / on-disk cache** across runs — explicitly deferred (decision:
  in-memory only). A fresh process re-queries.
- **Request batching** — OLS4 `terms?iri=` is one IRI per call; the in-memory
  cache already dedupes by unique ID, so per-term lazy lookup is sufficient.
- **Restricting to anatomy namespaces** — decision is to resolve any unresolved
  CURIE; non-OBO IRIs simply miss and fall back.
- **Retries / backoff** beyond the single attempt + consecutive-failure breaker.
- **Makefile changes** — always-on default means no flag is needed in the pipeline
  steps. (`--no-label-api` is available for offline/deterministic runs but not
  wired into the Makefile.)

## Test Plan

New tests in `tests/test_gocam_ttl.py` (use the `monkeypatch` fixture and a fake
session/response object; no real network):

1. **`test_select_label_*`** — unit tests of `_select_label()` against canned
   `_embedded.terms` payloads captured from the live API:
   - defining-ontology wins, junk same-as-ID labels are filtered (CL_0000540 case
     → `neuron`);
   - prefix-match rule resolves the UBERON case (no defining flag → `brain`);
   - single defining result (EMAPA → `brain`, WBbt → `germ cell`);
   - empty terms list → `None`.

2. **`test_term_label_api_fallback`** — construct a builder with
   `resolve_labels_api=True`, monkeypatch `_fetch_label_from_ols` (or the session
   `get`) to return a known label; assert `term_label(CL_0000540 URIRef)` returns
   `"neuron"`.

3. **`test_term_label_api_cached_once`** — monkeypatch the fetch with a call
   counter; call `term_label()` twice for the same URI; assert the fetch ran
   exactly once and both calls returned the label. Also assert a **miss** is
   negative-cached (fetch returns `None` → one call, subsequent calls return the
   CURIE without re-fetching).

4. **`test_term_label_api_disabled`** — with `resolve_labels_api=False`, assert
   `term_label()` returns the CURIE and the fetch helper is **never** called.

5. **`test_term_label_api_network_error`** — monkeypatch the session `get` to
   raise `requests.RequestException`; assert `term_label()` returns the CURIE and
   does not propagate the exception.

6. **`test_ols_circuit_breaker`** (optional) — feed `OLS4_MAX_CONSECUTIVE_FAILURES`
   failing responses; assert `_api_disabled` flips and a subsequent fetch returns
   `None` without calling the network.

Run: `pytest tests/test_gocam_ttl.py -v` (requires `target/go_20250601.json` and
`resources/test/ro_20250723.owl`).
