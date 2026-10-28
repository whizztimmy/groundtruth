"""GroundTruth sync server.

Deliberately a separate distribution from `groundtruth-agent`. The two share the contracts
in `docs/API.md` and `agent/groundtruth/types.py` by convention rather than by import, so
neither can be deployed only because the other happens to be installed: the agent runs on
a laptop with a GPU, the server runs wherever sync is convenient.

The server never sees pixels. No endpoint accepts an image, and nothing here decodes one.
That is an architectural constraint, so a future endpoint that takes a frame is a design
error and not a feature.
"""

from __future__ import annotations

__version__ = "0.1.0"
