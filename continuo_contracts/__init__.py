"""Canonical JSON, the Python code projection, the content_hash folds and the fingerprint
field declaration that continuo and its producers share.

A producer's CI, continuo's topology-controller and the runtime harness import the same
functions, so they compute the same bytes and the same hashes for a node.
"""

import importlib.metadata

__version__ = importlib.metadata.version("continuo-contracts")
