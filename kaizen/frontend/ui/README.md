# Kaizen Web UI

This directory contains the React/Vite frontend for the Kaizen project.

## Development Setup

The UI is built using React, Vite, TSX, and Tailwind-inspired custom CSS. It connects to the FastAPI backend provided by the Kaizen MCP server.

### Prerequisites
- Node.js (v18+ recommended)
- `npm` (Node Package Manager)

### Quick Start

1. Install dependencies:
   ```bash
   npm ci
   ```

2. Start the development server (with Hot Module Replacement):
   ```bash
   npm run dev
   ```
   The UI will be available at `http://localhost:5173`. 
   
   *Note: For the UI to fully load data, the Kaizen backend (`uv run uvicorn kaizen.frontend.mcp.mcp_server:app`) must also be running.*

### Building for Production

To build the static assets required for the python backend to serve the UI:

```bash
npm run build
```

This will compile the application into the `dist/` directory. The FastAPI backend is configured to automatically serve the contents of this `dist/` folder when users navigate to `/ui/`.

### Testing

Tests are written using Vitest and React Testing Library.

```bash
# Run tests
npm run test

# Run tests in watch mode
npm run test:watch
```
