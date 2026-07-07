#!/usr/bin/env python3
# ==============================================================================
# PgSpy - PostgreSQL Structural & Efficiency Auditor CLI Tool
# Copyright (c) 2026 Maciej Kupiec / bitebyte.pl
# Licensed under the MIT License (see LICENSE file for details)
# ==============================================================================

import json
import psycopg2
from psycopg2.extras import RealDictCursor
import argparse
import sys

C_BLUE = "\033[94m"
C_GREEN = "\033[92m"
C_YELLOW = "\033[93m"
C_RED = "\033[91m"
C_DIM = "\033[2m"
C_RESET = "\033[0m"

QUERIES = {
    "tables": """
        SELECT 
            c.relname AS table_name,
            n.nspname AS schema_name,
            CASE 
                WHEN c.relkind = 'f' THEN 'Foreign (FDW)'
                ELSE pg_size_pretty(pg_total_relation_size(c.oid))
            END AS total_size,
            CASE 
                WHEN c.relkind = 'f' THEN 0 
                ELSE pg_total_relation_size(c.oid)
            END AS total_size_bytes,
            CASE 
                WHEN c.relkind = 'p' THEN 'Yes (Partition Parent)'
                WHEN i.inhrelid IS NOT NULL THEN 'Yes (Physical Partition)'
                WHEN c.relkind = 'f' THEN 'No (Foreign FDW Table)'
                ELSE 'No'
            END AS is_partitioned
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_inherits i ON i.inhrelid = c.oid
        WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
          AND c.relkind IN ('r', 'p', 'f')
        ORDER BY total_size_bytes DESC;
    """,
    "foreign_tables": """
        SELECT 
            c.relname AS table_name,
            srv.srvname AS foreign_server_name,
            ft.ftoptions AS foreign_options
        FROM pg_foreign_table ft
        JOIN pg_class c ON ft.ftrelid = c.oid
        JOIN pg_foreign_server srv ON ft.ftserver = srv.oid;
    """,
    "relations": """
        SELECT
            kcu.table_name, kcu.column_name, tc.constraint_type,
            ccu.table_name AS foreign_table_name, ccu.column_name AS foreign_column_name
        FROM information_schema.table_constraints AS tc 
        JOIN information_schema.key_column_usage AS kcu ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
        LEFT JOIN information_schema.constraint_column_usage AS ccu ON ccu.constraint_name = tc.constraint_name
        WHERE tc.table_schema NOT IN ('pg_catalog', 'information_schema')
          AND tc.constraint_type IN ('PRIMARY KEY', 'FOREIGN KEY');
    """,
    "indexes": """
        SELECT tablename AS table_name, indexname AS index_name, indexdef AS index_definition
        FROM pg_indexes WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
        ORDER BY tablename;
    """,
    "triggers": """
        SELECT 
            trg.tgrelid::regclass::text AS table_name, trg.tgname AS trigger_name,
            CASE WHEN (trg.tgtype & 2) = 2 THEN 'BEFORE' WHEN (trg.tgtype & 64) = 64 THEN 'INSTEAD OF' ELSE 'AFTER' END AS timing,
            concat_ws(' OR ', CASE WHEN (trg.tgtype & 4) = 4 THEN 'INSERT' END, CASE WHEN (trg.tgtype & 8) = 8 THEN 'DELETE' END, CASE WHEN (trg.tgtype & 16) = 16 THEN 'UPDATE' END, CASE WHEN (trg.tgtype & 32) = 32 THEN 'TRUNCATE' END) AS event,
            CASE WHEN (trg.tgtype & 1) = 1 THEN 'Each Row (ROW)' ELSE 'Entire Statement (STATEMENT)' END AS level,
            p.prosrc AS definition
        FROM pg_trigger trg
        JOIN pg_proc p ON p.oid = trg.tgfoid
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE NOT trg.tgisinternal AND n.nspname NOT IN ('pg_catalog', 'information_schema');
    """,
    "checks": """
        SELECT tc.table_name, tc.constraint_name, cc.check_clause AS definition
        FROM information_schema.table_constraints tc
        JOIN information_schema.check_constraints cc ON tc.constraint_name = cc.constraint_name
        WHERE tc.table_schema NOT IN ('pg_catalog', 'information_schema') AND tc.constraint_name NOT LIKE '%_not_null';
    """,
    "unused_indexes": """
        SELECT 
            i.relname AS table_name, i.indexrelname AS index_name,
            pg_size_pretty(pg_relation_size(i.indexrelid)) AS index_size, i.idx_scan AS number_of_scans
        FROM pg_stat_user_indexes i
        JOIN pg_index d ON i.indexrelid = d.indexrelid
        WHERE NOT d.indisunique AND i.idx_scan = 0 AND schemaname = 'public'
        ORDER BY pg_relation_size(i.indexrelid) DESC LIMIT 20;
    """,
    "missing_fk_indexes": r"""
        SELECT 
            tc.table_name, kcu.column_name, tc.constraint_name
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public'
          AND NOT EXISTS (
              SELECT 1 FROM pg_index i 
              JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(string_to_array(textin(int2vectorout(i.indkey)), ' ')::int2[])
              WHERE i.indrelid = (tc.table_name)::regclass AND a.attname = kcu.column_name
          )
        ORDER BY tc.table_name;
    """,
    "table_bloat": """
        SELECT 
            relname AS table_name, n_live_tup AS live_rows, n_dead_tup AS dead_rows,
            ROUND((n_dead_tup::numeric / GREATEST(n_live_tup + n_dead_tup, 1)::numeric) * 100, 2) AS dead_rows_percentage,
            coalesce(to_char(last_autovacuum, 'YYYY-MM-DD HH24:MI'), 'Never') AS last_autovacuum
        FROM pg_stat_user_tables
        WHERE (n_dead_tup > 1000 AND n_live_tup > 0) OR last_autovacuum IS NULL
        ORDER BY dead_rows DESC LIMIT 20;
    """,
    "seq_scans": """
        SELECT 
            relname AS table_name, seq_scan AS total_seq_scans, seq_tup_read AS total_rows_read, idx_scan AS total_index_scans
        FROM pg_stat_user_tables WHERE seq_scan > 50
        ORDER BY seq_tup_read DESC LIMIT 20;
    """,
    "postgis_status": """
        SELECT 
            f_table_name AS table_name, f_geometry_column AS column_name, type, srid
        FROM geometry_columns WHERE f_table_schema = 'public';
    """
}

