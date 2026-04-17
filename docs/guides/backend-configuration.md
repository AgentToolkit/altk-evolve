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