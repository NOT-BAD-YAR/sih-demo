1. The biggest issue: don't train Isolation Forest separately for every entity

Your plan currently says:

"Trained per entity (or per entity-type for sparse data)"

This is the first thing I would change.

Imagine:

EMP001 → 7 days of data
EMP002 → 20 days
EMP003 → 4 days
EMP004 → 200 events

Training a separate Isolation Forest for every employee can become unreliable because some users simply don't have enough history.

Better approach

Use three levels of behavioral modeling:

                 BEHAVIORAL MODEL
                       │
          ┌────────────┼────────────┐
          ↓            ↓            ↓
      Individual     Peer Group    Global
       Baseline       Baseline     Baseline
Individual baseline
EMP104 normally:
09:00 login
Chennai
Laptop-104
HR files
40 MB/day
Peer-group baseline
HR employees normally:
09:00–18:00
HR resources
Chennai
20–100 MB/day
Global baseline
Organization-wide:
normal login patterns
normal network behavior
normal transfer patterns

Then:

Individual deviation
        +
Peer-group deviation
        +
Global anomaly
        ↓
Final behavioral evidence

This is much more UEBA-like.

2. Your 7-day baseline is too short

Your plan currently proposes:

rolling window, e.g. last 7 days

I'd change that.

Seven days can work for a demo, but it has a serious problem:

Suppose someone normally works Monday–Friday.

If you only look at the previous 7 days, the system doesn't have much knowledge about:

weekly patterns
month-end activity
weekends
holidays
unusual but legitimate events
Better:
Warm-up:
14–30 days

Primary behavioral baseline:
30 days

Recent behavior:
last 1 hour / 6 hours / 24 hours

Long-term:
90-day historical statistics

You don't necessarily need 90 days of actual data for the first prototype.

You can simulate 30–90 days.

3. Don't make ML responsible for all five anomalies

Your current architecture already does something good here:

Isolation Forest + heuristic rule engine.

Keep this.

In fact, I would make the distinction even stronger.

Some anomalies are naturally rule/statistical problems.

For example:

Impossible travel

Don't ask Isolation Forest:

"Is this impossible travel?"

Explicitly calculate:

Login A:
Chennai
10:00

Login B:
Delhi
10:20

Distance = ~X km
Time = 20 minutes

Required travel speed = impossible

→ deterministic detector.

Dormant account

Again:

last_activity = 45 days ago
current_activity = 2:00 AM

→ deterministic detector.

Novel peer

Use:

known_peer_set
+
peer_frequency
+
recent_peer_history

rather than expecting Isolation Forest alone to understand it.

Volume spike

Use both:

current_volume / baseline_volume

and ML.

Overall architecture should therefore be:
                         EVENT
                           │
                           ▼
                  Feature Engineering
                           │
             ┌─────────────┴──────────────┐
             ↓                            ↓
      Statistical/Rules                 ML
             │                            │
             │                            │
             └──────────────┬─────────────┘
                            ↓
                     Context Engine
                            ↓
                       Risk Engine

This is better than:

Everything → Isolation Forest
4. I would upgrade your "context engine"

Your plan currently has:

Role sensitivity tier, department, file sensitivity, admin privilege, hour-of-day risk.

That's good, but I'd add relationship context.

For example:

WHO
 ↓
EMP104

DOING WHAT
 ↓
Access Finance DB

USING WHAT
 ↓
Unknown Device

FROM WHERE
 ↓
Delhi

WHEN
 ↓
2:13 AM

HOW MUCH
 ↓
4.8 GB

WHO/WHAT IS TARGET
 ↓
Sensitive Finance resource

Now your system has a proper behavioral context vector.

5. Add "peer groups" to the data model

Your behavioral_profiles table is good.

But I'd add something conceptually like:

peer_groups

For example:

HR
Finance
Developers
DevOps
Administrators
Security
Contractors

Then:

EMP104
Department = HR
Role = HR Executive
Peer Group = HR Employees

Why?

Because:

Developer downloading 5 GB

