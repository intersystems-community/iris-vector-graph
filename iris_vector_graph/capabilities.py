"""
IRISCapabilities — runtime capability detection for the ObjectScript/.cls layer.

Determined once by initialize_schema() and cached on the engine instance.
"""
from dataclasses import dataclass, field
from typing import List


@dataclass
class IRISCapabilities:

    objectscript_deployed: bool = False
    kg_built: bool = False
    nkg_built: bool = False
    graphoperators_deployed: bool = False
    #: The packaged classes the namespace lacks, by name, ``Graph.KG.PageRank`` (the sentinel) first. Filled in by
    #: ``GraphSchema.check_objectscript_classes`` only when the sentinel is absent; empty when the classes are there.
    missing_classes: List[str] = field(default_factory=list)
