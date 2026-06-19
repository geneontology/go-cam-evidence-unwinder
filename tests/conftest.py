import pytest

from gocam_unwinder.gocam_ttl import GoCamGraphBuilder

ONTOLOGY_FILE = "target/go_20250601.json"
RO_ONTOLOGY_FILE = "resources/test/ro_20250723.owl"
GROUPS_YAML_FILE = "resources/test/groups.yaml"


@pytest.fixture(scope="session")
def builder():
    """A GoCamGraphBuilder loaded once with GO ontology, RO ontology, and groups.yaml.

    Session-scoped so the (slow) GO ontology parse happens a single time for the
    whole test run. parse_ttl() builds a fresh GoCamGraph and only reads from the
    builder, so sharing one instance across tests is safe.
    """
    return GoCamGraphBuilder(ONTOLOGY_FILE, RO_ONTOLOGY_FILE, GROUPS_YAML_FILE,
                             resolve_labels_api=False)
