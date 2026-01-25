#!/usr/bin/env python3
"""
Biomedical Domain Layer for Graph-AI

Domain-specific utilities and configurations for biomedical knowledge graphs.
Built on top of the generic iris_vector_graph module.
"""

from .biomedical_engine import BiomedicalGraphEngine
from .biomedical_schema import BiomedicalSchema

__all__ = ["BiomedicalGraphEngine", "BiomedicalSchema"]
