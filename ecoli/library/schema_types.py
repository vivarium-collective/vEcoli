"""
=====================
E. coli Schema Types
=====================

Bigraph-schema type expressions for the E. coli whole-cell model's
structured numpy arrays and common port types.

These type strings can be used directly in ``inputs()`` and ``outputs()``
methods, and are parsed by bigraph-schema into proper ``Array`` schemas
with structured numpy dtypes.
"""

# ---------------------------------------------------------------------------
# Mass submass fields shared across all unique molecule types
# ---------------------------------------------------------------------------
_SUBMASS_FIELDS = (
    'massDiff_rRNA:float|massDiff_tRNA:float|massDiff_mRNA:float|'
    'massDiff_miscRNA:float|massDiff_nonspecific_RNA:float|massDiff_protein:float|'
    'massDiff_metabolite:float|massDiff_water:float|massDiff_DNA:float'
)

_UNIQUE_TAIL = f'{_SUBMASS_FIELDS}|_entryState:integer|unique_index:integer'

# ---------------------------------------------------------------------------
# Bulk molecule array
# ---------------------------------------------------------------------------
BULK_ARRAY = (
    'array[id:string|count:integer|'
    'rRNA_submass:float|tRNA_submass:float|mRNA_submass:float|'
    'miscRNA_submass:float|nonspecific_RNA_submass:float|protein_submass:float|'
    'metabolite_submass:float|water_submass:float|DNA_submass:float]'
)

# ---------------------------------------------------------------------------
# Unique molecule arrays
# ---------------------------------------------------------------------------
PROMOTER_ARRAY = f'unique_array[TU_index:integer|coordinates:integer|domain_index:integer|bound_TF:array[23,boolean]|{_UNIQUE_TAIL}]'

RNA_ARRAY = f'unique_array[TU_index:integer|transcript_length:integer|is_mRNA:boolean|is_full_transcript:boolean|can_translate:boolean|RNAP_index:integer|{_UNIQUE_TAIL}]'

ACTIVE_RNAP_ARRAY = f'unique_array[domain_index:integer|coordinates:integer|is_forward:boolean|{_UNIQUE_TAIL}]'

ACTIVE_RIBOSOME_ARRAY = f'unique_array[protein_index:integer|peptide_length:integer|mRNA_index:integer|pos_on_mRNA:integer|{_UNIQUE_TAIL}]'

ACTIVE_REPLISOME_ARRAY = f'unique_array[domain_index:integer|right_replichore:boolean|coordinates:integer|{_UNIQUE_TAIL}]'

FULL_CHROMOSOME_ARRAY = f'unique_array[division_time:float|has_triggered_division:boolean|domain_index:integer|{_UNIQUE_TAIL}]'

ORIC_ARRAY = f'unique_array[domain_index:integer|{_UNIQUE_TAIL}]'

CHROMOSOME_DOMAIN_ARRAY = f'unique_array[domain_index:integer|child_domains:array[2,integer]|{_UNIQUE_TAIL}]'

CHROMOSOMAL_SEGMENT_ARRAY = f'unique_array[boundary_molecule_indexes:array[2,integer]|boundary_coordinates:array[2,integer]|domain_index:integer|linking_number:float|{_UNIQUE_TAIL}]'

GENE_ARRAY = f'unique_array[cistron_index:integer|coordinates:integer|domain_index:integer|{_UNIQUE_TAIL}]'

DNAA_BOX_ARRAY = f'unique_array[coordinates:integer|domain_index:integer|DnaA_bound:boolean|{_UNIQUE_TAIL}]'

# ---------------------------------------------------------------------------
# Convenience mapping: port name → type expression
# ---------------------------------------------------------------------------
UNIQUE_TYPES = {
    'promoters': PROMOTER_ARRAY,
    'promoter': PROMOTER_ARRAY,
    'RNAs': RNA_ARRAY,
    'RNA': RNA_ARRAY,
    'active_RNAPs': ACTIVE_RNAP_ARRAY,
    'active_RNAP': ACTIVE_RNAP_ARRAY,
    'active_ribosome': ACTIVE_RIBOSOME_ARRAY,
    'active_ribosomes': ACTIVE_RIBOSOME_ARRAY,
    'active_replisomes': ACTIVE_REPLISOME_ARRAY,
    'active_replisome': ACTIVE_REPLISOME_ARRAY,
    'full_chromosomes': FULL_CHROMOSOME_ARRAY,
    'full_chromosome': FULL_CHROMOSOME_ARRAY,
    'oriCs': ORIC_ARRAY,
    'oriC': ORIC_ARRAY,
    'chromosome_domains': CHROMOSOME_DOMAIN_ARRAY,
    'chromosome_domain': CHROMOSOME_DOMAIN_ARRAY,
    'chromosomal_segments': CHROMOSOMAL_SEGMENT_ARRAY,
    'chromosomal_segment': CHROMOSOMAL_SEGMENT_ARRAY,
    'genes': GENE_ARRAY,
    'gene': GENE_ARRAY,
    'DnaA_boxes': DNAA_BOX_ARRAY,
    'DnaA_box': DNAA_BOX_ARRAY,
}


# ---------------------------------------------------------------------------
# Composite-schema helpers
# ---------------------------------------------------------------------------
def schema_node_to_plain_dict(core, schema_node):
    """Walk a resolved Schema Node tree and produce a plain dict of
    type-string leaves suitable for use in ``Process.inputs()`` /
    ``outputs()`` declarations.

    The bigraph-schema wire system only includes keys in the projected
    state when the input port is declared as a NESTED DICT (branch)
    with non-underscore keys. String types like ``'tree[node]'`` are
    leaves — values get retrieved but don't appear in the projected
    state dict. See memory: wire-projection-requires-nested-dicts.

    This helper converts large composite schemas (e.g. the cell
    tree's listeners / process_state Schema Node trees produced by
    ``Composite.schema['agents'][<id>][<slot>]`` after build) into
    the plain nested-dict-of-type-strings form the framework can
    project through wires.

    Args:
        core: the bigraph-schema core (provides ``render`` for leaf
            Schema Node objects).
        schema_node: a Schema Node tree (dict at branches, Schema
            Node at leaves).

    Returns:
        Plain Python dict: nested dicts at branches, type-string
        leaves (rendered via ``core.render``).
    """
    if isinstance(schema_node, dict):
        return {
            k: schema_node_to_plain_dict(core, v)
            for k, v in schema_node.items()
            if isinstance(k, str) and not k.startswith('_')
        }
    return core.render(schema_node)