may be normal.

But:

HR employee downloading 5 GB

may be highly unusual.

This makes your risk engine much smarter.

6. Your risk score needs one serious upgrade

You currently have:

risk = f(ml_score, rule_hits × weights, context) and 0–100 bands.

That's fine as a starting point.

But don't simply do:

ML = 80
Rule = 20
Context = 30

80 + 20 + 30 = 130

and then arbitrarily cap at 100.

Instead, define three distinct concepts:

Anomaly

How unusual is this behavior?

0–1
Impact

How dangerous is the resource/action?

0–1
Confidence

How confident are we that this is actually meaningful?

0–1

Then conceptually:

Risk = Anomaly × Impact × Confidence

Then map to:

0–100

You can still have rule bonuses, but this gives your scoring system a much more defensible foundation.

7. Add a "cold start" mechanism

This is missing from the plan.

Imagine:

New employee

has only:

2 hours of history

What does the system know about them?

Almost nothing.

You shouldn't immediately say:

Unknown behavior = suspicious

Instead:

New user
   ↓
Use peer-group baseline
   ↓
Use organization baseline
   ↓
Gradually build individual baseline

This is called the cold-start problem.

It's a very good thing to mention during your SIH presentation.

8. Add baseline confidence

This is another upgrade I'd strongly recommend.

Instead of storing only:

average_download = 40 MB

store:

average_download = 40 MB
samples = 438
confidence = HIGH

For a new user:

average_download = 50 MB
samples = 12
confidence = LOW

Then the risk engine knows:

"I don't have enough history to confidently judge this person."

That's much more realistic.

9. Your correlation engine needs one small upgrade

Currently you say:

cluster related alerts on same entity_id + close time window.

That's a good MVP.

But it has a limitation.

Imagine:

EMP104
   ↓
Laptop-104
   ↓
Finance Server

The events belong to different entities.

So eventually you want:

User
 ↓
Device
 ↓
Server
 ↓
Resource
 ↓
Network destination

to be treated as an entity relationship graph.

You don't need a graph database.

PostgreSQL is sufficient initially.

Just model the relationships:

event.actor
event.source_entity
event.target_entity
event.peer_entity

Then correlation can say:

These five entities and eight events form one suspicious chain.

That's a significant UEBA improvement.

10. Kafka: one technical correction

Your plan says:

"Consumer ... deduped, exactly-once / at-least-once delivery"

I would clean this wording up.

Don't claim:

exactly-once + at-least-once

as though they're the same guarantee.

For your project, use:

Kafka delivery:
At-least-once

Application:
Idempotent processing

Database:
event_id UNIQUE constraint

So if Kafka delivers the same event twice:

event_id = EVT123

PostgreSQL says:

EVT123 already exists

and you don't process it twice.

That's simple and defensible.

11. I would NOT add Redis yet

Your current plan actually doesn't use Redis, which I think is good.

Earlier we discussed Redis, but after seeing the complete plan:

Don't add it right now.

You already have:

Kafka
+
PostgreSQL
+
Python analytics

That's enough.

Adding Redis gives you:

another service
another failure point
another deployment issue
another thing to explain

Only add Redis if you actually discover a performance need.

12. Your Windows Agent is ambitious — keep it, but don't let it block the project

This is probably the biggest scope risk.

Your plan wants:

Windows Security logs
Sysmon
filesystem events
USB events
process events
network events

That's impressive.

But building a reliable endpoint agent itself can become a whole project.

So I'd change the priority:

                DATA SOURCE
                    │
          ┌─────────┴─────────┐
          ↓                   ↓
      Simulator          Windows Agent
      PRIMARY             SECONDARY
          │                   │
          └─────────┬─────────┘
                    ↓
                  Kafka
First:

Get the simulator working.

Then:

Build the Windows agent.

Then:

Show:

"These events aren't only synthetic; our endpoint collector can produce real events."

That's a very strong demo.

13. Your simulator is actually one of the strongest parts

Don't think of the simulator as "fake data."

Build it properly.

