"""The served `createProtein` must store the embedding it was given.

`api.gql.schema.Mutation.create_protein` routes to
`BiomedicalDomainResolver._create_protein_mutation`, not to
`api.gql.resolvers.mutation.Mutation` (whose SQL `test_gql_protein_mutations.py`
covers). The biomedical resolver checked the vector's width and then dropped it:
the protein was created, no `kg_NodeEmbeddings` row was written, and no error was
raised. The live test that would have caught it was marked skip for "VECTOR type
not available".
"""

import asyncio
import types as pytypes
from unittest.mock import MagicMock, patch

import pytest

from api.gql.types import CreateProteinInput
from examples.domains.biomedical.resolver import BiomedicalDomainResolver


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Loader:
    async def load(self, key):
        return {"id": key, "labels": ["Protein"], "properties": {}, "name": "X"}


def _create(inp):
    eng = MagicMock()
    eng.node_exists.return_value = False
    eng.create_node.return_value = True
    info = pytypes.SimpleNamespace(
        context={"db_connection": MagicMock(), "protein_loader": _Loader()}
    )
    with patch("iris_vector_graph.engine.IRISGraphEngine", return_value=eng):
        _run(BiomedicalDomainResolver(MagicMock())._create_protein_mutation(info, inp))
    return eng


def test_embedding_is_stored():
    emb = [0.5] * 768
    eng = _create(CreateProteinInput(id="PROTEIN:X", name="X", embedding=emb))
    eng.store_embedding.assert_called_once()
    args, kwargs = eng.store_embedding.call_args
    assert args[0] == "PROTEIN:X"
    assert list(args[1]) == emb


def test_no_embedding_stores_nothing():
    eng = _create(CreateProteinInput(id="PROTEIN:X", name="X"))
    eng.store_embedding.assert_not_called()


def test_wrong_width_is_refused_before_storing():
    with pytest.raises(Exception, match="768"):
        _create(CreateProteinInput(id="PROTEIN:X", name="X", embedding=[0.5] * 4))
