"""Research knowledge base: raw data → markdown → projection → reviewed graph.

The package is layered so the pipeline can run with or without a host:

* ``actions``, ``client``, ``markdown``, ``pipelines``, ``graph_store``, ``operations``,
  ``projection``, ``context`` and ``errors`` import no host at all. A test enforces it.
* ``plugin`` and ``lifecycle`` register the OAW ``knowledge.base`` card.
* ``service`` serves the same operations over HTTP and MCP, and drives them from a CLI.
"""

__version__ = "0.1.0"
