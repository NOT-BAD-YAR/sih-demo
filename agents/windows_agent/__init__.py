"""Windows Agent — real endpoint event collection.

Readers poll OS sources, normalize raw records into the Common Event Schema
(simulator/schema.py), and a batching sender flushes them to Kafka through the
shared producer path (streaming/producer.py).
"""