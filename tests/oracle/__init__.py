"""Independent references for IVG's answers (4.6.0 R306-R308): an expected answer comes from networkx, the
documentation or openCypher, never from IVG's own output.

`graphs` and `reference` (R306) generate small adversarial graphs and compute each algorithm's documented answer;
`values` and `spec` (R307) give adversarial property values and the documented answer per writer and reader;
`cypher_gen` (R308) generates Cypher reads over a seeded graph and answers each with a small openCypher interpreter.
"""