def get_all_databases(db_config):
    try:
        conn = psycopg2.connect(**db_config)
        cur = conn.cursor()
        cur.execute("SELECT datname FROM pg_database WHERE datistemplate = false AND datallowconn = true;")
        dbs = [row[0] for row in cur.fetchall()]
        cur.close()
        conn.close()
        return dbs
    except Exception as e:
        print(f"{C_RED}[X] Failed to list databases: {e}{C_RESET}")
        return []

def fetch_db_metadata(db_config):
    print(f"{C_BLUE}[*] Spying on database: {db_config.get('dbname')}{C_RESET}")
    try:
        conn = psycopg2.connect(**db_config)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        data = {}
        for key, sql in QUERIES.items():
            print(f"  {C_DIM}L-> Fetching module:{C_RESET} {C_YELLOW}{key}{C_RESET}...\033[K", end="\r")
            try:
                cur.execute(sql)
                data[key] = cur.fetchall()
            except Exception as sql_err:
                conn.rollback()
                data[key] = []
                
                err_str = str(sql_err)
                if key == "postgis_status" and "geometry_columns" in err_str:
                    print(f"\n{C_YELLOW}[!] Module [{key}] skipped: PostGIS extension not found{C_RESET}")
                else:
                    err_msg = err_str.split('\n')[0]
                    print(f"\n{C_YELLOW}[!] Module [{key}] skipped: {err_msg}{C_RESET}")
                
        cur.close()
        conn.close()
        print(f"\n{C_GREEN}[+] Metadata successfully collected.{C_RESET}")
        return data
    except Exception as e:
        print(f"\n{C_RED}[X] Connection error to [{db_config.get('dbname')}]: {e}{C_RESET}")
        return None

