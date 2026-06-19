# OLS API Label Resolution for Non-GO/RO Terms Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve human-readable labels for non-GO/RO terms (anatomy CL/UBERON/EMAPA/WBbt/…, plus CHEBI/ECO/PR/…) by querying the EBI OLS4 REST API as a fallback in `term_label()`, with an in-memory per-run cache so each ID is fetched at most once.

**Architecture:** Add a strict fallback at the end of `GoCamGraphBuilder.term_label()` — after GO and RO resolution, before the CURIE fallback. The fallback queries OLS4 `terms?iri={iri}`, selects the best label via a small pure helper, and memoizes hits *and* misses on the builder. Both report paths (criteria-failure report and the `debug_non_standard.py` remainders report) route through `term_label()`, so they benefit automatically. Always-on by default with a `--no-label-api` opt-out.

**Tech Stack:** Python, `requests` 2.32.3 (already in the env via ontobio — no new dependency), `rdflib`, `prefixcommons.curie_util`, `pytest`.

**Affected Areas:** `src/gocam_unwinder/gocam_ttl.py` (builder constructor, `term_label`, two new helpers, class constants, import), `debug_non_standard.py` + `gocam_ttl.py __main__` (CLI flag), `tests/conftest.py` (hermetic fixture), `tests/test_gocam_ttl.py` (new tests).

Design doc: `docs/plans/2026-06-18-api-label-resolution-design.md`.

---

## Context

`term_label()` (`src/gocam_unwinder/gocam_ttl.py:1207-1233`) resolves GO terms via the ontobio GO ontology and RO/BFO terms via the loaded RO rdflib graph; every other term falls back to its bare CURIE. Anatomy and other non-GO/RO terms therefore appear in reports as opaque CURIEs (e.g. `CL:0000540` instead of `neuron`). The EBI OLS4 API indexes all of these ontologies. A single endpoint — `GET https://www.ebi.ac.uk/ols4/api/terms?iri={full_iri}` — resolves any OBO PURL without per-ontology configuration, and `term_label()` already holds the full IRI (`str(uri)`).

The OLS4 response is `{"_embedded": {"terms": [ {term}, ... ]}}`. The same IRI can be present in multiple ontologies; the importing ontologies sometimes carry a junk label equal to the ID fragment (e.g. caro returns label `"CL_0000540"`). Label selection prefers the term flagged `is_defining_ontology`, then a term whose `ontology_name` matches the CURIE prefix, then any term with a "real" label (non-empty and not the bare ID).

Live-probed examples (2026-06-18): `CL_0000540` → `neuron`, `EMAPA_16894` → `brain`, `WBbt_0006796` → `germ cell`, `UBERON_0000955` → `brain`, `CL_9999999999` → 0 terms.

## Constraints

- **No new hard dependency** — use `requests` (already importable in the env).
- **Each unique ID queried at most once per run** — cache both hits and misses (negative caching).
- **The test suite must stay hermetic** — no live network calls in CI. The session-scoped shared `builder` fixture is set to `resolve_labels_api=False`; API behavior is tested with a mocked session.
- **Never override a locally-found label** — the API is a strict fallback after GO/RO.
- **Offline-safe** — a fully offline run must not make one doomed HTTP call per unique term (consecutive-failure circuit breaker), and must never raise out of `term_label()`.

## Out of Scope

- Persistent / on-disk cache across runs (decision: in-memory only).
- Request batching, retries, or backoff beyond a single attempt + circuit breaker.
- Restricting resolution to anatomy namespaces (decision: any unresolved CURIE).
- Makefile changes (always-on default needs no flag in the pipeline).

---

## Tasks

### Task 1: Constructor arg, builder state, class constants, import; make the test fixture hermetic

Adds the `resolve_labels_api` knob, the cache/session/circuit-breaker state, the OLS constants, and the `requests` import. Flips the shared test fixture offline in the same task so no later task can introduce live network calls when the integration lands.

**Files:**
- Modify: `src/gocam_unwinder/gocam_ttl.py:1` (imports), class-constant block (`gocam_ttl.py:694-724`), `__init__` (`gocam_ttl.py:726-756`)
- Modify: `tests/conftest.py:20`
- Test: `tests/test_gocam_ttl.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_gocam_ttl.py`:

```python
def test_builder_api_state_defaults(builder):
    # The shared fixture is constructed offline (resolve_labels_api=False),
    # but the cache/circuit-breaker state must still be initialized.
    assert builder.resolve_labels_api is False
    assert builder._api_label_cache == {}
    assert builder._api_consecutive_failures == 0
    assert builder._api_disabled is False
    assert GoCamGraphBuilder.OLS4_TERMS_URL == "https://www.ebi.ac.uk/ols4/api/terms"
    assert GoCamGraphBuilder.OLS4_TIMEOUT == 10
    assert GoCamGraphBuilder.OLS4_MAX_CONSECUTIVE_FAILURES == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gocam_ttl.py::test_builder_api_state_defaults -v`
Expected: FAIL with `AttributeError: 'GoCamGraphBuilder' object has no attribute 'resolve_labels_api'` (and the fixture still passes `resolve_labels_api=False` only after the conftest edit below — see Step 3).

- [ ] **Step 3: Write minimal implementation**

In `src/gocam_unwinder/gocam_ttl.py`, add the import (after `import rdflib` at line 6):

```python
import requests
```

Add the class constants to `GoCamGraphBuilder` alongside the existing constants (e.g. just after the `ANATOMY_NAMESPACE_KEYS` block, before `def __init__` at `gocam_ttl.py:726`):

```python
    # EBI OLS4 REST API for resolving labels of terms not in the local GO/RO
    # ontologies (anatomy CL/UBERON/EMAPA/WBbt/..., plus CHEBI/ECO/PR/...).
    OLS4_TERMS_URL = "https://www.ebi.ac.uk/ols4/api/terms"
    OLS4_TIMEOUT = 10                  # seconds, per request
    OLS4_MAX_CONSECUTIVE_FAILURES = 5  # disable API for the run after this many
```

Change the constructor signature (`gocam_ttl.py:726`):

```python
    def __init__(self, ontology_path, ro_ontology_path=None, groups_yaml_path=None,
                 resolve_labels_api=True):
```

Add the new state at the end of `__init__` (after the existing `self.rel_*` / relation setup; place it after the `groups_lookup` block around `gocam_ttl.py:752` is also fine — anywhere in the body):

```python
        # OLS API label-resolution state. The cache maps an IRI string to a
        # label (str) or None (negative-cached miss), so each ID is queried at
        # most once per run. The session is created lazily on first fetch.
        self.resolve_labels_api = resolve_labels_api
        self._api_label_cache = {}
        self._api_session = None
        self._api_consecutive_failures = 0
        self._api_disabled = False
```

In `tests/conftest.py`, change the fixture return (line 20) to keep the suite offline:

```python
    return GoCamGraphBuilder(ONTOLOGY_FILE, RO_ONTOLOGY_FILE, GROUPS_YAML_FILE,
                             resolve_labels_api=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gocam_ttl.py::test_builder_api_state_defaults -v`
Expected: PASS

- [ ] **Step 5: Run full test suite for regressions**

Run: `pytest -v`
Expected: All tests PASS (the constructor arg defaults preserve existing behavior; `term_label` is unchanged so far).

---

### Task 2: `_select_label()` pure label-selection helper

A network-free helper that picks the best label from an OLS4 `_embedded.terms` list. Pure and independently unit-testable with canned payloads.

**Files:**
- Modify: `src/gocam_unwinder/gocam_ttl.py` (new method on `GoCamGraphBuilder`, near `term_label` at `gocam_ttl.py:1207`)
- Test: `tests/test_gocam_ttl.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_gocam_ttl.py`:

```python
def test_select_label_prefers_defining_ontology(builder):
    # CL_0000540: cl is defining ("neuron"); caro carries a junk label == the ID.
    terms = [
        {"ontology_name": "ado", "label": "neuron", "is_defining_ontology": False},
        {"ontology_name": "caro", "label": "CL_0000540", "is_defining_ontology": False},
        {"ontology_name": "cl", "label": "neuron", "is_defining_ontology": True},
    ]
    assert builder._select_label(terms, "CL:0000540") == "neuron"


def test_select_label_prefix_match_when_no_defining(builder):
    # UBERON_0000955: no defining flag in results; pick the term whose
    # ontology_name matches the CURIE prefix.
    terms = [
        {"ontology_name": "fma", "label": "Brain", "is_defining_ontology": False},
        {"ontology_name": "uberon", "label": "brain", "is_defining_ontology": False},
    ]
    assert builder._select_label(terms, "UBERON:0000955") == "brain"


def test_select_label_first_real_label_fallback(builder):
    # No defining flag, no prefix match -> first term with a real label.
    terms = [
        {"ontology_name": "x", "label": "EMAPA_16894", "is_defining_ontology": False},
        {"ontology_name": "y", "label": "brain", "is_defining_ontology": False},
    ]
    assert builder._select_label(terms, "EMAPA:16894") == "brain"


def test_select_label_single_defining(builder):
    terms = [{"ontology_name": "wbbt", "label": "germ cell", "is_defining_ontology": True}]
    assert builder._select_label(terms, "WBbt:0006796") == "germ cell"


def test_select_label_empty_returns_none(builder):
    assert builder._select_label([], "CL:9999999999") is None


def test_select_label_only_junk_returns_none(builder):
    # Every candidate's label is just the ID fragment or the CURIE itself.
    terms = [
        {"ontology_name": "caro", "label": "CL_0000540", "is_defining_ontology": False},
        {"ontology_name": "z", "label": "CL:0000540", "is_defining_ontology": True},
    ]
    assert builder._select_label(terms, "CL:0000540") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gocam_ttl.py -k select_label -v`
Expected: FAIL with `AttributeError: 'GoCamGraphBuilder' object has no attribute '_select_label'`

- [ ] **Step 3: Write minimal implementation**

Add this method to `GoCamGraphBuilder` (place immediately above `term_label` at `gocam_ttl.py:1207`):

```python
    def _select_label(self, terms, curie):
        """Pick the best human-readable label from an OLS4 _embedded.terms list.

        The same IRI may appear in several ontologies; importing ontologies
        sometimes carry a junk label equal to the bare ID fragment. Priority:
          1. a term flagged is_defining_ontology with a "real" label;
          2. a term whose ontology_name matches the CURIE prefix (lowercased);
          3. the first term with any "real" label;
          4. None (caller falls back to the CURIE).
        A label is "real" iff it is non-empty and not equal to the bare ID
        fragment ("CL_0000540") or the CURIE itself ("CL:0000540").
        """
        if not terms:
            return None
        local_id = curie.replace(":", "_")          # "CL:0000540" -> "CL_0000540"
        prefix = curie.split(":", 1)[0].lower()     # "CL:0000540" -> "cl"

        def is_real(term):
            label = term.get("label")
            return bool(label) and label != local_id and label != curie

        for term in terms:                          # rule 1
            if term.get("is_defining_ontology") and is_real(term):
                return term["label"]
        for term in terms:                          # rule 2
            if term.get("ontology_name") == prefix and is_real(term):
                return term["label"]
        for term in terms:                          # rule 3
            if is_real(term):
                return term["label"]
        return None                                 # rule 4
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gocam_ttl.py -k select_label -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Run full test suite for regressions**

Run: `pytest -v`
Expected: All tests PASS

---

### Task 3: `_fetch_label_from_ols()` network method with circuit breaker

Performs one HTTP GET against OLS4, delegates label selection to `_select_label`, and never raises. Tracks consecutive failures and disables the API for the run after the threshold. Tested with an injected fake session — no live network.

**Files:**
- Modify: `src/gocam_unwinder/gocam_ttl.py` (new method on `GoCamGraphBuilder`, above `term_label`)
- Test: `tests/test_gocam_ttl.py`

- [ ] **Step 1: Write the failing test**

Add the fake-session helpers and tests to `tests/test_gocam_ttl.py` (add `import requests` to the test file's imports if not present):

```python
class _FakeResponse:
    def __init__(self, payload, ok=True):
        self._payload = payload
        self._ok = ok

    def raise_for_status(self):
        if not self._ok:
            raise requests.HTTPError("simulated non-2xx")

    def json(self):
        return self._payload


class _FakeSession:
    """Stand-in for requests.Session that returns a canned response or raises."""
    def __init__(self, response=None, exc=None):
        self.response = response
        self.exc = exc
        self.calls = 0
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.response


def _ols_payload(terms):
    return {"_embedded": {"terms": terms}}