For example:

100 employees
50 devices
20 servers
10 applications
5 departments

Then generate:

90 days normal behavior

Then inject:

Scenario 1 → Volume spike
Scenario 2 → Impossible travel
Scenario 3 → Out-of-scope access
Scenario 4 → Dormant account
Scenario 5 → Novel peer

Your plan already describes this approach.

I'd make it even better by injecting multi-stage scenarios.

Example:

Account Compromise

02:13 → New location
02:14 → New device
02:16 → Sensitive access
02:21 → Large download
02:24 → External upload

Then demonstrate that the system doesn't merely detect five anomalies—it correlates them into one incident.

That will be one of your strongest demonstrations.

14. Add evaluation metrics

This is the biggest thing I think is missing from the final plan.

You have exit criteria, but you need actual UEBA performance metrics.

Add:

Detection rate / Recall
How many planted anomalies did we detect?
Precision
How many alerts were actually meaningful?
False Positive Rate

Extremely important for UEBA.

Normal events incorrectly flagged
Detection latency
Event occurred
      ↓
Alert generated

How many seconds?
Incident correlation accuracy
5 related events
      ↓
1 incident

rather than:

5 unrelated alerts
Model performance

For your controlled simulator:

Precision
Recall
F1
False positive rate

Don't rely only on:

"Our model gives an anomaly score."

15. Add a proper baseline evaluation

This is particularly important because you're claiming:

"The system learns normal behavior."

You need to demonstrate that.

Run:

30 days normal data
       ↓
Build baseline
       ↓
Day 31
Inject anomaly
       ↓
Measure detection

Then compare:

Baseline-only
        vs
Baseline + Rules
        vs
Baseline + Rules + ML

That could actually become a very good technical evaluation for your SIH presentation.

16. Don't call the system "AI-powered" everywhere

I'd avoid marketing it as:

"AI detects insider threats."

Instead:

"Behavioral analytics combines statistical baselines, machine-learning anomaly detection, contextual rules, and event correlation."

That's more technically honest.

Your ML model detects:

anomalous behavior

It does not magically know:

malicious intent

That's an important distinction.

17. Your five anomalies are good, but I'd add a second-tier set

Keep your five canonical anomalies as the official MVP.

Your plan correctly identifies them as:

volume spike
impossible travel
access outside scope
dormant account activation
novel peer

Then, after those work, add:

Tier 2

6. New device
7. Privilege escalation
8. USB data transfer
9. External cloud upload
10. Suspicious configuration change
11. Unusual application usage

Don't make these mandatory for the first milestone.

18. Your response engine is good — but phrase it carefully

Your plan has:

Force MFA, revoke session, restrict access, isolate device.

Keep these.

But call them:

Recommended / simulated response actions

until you have actual enterprise integrations.

You already correctly specify that real enforcement is out of scope.

That's exactly right.

19. Blockchain — I agree with your decision now

Your current plan explicitly drops blockchain and says it can be added later.

After seeing the entire architecture, I agree with that decision for the first build.

Don't waste your initial development time on blockchain.

Your actual core is:

Event Collection
      ↓
Kafka
      ↓
Feature Engineering
      ↓
Behavioral Baseline
      ↓
Rules + ML
      ↓
Context
      ↓
Risk
      ↓
Correlation
      ↓
Incident

That is the thing that needs to be excellent.

Later, if the SIH evaluation strongly expects a blockchain component, add:

Incident
   ↓
SHA-256
   ↓
Hyperledger Fabric
   ↓
Audit Verification

as an independent module.

Your current architecture is actually better without blockchain at this stage.

20. One architectural change I would make

I'd change your analytics engine from:

Kafka
  ↓
Analytics Consumer
  ├── Baseline
  ├── ML
  ├── Rules
  ├── Context
  ├── Risk
  └── Correlation

