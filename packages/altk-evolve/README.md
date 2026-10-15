# altk-evolve

The convenience distribution for [Evolve](https://github.com/AgentToolkit/altk-evolve).
Installs the matching `altk-evolve-core` release with server, CLI, FastEmbed and tracing.
The `altk_evolve` Python imports and `evolve` / `evolve-mcp` commands are provided
by core. This distribution contains no Python implementation.

Use `altk-evolve-core` to select your own extras without the default bundle.
All core extras are also available through this package for compatibility.
The full Phoenix server is opt-in: `pip install 'altk-evolve[phoenix]'`.
