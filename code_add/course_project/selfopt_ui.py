from pathlib import Path


# Compatibility export for scripts that import the console HTML.
INDEX_HTML = (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")
