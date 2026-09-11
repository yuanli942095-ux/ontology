# Compile Notes

Use `main.tex` as the Chinese draft entrypoint.

On Overleaf:

1. Set **Main document** to `main.tex`.
2. Set **Compiler** to `pdfLaTeX`.
3. Recompile.

The included `latexmkrc` also uses pdfLaTeX. The previous `ctexart` version could fail with errors such as `ctexart fontset fandol is unavailable` or `Font unihei74 not found`; the current `main.tex` avoids `ctexart` and uses `CJKutf8` instead.
