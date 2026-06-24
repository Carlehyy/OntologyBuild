import { Router } from 'express';
import { successResponse } from '../middleware/auth.js';
import { query, run, getOne, saveDatabase } from '../db/database.js';

const router = Router();

// POST /core/api/v1/datasource/list
router.post('/api/v1/datasource/list', (req, res) => {
  const { ontologyId, keyword, status } = req.body;
  let sql = 'SELECT * FROM data_sources WHERE 1=1';
  const params: any[] = [];
  if (ontologyId) { sql += ' AND ontology_id = ?'; params.push(ontologyId); }
  if (keyword) { sql += ' AND name LIKE ?'; params.push(`%${keyword}%`); }
  if (status) { sql += ' AND status = ?'; params.push(status); }
  sql += ' ORDER BY updated_at DESC';
  const records = query(sql, params);
  res.json(successResponse(records));
});

// POST /core/api/v1/datasource/get
router.post('/api/v1/datasource/get', (req, res) => {
  const { id } = req.body;
  const record = getOne('SELECT * FROM data_sources WHERE id = ?', [id]);
  res.json(successResponse(record));
});

// POST /core/api/v1/datasource/create
router.post('/api/v1/datasource/create', (req, res) => {
  const { ontologyId, name, subtype, host, port, databaseName, username, password, brandColor } = req.body;
  const result = run(
    'INSERT INTO data_sources (ontology_id, name, subtype, host, port, database_name, username, password, brand_color) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
    [ontologyId, name, subtype, host, port, databaseName, username, password, brandColor]
  );
  saveDatabase();
  const record = getOne('SELECT * FROM data_sources WHERE id = ?', [result.lastID]);
  res.json(successResponse(record));
});

// POST /core/api/v1/datasource/update
router.post('/api/v1/datasource/update', (req, res) => {
  const { id, name, host, port, databaseName, username, password, brandColor } = req.body;
  const fields: string[] = [];
  const params: any[] = [];
  if (name !== undefined) { fields.push('name = ?'); params.push(name); }
  if (host !== undefined) { fields.push('host = ?'); params.push(host); }
  if (port !== undefined) { fields.push('port = ?'); params.push(port); }
  if (databaseName !== undefined) { fields.push('database_name = ?'); params.push(databaseName); }
  if (username !== undefined) { fields.push('username = ?'); params.push(username); }
  if (password !== undefined) { fields.push('password = ?'); params.push(password); }
  if (brandColor !== undefined) { fields.push('brand_color = ?'); params.push(brandColor); }
  fields.push("updated_at = datetime('now')");
  params.push(id);
  run(`UPDATE data_sources SET ${fields.join(', ')} WHERE id = ?`, params);
  saveDatabase();
  const record = getOne('SELECT * FROM data_sources WHERE id = ?', [id]);
  res.json(successResponse(record));
});

// POST /core/api/v1/datasource/delete
router.post('/api/v1/datasource/delete', (req, res) => {
  const { id } = req.body;
  run('DELETE FROM data_sources WHERE id = ?', [id]);
  saveDatabase();
  res.json(successResponse(null));
});

// POST /core/api/v1/datasource/update-status
router.post('/api/v1/datasource/update-status', (req, res) => {
  const { id, status, errorMessage } = req.body;
  run('UPDATE data_sources SET status = ?, last_tested_at = datetime("now"), last_error_message = ? WHERE id = ?', [status, errorMessage || null, id]);
  saveDatabase();
  res.json(successResponse({ id, status, lastTestedAt: new Date().toISOString() }));
});

// POST /core/api/v1/datasource/schema/save
router.post('/api/v1/datasource/schema/save', (req, res) => {
  const { id, schemaJson } = req.body;
  run('UPDATE data_sources SET schema_json = ?, schema_discovered_at = datetime("now") WHERE id = ?', [JSON.stringify(schemaJson), id]);
  saveDatabase();
  res.json(successResponse({ id, schemaDiscoveredAt: new Date().toISOString() }));
});

// ===================== FIELD MAPPING APIs =====================

// POST /core/api/v1/datasource/mapping/list
router.post('/api/v1/datasource/mapping/list', (req, res) => {
  const { dataSourceId, sourceTable, targetClassId } = req.body;
  let sql = `SELECT fm.*, c.name as target_class_name, p.name as target_property_name
    FROM field_mappings fm
    LEFT JOIN classes c ON fm.target_class_id = c.id
    LEFT JOIN properties p ON fm.target_property_id = p.id
    WHERE fm.data_source_id = ?`;
  const params: any[] = [dataSourceId];
  if (sourceTable) { sql += ' AND fm.source_table = ?'; params.push(sourceTable); }
  if (targetClassId) { sql += ' AND fm.target_class_id = ?'; params.push(targetClassId); }
  const records = query(sql, params);
  res.json(successResponse(records));
});

