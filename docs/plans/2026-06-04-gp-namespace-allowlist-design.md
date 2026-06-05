# GP-Namespace Allowlist for `invalid_gp_mf_relation` Design

Issue: #22 (standard-annotation criteria). Branch: `issue-22-std-annot-criteria`.

## Problem

The `invalid_gp_mf_relation` check (Check 5 in `filter_out_non_std_annotations`,
`src/gocam_unwinder/gocam_ttl.py:929-935`) decides whether the non-MF endpoint of
an MF↔X edge is a gene product (GP) using a **blocklist**:

```python
other_curies = curie_util.contract_uri(str(other_type))
if other_curies:
    prefix = other_curies[0].split(":", 1)[0]
    if prefix in {"GO", "RO", "BFO"}:
        continue
# else: treated as a GP backbone candidate
```

Any non-MF entity whose CURIE prefix is *not* in `{GO, RO, BFO}` is treated as a
gene product. Anatomy / cell-type terms contract to other prefixes and slip
through as false "GPs":

- `EMAPA:16894` (mouse anatomy)
- `WBbt:0006796` (C. elegans anatomy)
- `CL:…` (cell type), `UBERON:…` (anatomy), etc.

An `MF —occurs_in→ EMAPA` (or `CL`, `WBbt`) extension edge is therefore mistaken
for a GP-MF backbone edge with the wrong relation and falsely flagged
`invalid_gp_mf_relation`.

### Why the prefix test is unreliable

Real MOD gene-product class URIs follow `http://identifiers.org/{prefix}/{id}`
and mostly **do not contract** via `curie_util` (only UniProtKB does). Their
shapes also vary:

| URI | Embedded prefix? |
|-----|------------------|
| `http://identifiers.org/mgi/MGI:1100089` | yes (`MGI:`) — the lone exception |
| `http://identifiers.org/sgd/S000005274` | no |
| `http://identifiers.org/zfin/ZDB-GENE-…` | no |
| `http://identifiers.org/uniprot/P12345` | no (contracts to `UniProtKB:`) |

The one uniform, reliable signal is the **path segment** (`{prefix}`).

## Solution: invert to a namespace-keyed allowlist

Replace the blocklist with a positive set of gene-product **namespace keys**,
sourced from the `mod_id_space` values in go-site `metadata/goex.yaml` plus
protein complexes (ComplexPortal) and the Protein Ontology (PR). Match on the
namespace key extracted directly from the entity URI.

### Change 1: new namespace-key set (class constant on `GoCamGraphBuilder`)

Added near `CAUSALLY_UPSTREAM_OF_OR_WITHIN` (`gocam_ttl.py:693`):

```python
# Gene-product identifier namespaces, keyed by the identifiers.org path segment
# (http://identifiers.org/{key}/{id}). Sourced from the mod_id_space values in
# go-site metadata/goex.yaml, plus protein complexes (ComplexPortal) and the
# Protein Ontology (PR). Distinguishes real gene products from anatomy/ontology
# entities (EMAPA, WBbt, CL, UBERON, ...) in the GP-MF backbone check.
GP_NAMESPACE_KEYS = {
    "uniprot", "mgi", "sgd", "wormbase", "rgd", "zfin", "flybase",
    "tair", "pombase", "japonicusdb", "cgd", "dictybase", "ecocyc",
    "xenbase", "complexportal", "pr",
}
```

Provenance — each `goex.yaml` `mod_id_space` maps to one key:

| goex.yaml `mod_id_space` | key | goex.yaml `mod_id_space` | key |
|--------------------------|-----|--------------------------|-----|
| UniProtKB | `uniprot` | MGI:MGI | `mgi` |
| TAIR | `tair` | RGD | `rgd` |
| WB | `wormbase` | SGD | `sgd` |
| CGD | `cgd` | JaponicusDB | `japonicusdb` |
| EcoCyc | `ecocyc` | PomBase | `pombase` |
| ZFIN | `zfin` | XenBase | `xenbase` |
| dictyBase | `dictybase` | FB | `flybase` |

