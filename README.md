# CarParts – discontinued car plastics, printable or buyable
Run (SQLite dev, zero setup):
    pip install -r requirements.txt
    python seed.py        # creates schema + loads seed.sql + placeholder STL files
    python app.py         # http://localhost:5000
MySQL: set DATABASE_URL=mysql+pymysql://user:pass@host/db, run schema_mysql.sql then seed.sql, then python app.py.
All catalogue data (brands, models, parts, hotspots) lives in seed.sql / the DB, never in code.
Files are stored in ./uploads in dev; swap `store_file()` in app.py for a Cloudflare R2 (boto3, S3 API) upload.
