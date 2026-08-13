"""Seed the demo organisation + dashboard accounts (Phase 3).

Loads the deterministic simulator org (100 employees / 50 devices / 20
servers / 10 apps / 7 peer groups) into PostgreSQL and creates the analyst
and admin dashboard accounts. Idempotent — safe to re-run after migrations.
"""

from __future__ import annotations

import logging
from typing import Any

from psycopg2.extensions import connection as PgConnection

from .conn import connect, dict_cursor
from .passwords import hash_password

log = logging.getLogger(__name__)

PEER_GROUP_NAMES = ("HR", "Finance", "Developers", "DevOps", "Security", "Administrators", "Contractors")

DEMO_ACCOUNTS = {
    "analyst": "analyst",
    "admin": "admin",
}


def _conn(dsn: str | None = None) -> PgConnection:
    return connect(dsn)


def seed_peer_groups(conn: PgConnection) -> int:
    with conn.cursor() as cur:
        for name in PEER_GROUP_NAMES:
            cur.execute("INSERT INTO peer_groups (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (name,))
    conn.commit()
    return len(PEER_GROUP_NAMES)


def _peer_group_map(conn: PgConnection) -> dict[str, int]:
    with dict_cursor(conn) as cur:
        cur.execute("SELECT id, name FROM peer_groups")
        return {row["name"]: row["id"] for row in cur.fetchall()}


def seed_users(conn: PgConnection, org: Any) -> int:
    groups = _peer_group_map(conn)
    inserted = 0
    with conn.cursor() as cur:
        for emp in org.employees:
            cur.execute(
                """
                INSERT INTO users (
                    emp_id, name, department, peer_group_id, role, sensitivity_tier,
                    primary_device_id, office_geo, last_activity_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NULL)
                ON CONFLICT (emp_id) DO NOTHING
                """,
                (
                    emp.emp_id,
                    emp.name,
                    emp.department,
                    groups.get(emp.peer_group, groups.get(emp.department)),
                    emp.role,
                    emp.sensitivity_tier,
                    emp.device_id,
                    emp.geo,
                ),
            )
            inserted += cur.rowcount
    conn.commit()
    return inserted


def _user_id_map(conn: PgConnection) -> dict[str, int]:
    with dict_cursor(conn) as cur:
        cur.execute("SELECT id, emp_id FROM users")
        return {row["emp_id"]: row["id"] for row in cur.fetchall()}


def seed_entities(conn: PgConnection, org: Any) -> int:
    users = _user_id_map(conn)
    inserted = 0
    with conn.cursor() as cur:
        for dev in org.devices:
            cur.execute(
                """
                INSERT INTO entities (entity_id, kind, owner_user_id, location, ip)
                VALUES (%s, 'device', %s, NULL, NULL)
                ON CONFLICT (entity_id) DO NOTHING
                """,
                (dev.device_id, users.get(dev.owner_emp_id)),
            )
            inserted += cur.rowcount
        for srv in org.servers:
            cur.execute(
                """
                INSERT INTO entities (entity_id, kind, owner_user_id, location, ip)
                VALUES (%s, 'server', NULL, %s, NULL)
                ON CONFLICT (entity_id) DO NOTHING
                """,
                (srv.server_id, srv.department),
            )
            inserted += cur.rowcount
        for app in org.apps:
            cur.execute(
                """
                INSERT INTO entities (entity_id, kind, owner_user_id, location, ip)
                VALUES (%s, 'app', NULL, %s, NULL)
                ON CONFLICT (entity_id) DO NOTHING
                """,
                (app.app_id, app.department),
            )
            inserted += cur.rowcount
    conn.commit()
    return inserted


def seed_accounts(conn: PgConnection, passwords: dict[str, str] | None = None) -> int:
    """Create analyst + admin demo accounts (idempotent). Passwords optional."""
    passwords = passwords or {
        "analyst": "analyst",
        "admin": "admin",
    }
    inserted = 0
    with conn.cursor() as cur:
        for username, role in (("analyst", "analyst"), ("admin", "admin")):
            cur.execute(
                "INSERT INTO users_accounts (username, password_hash, role, disabled)"
                " VALUES (%s, %s, %s, false)"
                " ON CONFLICT (username) DO NOTHING",
                (username, hash_password(passwords[role]), role),
            )
            inserted += cur.rowcount
    conn.commit()
    return inserted


def seed_org(conn: PgConnection, org: Any) -> dict[str, int]:
    """Seed everything from an org object. Returns counts by section."""
    return {
        "peer_groups": seed_peer_groups(conn),
        "users": seed_users(conn, org),
        "entities": seed_entities(conn, org),
    }


def main(argv: list[str] | None = None) -> None:
    import argparse
    from simulator.org import generate_org

    parser = argparse.ArgumentParser(prog="db.seed")
    parser.add_argument("--dsn", default=None, help="Postgres DSN (default: .env POSTGRES_DSN)")
    args = parser.parse_args(argv)

    with _conn(args.dsn) as conn:
        org = generate_org(seed=42)
        counts = seed_org(conn, org)
        accounts = seed_accounts(conn)
        print("seeded org:", counts)
        print("seeded demo accounts:", accounts)


if __name__ == "__main__":
    main()