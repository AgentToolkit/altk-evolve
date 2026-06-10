---
type: section-index
section: skills
verified_at: 2026-06-10
count: 6
---

# Skills

Wiki-resident, callable workflow pages. Each `<slug>/SKILL.md` is a structured procedural artifact: frontmatter + Overview + When To Use + Workflow + (optional) supporting scripts under `<slug>/scripts/`. At retrieval time, skills sort between clusters and atomic guidelines in `_index.jsonl` — directly callable, recall-preferred over guidelines for the same trigger.

| Skill | Description | Trigger | Verified at |
|---|---|---|---|
| **[aggregate-jsonl-records-top-n-by-key](aggregate-jsonl-records-top-n-by-key/SKILL.md)** | Stream-read many JSONL record files, sum a numeric field per group key and ta… | A task hands you several large line-delimited (JSONL) record files and asks f… | 2026-06-09 |
| **[author-jq-transform-with-yaml-roundtrip](author-jq-transform-with-yaml-roundtrip/SKILL.md)** | Write a single jq pipeline that filters, reshapes, formats and sorts an array… | A data-transformation task asks you to produce an output file with jq only (n… | 2026-06-09 |
| **[diagnose-and-bump-version-mismatch-on-upgrade](diagnose-and-bump-version-mismatch-on-upgrade/SKILL.md)** | Diagnose a dtype_backend / unexpected-keyword TypeError as a library-version … | A Python program fails with an 'unexpected keyword argument' TypeError (or a … | 2026-06-09 |
| **[install-python-data-stack-on-pep668-debian](install-python-data-stack-on-pep668-debian/SKILL.md)** | Install pandas + pyarrow (and friends) on a minimal Debian/Ubuntu host that h… | A task needs a Python data library (pandas, pyarrow, numpy) on a bare Debian/… | 2026-06-09 |
| **[join-csvs-on-heterogeneous-date-keys](join-csvs-on-heterogeneous-date-keys/SKILL.md)** | Match and join two CSV files on a date column whose rows use inconsistent for… | Combining two tabular sources keyed on a date that appears in different strin… | 2026-06-09 |
| **[repair-broken-system-pip-installation](repair-broken-system-pip-installation/SKILL.md)** | Repair a broken system-wide pip when `pip3`/`python3 -m pip` reports `No modu… | A Python install where pip is broken — `pip3 --version` or `python3 -m pip` f… | 2026-06-09 |
