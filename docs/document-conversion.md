# Document conversion pipeline

mysecureprint-server's only real "business logic" beyond user management and
auth is converting various document formats into something the target
printer understands. Apple AirPrint can talk to most printers, but business
printers often want PCL XL or vendor-specific PDLs. This server bridges
that gap.

## Supported input formats

| Format | Converter | Notes |
|---|---|---|
| PDF | Ghostscript | Native pass-through if printer speaks PDF/PostScript |
| PostScript (.ps) | Ghostscript | Pass-through |
| Word (.docx, .doc) | LibreOffice headless → PDF → Ghostscript | First conversion warm-start: ~5-15 s. Subsequent: <2 s. |
| Excel (.xlsx, .xls) | LibreOffice → PDF → Ghostscript | Same as Word |
| PowerPoint (.pptx, .ppt) | LibreOffice → PDF → Ghostscript | Same as Word |
| ODF (.odt, .ods, .odp) | LibreOffice → PDF → Ghostscript | Native |
| JPEG / PNG | Pillow + Ghostscript | Auto-scales to page size |
| Plain text (.txt) | enscript or built-in | Simple monospace |

## Output

PCL XL (default) — works on virtually every business laser printer. Other
output PDLs can be configured per printer in the Printix tenant's printer
settings.

## Debugging conversion failures

If a print job fails server-side:

1. Check `/admin/audit` for the upload event — it shows the original
   filename, size and chosen printer
2. Container logs (`az webapp log tail --resource-group $RG --name $APP`)
   show the `print_conversion.py` step output, including any LibreOffice
   stderr
3. Common issues:
   - **LibreOffice timeout on huge Office docs** — increase SKU to B2 or
     reject files > N MB at the upload endpoint
   - **Corrupted PDF input** — Ghostscript bails. Re-export the PDF
     cleanly.
   - **Missing fonts in LibreOffice** — the container ships the
     default LibreOffice font set. Custom fonts can be added by
     bind-mounting `/usr/share/fonts/truetype/<your-fonts>/` (Azure Files
     supports this).

## RAM sizing

LibreOffice is the heaviest dependency. For typical office documents:

- < 1 MB Word doc on F1 (1 GB) → mostly fine
- 10 MB Word doc on F1 → tight, occasional OOM
- 50 MB Office documents → B1 minimum, B2 safer

The container image (~600 MB) includes the full LibreOffice-core +
Ghostscript stack — no further apt-install needed.