Plus non-goex.yaml additions: `complexportal` (protein complexes), `pr`
(Protein Ontology).

Corpus-verified segments (full model corpus, ~3.9M GP URIs): `mgi, sgd, zfin,
rgd, uniprot, wormbase, pombase, xenbase, flybase, dictybase.gene, tair.locus`.
`cgd`, `ecocyc`, `japonicusdb` are absent from the current corpus but retained
from goex.yaml. The `.gene`/`.locus` sub-namespace suffixes are stripped by the
resolver (Change 2).

### Change 2: new resolver `_gene_product_namespace_key()`

New method on `GoCamGraphBuilder`. No regex, no new imports (string ops only):

```python
def _gene_product_namespace_key(self, type_uri):
    """Return the gene-product namespace key for a type URI, else None.

    - {http,https}://identifiers.org/{seg}/...  -> seg, truncated at '.',
      lowercased (handles dictybase.gene -> dictybase, tair.locus -> tair, and
      MGI's double-prefixed .../mgi/MGI:1100089 -> mgi)
    - ComplexPortal host URLs (ebi.ac.uk/complexportal) -> "complexportal"
    - OBO PURLs (.../obo/PREFIX_local) -> PREFIX lowercased (so PR_ -> "pr",
      GO_ -> "go", EMAPA_ -> "emapa", ...)
    Returns None for non-URIRef nodes and unrecognized URI shapes.
    """
```

The GP test is then: `self._gene_product_namespace_key(other_type) in self.GP_NAMESPACE_KEYS`.

OBO anatomy/ontology terms resolve to keys (`go`, `ro`, `bfo`, `emapa`, `wbbt`,
`cl`, `uberon`, …) that are absent from `GP_NAMESPACE_KEYS`, so only `pr` among
OBO terms counts as a GP — the blocklist behavior is preserved and extended.

### Change 3: integrate into Check 5 (GP test + allowed MF→GP relations)

In `filter_out_non_std_annotations` (`gocam_ttl.py:929-942`), delete the
`curie_util` blocklist block and the now-redundant `isinstance(other_type,
URIRef)` guard (folded into the resolver), and refine the per-direction relation
decision:

```python
enabled_by     = URIRef(relations.lookup_label("enabled by"))      # RO:0002333
contributes_to = URIRef(relations.lookup_label("contributes to"))  # RO:0002326
has_input      = URIRef(relations.lookup_label("has input"))       # RO:0002233
has_output     = URIRef(relations.lookup_label("has output"))      # RO:0002234
mf_source_extensions = {has_input, has_output}
...
# skip if the non-MF endpoint is not a gene product
if self._gene_product_namespace_key(other_type) not in self.GP_NAMESPACE_KEYS:
    continue   # anatomy/ontology target -> extension edge, not a GP-MF backbone

if mf_on == "source":
    if edge.property_uri == enabled_by:
        has_valid_backbone = True          # MF -enabled_by-> GP : the backbone
    elif edge.property_uri in mf_source_extensions:
        continue                           # MF -has_input/has_output-> GP : allowed extension
    else:
        invalid_candidates.append(edge.bnode_id)
elif mf_on == "target":
    if edge.property_uri == contributes_to:
        has_valid_backbone = True          # GP -contributes_to-> MF : the backbone
    else:
        invalid_candidates.append(edge.bnode_id)
```

`has_input` / `has_output` are accepted **only in the MF→GP direction**
(`mf_on == "source"`). In the GP→MF direction (`mf_on == "target"`) they remain
invalid candidates — only `contributes_to` is accepted there.

They are *allowed* (skipped), not treated as a backbone: an MF→GP input/output
edge is a legitimate extension, but it is not a GP-MF backbone, so it does **not**
set `has_valid_backbone`. Only `enabled_by` (MF source) / `contributes_to` (MF
target) prove a backbone exists. This keeps a genuinely malformed sibling edge
(e.g., `MF -part_of-> GP` with no `enabled_by` present) flaggable.

