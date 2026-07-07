# PgSpy 🔍

**PgSpy** is a minimalist, fast, and powerful PostgreSQL structural and efficiency database auditor tool written in Python. It scans your database cluster, reveals relations, triggers, check constraints, and targets hidden performance bottlenecks—generating a single, beautifully formatted, comprehensive **HTML audit report**.

---

## 🚀 Key Features & Capabilities

PgSpy analyzes your PostgreSQL architecture by running native metadata catalog queries across several diagnostic vectors:

* **Unused Indexes Detector:** Identifies auxiliary or redundant indexes that consume system storage and slow down `INSERT`/`UPDATE` mutations without ever being used in analytical query paths.
* **Missing Foreign Key Indexes:** Highlights crucial target transaction columns mapped under Foreign Key constraints that lack proper indexes, preventing query planner nested loop bottlenecks.
* **Table Bloat Monitor:** Estimates row storage dead record thresholds (`n_dead_tup`) signaling tables that urgently require autovacuum tuning or optimization.
* **Sequential Scans Hotspots:** Exposes deep structural table bottlenecks where missing composite or targeted multi-column B-Tree indexes trigger continuous sequential table scans.
* **PostGIS & FDW Inspection:** Out-of-the-box native detection for PostGIS spatial columns, geography attributes, and Foreign Data Wrapper (FDW) server routing schemas.
* **Procedural Automations:** Decodes operational data schemas, database integrity `CHECK` constraints, and isolates procedural logic blocks inside `PL/pgSQL` database triggers.

---

## 🛠️ Installation & Setup (Using `venv`)

PgSpy utilizes a virtual Python environment approach (`venv`). This eliminates global package drift, bypasses modern PEP 668 restrictions on production systems, and requires **zero system compilers** or external library packages.

### 1. Initialize Virtual Environment
Navigate to your project directory containing `pgspy.py` and run:
```bash
python3 -m venv .venv
```

### 2. Activate the Environment
* **Linux / macOS:**
  ```bash
  source .venv/bin/activate
  ```
* **Windows (Command Prompt):**
  ```cmd
  .\.venv\Scripts\activate.bat
  ```
* **Windows (PowerShell):**
  ```powershell
  .\.venv\Scripts\Activate.ps1
  ```

### 3. Install Pre-compiled Dependencies
Install the standard `psycopg2-binary` framework. It loads optimized pre-compiled catalog wheels directly into the local environment context:
```bash
pip install -r requirements.txt
```

---

## 📖 CLI Parameters & Usage Guide

```text
usage: pgspy.py [-h] [--dbname DBNAME] [--user USER] [--host HOST] [--port PORT]
                [--password PASSWORD] [--socket] [--output OUTPUT] [--all-databases]
```

### Options:
* `--dbname`: Target database instance catalog name *(default: postgres)*
* `--user`: Database user role credential handler *(default: postgres)*
* `--host`: Server IP address or hostname. *Leave completely blank to trigger native Unix Sockets / PEER authentication.*
* `--port`: PostgreSQL engine port identifier *(default: 5432)*
* `--password`: Database security authentication plain token *(optional)*
* `--socket`: Forces local explicit Unix socket layer linkage overrides.
* `--output`: Target inspection report HTML file path location *(default: db_report.html)*
* `--all-databases`: Instructs PgSpy to loop sequentially across all active logical databases available on the targeted instance.

---

## 💡 Practical Examples

### Local Peer Auditing via Unix Sockets
Run this directly on your core database server instance for high-speed PEER authorization bypass:
```bash
./pgspy.py --dbname my_production_db --user postgres
```

### Remote Network Connection with Password Authentication
```bash
./pgspy.py --host 10.0.0.45 --port 5432 --dbname inventory_db --user data_analyst --password "SecurePass123"
```

### Cluster-Wide Automation (Multi-Tenant Architecture)
Analyze **every connectable logical database** across your ecosystem at once. PgSpy will process them in sequence and output individual isolated reports named `db_report_[database_name].html` automatically:
```bash
./pgspy.py --all-databases --user postgres
```

### Custom Report Allocation
```bash
./pgspy.py --dbname replica_db --output data_warehouse_audit.html
```

---

## 📄 License

This framework is licensed under the terms of the open-source **MIT License**. Check the root `LICENSE` text file for explicit copyright metadata distributions.
