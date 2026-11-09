# CivicTrack

Serverless civic complaint management platform: citizens report issues with location and photo evidence, administrators triage and assign, field officers resolve with proof, and a 48-hour SLA is enforced automatically.

**Stack:** React 19 + Vite · Python 3.14 AWS Lambda · API Gateway (Cognito authorizer) · DynamoDB · S3 (private) · SES · EventBridge · AWS SAM. Region ap-south-1, stack `civictrack-dev`.

## Quick start (local, no AWS, no Docker)

```bash
cd backend && python -m venv .venv && .venv/Scripts/python -m pip install "moto[server]" boto3 pytest pytest-cov
```

```bash
backend/.venv/Scripts/python backend/tools/local_dev_server.py --seed
```

```bash
cd frontend && npm install && npm run dev
```

Open http://localhost:5173 and use the *Preview … Portal* buttons.

## Tests

```bash
backend/.venv/Scripts/python -m pytest backend/tests -q
```

```bash
cd frontend && npm test
```

## Documentation

| Document | Contents |
|---|---|
| [PROJECT_AUDIT.md](PROJECT_AUDIT.md) | What existed, defects and security findings |
| [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md) | Phased plan |
| [FEATURE_STATUS.md](FEATURE_STATUS.md) | Done / partial / not done, local vs live |
| [ARCHITECTURE_OVERVIEW.md](ARCHITECTURE_OVERVIEW.md) | System, lifecycle, SLA design |
| [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) | Deploy, rollback, maintenance |
| [AWS_RESOURCE_INVENTORY.md](AWS_RESOURCE_INVENTORY.md) | Every AWS resource |
| [AWS_BUDGET_AUDIT.md](AWS_BUDGET_AUDIT.md) · [AWS_COST_MODEL.xlsx](AWS_COST_MODEL.xlsx) | Cost estimates (not billing) |
| [docs/API_REFERENCE.md](docs/API_REFERENCE.md) | Endpoints |
| [docs/DATABASE_SCHEMA.md](docs/DATABASE_SCHEMA.md) | Table and S3 layout |
| [docs/SECURITY_ARCHITECTURE.md](docs/SECURITY_ARCHITECTURE.md) | Controls and residual risks |
| [docs/UML_DIAGRAMS.md](docs/UML_DIAGRAMS.md) | Use case, class, sequence, activity, deployment, component, DFD |
| [docs/TESTING_REPORT.md](docs/TESTING_REPORT.md) | What was tested and results |
| [docs/USER_MANUAL.md](docs/USER_MANUAL.md) | How each role uses the app |