### What stays the same

- The MF detection (`_resolve_mf_type`), the skip of non-MF and MF↔MF edges,
  the `mf_on` source/target determination.
- The flagging condition: flag every invalid candidate only when the annotation
  has **no** valid backbone edge (`not has_valid_backbone and invalid_candidates`).
- Every other check (1–4) and the `failed_checks` reporting.

## Out of Scope

- **HGNC** (`identifiers.org/hgnc/…`, 104× in corpus): excluded. Not a
  `goex.yaml` `mod_id_space` (human → UniProtKB there). MF↔HGNC edges are neither
  validated as backbone nor flagged — ignored by this check.
- Relations other than `enabled_by`, `has_input`, `has_output` in the MF→GP
  direction (e.g., `part_of`, `regulates`) remain invalid candidates — only
  these three are accepted MF→GP relations.
- Rare non-MOD entity hosts (rnacentral, ncbi entrez, ebi cgi-bin,
  pseudomonas.com, arabidopsis.org servlets) resolve to `None` → not GPs →
  skipped. Conservative and intentional.
- No CLI arg, Makefile target, or runtime fetch — the list is a hardcoded
  constant per the chosen approach.

## Test Plan

New tests in `tests/test_gocam_ttl.py`:

1. **`test_gene_product_namespace_key`** — unit test of the resolver:
   - GP URIs → expected keys: `identifiers.org/mgi/MGI:1100089` → `mgi`;
     `identifiers.org/sgd/S000005274` → `sgd`;
     `identifiers.org/zfin/ZDB-GENE-…` → `zfin`;
     `identifiers.org/uniprot/P12345` → `uniprot`;
     `identifiers.org/wormbase/WB:…` → `wormbase`;
     `identifiers.org/dictybase.gene/…` → `dictybase`;
     `identifiers.org/tair.locus/…` → `tair`;
     `ebi.ac.uk/complexportal/complex/CPX-566` → `complexportal`;
     `obo/PR_000000001` → `pr`.
   - Non-GP URIs resolve to keys absent from `GP_NAMESPACE_KEYS`:
     `obo/EMAPA_16894`, `obo/WBbt_0006796`, `obo/CL_0000066`,
     `obo/GO_0003674`, `obo/RO_0002418`, `obo/BFO_0000050`.
   - HGNC URI resolves to `hgnc`, which is **not** in `GP_NAMESPACE_KEYS`.

2. **Regression (false-positive fix)** — a model with an `MF →(non-backbone
   relation)→ anatomy` edge (EMAPA / WBbt / CL) is **not** flagged
   `invalid_gp_mf_relation`. Candidate fixture: an existing C. elegans (WBbt) or
   MGI (EMAPA/CL) model in `resources/test/`, or a small new fixture if none has
   the exact shape. Confirm the bnode is absent from
   `failed_checks["invalid_gp_mf_relation"]`.

3. **Guard (true-positive preserved)** — a genuine `MF →(wrong relation, e.g.
   part_of)→ GP` edge in an annotation with no valid backbone is still flagged
   `invalid_gp_mf_relation`.

4. **MF→GP `has_input`/`has_output` allowed** — an `MF -has_input-> GP` (and
   `MF -has_output-> GP`) edge is **not** flagged `invalid_gp_mf_relation`, and
   (when it is the only GP-MF edge) does not set a valid backbone. Verify the
   bnode is absent from `failed_checks["invalid_gp_mf_relation"]`.

5. **Direction guard** — `has_input`/`has_output` are accepted only MF→GP: a
   `GP -has_input-> MF` edge (MF as target) with no `contributes_to` backbone is
   still flagged.

Run: `pytest tests/test_gocam_ttl.py -v` (requires `target/go_20250601.json` and
`resources/test/ro_20250723.owl`).
