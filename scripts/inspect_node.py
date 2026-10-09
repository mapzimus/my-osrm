"""List every way touching an OSM node (debug helper for stranded ferry docks).

    python inspect_node.py <file.osm.pbf> <node_id> [<node_id> ...]
"""

import sys

import osmium

targets = {int(n) for n in sys.argv[2:]}
for w in osmium.FileProcessor(sys.argv[1], osmium.osm.WAY):
    hits = targets.intersection(n.ref for n in w.nodes)
    if hits:
        tags = {k: v for k, v in w.tags if k in
                {"highway", "route", "access", "motor_vehicle", "name", "man_made", "railway"}}
        print(sorted(hits), f"w{w.id}", tags)
