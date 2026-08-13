"""Organization + schedule generators for the simulator.

Deterministic (seeded) generation of a realistic mid-size org:
100 employees, 50 devices, 20 servers, 10 apps, 5 departments + peer groups.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

DEPARTMENTS = ("HR", "Finance", "Developers", "DevOps", "Security")
PEER_GROUPS = ("HR", "Finance", "Developers", "DevOps", "Security", "Administrators", "Contractors")

N_EMPLOYEES = 100
N_DEVICES = 50
N_SERVERS = 20
N_APPS = 10

# geo: city -> (lat, lon). Used for impossible-travel distance calc.
GEO_CITIES = {
    "Chennai": (13.08, 80.27),
    "Delhi": (28.61, 77.21),
    "Mumbai": (19.08, 72.88),
    "Bangalore": (12.97, 77.59),
    "Hyderabad": (17.38, 78.49),
}

DEPARTMENT_RESOURCES = {
    "HR": {"HRMS", "Payroll-DB", "hr_share"},
    "Finance": {"Finance-DB", "ledger", "tax_docs"},
    "Developers": {"git", "build-server", "dev_db"},
    "DevOps": {"orchestrator", "monitoring", "ci-server"},
    "Security": {"SIEM", "vault", "dns_audit"},
}


@dataclass
class Employee:
    emp_id: str
    name: str
    department: str
    peer_group: str
    role: str
    sensitivity_tier: str  # public|internal|confidential|restricted
    geo: str
    device_id: str
    active_hours: tuple[int, int]
    download_scale_mb: tuple[int, int]  # normal daily download range in MB
    dormant: bool = False


@dataclass
class Device:
    device_id: str
    owner_emp_id: str
    capabilities: list[str] = field(default_factory=list)


@dataclass
class Server:
    server_id: str
    department: str
    peers: set[str] = field(default_factory=set)  # known peers (novel-peer baseline)


@dataclass
class App:
    app_id: str
    department: str
    sensitivity: str


@dataclass
class Organization:
    employees: list[Employee]
    devices: list[Device]
    servers: list[Server]
    apps: list[App]
    resource_owner: dict[str, str]   # resource -> owning department
    resource_sensitivity: dict[str, str]  # resource -> sensitivity tier


def _pick_role(dept: str, rng: random.Random) -> str:
    roles = {
        "HR": ["HR Executive", "HR Manager"],
        "Finance": ["Accountant", "Finance Analyst"],
        "Developers": ["Software Engineer", "Tech Lead"],
        "DevOps": ["DevOps Engineer", "SRE"],
        "Security": ["SOC Analyst", "Security Engineer"],
    }
    return rng.choice(roles[dept])


def _pick_sensitivity(rng: random.Random) -> str:
    return rng.choices(
        ("public", "internal", "confidential", "restricted"),
        weights=(0.4, 0.35, 0.2, 0.05),
        k=1,
    )[0]


def generate_org(seed: int = 42) -> Organization:
    rng = random.Random(seed)
    employees: list[Employee] = []
    devices: list[Device] = []

    # 50 devices first, so each employee can be assigned one deterministically.
    dev_ids = [f"LPT-{i:03d}" for i in range(1, N_DEVICES + 1)]
    devices = [
        Device(
            device_id=dev_ids[i],
            owner_emp_id="",
            capabilities=["usb", "process", "file"],
        )
        for i in range(N_DEVICES)
    ]

    for i in range(1, N_EMPLOYEES + 1):
        dept = rng.choice(DEPARTMENTS)
        city = rng.choice(list(GEO_CITIES))
        start = rng.choice([8, 9, 10])
        end = rng.choice([17, 18, 19])
        # devices: 100 employees, 50 laptops -> one laptop shared pattern: assign cyclically
        device = dev_ids[(i - 1) % N_DEVICES]
        peers = ("Analysts", "Admin") if dept == "Security" else ("Staff", "Manager")
        employees.append(
            Employee(
                emp_id=f"EMP{i:03d}",
                name=f"{dept}-User-{i}",
                department=dept,
                peer_group=dept,
                role=_pick_role(dept, rng),
                sensitivity_tier=_pick_sensitivity(rng),
                geo=city,
                device_id=device,
                active_hours=(start, end),
                download_scale_mb=(20, 60) if dept != "Developers" else (100, 400),
                dormant=False,
            )
        )
    # mark a fixed set dormant (plants dormant-account scenario targets)
    for idx in (5, 17, 33):
        employees[idx].dormant = True
        employees[idx].active_hours = (0, 0)

    for dev in devices:
        owner = next(e for e in employees if e.device_id == dev.device_id)
        dev.owner_emp_id = owner.emp_id

    servers: list[Server] = []
    for i in range(1, N_SERVERS + 1):
        dept = DEPARTMENTS[i % len(DEPARTMENTS)]
        srv = Server(server_id=f"SRV-{i:02d}", department=dept)
        # known peers: a handful of devices/other servers it talks to
        known = {rng.choice(dev_ids) for _ in range(rng.randint(3, 6))}
        srv.peers = known
        servers.append(srv)

    apps: list[App] = []
    for i in range(1, N_APPS + 1):
        dept = DEPARTMENTS[i % len(DEPARTMENTS)]
        apps.append(App(app_id=f"APP-{i:02d}", department=dept, sensitivity=_pick_sensitivity(rng)))

    resource_owner: dict[str, str] = {r: d for d, rs in DEPARTMENT_RESOURCES.items() for r in rs}
    resource_sensitivity: dict[str, str] = {
        r: ("restricted" if dept in ("Finance", "Security") else "confidential")
        for r, dept in resource_owner.items()
    }

    return Organization(
        employees=employees,
        devices=devices,
        servers=servers,
        apps=apps,
        resource_owner=resource_owner,
        resource_sensitivity=resource_sensitivity,
    )


def office_geo(employee: Employee) -> dict:
    """Resolve employee's office to a geo dict {city, lat, lon}."""
    lat, lon = GEO_CITIES[employee.geo]
    return {"city": employee.geo, "lat": lat, "lon": lon}


def department_resources(dept: str) -> set[str]:
    return DEPARTMENT_RESOURCES.get(dept, set())