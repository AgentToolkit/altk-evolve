# Session 2: Preference Recall Verification

Start a **new session** (no conversation history from Session 1). The gist from Session 1 should be automatically injected via the recall hook.

---

## Primary Verification Prompt

> I need to start a new data analysis project working with network telemetry data. What language and tools would you recommend I use?

### Expected Response WITH Gist Memory

The agent should recommend **Python and pandas**, referencing your known preference. Example:

> "Based on your preference for Python and pandas, I'd recommend using Python with pandas for the data analysis..."

### Expected Response WITHOUT Gist Memory

The agent gives a **generic recommendation** — likely mentioning both Python and R as options, or asking about your preference:

> "For network telemetry data analysis, popular options include Python (with pandas/numpy) or R (with tidyverse). Which do you prefer?"

---

## Additional Verification Prompts

These test whether the gist captured other signals:

**Prompt 2:**
> What's my background — do you know what kind of infrastructure I work with?

Expected (with gist): Mentions Kubernetes, container networking, cluster operations.

**Prompt 3:**
> If I need to do some quick data wrangling, which library should I reach for?

Expected (with gist): Recommends pandas specifically (not tidyverse or dplyr).

---

## Running the A/B Comparison

1. **With gist memory:** Ensure the gist entity from Session 1 exists in `.kaizen/entities/gist/` (Lite) or in the MCP backend
2. **Without gist memory:** Temporarily rename/remove the gist entity, or use a clean project directory
3. Run each verification prompt in both conditions and compare responses
