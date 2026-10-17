# app/experiments

Package for running, gating, and evaluating LLM experiments across RAG, SQL, and routing lanes.

## Glossary

- **canary**:
  - *judge canary*: Preflight stage in `core/spend_plan.py` validating judge connectivity and cost estimation.
  - *exact-wire probe*: Low-level probe in `sql_arm_canary.py`.
  - *case allowlist*: Allowed subset of cases in `sql_case_allowlist.py`.
- **freeze**:
  - *contract*: Static benchmark definition in `rag_experiment.py`.
  - *live assembly*: Resolved configuration snapshot in `rag_freeze.py`.
  - *blob projection*: Expected costs and limits in `freeze_projection.py`.
- **cell**: Coordinate in `core/work_matrix.py` representing `(case_id, arm_name, repeat)`.
- **oracle**: Ground-truth scoring engine (`core/protocol.py`).
- **lane**: Evaluation track (`rag`, `sql`, or `routing`).
- **Money vocabulary**:
  - **cap**: Hard ceiling on overall campaign spend.
  - **ceiling**: Max allowed spend for a single cell.
  - **forecast**: Estimated total spend for a run stage.
  - **reserve**: Funds committed in the spend ledger before executing calls.
  - **budget**: Allocated funds for a specific evaluation phase.

## Module Map

- `core/`: Task-agnostic experiment harness (spend plans, answer matrix, staging, checkpoints).
- `tasks/`: Task adapters (`rag_task.py`, `sql_task.py`, `routing_task.py`).
- `review/`: Post-hoc campaign audit and evidence verification.
- `gates/`: Quality, cost, and safety gates for experiment artifacts.

## Planned P10 Renames

- `work_matrix` -> `cell_execution`
- `answer_matrix` -> `answer_run`
- `routing_matrix` -> `router_policy_replay`
- `route_conformance` -> `route_probe`
- `estimand_guards` -> `sql_spend_eligibility`
- `spend_coordinator` -> `spend_ledger`
- `rag_freeze` -> `rag_freeze_assembly`
- `freeze_projection` -> `arm_blob_projection`
