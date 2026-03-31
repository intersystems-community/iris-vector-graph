"""
IRISCapabilities — runtime capability detection for the ObjectScript/.cls layer.

Determined once by initialize_schema() and cached on the engine instance.
"""
from dataclasses import dataclass


@dataclass
class IRISCapabilities:

    objectscript_deployed: bool = False
    kg_built: bool = False
    nkg_built: bool = False
    graphoperators_deployed: bool = False
