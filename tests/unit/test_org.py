"""Phase 1 — Organization generator determinism + shape tests."""

import pytest

from simulator.org import (
    generate_org,
    DEPARTMENTS,
    Employee,
    Device,
    Organization,
    DEPARTMENT_RESOURCES,
)

pytestmark = pytest.mark.unit


class TestOrgShape:
    def test_employee_count(self):
        org = generate_org()
        assert len(org.employees) == 100

    def test_device_count(self):
        org = generate_org()
        assert len(org.devices) == 50

    def test_server_count(self):
        org = generate_org()
        assert len(org.servers) == 20

    def test_app_count(self):
        org = generate_org()
        assert len(org.apps) == 10

    def test_five_departments_covered(self):
        org = generate_org()
        depts = {e.department for e in org.employees}
        assert depts == set(DEPARTMENTS)

    def test_every_employee_has_device_and_geo(self):
        org = generate_org()
        for emp in org.employees[:50]:
            assert emp.device_id
            assert emp.geo in ("Chennai", "Delhi", "Mumbai", "Bangalore", "Hyderabad")

    def test_resource_owner_map_populated(self):
        org = generate_org()
        assert len(org.resource_owner) == sum(len(v) for v in DEPARTMENT_RESOURCES.values())

    def test_devices_owned(self):
        org = generate_org()
        owners = {d.owner_emp_id for d in org.devices}
        assert len(owners) == 50  # every device assigned to its owner


class TestDeterminism:
    def test_same_seed_same_org(self):
        a, b = generate_org(seed=7), generate_org(seed=7)
        assert [e.emp_id for e in a.employees] == [e.emp_id for e in b.employees]
        assert [(e.name, e.geo) for e in a.employees] == [(e.name, e.geo) for e in b.employees]

    def test_different_seed_differs(self):
        a, b = generate_org(seed=1), generate_org(seed=2)
        # astronomically unlikely to have identical department+geo+hours for 100 people
        sig = lambda org: [(e.department, e.geo, e.active_hours) for e in org.employees]
        assert sig(a) != sig(b)

    def test_dormant_employees_marked(self):
        org = generate_org()
        dormant = [e for e in org.employees if e.dormant]
        assert len(dormant) >= 3
        for emp in dormant:
            assert emp.active_hours == (0, 0)