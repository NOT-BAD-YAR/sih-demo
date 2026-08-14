"""Endpoint agents (Phase 8).

`windows_agent` collects real Windows endpoint events and ships them as
normalized Common Event Schema events into Kafka — a SECONDARY data source
next to the simulator, exercising the exact same pipeline.
"""