// POST /core/api/v1/datasource/mapping/save
router.post('/api/v1/datasource/mapping/save', (req, res) => {
  const { dataSourceId, mappings } = req.body;
  let savedCount = 0;
  for (const m of mappings) {
    run(`INSERT INTO field_mappings (data_source_id, source_table, source_column, source_column_type, target_class_id, target_property_id, transform_type, custom_expression)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
      [dataSourceId, m.sourceTable, m.sourceColumn, m.sourceColumnType, m.targetClassId, m.targetPropertyId, m.transformType || 'DIRECT', m.customExpression]);
    savedCount++;
  }
  saveDatabase();
  res.json(successResponse({ savedCount }));
});

// POST /core/api/v1/datasource/mapping/delete
router.post('/api/v1/datasource/mapping/delete', (req, res) => {
  const { id } = req.body;
  run('DELETE FROM field_mappings WHERE id = ?', [id]);
  saveDatabase();
  res.json(successResponse(null));
});

// POST /core/api/v1/datasource/mapping/delete-batch
router.post('/api/v1/datasource/mapping/delete-batch', (req, res) => {
  const { dataSourceId, sourceTable, targetClassId } = req.body;
  let sql = 'DELETE FROM field_mappings WHERE data_source_id = ?';
  const params: any[] = [dataSourceId];
  if (sourceTable) { sql += ' AND source_table = ?'; params.push(sourceTable); }
  if (targetClassId) { sql += ' AND target_class_id = ?'; params.push(targetClassId); }
  const info = run(sql, params);
  saveDatabase();
  res.json(successResponse({ deletedCount: 1 }));
});

// ===================== MAPPING CONFIG APIs =====================

// POST /core/api/v1/datasource/mapping/config/list
router.post('/api/v1/datasource/mapping/config/list', (req, res) => {
  const { dataSourceId, sourceTable, targetClassId } = req.body;
  // Return grouped field mappings as configs
  let sql = `SELECT fm.*, c.name as target_class_name, ds.name as data_source_name,
    COUNT(*) as mapping_count
    FROM field_mappings fm
    LEFT JOIN classes c ON fm.target_class_id = c.id
    LEFT JOIN data_sources ds ON fm.data_source_id = ds.id
    WHERE fm.data_source_id = ?`;
  const params: any[] = [dataSourceId];
  if (sourceTable) { sql += ' AND fm.source_table = ?'; params.push(sourceTable); }
  if (targetClassId) { sql += ' AND fm.target_class_id = ?'; params.push(targetClassId); }
  sql += ' GROUP BY fm.source_table, fm.target_class_id';
  const records = query(sql, params);
  res.json(successResponse(records.map((r: any) => ({
    id: r.id,
    dataSourceId: r.data_source_id,
    dataSourceName: r.data_source_name,
    sourceTable: r.source_table,
    targetClassId: r.target_class_id,
    targetClassName: r.target_class_name,
    mappingCount: r.mapping_count,
    mappings: [],
  }))));
});

// POST /core/api/v1/datasource/mapping/config/save
router.post('/api/v1/datasource/mapping/config/save', (req, res) => {
  const { dataSourceId, sourceTable, targetClassId, mappings } = req.body;
  // Save as individual field mappings
  for (const m of mappings) {
    run(`INSERT OR REPLACE INTO field_mappings (data_source_id, source_table, source_column, source_column_type, target_class_id, target_property_id, transform_type, custom_expression)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
      [dataSourceId, sourceTable, m.sourceColumn, m.sourceColumnType, targetClassId, m.targetPropertyId, m.transformType || 'DIRECT', m.customExpression]);
  }
  saveDatabase();
  res.json(successResponse({ id: Date.now(), dataSourceId, sourceTable, targetClassId, mappings }));
});

// POST /core/api/v1/datasource/mapping/config/delete
router.post('/api/v1/datasource/mapping/config/delete', (req, res) => {
  const { id } = req.body;
  run('DELETE FROM field_mappings WHERE id = ?', [id]);
  saveDatabase();
  res.json(successResponse(null));
});

// POST /core/api/v1/datasource/mapping/config/page
router.post('/api/v1/datasource/mapping/config/page', (req, res) => {
  const { dataSourceId, targetClassId, page = 1, pageSize = 10 } = req.body;
  let sql = `SELECT fm.*, c.name as target_class_name, ds.name as data_source_name
    FROM field_mappings fm
    LEFT JOIN classes c ON fm.target_class_id = c.id
    LEFT JOIN data_sources ds ON fm.data_source_id = ds.id
    WHERE 1=1`;
  const params: any[] = [];
  if (dataSourceId) { sql += ' AND fm.data_source_id = ?'; params.push(dataSourceId); }
  if (targetClassId) { sql += ' AND fm.target_class_id = ?'; params.push(targetClassId); }

  const countRes = query(sql.replace(/SELECT fm\.\*, c\.name as target_class_name, ds\.name as data_source_name/, 'SELECT COUNT(*) as cnt'), [...params]);
  const total = countRes[0]?.cnt || 0;

  sql += ' ORDER BY fm.id DESC LIMIT ? OFFSET ?';
  params.push(pageSize, (page - 1) * pageSize);

  const records = query(sql, params);
  res.json(successResponse({
    current: page,
    size: pageSize,
    total,
    pages: Math.ceil(total / pageSize),
    records: records.map((r: any) => ({
      id: r.id,
      dataSourceId: r.data_source_id,
      dataSourceName: r.data_source_name,
      sourceTable: r.source_table,
      targetClassId: r.target_class_id,
      targetClassName: r.target_class_name,
      mappingCount: 1,
      mappings: [{
        sourceColumn: r.source_column,
        sourceColumnType: r.source_column_type,
        targetPropertyId: r.target_property_id,
        transformType: r.transform_type,
        customExpression: r.custom_expression,
      }],
    })),
  }));
});

export default router;