def test_fetch_label_from_ols_success(builder):
    terms = [{"ontology_name": "cl", "label": "neuron", "is_defining_ontology": True}]
    builder._api_session = _FakeSession(response=_FakeResponse(_ols_payload(terms)))
    uri = rdflib.URIRef("http://purl.obolibrary.org/obo/CL_0000540")
    assert builder._fetch_label_from_ols(uri, "CL:0000540") == "neuron"
    assert builder._api_session.calls == 1
    assert builder._api_consecutive_failures == 0


def test_fetch_label_from_ols_network_error_returns_none(builder):
    builder._api_session = _FakeSession(exc=requests.ConnectionError("down"))
    uri = rdflib.URIRef("http://purl.obolibrary.org/obo/CL_0000540")
    assert builder._fetch_label_from_ols(uri, "CL:0000540") is None
    assert builder._api_consecutive_failures == 1


def test_fetch_label_from_ols_circuit_breaker(builder):
    builder._api_session = _FakeSession(exc=requests.ConnectionError("down"))
    uri = rdflib.URIRef("http://purl.obolibrary.org/obo/CL_0000540")
    for _ in range(GoCamGraphBuilder.OLS4_MAX_CONSECUTIVE_FAILURES):
        assert builder._fetch_label_from_ols(uri, "CL:0000540") is None
    assert builder._api_disabled is True
    # Once disabled, no further network calls are made.
    builder._api_session.calls = 0
    assert builder._fetch_label_from_ols(uri, "CL:0000540") is None
    assert builder._api_session.calls == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gocam_ttl.py -k fetch_label_from_ols -v`
Expected: FAIL with `AttributeError: 'GoCamGraphBuilder' object has no attribute '_fetch_label_from_ols'`

- [ ] **Step 3: Write minimal implementation**

Add this method to `GoCamGraphBuilder` (immediately above `_select_label`):

```python
    def _fetch_label_from_ols(self, uri, curie):
        """Query OLS4 for the label of ``uri``; return the label or None.

        Never raises: network errors, non-2xx responses, and unparseable JSON
        are treated as a miss (None). After OLS4_MAX_CONSECUTIVE_FAILURES
        consecutive failures the API is disabled for the rest of the run so a
        fully offline run does not make one doomed call per unique term.
        """
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
            self._api_consecutive_failures += 1
            if self._api_consecutive_failures >= self.OLS4_MAX_CONSECUTIVE_FAILURES:
                self._api_disabled = True
            return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gocam_ttl.py -k fetch_label_from_ols -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run full test suite for regressions**

Run: `pytest -v`
Expected: All tests PASS

---

### Task 4: Integrate the API fallback into `term_label()` with caching

Wire the fetch + cache into `term_label()` as the final step before the CURIE fallback. Add a fixture that enables the API on the shared builder with a clean cache and restores offline state afterward.

**Files:**
- Modify: `src/gocam_unwinder/gocam_ttl.py:1226-1233` (end of `term_label`)
- Test: `tests/test_gocam_ttl.py` (new `api_builder` fixture + tests)

- [ ] **Step 1: Write the failing test**

These tests reuse the `_FakeResponse`, `_FakeSession`, and `_ols_payload` helpers added to `tests/test_gocam_ttl.py` in Task 3. Add to `tests/test_gocam_ttl.py`:

```python
@pytest.fixture
def api_builder(builder):
    """Shared builder with the OLS fallback enabled and a clean cache.

    Restores offline state afterward so the session-scoped builder stays
    hermetic for every other test.
    """
    builder.resolve_labels_api = True
    builder._api_label_cache.clear()
    builder._api_consecutive_failures = 0
    builder._api_disabled = False
    builder._api_session = None
    try:
        yield builder
    finally:
        builder.resolve_labels_api = False
        builder._api_session = None
        builder._api_label_cache.clear()
        builder._api_consecutive_failures = 0
        builder._api_disabled = False


def test_term_label_api_fallback(api_builder):
    terms = [{"ontology_name": "cl", "label": "neuron", "is_defining_ontology": True}]
    api_builder._api_session = _FakeSession(response=_FakeResponse(_ols_payload(terms)))
    uri = rdflib.URIRef("http://purl.obolibrary.org/obo/CL_0000540")
    assert api_builder.term_label(uri) == "neuron"


def test_term_label_api_cached_once(api_builder):
    terms = [{"ontology_name": "cl", "label": "neuron", "is_defining_ontology": True}]
    session = _FakeSession(response=_FakeResponse(_ols_payload(terms)))
    api_builder._api_session = session
    uri = rdflib.URIRef("http://purl.obolibrary.org/obo/CL_0000540")
    assert api_builder.term_label(uri) == "neuron"
    assert api_builder.term_label(uri) == "neuron"
    assert session.calls == 1  # second call served from cache


def test_term_label_api_miss_negative_cached(api_builder):
    session = _FakeSession(response=_FakeResponse(_ols_payload([])))  # 0 terms
    api_builder._api_session = session
    uri = rdflib.URIRef("http://purl.obolibrary.org/obo/CL_9999999999")
    assert api_builder.term_label(uri) == "CL:9999999999"   # falls back to CURIE
    assert api_builder.term_label(uri) == "CL:9999999999"
    assert session.calls == 1  # miss is cached; not re-queried


def test_term_label_api_disabled_no_network(builder, monkeypatch):
    # The default shared builder is offline; the fetch helper must never run.
    def _boom(*a, **k):
        raise AssertionError("API must not be called when resolve_labels_api is False")
    monkeypatch.setattr(builder, "_fetch_label_from_ols", _boom)
    uri = rdflib.URIRef("http://purl.obolibrary.org/obo/CL_0000540")
    assert builder.term_label(uri) == "CL:0000540"


def test_term_label_api_network_error_returns_curie(api_builder):
    api_builder._api_session = _FakeSession(exc=requests.ConnectionError("down"))
    uri = rdflib.URIRef("http://purl.obolibrary.org/obo/CL_0000540")
    assert api_builder.term_label(uri) == "CL:0000540"   # never raises
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gocam_ttl.py -k "term_label_api" -v`
Expected: FAIL — `test_term_label_api_fallback` returns `"CL:0000540"` instead of `"neuron"` (the fallback isn't wired in yet).

- [ ] **Step 3: Write minimal implementation**

In `term_label()`, replace the final fallback (`gocam_ttl.py:1232-1233`):

```python
        # Fall back to CURIE
        return str(curie)
```

with:

```python
        # API fallback for terms not in the local GO/RO ontologies (anatomy:
        # CL/UBERON/EMAPA/WBbt/..., plus CHEBI/ECO/PR/...). Cache hits and
        # misses so any IRI is queried at most once per run.
        if self.resolve_labels_api:
            iri = str(uri)
            if iri not in self._api_label_cache:
                self._api_label_cache[iri] = self._fetch_label_from_ols(uri, curie)
            if self._api_label_cache[iri]:
                return self._api_label_cache[iri]

        # Fall back to CURIE
        return str(curie)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gocam_ttl.py -k "term_label_api" -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Run full test suite for regressions**

Run: `pytest -v`
Expected: All tests PASS (existing tests use the offline shared `builder`, so anatomy terms still resolve to CURIEs and prior assertions hold).

---

### Task 5: CLI `--no-label-api` flag in both entry points

Expose the opt-out on `gocam_ttl.py` and `debug_non_standard.py`, passing `resolve_labels_api=not args.no_label_api` to the builder.

**Files:**
- Modify: `src/gocam_unwinder/gocam_ttl.py:28` (add arg) and `gocam_ttl.py:1282` (builder call)
- Modify: `debug_non_standard.py:122` (add arg) and `debug_non_standard.py:125` (builder call)
- Test: `tests/test_gocam_ttl.py`

- [ ] **Step 1: Write the failing test**

`gocam_ttl.py` defines its `parser` at module top level (lines 13-28), so it can be imported and exercised directly — this tests the real production wiring, not a copy. Add to `tests/test_gocam_ttl.py`:

```python
def test_gocam_ttl_parser_has_no_label_api_flag():
    from gocam_unwinder.gocam_ttl import parser
    # Absence of the flag defaults to False (resolution on by default).
    assert parser.parse_args(["-o", "go.json"]).no_label_api is False
    # Presence of the flag is True (-> resolve_labels_api=not True=False).
    assert parser.parse_args(["-o", "go.json", "--no-label-api"]).no_label_api is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gocam_ttl.py::test_gocam_ttl_parser_has_no_label_api_flag -v`
Expected: FAIL with `AttributeError: 'Namespace' object has no attribute 'no_label_api'`

> `debug_non_standard.py` builds its parser inside `main()` (local `ap`), so it cannot be imported the same way; its identical one-line addition is verified by the manual smoke test in Verification.

- [ ] **Step 3: Write minimal implementation**

In `src/gocam_unwinder/gocam_ttl.py`, add after line 28 (`--date-change-report`):

```python
parser.add_argument('--no-label-api', action='store_true',
                    help="Disable OLS API fallback for resolving non-GO/RO term labels")
```

Change the builder construction at `gocam_ttl.py:1282`:

```python
    go_cam_graph_builder = GoCamGraphBuilder(args.ontology_filename, args.ro_filename,
                                             args.groups_yaml,
                                             resolve_labels_api=not args.no_label_api)
```

In `debug_non_standard.py`, add after the `--tsv-output` arg (line 122):

```python
    ap.add_argument("--no-label-api", action="store_true",
                    help="Disable OLS API fallback for resolving non-GO/RO term labels")
```

Change the builder construction at `debug_non_standard.py:125`:

```python
    builder = GoCamGraphBuilder(args.ontology, args.ro, args.groups_yaml,
                                resolve_labels_api=not args.no_label_api)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gocam_ttl.py::test_gocam_ttl_parser_has_no_label_api_flag -v`
Expected: PASS

- [ ] **Step 5: Run full test suite for regressions**

Run: `pytest -v`
Expected: All tests PASS

---

## Verification

After all tasks are complete, run these commands to confirm everything works:

```bash
# Full test suite (offline / hermetic)
pytest -v

# Manual smoke test: live OLS resolution on a single model with anatomy terms.
# Requires network. Pick a model that surfaces an anatomy term in the
# criteria-failure or remainders report (e.g. one with a CL/EMAPA/WBbt edge).
python src/gocam_unwinder/gocam_ttl.py \
  -m resources/test/MGI_MGI_1100089.ttl \
  -o target/go_20250601.json \
  -r resources/test/ro_20250723.owl \
  --criteria-fail-report /tmp/failures.tsv
# Inspect /tmp/failures.tsv: anatomy columns should show labels (e.g. "neuron",
# "brain"), not raw CURIEs (e.g. "CL:0000540").

# Confirm --no-label-api keeps the run fully offline (no network calls):
python src/gocam_unwinder/gocam_ttl.py \
  -m resources/test/MGI_MGI_1100089.ttl \
  -o target/go_20250601.json \
  -r resources/test/ro_20250723.owl \
  --no-label-api \
  --criteria-fail-report /tmp/failures_offline.tsv
# Anatomy columns remain CURIEs; runs without network access.

# Optional: remainders report path also resolves labels via the same builder:
python debug_non_standard.py resources/test/ \
  -o target/go_20250601.json -r resources/test/ro_20250723.owl \
  --tsv-output /tmp/remainders.tsv
```

**Expected final state:**
- [ ] All tests pass (`pytest -v`), with no live network in the suite.
- [ ] Anatomy / non-GO/RO terms resolve to labels in both reports when online.
- [ ] `--no-label-api` produces a fully offline run (terms stay as CURIEs).
- [ ] Repeated occurrences of the same ID trigger exactly one HTTP call per run.
- [ ] An offline run trips the circuit breaker after 5 failures and does not hang or raise.

---

## Notes

- `requests` is already importable in the project env (transitive via ontobio); no `requirements.txt` change is needed. If you want it explicit, adding `requests` to `requirements.txt` is optional and harmless.
- The OLS4 `terms?iri=` endpoint accepts the full IRI directly; `term_label()` already has the `URIRef`, so no CURIE→IRI expansion is needed for the request (the CURIE is only used for prefix-matching and the fallback return).
- The cache and circuit-breaker live on the builder instance. Both entry points build one builder and reuse it across all models, so the cache spans the whole run as intended.
- Per the repo convention this plan contains no `git add`/`git commit` steps — staging and commits are done manually by the user.
- Related code worth reading: `term_label` (`src/gocam_unwinder/gocam_ttl.py:1207-1233`), its consumers `print_non_standard_annotation_failed_checks` (`gocam_ttl.py:1235-1256`) and `debug_non_standard.py:172-234`, and the shared fixture `tests/conftest.py`.
