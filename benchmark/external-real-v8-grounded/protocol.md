# External Real V8 Grounded Protocol

1. Cache official public full text outside the frozen revision.
2. Retrieve excerpt windows with event-target metadata only.
3. Store Source title, Source URL, and raw windows. Do not store Event, Target,
   Benchmark boundary, candidate, Oracle, formal-policy, gold, or Status labels.
4. Offline support scoring may read policy conclusion tokens. Those tokens are
   never written back into blind excerpts.
5. KEEP/READY events must have semantic support PASS. WARN and FAIL are replaced
   or dropped before freeze.
6. Blind model runs may read event metadata and stored raw windows only.
