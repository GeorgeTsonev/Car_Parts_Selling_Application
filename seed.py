import re, os
from sqlalchemy import create_engine, text
from app import engine, UPLOADS, BASE

def to_sqlite(s):
    s = re.sub(r"INT PRIMARY KEY AUTO_INCREMENT", "INTEGER PRIMARY KEY AUTOINCREMENT", s)
    s = re.sub(r"ENUM\([^)]*\)", "TEXT", s)
    s = s.replace(" ON UPDATE CURRENT_TIMESTAMP", "").replace("BIGINT UNSIGNED", "INTEGER")
    s = s.replace("TINYINT", "INTEGER").replace("CHAR(64)", "TEXT").replace("CHAR(2)", "TEXT")
    return re.sub(r"DEFAULT '(\w+)'(?=[,\s\n])", r"DEFAULT '\1'", s)

def stmts(sql):
    sql = re.sub(r"--[^\n]*", "", sql)
    return [s.strip() for s in sql.split(";") if s.strip()]

schema = open(f"{BASE}/schema_mysql.sql").read()
if engine.dialect.name == "sqlite": schema = to_sqlite(schema)
with engine.begin() as c:
    for s in stmts(schema): c.execute(text(s))
    for s in stmts(open(f"{BASE}/seed.sql").read()): c.execute(text(s.replace("\\n", "\n")))

STL = "solid p\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 10 0 0\nvertex 0 10 0\nendloop\nendfacet\nendsolid p\n"
with engine.connect() as c:
    for (k,) in c.execute(text("SELECT r2_object_key FROM Part_Files")):
        p = os.path.join(UPLOADS, k); os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write(STL)   # placeholder geometry
print("done")
