                         ┌──────────────────────────────────────┐
                         │          ENTERPRISE ENVIRONMENT      │
                         │                                      │
                         │  Users      Devices      Servers     │
                         │  Apps       Databases    Cloud       │
                         │  Network    File Systems  IAM        │
                         └──────────────────┬───────────────────┘
                                            │
                                            │ Activity / Logs
                                            ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 1. EVENT COLLECTION LAYER                                                   │
│                                                                             │
│ Endpoint Agent │ Application Logs │ Network Logs │ Cloud APIs │ IAM Logs    │
│ File Events    │ Database Events  │ Config Events│ USB Events│ Auth Events  │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
                                   │ Raw security events
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 2. EVENT INGESTION & NORMALIZATION                                          │
│                                                                             │
│                    Python Event Processor                                   │
│                                                                             │
│  Raw Event → Validate → Normalize → Enrich → Common Event Schema           │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 3. REAL-TIME EVENT STREAMING                                                │
│                                                                             │
│                         APACHE KAFKA                                        │
│                                                                             │
│  authentication-events   file-events   network-events   privilege-events   │
│  cloud-events             config-events device-events                       │
└───────────────┬───────────────────────────────┬─────────────────────────────┘
                │                               │
                │                               │
                ▼                               ▼
       ┌───────────────────┐          ┌─────────────────────────┐
       │   PostgreSQL      │          │   UEBA ANALYTICS ENGINE │
       │                   │          │                         │
       │ Raw Events        │          │  4.1 Preprocessing      │
       │ Users             │          │          ↓              │
       │ Devices           │          │  4.2 Feature Engineering│
       │ Entities          │          │          ↓              │
       │ Profiles          │          │  4.3 Baseline Engine    │
       │ Alerts            │          │          ↓              │
       │ Incidents         │          │  4.4 ML Anomaly Model   │
       └───────────────────┘          │          ↓              │
                                      │  4.5 Context Analysis   │
                                      │          ↓              │
                                      │  4.6 Risk Scoring       │
                                      │          ↓              │
                                      │  4.7 Correlation        │
                                      └────────────┬────────────┘
                                                   │
                                                   │ Suspicious behavior
                                                   ▼
                                  ┌────────────────────────────────┐
                                  │ 5. ALERT & INCIDENT ENGINE     │
                                  │                                │
                                  │ Anomaly → Context → Correlation│
                                  │          ↓                     │
                                  │     Risk Score 0–100          │
                                  │          ↓                     │
                                  │     Alert / Incident           │
                                  └───────────────┬────────────────┘
                                                  │
                         ┌────────────────────────┼──────────────────────┐
                         │                        │                      │
                         ▼                        ▼                      ▼
              ┌──────────────────┐    ┌────────────────────┐   ┌─────────────────┐
              │ SOC DASHBOARD    │    │ RESPONSE ENGINE    │   │ BLOCKCHAIN      │
              │                  │    │                    │   │ AUDIT LAYER     │
              │ React + TS       │    │ Recommended        │   │                 │
              │                  │    │ actions             │   │ SHA-256 Hash    │
              │ Risk Overview    │    │                    │   │       ↓         │
              │ User Risk        │    │ MFA                 │   │ Hyperledger     │
              │ Entity Risk      │    │ Revoke Session     │   │ Fabric          │
              │ Alerts           │    │ Restrict Access    │   │       ↓         │
              │ Incidents        │    │ Isolate Device     │   │ Audit Proof     │
              │ Timeline         │    │                    │   │                 │
              │ Explainability   │    │ Prototype: Simulated│   │                 │
              └────────┬─────────┘    └────────────────────┘   └─────────────────┘
                       │
                       ▼
              ┌──────────────────────┐
              │   SOC / SECURITY     │
              │      ANALYST         │
              │                      │
              │ Investigates incident│
              │ Reviews evidence     │
              │ Takes action         │
              └──────────────────────┘