def generate_html_report(data, dbname, filename):
    print(f"{C_BLUE}[*] Generating HTML report -> {filename}{C_RESET}")
    
    tables_dict = {t['table_name']: {
        'info': t, 'relations': [], 'indexes': [], 'triggers': [], 'checks': [], 'fdw_info': None, 'postgis': []
    } for t in data.get('tables', [])}
    
    for f in data.get('foreign_tables', []):
        if f['table_name'] in tables_dict: tables_dict[f['table_name']]['fdw_info'] = f
    for r in data.get('relations', []):
        if r['table_name'] in tables_dict: tables_dict[r['table_name']]['relations'].append(r)
    for i in data.get('indexes', []):
        if i['table_name'] in tables_dict: tables_dict[i['table_name']]['indexes'].append(i)
    for t in data.get('triggers', []):
        if t['table_name'] in tables_dict: tables_dict[t['table_name']]['triggers'].append(t)
    for c in data.get('checks', []):
        if c['table_name'] in tables_dict: tables_dict[c['table_name']]['checks'].append(c)
    if 'postgis_status' in data:
        for g in data['postgis_status']:
            if g['table_name'] in tables_dict: tables_dict[g['table_name']]['postgis'].append(g)

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>PgSpy Report - {dbname}</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.datatables.net/1.13.4/css/dataTables.bootstrap5.min.css">
    <style>
        body {{ background-color: #f4f6f9; color: #212529; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
        .card {{ margin-bottom: 2rem; box-shadow: 0 4px 12px rgba(0,0,0,0.05); border: none; border-radius: 10px; background-color: #ffffff; }}
        .card-header {{ font-weight: bold; font-size: 1.1em; }}
        .badge-pk {{ background-color: #0d6efd; }}
        .badge-fk {{ background-color: #198754; }}
        pre {{ background-color: #212529; color: #f8f9fa; padding: 14px; border-radius: 8px; font-size: 0.85em; font-family: "Fira Code", monospace; overflow-x: auto; }}
        .table-anchor {{ text-decoration: none; font-weight: 600; color: #0d6efd; }}
        .table-anchor:hover {{ text-decoration: underline; }}
        .alert-card {{ border-left: 5px solid #dc3545; }}
        .warn-card {{ border-left: 5px solid #ffc107; }}
        .header-container {{ background-color: #ffffff; padding: 2rem; border-radius: 10px; box-shadow: 0 4px 12px rgba(0,0,0,0.05); margin-bottom: 2rem; }}
        code {{ color: #d63384; }}
    </style>
</head>
<body>

<div class="container-fluid px-5 my-5">
    
    <div class="header-container border">
        <h1 class="display-5 fw-bold text-dark mb-1">PgSpy AUDITOR REPORT: <span class="text-primary">{dbname}</span></h1>
        <p class="text-muted fs-5 mb-0">Automated structural, index efficiency, performance bottleneck, and data lifecycle analysis.</p>
    </div>

    <div class="row">
        <div class="col-xl-6">
            <div class="card alert-card">
                <div class="card-header bg-danger text-white">&#9888; Unused Indexes (Slows down INSERTS / Candidates for deletion)</div>
                <div class="card-body" style="max-height: 350px; overflow-y: auto;">
                    <table class="table table-sm table-hover small">
                        <thead><tr><th>Table</th><th>Index Name</th><th>Size</th><th>Scans</th></tr></thead>
                        <tbody>"""
    for ui in data.get('unused_indexes', []):
        html_content += f"<tr><td><code>{ui['table_name']}</code></td><td><b class='text-danger'>{ui['index_name']}</b></td><td>{ui['index_size']}</td><td>{ui['number_of_scans']}</td></tr>"
    html_content += """</tbody></table></div></div></div>

        <div class="col-xl-6">
            <div class="card warn-card">
                <div class="card-header bg-warning text-dark">&#128273; Missing Indexes on Relations (FOREIGN KEY)</div>
                <div class="card-body" style="max-height: 350px; overflow-y: auto;">
                    <table class="table table-sm table-hover small">
                        <thead><tr><th>Table</th><th>FK Column</th><th>Constraint Name</th></tr></thead>
                        <tbody>"""
    for mf in data.get('missing_fk_indexes', []):
        html_content += f"<tr><td><code>{mf['table_name']}</code></td><td><b class='text-warning'>{mf['column_name']}</b></td><td><small class='text-muted'>{mf['constraint_name']}</small></td></tr>"
    html_content += """</tbody></table></div></div></div>
    </div>

    <div class="row">
        <div class="col-xl-6">
            <div class="card warn-card">
                <div class="card-header bg-dark text-white">&#128168; Table Bloat / Dead Records (Requires VACUUM)</div>
                <div class="card-body" style="max-height: 350px; overflow-y: auto;">
                    <table class="table table-sm table-hover small">
                        <thead><tr><th>Table</th><th>Live Rows</th><th>Dead Rows</th><th>% Dead</th><th>Last Autovacuum</th></tr></thead>
                        <tbody>"""
    for b in data.get('table_bloat', []):
        html_content += f"<tr><td><code>{b['table_name']}</code></td><td>{b['live_rows']}</td><td><span class='text-danger fw-bold'>{b['dead_rows']}</span></td><td>{b['dead_rows_percentage']}%</td><td><small>{b['last_autovacuum']}</small></td></tr>"
    html_content += """</tbody></table></div></div></div>

        <div class="col-xl-6">
            <div class="card alert-card">
                <div class="card-header bg-secondary text-white">&#128034; Sequential Scans Hotspots (Missing Multi-Column Indexes)</div>
                <div class="card-body" style="max-height: 350px; overflow-y: auto;">
                    <table class="table table-sm table-hover small">
                        <thead><tr><th>Table</th><th>Total Seq Scans</th><th>Rows Read</th><th>Index Scans</th></tr></thead>
                        <tbody>"""
    for ss in data.get('seq_scans', []):
        html_content += f"<tr><td><code>{ss['table_name']}</code></td><td>{ss['total_seq_scans']}</td><td><span class='text-danger fw-bold'>{ss['total_rows_read']:,}</span></td><td>{ss['total_index_scans']}</td></tr>"
    html_content += """</tbody></table></div></div></div>
    </div>

    <div class="card p-4">
        <h2 class="h4 mb-3 fw-bold text-dark">All Tables, Sizes and Structures</h2>
        <table id="summaryTable" class="table table-striped table-hover align-middle">
            <thead>
                <tr>
                    <th>Table Name</th>
                    <th>Schema</th>
                    <th>Total Size</th>
                    <th>Structure Type / Partitioning</th>
                </tr>
            </thead>
            <tbody>"""
    
    for t in data.get('tables', []):
        if t['is_partitioned'] == 'Yes (Partition Parent)': badge_class = 'bg-success text-white'
        elif t['is_partitioned'] == 'Yes (Physical Partition)': badge_class = 'bg-info text-white'
        elif t['is_partitioned'] == 'No (Foreign FDW Table)': badge_class = 'bg-dark text-white'
        else: badge_class = 'bg-light text-dark border'

        html_content += f"""
                <tr>
                    <td><a class="table-anchor" href="#table-{t['table_name']}">{t['table_name']}</a></td>
                    <td><span class="badge bg-secondary">{t['schema_name']}</span></td>
                    <td data-order="{t['total_size_bytes']}">{t['total_size']}</td>
                    <td><span class="badge {badge_class}">{t['is_partitioned']}</span></td>
                </tr>"""
        
    html_content += """
            </tbody>
        </table>
    </div>

    <hr class="my-5">
    <h2 class="display-6 mb-4 fw-bold">Detailed Per-Table Inspection</h2>"""

    for t_name, t_data in tables_dict.items():
        html_content += f"""
        <div class="card p-4" id="table-{t_name}">
            <div class="d-flex justify-content-between align-items-center mb-4 border-bottom pb-2">
                <div>
                    <h3 class="text-primary fw-bold h3 d-inline-block mb-0 me-2">{t_name}</h3>
                    <small class="text-muted">({t_data['info']['is_partitioned']})</small>
                </div>
                <span class="badge bg-light text-dark fs-6 p-2 border">Total Size: {t_data['info']['total_size']}</span>
            </div>
            
            <div class="row">
                <div class="col-md-6 mb-4">"""
        if t_data['fdw_info']:
            options_str = ", ".join(t_data['fdw_info']['foreign_options']) if t_data['fdw_info']['foreign_options'] else "No options"
            html_content += f"""
                    <h5 class="fw-bold border-bottom pb-1">&#127760; Foreign Configuration (FDW)</h5>
                    <div class="p-3 bg-light rounded border border-start border-4 border-primary">
                        <p class="mb-1"><strong>Remote Server:</strong> <code>{t_data['fdw_info']['foreign_server_name']}</code></p>
                        <p class="mb-0 text-muted small"><strong>Parameters:</strong> {options_str}</p>
                    </div>"""
        else:
            html_content += "<h5 class='text-secondary fw-bold border-bottom pb-1'>&#128273; Keys and Relations</h5>"
            if not t_data['relations']:
                html_content += "<p class='text-muted small italic'>No PK/FK definitions found for this table.</p>"
            else:
                html_content += "<ul class='list-group list-group-flush'>"
                for r in t_data['relations']:
                    if r['constraint_type'] == 'PRIMARY KEY':
                        html_content += f"<li class='list-group-item small px-0'><span class='badge badge-pk me-2'>PK</span> <strong>{r['column_name']}</strong></li>"
                    else:
                        html_content += f"<li class='list-group-item small px-0'><span class='badge badge-fk me-2'>FK</span> <strong>{r['column_name']}</strong> &rarr; {r['foreign_table_name']}({r['foreign_column_name']})</li>"
                html_content += "</ul>"

        html_content += f"""
                </div>
                <div class="col-md-6 mb-4">
                    <h5 class="text-secondary fw-bold border-bottom pb-1">&#9889; Indexes</h5>"""
        if t_data['postgis']:
            for geo in t_data['postgis']:
                html_content += f"<div class='alert alert-info py-1 px-2 small mb-2'>&#127773; <b>PostGIS:</b> Column <code>{geo['column_name']}</code> type <code>{geo['type']}</code> (SRID: {geo['srid']})</div>"

        if not t_data['indexes']:
            html_content += "<p class='text-muted small italic'>No indexes allocated for this table.</p>"
        else:
            for idx in t_data['indexes']:
                html_content += f"<div class='mb-2'><code>{idx['index_name']}</code><pre class='mb-1'>{idx['index_definition']}</pre></div>"

        html_content += """
                </div>
            </div>

            <div class="row">
                <div class="col-md-6 mb-4">
                    <h5 class="text-secondary fw-bold border-bottom pb-1">&#9881; Triggers &amp; Automations</h5>"""
        if not t_data['triggers']:
            html_content += "<p class='text-muted small italic'>No procedural triggers attached to this table.</p>"
        else:
            for trig in t_data['triggers']:
                badge_color = "bg-warning text-dark" if trig['timing'] == 'BEFORE' else "bg-info text-white"
                html_content += f"""
                <div class='mb-3 border-start border-3 border-warning ps-2'>
                    <div class="d-flex align-items-center flex-wrap gap-1 mb-1">
                        <strong class="text-dark">{trig['trigger_name']}</strong>
                        <span class="badge {badge_color} small">{trig['timing']}</span>
                        <span class="badge bg-dark small">{trig['event']}</span>
                    </div>
                    <div class="text-muted small mb-1">Fires <code>{trig['timing']}</code> during <code>{trig['event']}</code> ({trig['level']}).</div>
                    <details class="small">
                        <summary class="text-primary" style="cursor: pointer;">View Procedural Logic (PL/pgSQL)</summary>
                        <pre class='mt-1 mb-0' style="max-height: 250px; overflow-y: auto;">{trig['definition'].strip()}</pre>
                    </details>
                </div>"""

        html_content += """
                </div>
                <div class="col-md-6 mb-4">
                    <h5 class="text-secondary fw-bold border-bottom pb-1">&#9989; Check Constraints</h5>"""
        if not t_data['checks']:
            html_content += "<p class='text-muted small italic'>No CHECK constraint conditions specified.</p>"
        else:
            for chk in t_data['checks']:
                html_content += f"<div class='mb-2'>&#9989; <strong>{chk['constraint_name']}</strong><pre class='mb-1'>{chk['definition']}</pre></div>"

        html_content += """
                </div>
            </div>
        </div>"""

    html_content += """
</div>

<script src="https://code.jquery.com/jquery-3.6.0.min.js"></script>
<script src="https://cdn.datatables.net/1.13.4/js/jquery.dataTables.min.js"></script>
<script src="https://cdn.datatables.net/1.13.4/js/dataTables.bootstrap5.min.js"></script>
<script>
    $(document).ready(function() {
        $('#summaryTable').DataTable({
            "language": {
                "lengthMenu": "Show _MENU_ entries per page",
                "zeroRecords": "No tables detected",
                "info": "Page _PAGE_ of _PAGES_",
                "infoEmpty": "No matching data available",
                "infoFiltered": "(filtered from _MAX_ total records)",
                "search": "Filter Tables:"
            },
            "pageLength": 25,
            "order": [[2, "desc"]]
        });
    });
</script>
</body>
</html>"""

    with open(filename, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"{C_GREEN}[+] Success! Inspection report exported to: {filename}{C_RESET}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="PgSpy - Minimalist PostgreSQL structural & efficiency auditor CLI tool."
    )
    
    parser.add_argument("--dbname", default="postgres", help="Target database catalog name (default: postgres)")
    parser.add_argument("--user", default="postgres", help="Database user role (default: postgres)")
    parser.add_argument("--host", default="", help="Server hostname. Leave empty to use local Unix Sockets (default)")
    parser.add_argument("--port", default="5432", help="Server port identifier (default: 5432)")
    parser.add_argument("--password", default=None, help="Database access password (optional)")
    parser.add_argument("--socket", action="store_true", help="Force local Unix Socket protocol linkage (overrides --host)")
    parser.add_argument("--output", default="db_report.html", help="Resulting audit HTML filename destination (default: db_report.html)")
    parser.add_argument("--all-databases", action="store_true", help="Audit all discoverable databases on the cluster sequentially")

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    args = parser.parse_rows = parser.parse_args()

    base_config = {
        "user": args.user,
        "port": args.port
    }

    if args.socket:
        base_config["host"] = ""
    else:
        base_config["host"] = args.host

    if args.password:
        base_config["password"] = args.password

    databases_to_audit = []

    if args.all_databases:
        meta_config = base_config.copy()
        meta_config["dbname"] = args.dbname
        print(f"{C_BLUE}[*] Fetching cluster database inventory list...{C_RESET}")
        databases_to_audit = get_all_databases(meta_config)
    else:
        databases_to_audit = [args.dbname]

    if not databases_to_audit:
        print(f"{C_RED}[X] Aborted: No valid target databases discovered.{C_RESET}")
        sys.exit(1)

    for current_db in databases_to_audit:
        db_config = base_config.copy()
        db_config["dbname"] = current_db
        
        metadata = fetch_db_metadata(db_config)
        if metadata:
            if args.all_databases or args.output == "db_report.html":
                out_filename = f"db_report_{current_db}.html"
            else:
                out_filename = args.output
                
            generate_html_report(metadata, current_db, out_filename)
            print("-" * 40)
        else:
            print(f"{C_YELLOW}[!] Pipeline skipped for database [{current_db}] due to harvest error.{C_RESET}")
            print("-" * 40)
