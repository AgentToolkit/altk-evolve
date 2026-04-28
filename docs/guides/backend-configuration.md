# Backend Configuration Guide

Evolve supports multiple storage backends for entity storage. Choose the backend that best fits your needs.

## Available Backends

### Filesystem (Default)

The filesystem backend stores entities in local JSON files. It's the simplest option and requires no additional dependencies or setup.

**Search Method:** Simple case-insensitive text matching (no vector embeddings)

**Pros:**
- No external dependencies
- Easy to set up and use
- Good for development and testing
- Portable across systems
- Fast for small datasets

**Cons:**
- **No vector similarity search** - uses basic text matching only
- Not suitable for high-concurrency scenarios
- Limited scalability
- Less accurate semantic search compared to vector-based backends

**Configuration:**
```bash
# In .env file
EVOLVE_BACKEND=filesystem
EVOLVE_NAMESPACE_ID=evolve
```

**Installation:**
```bash
# No extra dependencies needed
uv sync
```

### PostgreSQL with pgvector

PostgreSQL with the pgvector extension provides robust, production-ready vector storage with ACID guarantees.

**Search Method:** Vector similarity search using sentence-transformers embeddings

**Pros:**
- **Semantic vector similarity search** - finds conceptually similar content
- Production-ready with ACID guarantees
- Excellent for concurrent access
- Efficient vector similarity search with HNSW indexing
- Automatic database creation with fallback logic
- Well-established ecosystem

**Cons:**
- Requires PostgreSQL server with pgvector extension
- More complex setup than filesystem
- Requires sentence-transformers model (downloaded on first use)

**Configuration:**
```bash
# In .env file
EVOLVE_BACKEND=postgres
EVOLVE_PG_HOST=127.0.0.1
EVOLVE_PG_PORT=5432
EVOLVE_PG_USER=your_username
EVOLVE_PG_PASSWORD=your_password  # pragma: allowlist secret
EVOLVE_PG_DBNAME=evolve
EVOLVE_PG_AUTO_CREATE_DB=true
EVOLVE_PG_BOOTSTRAP_DB=postgres
```

**Installation:**
```bash
# Install with pgvector support
uv sync --extra pgvector

# Ensure PostgreSQL is running with pgvector extension
# On macOS with Homebrew:
brew install postgresql pgvector
brew services start postgresql
```

**Bootstrap Database Fallback:**

When `EVOLVE_PG_AUTO_CREATE_DB=true`, Evolve will automatically create the target database if it doesn't exist. It tries multiple bootstrap databases in order:

1. User-configured bootstrap database (default: `postgres`)
2. `template1` (PostgreSQL default template database)
3. User's default database (often created automatically)

This ensures maximum compatibility across different PostgreSQL installations.

### Milvus

Milvus is a purpose-built vector database optimized for similarity search at scale.

**Search Method:** Advanced vector similarity search with multiple index types

**Pros:**
- **Highly optimized vector similarity search** - purpose-built for embeddings
- Excellent scalability
- High performance for large datasets
- Multiple index types (IVF, HNSW, etc.)
- Supports Milvus Lite for local development

**Cons:**
- More complex setup
- Requires Milvus server (or Milvus Lite)
- Requires sentence-transformers model (downloaded on first use)

**Configuration:**
```bash
# In .env file
EVOLVE_BACKEND=milvus
EVOLVE_NAMESPACE_ID=evolve
```

**Installation:**
```bash
# Install with Milvus support
uv sync --extra milvus
```

## Switching Backends

You can switch backends at any time by changing the `EVOLVE_BACKEND` environment variable. Note that data is not automatically migrated between backends.

## Recommendations

- **Development/Testing**: Use `filesystem` for simplicity (note: no vector search)
- **Production (Single Server)**: Use `postgres` for reliability, ACID guarantees, and semantic search
- **Production (High Scale)**: Use `milvus` for optimized vector search at scale
- **Quick Start**: Use `filesystem` (default) - no setup required, but limited to text matching
- **Semantic Search Required**: Use `postgres` or `milvus` for vector-based similarity search

## Identity & Isolation Model

Evolve uses a layered identity model. Understanding how each layer maps to physical storage is important when designing multi-tenant or multi-user deployments.

### Isolation Layers

| Layer | Purpose | Scope |
|---|---|---|
| **Namespace** (`namespace_id`) | Org / tenant boundary | First-class — physically separates data |
| **User** (`user_id`) | Individual user within a namespace | Metadata-only — stored in the entity `metadata` dict |
| **Session** (`session_id`) | Single agent session / conversation | Metadata-only — stored in the entity `metadata` dict |

### How Each Backend Stores These Layers

| Aspect | Filesystem | PostgreSQL (pgvector) | Milvus |
|---|---|---|---|
| **Namespace isolation** | One JSON file per namespace (`{id}.json`) | One table per namespace (`ns_{id}`) | One collection per namespace |
| **`user_id` storage** | `metadata.user_id` in JSON entity dict | `metadata` JSONB column (`metadata->>'user_id'`) | `metadata` JSON field (`metadata["user_id"]`) |
| **`session_id` storage** | `metadata.session_id` in JSON entity dict | `metadata` JSONB column (`metadata->>'session_id'`) | `metadata` JSON field (`metadata["session_id"]`) |
| **`user_id` index** | None (in-memory scan) | None (JSONB GIN possible but not created) | None (scan within collection) |
| **`session_id` index** | None (in-memory scan) | None (JSONB GIN possible but not created) | None (scan within collection) |
| **Filter mechanism** | Python dict match in `_entity_matches_filter` | `metadata @> '{"user_id": "..."}'::jsonb` | `metadata["user_id"] == "..."` expression |
| **Cross-namespace query** | Loop over namespace files | Loop over `ns_*` tables | Loop over collections |

### Key Implications

1. **Namespace is the only hard boundary.** Data in different namespaces is physically separated across all backends. There is no way to accidentally query across namespaces without explicitly iterating over them.

2. **`user_id` and `session_id` are soft filters.** They rely on query-time filtering against the `metadata` dict. There are no dedicated columns, foreign keys, or indexes — a missing filter silently returns all entities in the namespace regardless of owner.

3. **No indexes on identity metadata.** For small-to-medium namespaces this is fine. For large namespaces with frequent per-user queries, consider adding:
   - **PostgreSQL:** A GIN index on the `metadata` JSONB column, or a partial index on `(metadata->>'user_id')`.
   - **Milvus:** A scalar index on `metadata["user_id"]` (supported in Milvus 2.3+).
   - **Filesystem:** Not applicable — the backend scans in memory regardless.

4. **Public entity queries iterate namespaces.** `get_public_entities` loops over all namespaces and filters for `metadata.visibility = "public"`. This is O(N) in the number of namespaces and is not a concern at small scale but should be revisited for deployments with many tenants.

## Troubleshooting

### PostgreSQL Connection Issues

If you encounter "database does not exist" errors:

1. Ensure `EVOLVE_PG_AUTO_CREATE_DB=true` in your `.env` file
2. Verify PostgreSQL is running: `psql -l`
3. Check that your user has database creation privileges
4. Verify the bootstrap database exists (usually `postgres` or `template1`)

### Milvus Connection Issues

If Milvus fails to connect:

1. Ensure Milvus server is running
2. Check connection settings in your configuration
3. For Milvus Lite, ensure it's properly installed

### Filesystem Permissions

If you encounter permission errors with the filesystem backend:

1. Check that the application has write permissions to the data directory
2. Verify the `EVOLVE_NAMESPACE_ID` directory can be created