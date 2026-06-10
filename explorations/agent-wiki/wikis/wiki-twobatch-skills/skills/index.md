---
type: section-index
section: skills
verified_at: 2026-06-10
count: 3
---

# Skills

Wiki-resident, callable workflow pages. Each `<slug>/SKILL.md` is a structured procedural artifact: frontmatter + Overview + When To Use + Workflow + (optional) supporting scripts under `<slug>/scripts/`. At retrieval time, skills sort between clusters and atomic guidelines in `_index.jsonl` — directly callable, recall-preferred over guidelines for the same trigger.

| Skill | Description | Trigger | Verified at |
|---|---|---|---|
| **[count-csv-rows-with-quoted-fields](count-csv-rows-with-quoted-fields/SKILL.md)** | Count CSV rows whose any field contains a literal comma (or other RFC-4180 sp… | When parsing CSV and a field might contain a comma, newline, or embedded quot… | 2026-06-08 |
| **[extract-jpeg-exif-camera-optics](extract-jpeg-exif-camera-optics/SKILL.md)** | Read camera-optics fields (LensModel, FocalLength, ISO, Aperture) from a JPEG… | When you need any non-GPS, non-IFD0 EXIF field from a JPEG and Pillow / piexi… | 2026-06-08 |
| **[read-image-format-dimensions](read-image-format-dimensions/SKILL.md)** | Read width/height (and version/bit-depth) from PNG, GIF, BMP, or WebP via std… | When you need dimensions/version/bit-depth from a binary image format and Pil… | 2026-06-08 |
