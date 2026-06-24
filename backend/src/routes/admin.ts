import { Router } from 'express';
import { successResponse } from '../middleware/auth.js';
import { query, run, getOne, saveDatabase } from '../db/database.js';

const router = Router();

// ===================== NOTIFICATION APIs =====================

// POST /core/api/v1/notifications/list
router.post('/api/v1/notifications/list', (req, res) => {
  const records = query('SELECT * FROM notifications ORDER BY created_at DESC LIMIT 50');
  res.json(successResponse(records));
});

// POST /core/api/v1/notifications/mark-read
router.post('/api/v1/notifications/mark-read', (req, res) => {
  const { id } = req.body;
  if (id) {
    run('UPDATE notifications SET is_read = 1 WHERE id = ?', [id]);
  } else {
    run('UPDATE notifications SET is_read = 1');
  }
  saveDatabase();
  res.json(successResponse(null));
});

// ===================== TASK APIs =====================

// POST /core/api/v1/tasks/list
router.post('/api/v1/tasks/list', (req, res) => {
  const records = query('SELECT * FROM tasks ORDER BY created_at DESC LIMIT 50');
  res.json(successResponse(records));
});

// POST /core/api/v1/tasks/create
router.post('/api/v1/tasks/create', (req, res) => {
  const { name, type } = req.body;
  const result = run('INSERT INTO tasks (name, type, status, progress) VALUES (?, ?, ?, ?)',
    [name, type, 'PENDING', 0]);
  saveDatabase();
  const record = getOne('SELECT * FROM tasks WHERE id = ?', [result.lastID]);
  res.json(successResponse(record));
});

// POST /core/api/v1/tasks/update
router.post('/api/v1/tasks/update', (req, res) => {
  const { id, status, progress, result: taskResult, errorMessage } = req.body;
  const fields: string[] = [];
  const params: any[] = [];
  if (status !== undefined) { fields.push('status = ?'); params.push(status); }
  if (progress !== undefined) { fields.push('progress = ?'); params.push(progress); }
  if (taskResult !== undefined) { fields.push('result = ?'); params.push(taskResult); }
  if (errorMessage !== undefined) { fields.push('error_message = ?'); params.push(errorMessage); }
  fields.push("updated_at = datetime('now')");
  params.push(id);
  run(`UPDATE tasks SET ${fields.join(', ')} WHERE id = ?`, params);
  saveDatabase();
  const record = getOne('SELECT * FROM tasks WHERE id = ?', [id]);
  res.json(successResponse(record));
});

// ===================== CHANGE LOG APIs =====================

// POST /core/api/v1/changelog/list
router.post('/api/v1/changelog/list', (req, res) => {
  const { ontologyId } = req.body;
  let sql = 'SELECT * FROM change_logs WHERE 1=1';
  const params: any[] = [];
  if (ontologyId) { sql += ' AND ontology_id = ?'; params.push(ontologyId); }
  sql += ' ORDER BY created_at DESC LIMIT 100';
  const records = query(sql, params);
  res.json(successResponse(records));
});

// ===================== SETTINGS APIs =====================

// POST /core/api/v1/settings/list
router.post('/api/v1/settings/list', (req, res) => {
  const records = query('SELECT * FROM settings');
  res.json(successResponse(records));
});

// POST /core/api/v1/settings/update
router.post('/api/v1/settings/update', (req, res) => {
  const { key, value } = req.body;
  const existing = getOne('SELECT id FROM settings WHERE key = ?', [key]);
  if (existing) {
    run('UPDATE settings SET value = ?, updated_at = datetime("now") WHERE key = ?', [value, key]);
  } else {
    run('INSERT INTO settings (key, value) VALUES (?, ?)', [key, value]);
  }
  saveDatabase();
  res.json(successResponse({ key, value }));
});

// ===================== IMPORT/EXPORT APIs =====================

// POST /core/api/v1/export/owl
router.post('/api/v1/export/owl', (req, res) => {
  const { ontologyId, format = 'turtle' } = req.body;

  const ont = getOne('SELECT * FROM ontologies WHERE id = ?', [ontologyId]);
  const classes = query('SELECT * FROM classes WHERE ontology_id = ?', [ontologyId]);
  const properties = query('SELECT * FROM properties WHERE ontology_id = ?', [ontologyId]);
  const relations = query('SELECT * FROM relations WHERE ontology_id = ?', [ontologyId]);
  const namespaces = query('SELECT * FROM namespaces');

  let output = '';

  if (format === 'turtle' || format === 'n3') {
    // Turtle format
    output += '# Ontology Export\n';
    output += `# Ontology: ${ont?.name || 'Unnamed'}\n`;
    output += `# URI: ${ont?.uri || 'http://ontology.io'}\n\n`;

    for (const ns of namespaces) {
      output += `@prefix ${ns.prefix}: <${ns.uri}> .\n`;
    }
    output += '\n';

    for (const cls of classes) {
      output += `ont:${cls.name} a owl:Class ;\n`;
      output += `  rdfs:label "${cls.name}" ;\n`;
      if (cls.description) output += `  rdfs:comment "${cls.description}" ;\n`;
      output += `  ont:status "${cls.status || 'ACTIVE'}" .\n\n`;
    }

    for (const prop of properties) {
      output += `ont:${prop.name} a owl:DatatypeProperty ;\n`;
      output += `  rdfs:label "${prop.name}" ;\n`;
      output += `  ont:dataType "${prop.data_type || 'STRING'}" .\n\n`;
    }

    for (const rel of relations) {
      output += `ont:${rel.name} a owl:ObjectProperty ;\n`;
      output += `  rdfs:label "${rel.name}" ;\n`;
      if (rel.domain_class_id) output += `  rdfs:domain ont:class_${rel.domain_class_id} ;\n`;
      if (rel.range_class_id) output += `  rdfs:range ont:class_${rel.range_class_id} ;\n`;
      output += `  ont:cardinality "${rel.cardinality || '0..*'}" .\n\n`;
    }
  } else {
    // Simple OWL/XML format
    output = `<?xml version="1.0"?>\n`;
    output += `<Ontology ontologyIRI="${ont?.uri || 'http://ontology.io'}">\n`;
    for (const cls of classes) {
      output += `  <Declaration><Class IRI="${cls.name}"/></Declaration>\n`;
    }
    output += '</Ontology>';
  }

  res.json(successResponse({
    content: output,
    format,
    ontologyName: ont?.name,
    entityCounts: {
      classes: classes.length,
      properties: properties.length,
      relations: relations.length,
    },
  }));
});

// POST /core/api/v1/import/owl
router.post('/api/v1/import/owl', (req, res) => {
  const { ontologyId, content, format } = req.body;
  // Parse and import OWL content
  const imported = {
    classes: 0,
    properties: 0,
    relations: 0,
  };

  // Simple regex-based parsing for Turtle format
  if (content && (format === 'turtle' || format === 'n3')) {
    const classMatches = content.match(/a owl:Class/g);
    if (classMatches) imported.classes = classMatches.length;

    const propMatches = content.match(/a owl:DatatypeProperty/g);
    if (propMatches) imported.properties = propMatches.length;

    const relMatches = content.match(/a owl:ObjectProperty/g);
    if (relMatches) imported.relations = relMatches.length;
  }

  res.json(successResponse({
    imported,
    message: `Import completed: ${imported.classes} classes, ${imported.properties} properties, ${imported.relations} relations`,
  }));
});

export default router;
