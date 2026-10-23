"""Build dist/nexora-lambda.zip for AWS Lambda.  python scripts/package_lambda.py

Contains only: backend/*.py, frontend/*, data/sample_transactions.csv. No dependencies to install.
"""
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist" / "nexora-lambda.zip"
INCLUDE = [("backend", "*.py"), ("frontend", "*"), ("data", "sample_transactions.csv")]
SKIP = {"server.py"}  # local-only


def main():
    OUT.parent.mkdir(exist_ok=True)
    n = 0
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for folder, pattern in INCLUDE:
            for f in sorted((ROOT / folder).glob(pattern)):
                if f.is_file() and f.name not in SKIP:
                    z.write(f, f.relative_to(ROOT).as_posix())
                    n += 1
    print(f"Wrote {OUT} ({n} files, {OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
