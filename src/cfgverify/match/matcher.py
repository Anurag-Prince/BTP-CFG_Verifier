"""L0 function pairing + L2 exact structural match + L3 anchors (+ L4 polarity, if [C1] requires it).

L0  pair functions by symbol name (decoration rules come from [A5]).
L2  directed-graph isomorphism (networkx VF2): entry fixed to entry, EXIT fixed to EXIT, and node attributes
    (out-degree, loop header, loop depth) must agree. Result: a block mapping.
L3  anchors: block content signatures and debug line info, used to choose between several possible mappings.
L4  polarity: check that IR true/false edges map onto the matching taken/fall-through edges ([C1]).
Status
    STUB (Part 5).
"""
