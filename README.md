# Ontology Web - Fullstack Application

A complete ontology management platform with a real backend, featuring ontology CRUD, SPARQL querying, reasoning engines, knowledge graph visualization, and more.

## Architecture

- **Frontend**: React 19 + TypeScript + Vite + Ant Design + D3.js (from [ontology-web](https://github.com/catface996/ontology-web))
- **Backend**: Node.js + TypeScript + Express + sql.js (SQLite in WASM) + N3.js (RDF/SPARQL)

## Prerequisites

- **Node.js 20+** (required for both frontend and backend)
- **npm** (comes with Node.js)

No Docker, no WSL2, no external services required.

## Quick Start

### 1. Start the Backend

```bash
cd backend
npm install
npm run build   # Compile TypeScript to JavaScript
npm start       # Start the server on port 3001
```

The backend will:
- Initialize an embedded SQLite database (`data/ontology.db`)
- Seed realistic demo data (Smart Manufacturing ontology)
- Start the API server at `http://localhost:3001`

### 2. Start the Frontend

In a new terminal:

```bash
cd app
npm install
npm run dev     # Start the Vite dev server (port 5173)
```

The frontend is pre-configured to connect to `http://localhost:3001`.

### 3. Login

Open `http://localhost:5173` in your browser and login with:

| Username | Password | Role |
|----------|----------|------|
| `admin` | `admin123` | Administrator |
| `demo` | `demo123` | User |

## API Documentation

### Authentication Endpoints (`/auth`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/auth/api/v1/public/auth/login` | Login, returns JWT token |
| POST | `/auth/api/v1/public/auth/register` | Register new user |
| POST | `/auth/api/v1/public/auth/logout` | Logout |
| GET | `/auth/api/v1/me` | Get current user profile |
| POST | `/auth/api/v1/public/auth/user/list` | List users (paginated) |

### Core Endpoints (`/core`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/core/api/v1/ontology/list` | List ontologies |
| POST | `/core/api/v1/ontology/get` | Get ontology by ID |
| POST | `/core/api/v1/ontology/create` | Create ontology |
| POST | `/core/api/v1/ontology/update` | Update ontology |
| POST | `/core/api/v1/ontology/delete` | Delete ontology |
| POST | `/core/api/v1/ontology/tree` | Get ontology tree |
| POST | `/core/api/v1/ontology/graph` | Get knowledge graph data |
| POST | `/core/api/v1/ontology/validate` | Run validation checks |
| POST | `/core/api/v1/ontology/versions` | Get version history |

### Class Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/core/api/v1/class/list` | List classes |
| POST | `/core/api/v1/class/get` | Get class by ID |
| POST | `/core/api/v1/class/create` | Create class |
| POST | `/core/api/v1/class/update` | Update class |
| POST | `/core/api/v1/class/delete` | Delete class |
| POST | `/core/api/v1/class/logic/get` | Get class logic axioms |
| POST | `/core/api/v1/class/logic/update` | Update class logic axioms |
| POST | `/core/api/v1/class/logic/analyze` | NL axiom analysis |
| POST | `/core/api/v1/class/property/list` | List class properties |
| POST | `/core/api/v1/class/property/bindList` | Bind properties to class |
| POST | `/core/api/v1/class/property/unbind` | Unbind property |
| POST | `/core/api/v1/class/relation/list` | List class relations |
| POST | `/core/api/v1/class/relation/bindList` | Bind relations to class |
| POST | `/core/api/v1/class/relation/unbind` | Unbind relation |
| POST | `/core/api/v1/class/topology` | Get class topology |
| POST | `/core/api/v1/class/save-positions` | Save node positions |

### Property Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/core/api/v1/property/list` | List properties |
| POST | `/core/api/v1/property/get` | Get property by ID |
| POST | `/core/api/v1/property/create` | Create property |
| POST | `/core/api/v1/property/update` | Update property |
| POST | `/core/api/v1/property/delete` | Delete property |

### Relation Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/core/api/v1/relation/list` | List relations |
| POST | `/core/api/v1/relation/get` | Get relation by ID |
| POST | `/core/api/v1/relation/create` | Create relation |
| POST | `/core/api/v1/relation/update` | Update relation |
| POST | `/core/api/v1/relation/delete` | Delete relation |
| POST | `/core/api/v1/relation/logic/get` | Get relation logic rules |
| POST | `/core/api/v1/relation/logic/update` | Update relation logic rules |
| POST | `/core/api/v1/relation/logic/analyze` | NL rule analysis |

### Instance Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/core/api/v1/instance/list` | List instances (paginated) |
| POST | `/core/api/v1/instance/get` | Get instance by ID |
| POST | `/core/api/v1/instance/create` | Create instance |
| POST | `/core/api/v1/instance/update` | Update instance |
| POST | `/core/api/v1/instance/delete` | Delete instance |
| POST | `/core/api/v1/instance/property/get` | Get instance property values |
| POST | `/core/api/v1/instance/property/update` | Save property values |
| POST | `/core/api/v1/instance/relation/list` | List instance relations |
| POST | `/core/api/v1/instance/relation/add` | Add relation |
| POST | `/core/api/v1/instance/relation/remove` | Remove relation |
| POST | `/core/api/v1/instance/topology` | Get instance topology |
| POST | `/core/api/v1/instance/multi-topology` | Multi-class topology |
| POST | `/core/api/v1/instance/grouped-topology` | Grouped topology |

### Topology Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/core/api/v1/topology/list` | List topologies |
| POST | `/core/api/v1/topology/get` | Get topology by ID |
| POST | `/core/api/v1/topology/create` | Create topology |
| POST | `/core/api/v1/topology/update` | Update topology |
| POST | `/core/api/v1/topology/delete` | Delete topology |
| POST | `/core/api/v1/topology/save-positions` | Save positions |
| POST | `/core/api/v1/topology/reset-positions` | Reset positions |

### SPARQL Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/core/api/v1/sparql/query` | Execute SPARQL query |
| POST | `/core/api/v1/sparql/export` | Export RDF/Turtle |

### Reasoning Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/core/api/v1/reasoning/forward` | Forward chaining |
| POST | `/core/api/v1/reasoning/backward` | Backward chaining |
| POST | `/core/api/v1/reasoning/pattern` | Pattern matching |
| POST | `/core/api/v1/reasoning/constraint` | Constraint checking |
| POST | `/core/api/v1/reasoning/diff` | Ontology diff |
| POST | `/core/api/v1/reasoning/whatif` | What-if analysis |

### Data Source Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/core/api/v1/datasource/list` | List data sources |
| POST | `/core/api/v1/datasource/get` | Get data source |
| POST | `/core/api/v1/datasource/create` | Create data source |
| POST | `/core/api/v1/datasource/update` | Update data source |
| POST | `/core/api/v1/datasource/delete` | Delete data source |
| POST | `/core/api/v1/datasource/update-status` | Update connection status |
| POST | `/core/api/v1/datasource/schema/save` | Save discovered schema |
| POST | `/core/api/v1/datasource/mapping/list` | List field mappings |
| POST | `/core/api/v1/datasource/mapping/save` | Save mappings |
| POST | `/core/api/v1/datasource/mapping/delete` | Delete mapping |
| POST | `/core/api/v1/datasource/mapping/delete-batch` | Batch delete mappings |
| POST | `/core/api/v1/datasource/mapping/config/list` | List mapping configs |
| POST | `/core/api/v1/datasource/mapping/config/save` | Save mapping config |
| POST | `/core/api/v1/datasource/mapping/config/delete` | Delete mapping config |
| POST | `/core/api/v1/datasource/mapping/config/page` | Paginated mapping configs |

### Report Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/core/api/v1/report/list` | List reports |
| POST | `/core/api/v1/report/detail` | Get report detail |
| POST | `/core/api/v1/report/update` | Update report |
| POST | `/core/api/v1/report/delete` | Delete report |
| POST | `/core/api/v1/report-template/list` | List report templates |
| POST | `/core/api/v1/report-template/detail` | Get template detail |
| POST | `/core/api/v1/report-template/create` | Create template |
| POST | `/core/api/v1/report-template/update` | Update template |
| POST | `/core/api/v1/report-template/delete` | Delete template |
| POST | `/core/api/v1/report-template/versions` | List template versions |
| POST | `/core/api/v1/report-template/version-detail` | Get version detail |

### Namespace Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/core/api/v1/namespace/list` | List namespaces |
| POST | `/core/api/v1/namespace/create` | Create namespace |
| POST | `/core/api/v1/namespace/update` | Update namespace |
| POST | `/core/api/v1/namespace/delete` | Delete namespace |

### Admin Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/core/api/v1/notifications/list` | List notifications |
| POST | `/core/api/v1/notifications/mark-read` | Mark notifications read |
| POST | `/core/api/v1/tasks/list` | List tasks |
| POST | `/core/api/v1/tasks/create` | Create task |
| POST | `/core/api/v1/tasks/update` | Update task |
| POST | `/core/api/v1/changelog/list` | List change logs |
| POST | `/core/api/v1/settings/list` | List settings |
| POST | `/core/api/v1/settings/update` | Update setting |
| POST | `/core/api/v1/export/owl` | Export OWL |
| POST | `/core/api/v1/import/owl` | Import OWL |

## Data Storage

The backend uses **sql.js** (SQLite compiled to WebAssembly) for embedded persistence:
- Database file: `backend/data/ontology.db`
- Pure JavaScript, no native dependencies
- Auto-saved every 30 seconds and on graceful shutdown
- RDF/SPARQL operations powered by **N3.js** with a lightweight query engine

## Demo Data

The backend seeds a complete **Smart Manufacturing** ontology on first startup:

- **2 users** (admin/admin123, demo/demo123)
- **8 namespaces** (rdf, rdfs, owl, xsd, ont, foaf, dc, skos)
- **1 ontology** with 12 classes, 18 properties, 14 relations
- **49 instances** with property values and relations
- **4 topologies**, **5 versions**
- **5 data sources**, **12 field mappings**
- **5 reports**, **3 report templates**
- Validation results, notifications, tasks, change logs

## E2E Testing

Install Playwright:

```bash
npm install -g @playwright/test
npx playwright install chromium
```

Run tests:

```bash
cd backend && npm start &  # Start backend
cd app && npm run dev &    # Start frontend
npx playwright test         # Run E2E tests
```

## Project Structure

```
ontology-web/
├── app/                    # Frontend (React + Vite)
│   ├── src/
│   │   ├── services/       # API service layer
│   │   ├── pages/          # Page components (~50 pages)
│   │   ├── components/     # Reusable components
│   │   └── utils/          # Auth, request helpers
│   └── package.json
├── backend/                # Backend (Express + TypeScript)
│   ├── src/
│   │   ├── db/             # Database layer + seed data
│   │   ├── middleware/     # Auth middleware
│   │   ├── routes/         # API route handlers
│   │   └── index.ts        # Entry point
│   └── package.json
└── README.md
```

## Technology Decisions

| Decision | Choice | Reason |
|----------|--------|--------|
| Storage | sql.js (SQLite WASM) | Pure JS, no native deps, persistent |
| RDF/SPARQL | N3.js + custom engine | Pure JS, sufficient for demo queries |
| Auth | JWT + bcryptjs | Stateless, no session store needed |
| Framework | Express | Lightweight, proven, TypeScript-friendly |
| Frontend | Unchanged from original | Preserves existing UI/UX investment |

## Notes

- The backend auto-creates and seeds the database on first startup
- JWT tokens expire after 7 days
- All SPARQL queries execute against a live N3 Store built from the database
- Reasoning engines use deterministic rule-based logic (no external LLM calls)
- NL analysis uses keyword matching with confidence scoring (works without API keys)