to a clearer internal pipeline:

                    KAFKA
                      │
                      ▼
             ┌─────────────────┐
             │ Event Processor  │
             └────────┬────────┘
                      ▼
             ┌─────────────────┐
             │ Feature Engine   │
             └────────┬────────┘
                      ▼
             ┌─────────────────┐
             │ Baseline Engine  │
             └────────┬────────┘
                      │
            ┌─────────┴──────────┐
            ▼                    ▼
     ┌──────────────┐    ┌──────────────┐
     │ Rule Engine  │    │ ML Engine    │
     └──────┬───────┘    └──────┬───────┘
            │                   │
            └─────────┬─────────┘
                      ▼
             ┌─────────────────┐
             │ Context Engine   │
             └────────┬────────┘
                      ▼
             ┌─────────────────┐
             │ Risk Engine      │
             └────────┬────────┘
                      ▼
             ┌─────────────────┐
             │ Correlation      │
             └────────┬────────┘
                      ▼
                  INCIDENT

This makes the architecture easier to explain and easier for your team to implement independently.

21. What I would change in your phases

Your current 0–8 phases are good.

I'd modify them slightly:

PHASE 0
Infrastructure
       ↓
PHASE 1
Event Schema + Simulator
       ↓
PHASE 2
Kafka
       ↓
PHASE 3
PostgreSQL
       ↓
PHASE 4A
Behavioral Baselines
       ↓
PHASE 4B
Rule Detectors
       ↓
PHASE 4C
ML Anomaly Detection
       ↓
PHASE 4D
Context + Risk
       ↓
PHASE 4E
Correlation
       ↓
PHASE 5
Alerts + Incidents
       ↓
PHASE 6
FastAPI
       ↓
PHASE 7
React Dashboard
       ↓
PHASE 8
Windows Agent
       ↓
PHASE 9
Evaluation + Demo
       ↓
OPTIONAL
Blockchain
Why move Windows Agent later?

Because you don't want this happening:

Week 1:
Windows API problem ❌

Week 2:
Sysmon problem ❌

Week 3:
USB detection problem ❌

Week 4:
Still no UEBA engine

Instead:

Simulator
   ↓
UEBA works
   ↓
Dashboard works
   ↓
Then real endpoint data

Much safer.

22. One more thing: don't over-engineer the dashboard

Your dashboard list is good:

Overview
Users/Entities
Drill-down
Alerts
Incidents
Admin

That's enough.

Don't add:

50 different pages

Your most important page should be:

Entity Investigation

Because that's where you demonstrate:

Normal behavior
       ↓
Current behavior
       ↓
Deviation
       ↓
Reason
       ↓
Risk
       ↓
Timeline
       ↓
Incident
The upgraded architecture I'd lock

After reviewing the complete plan, this is what I would actually freeze as the project architecture:

                    ENTERPRISE
                        │
        ┌───────────────┼─────────────────┐
        ↓               ↓                 ↓
     Users           Devices           Servers
        │               │                 │
        └───────────────┼─────────────────┘
                        ↓
               EVENT COLLECTION
             Simulator + Windows Agent
                        ↓
                EVENT NORMALIZATION
                        ↓
                     KAFKA
                        ↓
                ┌───────┴────────┐
                ↓                ↓
            PostgreSQL      Analytics Engine
                                 │
                                 ▼
                         Feature Engineering
                                 │
                                 ▼
                       Behavioral Baselines
                       ┌─────────┼──────────┐
                       ↓         ↓          ↓
                  Individual   Peer       Global
                       └─────────┼──────────┘
                                 ↓
                       ┌─────────┴─────────┐
                       ↓                   ↓
                  Rule Engine          ML Engine
                       │              Isolation Forest
                       └─────────┬─────────┘
                                 ↓
                         Context Engine
                                 ↓
                           Risk Engine
                                 ↓
                       Correlation Engine
                                 ↓
                         Alert / Incident
                                 ↓
                    ┌────────────┴───────────┐
                    ↓                        ↓
              SOC Dashboard            Response Engine
                    │                        │
                    ↓                        ↓
              Investigation          Simulated Action
                    │
                    ↓
               Analyst Notes
                    │
                    ↓
             PostgreSQL Audit