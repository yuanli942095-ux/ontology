# Stage access

Gold-assisted dataset curation ≠ Gold-assisted model inference.

| Stage | May read |
|---|---|
| Auto Policy Construction | `public/` only |
| Candidate Selection | `public/` + `repair-stage/` |
| Evaluation | `public/` + `repair-stage/` + `private/` |
| Hard Gate | `public/` + `repair-stage/` + `rules/` as upper bound only |
| Curation / support gate | all of the above, before freeze, never after model runs |
