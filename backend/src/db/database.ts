/**
 * Database layer using sql.js (pure JS SQLite)
 * All data persisted to disk as binary SQLite file.
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

// Dynamic import for ES module compatibility
let SQL: any;

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const DB_PATH = path.resolve(__dirname, '../../data/ontology.db');

let db: any = null;

export async function initDatabase(): Promise<void> {
  const sqlModule = await import('sql.js');
  const initSqlJs = sqlModule.default || sqlModule.initSqlJs;
  SQL = await initSqlJs();

  // Ensure data directory exists
  const dataDir = path.dirname(DB_PATH);
  if (!fs.existsSync(dataDir)) {
    fs.mkdirSync(dataDir, { recursive: true });
  }

  // Load existing DB or create new
  if (fs.existsSync(DB_PATH)) {
    const filebuffer = fs.readFileSync(DB_PATH);
    db = new SQL.Database(filebuffer);
  } else {
    db = new SQL.Database();
  }

  // Create tables
  createTables();
  saveDatabase();
}

function createTables(): void {
  // Users & Auth
  db.run(`
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      username TEXT UNIQUE NOT NULL,
      email TEXT,
      password_hash TEXT NOT NULL,
      nickname TEXT,
      role TEXT DEFAULT 'USER',
      status TEXT DEFAULT 'ACTIVE',
      last_login_at TEXT,
      created_at TEXT DEFAULT (datetime('now')),
      updated_at TEXT DEFAULT (datetime('now'))
    )
  `);

  // Ontologies
  db.run(`
    CREATE TABLE IF NOT EXISTS ontologies (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      description TEXT,
      uri TEXT NOT NULL,
      status TEXT DEFAULT 'Draft',
      version TEXT DEFAULT '1.0.0',
      class_count INTEGER DEFAULT 0,
      relation_count INTEGER DEFAULT 0,
      property_count INTEGER DEFAULT 0,
      instance_count INTEGER DEFAULT 0,
      created_at TEXT DEFAULT (datetime('now')),
      updated_at TEXT DEFAULT (datetime('now'))
    )
  `);

  // Namespaces
  db.run(`
    CREATE TABLE IF NOT EXISTS namespaces (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ontology_id INTEGER,
      prefix TEXT NOT NULL,
      uri TEXT NOT NULL,
      type TEXT DEFAULT 'Custom',
      description TEXT,
      created_at TEXT DEFAULT (datetime('now'))
    )
  `);

  // Classes
  db.run(`
    CREATE TABLE IF NOT EXISTS classes (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ontology_id INTEGER NOT NULL,
      name TEXT NOT NULL,
      uri TEXT,
      description TEXT,
      color TEXT,
      icon TEXT,
      status TEXT DEFAULT 'ACTIVE',
      instance_count INTEGER DEFAULT 0,
      created_at TEXT DEFAULT (datetime('now')),
      updated_at TEXT DEFAULT (datetime('now'))
    )
  `);

  // Properties
  db.run(`
    CREATE TABLE IF NOT EXISTS properties (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ontology_id INTEGER NOT NULL,
      name TEXT NOT NULL,
      uri TEXT,
      description TEXT,
      data_type TEXT DEFAULT 'STRING',
      constraints TEXT,
      status TEXT DEFAULT 'ACTIVE',
      created_at TEXT DEFAULT (datetime('now')),
      updated_at TEXT DEFAULT (datetime('now'))
    )
  `);

  // Relations
  db.run(`
    CREATE TABLE IF NOT EXISTS relations (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ontology_id INTEGER NOT NULL,
      name TEXT NOT NULL,
      uri TEXT,
      description TEXT,
      domain_class_id INTEGER,
      range_class_id INTEGER,
      is_functional INTEGER DEFAULT 0,
      is_inverse_functional INTEGER DEFAULT 0,
      is_symmetric INTEGER DEFAULT 0,
      is_transitive INTEGER DEFAULT 0,
      cardinality TEXT DEFAULT '0..*',
      status TEXT DEFAULT 'ACTIVE',
      created_at TEXT DEFAULT (datetime('now')),
      updated_at TEXT DEFAULT (datetime('now'))
    )
  `);

  // Class Logic Axioms
  db.run(`
    CREATE TABLE IF NOT EXISTS class_logics (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      class_id INTEGER NOT NULL,
      logic_type TEXT NOT NULL,
      expression TEXT NOT NULL,
      created_at TEXT DEFAULT (datetime('now'))
    )
  `);

  // Relation Logic Rules
  db.run(`
    CREATE TABLE IF NOT EXISTS relation_logics (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      relation_id INTEGER NOT NULL,
      logic_type TEXT NOT NULL,
      expression TEXT NOT NULL,
      created_at TEXT DEFAULT (datetime('now'))
    )
  `);

  // Class-Property Bindings
  db.run(`
    CREATE TABLE IF NOT EXISTS class_properties (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      class_id INTEGER NOT NULL,
      property_id INTEGER NOT NULL,
      is_required INTEGER DEFAULT 0,
      is_unique INTEGER DEFAULT 0,
      default_value TEXT,
      sort_order INTEGER DEFAULT 0,
      created_at TEXT DEFAULT (datetime('now'))
    )
  `);

  // Class-Relation Bindings
  db.run(`
    CREATE TABLE IF NOT EXISTS class_relations (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      class_id INTEGER NOT NULL,
      relation_id INTEGER NOT NULL,
      sort_order INTEGER DEFAULT 0,
      created_at TEXT DEFAULT (datetime('now'))
    )
  `);

  // Instances
  db.run(`
    CREATE TABLE IF NOT EXISTS instances (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ontology_id INTEGER NOT NULL,
      class_id INTEGER NOT NULL,
      name TEXT NOT NULL,
      description TEXT,
      domain TEXT,
      status TEXT DEFAULT 'ACTIVE',
      created_at TEXT DEFAULT (datetime('now')),
      updated_at TEXT DEFAULT (datetime('now'))
    )
  `);

  // Instance Property Values
  db.run(`
    CREATE TABLE IF NOT EXISTS instance_property_values (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      instance_id INTEGER NOT NULL,
      property_id INTEGER NOT NULL,
      value TEXT NOT NULL
    )
  `);

  // Instance Relations
  db.run(`
    CREATE TABLE IF NOT EXISTS instance_relations (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      source_instance_id INTEGER NOT NULL,
      relation_id INTEGER NOT NULL,
      target_instance_id INTEGER NOT NULL
    )
  `);

  // Topologies
  db.run(`
    CREATE TABLE IF NOT EXISTS topologies (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ontology_id INTEGER NOT NULL,
      name TEXT NOT NULL,
      description TEXT,
      central_class_id INTEGER,
      class_ids TEXT,
      class_count INTEGER DEFAULT 0,
      instance_count INTEGER DEFAULT 0,
      created_at TEXT DEFAULT (datetime('now')),
      updated_at TEXT DEFAULT (datetime('now'))
    )
  `);

  // Topology Positions
  db.run(`
    CREATE TABLE IF NOT EXISTS topology_positions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ontology_id INTEGER,
      topology_id INTEGER,
      class_id INTEGER NOT NULL,
      position_x REAL,
      position_y REAL
    )
  `);

  // Versions
  db.run(`
    CREATE TABLE IF NOT EXISTS versions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ontology_id INTEGER,
      tag TEXT NOT NULL,
      description TEXT,
      author TEXT,
      additions INTEGER DEFAULT 0,
      deletions INTEGER DEFAULT 0,
      created_at TEXT DEFAULT (datetime('now'))
    )
  `);

  // Data Sources
  db.run(`
    CREATE TABLE IF NOT EXISTS data_sources (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ontology_id INTEGER NOT NULL,
      name TEXT NOT NULL,
      type TEXT DEFAULT 'DATABASE',
      subtype TEXT DEFAULT 'postgresql',
      host TEXT,
      port INTEGER,
      database_name TEXT,
      username TEXT,
      password TEXT,
      status TEXT DEFAULT 'NOT_TESTED',
      last_tested_at TEXT,
      last_error_message TEXT,
      schema_json TEXT,
      schema_discovered_at TEXT,
      brand_color TEXT,
      created_at TEXT DEFAULT (datetime('now')),
      updated_at TEXT DEFAULT (datetime('now'))
    )
  `);

  // Field Mappings
  db.run(`
    CREATE TABLE IF NOT EXISTS field_mappings (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      data_source_id INTEGER NOT NULL,
      source_table TEXT NOT NULL,
      source_column TEXT NOT NULL,
      source_column_type TEXT,
      target_class_id INTEGER,
      target_property_id INTEGER,
      transform_type TEXT DEFAULT 'DIRECT',
      custom_expression TEXT,
      created_at TEXT DEFAULT (datetime('now'))
    )
  `);

  // Reports
  db.run(`
    CREATE TABLE IF NOT EXISTS reports (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ontology_id INTEGER NOT NULL,
      title TEXT NOT NULL,
      description TEXT,
      icon TEXT DEFAULT 'FileText',
      icon_color TEXT DEFAULT '#60a5fa',
      status TEXT DEFAULT 'Draft',
      content TEXT,
      created_by INTEGER,
      updated_by INTEGER,
      created_at TEXT DEFAULT (datetime('now')),
      updated_at TEXT DEFAULT (datetime('now'))
    )
  `);

  // Report Templates
  db.run(`
    CREATE TABLE IF NOT EXISTS report_templates (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ontology_id INTEGER NOT NULL,
      title TEXT NOT NULL,
      description TEXT,
      icon TEXT DEFAULT 'FileText',
      icon_color TEXT DEFAULT '#60a5fa',
      status TEXT DEFAULT 'Draft',
      content TEXT,
      major_version INTEGER DEFAULT 1,
      minor_version INTEGER DEFAULT 0,
      created_by INTEGER,
      updated_by INTEGER,
      created_at TEXT DEFAULT (datetime('now')),
      updated_at TEXT DEFAULT (datetime('now'))
    )
  `);

  // Validation Results
  db.run(`
    CREATE TABLE IF NOT EXISTS validation_results (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ontology_id INTEGER,
      severity TEXT,
      check_name TEXT,
      description TEXT,
      target TEXT,
      status TEXT,
      created_at TEXT DEFAULT (datetime('now'))
    )
  `);

  // Notifications
  db.run(`
    CREATE TABLE IF NOT EXISTS notifications (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER,
      type TEXT,
      title TEXT NOT NULL,
      message TEXT,
      is_read INTEGER DEFAULT 0,
      created_at TEXT DEFAULT (datetime('now'))
    )
  `);

  // Tasks
  db.run(`
    CREATE TABLE IF NOT EXISTS tasks (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      type TEXT,
      status TEXT DEFAULT 'PENDING',
      progress INTEGER DEFAULT 0,
      result TEXT,
      error_message TEXT,
      created_at TEXT DEFAULT (datetime('now')),
      updated_at TEXT DEFAULT (datetime('now'))
    )
  `);

  // Change Logs
  db.run(`
    CREATE TABLE IF NOT EXISTS change_logs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ontology_id INTEGER,
      entity_type TEXT,
      entity_id INTEGER,
      action TEXT,
      details TEXT,
      author TEXT,
      created_at TEXT DEFAULT (datetime('now'))
    )
  `);

  // Settings
  db.run(`
    CREATE TABLE IF NOT EXISTS settings (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      key TEXT UNIQUE NOT NULL,
      value TEXT,
      updated_at TEXT DEFAULT (datetime('now'))
    )
  `);
}

export function getDb(): any {
  if (!db) throw new Error('Database not initialized');
  return db;
}

export function saveDatabase(): void {
  if (!db) return;
  const data = db.export();
  fs.writeFileSync(DB_PATH, Buffer.from(data));
}

// Helper: run query and return results
export function query(sql: string, params?: any[]): any[] {
  const db = getDb();
  const stmt = db.prepare(sql);
  if (params) {
    stmt.bind(params);
  }
  const results: any[] = [];
  while (stmt.step()) {
    results.push(stmt.getAsObject());
  }
  stmt.free();
  return results;
}

// Helper: run insert and return lastID
export function run(sql: string, params?: any[]): { lastID: number; changes: number } {
  const db = getDb();
  db.run(sql, params);
  // Get last inserted id
  const result = query("SELECT last_insert_rowid() as id");
  const lastID = result[0]?.id || 0;
  return { lastID, changes: 1 };
}

// Helper: get single row
export function getOne(sql: string, params?: any[]): any | null {
  const results = query(sql, params);
  return results.length > 0 ? results[0] : null;
}
