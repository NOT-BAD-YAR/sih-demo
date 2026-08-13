"""Phase 2/3 — streaming CLI subcommand registration (no broker needed).

Proves every `python -m streaming ...` entrypoint the runbooks rely on is
wired, including the Phase 3 `persist` command (Kafka -> consumer ->
raw_events). Handlers are monkeypatched so no broker is contacted.
"""

import pytest

import streaming.__main__ as cli

pytestmark = pytest.mark.unit

COMMANDS = ("ensure-topics", "roundtrip", "dedupe", "persist", "demo", "lag")


class TestCliRegistration:
    def test_persist_subcommand_registered_and_dispatches(self, monkeypatch):
        captured: dict = {}

        def fake(args):
            captured["bootstrap"] = args.bootstrap
            captured["group"] = args.group

        monkeypatch.setattr(cli, "_cmd_persist", fake)
        cli.main(["persist", "--bootstrap", "localhost:9092", "--group", "manual-unit"])
        assert captured == {"bootstrap": "localhost:9092", "group": "manual-unit"}

    def test_all_expected_subcommands_registered(self, monkeypatch):
        for name in COMMANDS:
            seen: list[str] = []

            def fake(args, _name=name):
                seen.append(_name)

            fn = getattr(cli, f"_cmd_{name.replace('-', '_')}")
            monkeypatch.setattr(cli, fn.__name__, fake)
            cli.main([name])
            assert seen == [name], f"{name} not dispatched"