# Infrastructure Risk Analyzer — Frontend

React 18 + TypeScript single-page app, built with Vite: the sidebar with the controls and results, the
MapLibre map, and the bar chart. All data and analysis come from the backend; see the
[root README](../README.md).

## Setup

Node.js 18 or newer to build (22 or 24 LTS recommended; `npm test` needs 22.18 or newer).

```bash
npm install
npm run dev        # http://localhost:5173
```

The development server forwards `/api/*` to the backend at `http://localhost:8000`; set `BACKEND_URL` to use
another address. It also reads `CARTO_API_KEY` from `../backend/.env` for the basemaps.

## Scripts

| Command | What it does |
|---|---|
| `npm run dev` | Development server with hot reload |
| `npm run build` | Type check, then the production build in `dist/` |
| `npm run preview` | Serve the production build locally |
| `npm test` | Unit tests in `tests/` (Node's own test runner) |
| `npm run lint` | ESLint, no warnings allowed |

The production build leaves the placeholder `"__CARTO_API_KEY__"` in `dist/index.html`; write the key into it
after every build (see the root README), or the default basemap shows a watermark.

## Structure

```
src/
├── App.tsx                # Top-level state; runs the analysis when an input changes
├── components/
│   ├── Sidebar.tsx        # Upload, layer, palette, threshold, vulnerability inputs, results, exports
│   ├── MapView.tsx        # MapLibre map: basemaps, hazard tiles, asset layers, pop-ups
│   ├── BarChart.tsx       # Results chart (Recharts)
│   ├── DisclaimerDialog.tsx
│   └── ui/                # Small shared components (button, input, select, slider, dialog)
├── lib/
│   ├── featurePopup.ts    # Map pop-up HTML; escapes everything that comes from the user's file
│   └── utils.ts
├── services/api.ts        # Calls to the backend
└── types/index.ts
public/                    # The user guide opened by the Guide button
tests/
```
