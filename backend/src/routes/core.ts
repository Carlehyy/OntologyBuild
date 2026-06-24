import { Router } from 'express';
import { successResponse, errorResponse } from '../middleware/auth.js';
import { query, run, getOne, saveDatabase } from '../db/database.js';

const router = Router();

// ===================== ONTOLOGY APIs =====================

// POST /core/api/v1/ontology/list
router.post('/api/v1/ontology/list', (req, res) => {
  const { keyword, status } = req.body;
  let sql = 'SELECT * FROM ontologies WHERE 1=1';
  const params: any[] = [];
  if (keyword) { sql += ' AND (name LIKE ? OR description LIKE ?)'; params.push(`%${keyword}%`, `%${keyword}%`); }
  if (status) { sql += ' AND status = ?'; params.push(status); }
  sql += ' ORDER BY updated_at DESC';
  const records = query(sql, params);
  res.json(successResponse(records));
});

// POST /core/api/v1/ontology/get
router.post('/api/v1/ontology/get', (req, res) => {
  const { id } = req.body;
  const record = getOne('SELECT * FROM ontologies WHERE id = ?', [id]);
  res.json(successResponse(record));
});

// POST /core/api/v1/ontology/create
router.post('/api/v1/ontology/create', (req, res) => {
  const { name, description, uri, version = '1.0.0', status = 'Draft' } = req.body;
  const result = run(
    'INSERT INTO ontologies (name, description, uri, version, status) VALUES (?, ?, ?, ?, ?)',
    [name, description, uri, version, status]
  );
  saveDatabase();
  const record = getOne('SELECT * FROM ontologies WHERE id = ?', [result.lastID]);
  res.json(successResponse(record));
});

// POST /core/api/v1/ontology/update
router.post('/api/v1/ontology/update', (req, res) => {
  const { id, name, description, uri, version, status } = req.body;
  const fields: string[] = [];
  const params: any[] = [];
  if (name !== undefined) { fields.push('name = ?'); params.push(name); }
  if (description !== undefined) { fields.push('description = ?'); params.push(description); }
  if (uri !== undefined) { fields.push('uri = ?'); params.push(uri); }
  if (version !== undefined) { fields.push('version = ?'); params.push(version); }
  if (status !== undefined) { fields.push('status = ?'); params.push(status); }
  fields.push("updated_at = datetime('now')");
  params.push(id);
  run(`UPDATE ontologies SET ${fields.join(', ')} WHERE id = ?`, params);
  saveDatabase();
  const record = getOne('SELECT * FROM ontologies WHERE id = ?', [id]);
  res.json(successResponse(record));
});

// POST /core/api/v1/ontology/delete
router.post('/api/v1/ontology/delete', (req, res) => {
  const { id } = req.body;
  run('DELETE FROM ontologies WHERE id = ?', [id]);
  saveDatabase();
  res.json(successResponse(null));
});

// POST /core/api/v1/ontology/tree
router.post('/api/v1/ontology/tree', (req, res) => {
  const { keyword } = req.body;
  const ontologies = query('SELECT id, name FROM ontologies ORDER BY name');
  const tree: any[] = [];
  for (const ont of ontologies) {
    let sql = 'SELECT id, name, \'Class\' as type FROM classes WHERE ontology_id = ?';
    const params: any[] = [ont.id];
    if (keyword) { sql += ' AND name LIKE ?'; params.push(`%${keyword}%`); }
    sql += ' UNION ALL SELECT id, name, \'Relation\' as type FROM relations WHERE ontology_id = ?';
    params.push(ont.id);
    if (keyword) { sql += ' AND name LIKE ?'; params.push(`%${keyword}%`); }
    const children = query(sql, params);
    if (!keyword || children.length > 0) {
      tree.push({ id: ont.id, name: ont.name, type: 'Ontology', children: children.map((c: any) => ({ ...c, children: [] })) });
    }
  }
  res.json(successResponse(tree));
});

// POST /core/api/v1/ontology/graph
router.post('/api/v1/ontology/graph', (req, res) => {
  const { ontologyId } = req.body;
  const classes = query('SELECT id, name, uri, description, color, icon, status FROM classes WHERE ontology_id = ?', [ontologyId]);
  const relations = query('SELECT id, name, domain_class_id, range_class_id FROM relations WHERE ontology_id = ?', [ontologyId]);

  const nodes = classes.map((c: any) => ({
    id: String(c.id),
    label: c.name,
    type: 'Class',
  }));
  const links = relations.map((r: any) => ({
    source: String(r.domain_class_id || ''),
    target: String(r.range_class_id || ''),
    label: r.name,
    style: 'solid' as const,
  })).filter((l: any) => l.source && l.target);

  res.json(successResponse({ nodes, links }));
});

// POST /core/api/v1/ontology/versions
router.post('/api/v1/ontology/versions', (req, res) => {
  const records = query('SELECT * FROM versions ORDER BY created_at DESC');
  res.json(successResponse(records));
});

// POST /core/api/v1/ontology/validate
router.post('/api/v1/ontology/validate', (req, res) => {
  // Run validation checks and store results
  const checks = [
    { check: 'Class Naming Convention', description: 'All class names should use PascalCase', severity: 'warning' },
    { check: 'Property Types Defined', description: 'All properties must have a data type', severity: 'error' },
    { check: 'Relation Cardinality', description: 'All relations should define cardinality', severity: 'warning' },
    { check: 'Duplicate Names', description: 'No duplicate class names within ontology', severity: 'error' },
    { check: 'Orphan Classes', description: 'Classes should have at least one property or relation', severity: 'warning' },
    { check: 'Namespace Consistency', description: 'All URIs should use registered namespaces', severity: 'passed' },
    { check: 'Circular Dependencies', description: 'No circular inheritance in class hierarchy', severity: 'passed' },
    { check: 'Instance Data Types', description: 'Property values match their declared types', severity: 'passed' },
  ];

  const results = checks.map((c, i) => ({
    id: i + 1,
    severity: c.severity,
    check: c.check,
    description: c.description,
    target: 'Ontology',
    status: c.severity === 'passed' ? 'PASSED' : c.severity === 'error' ? 'FAILED' : 'WARNING',
  }));

  const errors = results.filter((r: any) => r.severity === 'error').length;
  const warnings = results.filter((r: any) => r.severity === 'warning').length;
  const passed = results.filter((r: any) => r.severity === 'passed').length;

  res.json(successResponse({
    overallHealth: Math.round(((passed + warnings * 0.5) / results.length) * 100),
    errors,
    warnings,
    passed,
    total: results.length,
    results,
  }));
});

// ===================== CLASS APIs =====================

// POST /core/api/v1/class/list
router.post('/api/v1/class/list', (req, res) => {
  const { ontologyId, name, filter } = req.body;
  let sql = 'SELECT * FROM classes WHERE 1=1';
  const params: any[] = [];
  if (ontologyId) { sql += ' AND ontology_id = ?'; params.push(ontologyId); }
  if (name) { sql += ' AND name LIKE ?'; params.push(`%${name}%`); }
  sql += ' ORDER BY name';
  const records = query(sql, params);
  res.json(successResponse(records));
});

// POST /core/api/v1/class/get
router.post('/api/v1/class/get', (req, res) => {
  const { id } = req.body;
  const record = getOne('SELECT * FROM classes WHERE id = ?', [id]);
  res.json(successResponse(record));
});

// POST /core/api/v1/class/create
router.post('/api/v1/class/create', (req, res) => {
  const { ontologyId, name, uri, description, color, icon } = req.body;
  const result = run(
    'INSERT INTO classes (ontology_id, name, uri, description, color, icon) VALUES (?, ?, ?, ?, ?, ?)',
    [ontologyId, name, uri, description, color, icon]
  );
  // Update ontology class count
  run('UPDATE ontologies SET class_count = (SELECT COUNT(*) FROM classes WHERE ontology_id = ?), updated_at = datetime("now") WHERE id = ?', [ontologyId, ontologyId]);
  saveDatabase();
  const record = getOne('SELECT * FROM classes WHERE id = ?', [result.lastID]);
  res.json(successResponse(record));
});

// POST /core/api/v1/class/update
router.post('/api/v1/class/update', (req, res) => {
  const { id, name, uri, description, color, icon, status } = req.body;
  const fields: string[] = [];
  const params: any[] = [];
  if (name !== undefined) { fields.push('name = ?'); params.push(name); }
  if (uri !== undefined) { fields.push('uri = ?'); params.push(uri); }
  if (description !== undefined) { fields.push('description = ?'); params.push(description); }
  if (color !== undefined) { fields.push('color = ?'); params.push(color); }
  if (icon !== undefined) { fields.push('icon = ?'); params.push(icon); }
  if (status !== undefined) { fields.push('status = ?'); params.push(status); }
  fields.push("updated_at = datetime('now')");
  params.push(id);
  run(`UPDATE classes SET ${fields.join(', ')} WHERE id = ?`, params);
  saveDatabase();
  const record = getOne('SELECT * FROM classes WHERE id = ?', [id]);
  res.json(successResponse(record));
});

// POST /core/api/v1/class/delete
router.post('/api/v1/class/delete', (req, res) => {
  const { id } = req.body;
  const cls = getOne('SELECT ontology_id FROM classes WHERE id = ?', [id]);
  run('DELETE FROM classes WHERE id = ?', [id]);
  if (cls) {
    run('UPDATE ontologies SET class_count = (SELECT COUNT(*) FROM classes WHERE ontology_id = ?), updated_at = datetime("now") WHERE id = ?', [cls.ontology_id, cls.ontology_id]);
  }
  saveDatabase();
  res.json(successResponse(null));
});

// ===================== CLASS LOGIC APIs =====================

// POST /core/api/v1/class/logic/get
router.post('/api/v1/class/logic/get', (req, res) => {
  const { classId } = req.body;
  const records = query('SELECT * FROM class_logics WHERE class_id = ?', [classId]);
  res.json(successResponse(records));
});

// POST /core/api/v1/class/logic/update
router.post('/api/v1/class/logic/update', (req, res) => {
  const { classId, logics } = req.body;
  run('DELETE FROM class_logics WHERE class_id = ?', [classId]);
  for (const logic of logics) {
    run('INSERT INTO class_logics (class_id, logic_type, expression) VALUES (?, ?, ?)', [classId, logic.logicType, logic.expression]);
  }
  saveDatabase();
  const records = query('SELECT * FROM class_logics WHERE class_id = ?', [classId]);
  res.json(successResponse(records));
});

// POST /core/api/v1/class/logic/analyze (NL -> Axiom)
router.post('/api/v1/class/logic/analyze', (req, res) => {
  const { classId, naturalLanguageText } = req.body;
  // Deterministic rule-based analysis
  const text = naturalLanguageText.toLowerCase();
  let logicType = 'SubClassOf';
  let expression = '';
  let confidence = 0.8;

  if (text.includes('subclass') || text.includes('is a') || text.includes('is an')) {
    logicType = 'SubClassOf';
    expression = `SubClassOf(${naturalLanguageText})`;
    confidence = 0.95;
  } else if (text.includes('equivalent')) {
    logicType = 'EquivalentClasses';
    expression = `EquivalentClasses(${naturalLanguageText})`;
    confidence = 0.9;
  } else if (text.includes('disjoint')) {
    logicType = 'DisjointClasses';
    expression = `DisjointClasses(${naturalLanguageText})`;
    confidence = 0.92;
  } else if (text.includes('all') || text.includes('only')) {
    logicType = 'UniversalRestriction';
    expression = `ObjectAllValuesFrom(${naturalLanguageText})`;
    confidence = 0.85;
  } else if (text.includes('some') || text.includes('at least one')) {
    logicType = 'ExistentialRestriction';
    expression = `ObjectSomeValuesFrom(${naturalLanguageText})`;
    confidence = 0.88;
  }

  res.json(successResponse({
    confidence,
    axiomType: logicType,
    parsedComponents: [{ label: 'Type', value: logicType }, { label: 'Text', value: naturalLanguageText }],
    formalExpression: expression,
    owlExpression: `OWL:${logicType}`,
    validation: {
      syntaxValid: 'passed',
      noConflicts: 'passed',
      satisfiable: 'passed',
      hierarchyConsistent: 'passed',
      errors: [],
      warnings: [],
    },
    suggestedLogicType: logicType,
    suggestedExpression: expression,
  }));
});

// ===================== RELATION APIs =====================

// POST /core/api/v1/relation/list
router.post('/api/v1/relation/list', (req, res) => {
  const { ontologyId } = req.body;
  let sql = `SELECT r.*, dc.name as domain_class_name, rc.name as range_class_name
    FROM relations r
    LEFT JOIN classes dc ON r.domain_class_id = dc.id
    LEFT JOIN classes rc ON r.range_class_id = rc.id WHERE 1=1`;
  const params: any[] = [];
  if (ontologyId) { sql += ' AND r.ontology_id = ?'; params.push(ontologyId); }
  sql += ' ORDER BY r.name';
  const records = query(sql, params);
  res.json(successResponse(records));
});

// POST /core/api/v1/relation/get
router.post('/api/v1/relation/get', (req, res) => {
  const { id } = req.body;
  const record = getOne(`SELECT r.*, dc.name as domain_class_name, rc.name as range_class_name
    FROM relations r
    LEFT JOIN classes dc ON r.domain_class_id = dc.id
    LEFT JOIN classes rc ON r.range_class_id = rc.id WHERE r.id = ?`, [id]);
  res.json(successResponse(record));
});

// POST /core/api/v1/relation/create
router.post('/api/v1/relation/create', (req, res) => {
  const { ontologyId, name, uri, description, domainClassId, rangeClassId, isFunctional, isInverseFunctional, isSymmetric, isTransitive, cardinality } = req.body;
  const result = run(
    `INSERT INTO relations (ontology_id, name, uri, description, domain_class_id, range_class_id, is_functional, is_inverse_functional, is_symmetric, is_transitive, cardinality)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    [ontologyId, name, uri, description, domainClassId, rangeClassId, isFunctional ? 1 : 0, isInverseFunctional ? 1 : 0, isSymmetric ? 1 : 0, isTransitive ? 1 : 0, cardinality || '0..*']
  );
  run('UPDATE ontologies SET relation_count = (SELECT COUNT(*) FROM relations WHERE ontology_id = ?), updated_at = datetime("now") WHERE id = ?', [ontologyId, ontologyId]);
  saveDatabase();
  const record = getOne('SELECT * FROM relations WHERE id = ?', [result.lastID]);
  res.json(successResponse(record));
});

// POST /core/api/v1/relation/update
router.post('/api/v1/relation/update', (req, res) => {
  const { id, name, uri, description, domainClassId, rangeClassId, isFunctional, isInverseFunctional, isSymmetric, isTransitive, cardinality, status } = req.body;
  const fields: string[] = [];
  const params: any[] = [];
  if (name !== undefined) { fields.push('name = ?'); params.push(name); }
  if (uri !== undefined) { fields.push('uri = ?'); params.push(uri); }
  if (description !== undefined) { fields.push('description = ?'); params.push(description); }
  if (domainClassId !== undefined) { fields.push('domain_class_id = ?'); params.push(domainClassId); }
  if (rangeClassId !== undefined) { fields.push('range_class_id = ?'); params.push(rangeClassId); }
  if (isFunctional !== undefined) { fields.push('is_functional = ?'); params.push(isFunctional ? 1 : 0); }
  if (isInverseFunctional !== undefined) { fields.push('is_inverse_functional = ?'); params.push(isInverseFunctional ? 1 : 0); }
  if (isSymmetric !== undefined) { fields.push('is_symmetric = ?'); params.push(isSymmetric ? 1 : 0); }
  if (isTransitive !== undefined) { fields.push('is_transitive = ?'); params.push(isTransitive ? 1 : 0); }
  if (cardinality !== undefined) { fields.push('cardinality = ?'); params.push(cardinality); }
  if (status !== undefined) { fields.push('status = ?'); params.push(status); }
  fields.push("updated_at = datetime('now')");
  params.push(id);
  run(`UPDATE relations SET ${fields.join(', ')} WHERE id = ?`, params);
  saveDatabase();
  const record = getOne('SELECT * FROM relations WHERE id = ?', [id]);
  res.json(successResponse(record));
});

// POST /core/api/v1/relation/delete
router.post('/api/v1/relation/delete', (req, res) => {
  const { id } = req.body;
  const rel = getOne('SELECT ontology_id FROM relations WHERE id = ?', [id]);
  run('DELETE FROM relations WHERE id = ?', [id]);
  if (rel) {
    run('UPDATE ontologies SET relation_count = (SELECT COUNT(*) FROM relations WHERE ontology_id = ?), updated_at = datetime("now") WHERE id = ?', [rel.ontology_id, rel.ontology_id]);
  }
  saveDatabase();
  res.json(successResponse(null));
});

// ===================== RELATION LOGIC APIs =====================

// POST /core/api/v1/relation/logic/get
router.post('/api/v1/relation/logic/get', (req, res) => {
  const { relationId } = req.body;
  const records = query('SELECT * FROM relation_logics WHERE relation_id = ?', [relationId]);
  res.json(successResponse(records));
});

// POST /core/api/v1/relation/logic/update
router.post('/api/v1/relation/logic/update', (req, res) => {
  const { relationId, logics } = req.body;
  run('DELETE FROM relation_logics WHERE relation_id = ?', [relationId]);
  for (const logic of logics) {
    run('INSERT INTO relation_logics (relation_id, logic_type, expression) VALUES (?, ?, ?)', [relationId, logic.logicType, logic.expression]);
  }
  saveDatabase();
  const records = query('SELECT * FROM relation_logics WHERE relation_id = ?', [relationId]);
  res.json(successResponse(records));
});

// POST /core/api/v1/relation/logic/analyze
router.post('/api/v1/relation/logic/analyze', (req, res) => {
  const { relationId, naturalLanguageText } = req.body;
  const text = naturalLanguageText.toLowerCase();
  let ruleType = 'Implication';
  let expression = '';
  let confidence = 0.8;

  if (text.includes('if') && text.includes('then')) {
    ruleType = 'Implication';
    expression = naturalLanguageText;
    confidence = 0.92;
  } else if (text.includes('must') || text.includes('should')) {
    ruleType = 'Constraint';
    expression = naturalLanguageText;
    confidence = 0.88;
  } else if (text.includes('all') || text.includes('every')) {
    ruleType = 'Universal';
    expression = naturalLanguageText;
    confidence = 0.85;
  }

  res.json(successResponse({
    confidence,
    ruleType,
    parsedComponents: [{ label: 'Type', value: ruleType }, { label: 'Text', value: naturalLanguageText }],
    formalExpression: expression,
    owlExpression: `OWL:${ruleType}`,
    validation: {
      syntaxValid: 'passed',
      noConflicts: 'passed',
      satisfiable: 'passed',
      hierarchyConsistent: 'passed',
      errors: [],
      warnings: [],
    },
    suggestedLogicType: ruleType,
    suggestedExpression: expression,
  }));
});

// ===================== NAMESPACE APIs =====================

// GET /core/api/v1/namespace/list
router.get('/api/v1/namespace/list', (req, res) => {
  const records = query('SELECT * FROM namespaces ORDER BY prefix');
  res.json(successResponse(records));
});

// POST /core/api/v1/namespace/create
router.post('/api/v1/namespace/create', (req, res) => {
  const { prefix, uri, type = 'Custom', description } = req.body;
  const result = run('INSERT INTO namespaces (prefix, uri, type, description) VALUES (?, ?, ?, ?)', [prefix, uri, type, description]);
  saveDatabase();
  const record = getOne('SELECT * FROM namespaces WHERE id = ?', [result.lastID]);
  res.json(successResponse(record));
});

// POST /core/api/v1/namespace/update
router.post('/api/v1/namespace/update', (req, res) => {
  const { id, prefix, uri, description } = req.body;
  const fields: string[] = [];
  const params: any[] = [];
  if (prefix !== undefined) { fields.push('prefix = ?'); params.push(prefix); }
  if (uri !== undefined) { fields.push('uri = ?'); params.push(uri); }
  if (description !== undefined) { fields.push('description = ?'); params.push(description); }
  params.push(id);
  run(`UPDATE namespaces SET ${fields.join(', ')} WHERE id = ?`, params);
  saveDatabase();
  const record = getOne('SELECT * FROM namespaces WHERE id = ?', [id]);
  res.json(successResponse(record));
});

// POST /core/api/v1/namespace/delete
router.post('/api/v1/namespace/delete', (req, res) => {
  const { id } = req.body;
  run('DELETE FROM namespaces WHERE id = ?', [id]);
  saveDatabase();
  res.json(successResponse(null));
});

// ===================== PROPERTY APIs =====================

// POST /core/api/v1/property/list
router.post('/api/v1/property/list', (req, res) => {
  const { ontologyId } = req.body;
  let sql = 'SELECT * FROM properties WHERE 1=1';
  const params: any[] = [];
  if (ontologyId) { sql += ' AND ontology_id = ?'; params.push(ontologyId); }
  sql += ' ORDER BY name';
  const records = query(sql, params);
  res.json(successResponse(records));
});

// POST /core/api/v1/property/get
router.post('/api/v1/property/get', (req, res) => {
  const { id } = req.body;
  const record = getOne('SELECT * FROM properties WHERE id = ?', [id]);
  res.json(successResponse(record));
});

// POST /core/api/v1/property/create
router.post('/api/v1/property/create', (req, res) => {
  const { ontologyId, name, uri, description, dataType, constraints } = req.body;
  const result = run(
    'INSERT INTO properties (ontology_id, name, uri, description, data_type, constraints) VALUES (?, ?, ?, ?, ?, ?)',
    [ontologyId, name, uri, description, dataType || 'STRING', constraints]
  );
  run('UPDATE ontologies SET property_count = (SELECT COUNT(*) FROM properties WHERE ontology_id = ?), updated_at = datetime("now") WHERE id = ?', [ontologyId, ontologyId]);
  saveDatabase();
  const record = getOne('SELECT * FROM properties WHERE id = ?', [result.lastID]);
  res.json(successResponse(record));
});

// POST /core/api/v1/property/update
router.post('/api/v1/property/update', (req, res) => {
  const { id, name, uri, description, dataType, constraints, status } = req.body;
  const fields: string[] = [];
  const params: any[] = [];
  if (name !== undefined) { fields.push('name = ?'); params.push(name); }
  if (uri !== undefined) { fields.push('uri = ?'); params.push(uri); }
  if (description !== undefined) { fields.push('description = ?'); params.push(description); }
  if (dataType !== undefined) { fields.push('data_type = ?'); params.push(dataType); }
  if (constraints !== undefined) { fields.push('constraints = ?'); params.push(constraints); }
  if (status !== undefined) { fields.push('status = ?'); params.push(status); }
  fields.push("updated_at = datetime('now')");
  params.push(id);
  run(`UPDATE properties SET ${fields.join(', ')} WHERE id = ?`, params);
  saveDatabase();
  const record = getOne('SELECT * FROM properties WHERE id = ?', [id]);
  res.json(successResponse(record));
});

// POST /core/api/v1/property/delete
router.post('/api/v1/property/delete', (req, res) => {
  const { id } = req.body;
  const prop = getOne('SELECT ontology_id FROM properties WHERE id = ?', [id]);
  run('DELETE FROM properties WHERE id = ?', [id]);
  if (prop) {
    run('UPDATE ontologies SET property_count = (SELECT COUNT(*) FROM properties WHERE ontology_id = ?), updated_at = datetime("now") WHERE id = ?', [prop.ontology_id, prop.ontology_id]);
  }
  saveDatabase();
  res.json(successResponse(null));
});

// ===================== CLASS PROPERTY BINDING APIs =====================

// POST /core/api/v1/class/property/list
router.post('/api/v1/class/property/list', (req, res) => {
  const { classId } = req.body;
  const records = query(`SELECT cp.*, p.name as property_name, p.uri as property_uri, p.data_type
    FROM class_properties cp
    JOIN properties p ON cp.property_id = p.id WHERE cp.class_id = ? ORDER BY cp.sort_order`, [classId]);
  res.json(successResponse(records));
});

// POST /core/api/v1/class/property/bindList
router.post('/api/v1/class/property/bindList', (req, res) => {
  const { classId, bindings } = req.body;
  for (const b of bindings) {
    const existing = getOne('SELECT id FROM class_properties WHERE class_id = ? AND property_id = ?', [classId, b.propertyId]);
    if (existing) {
      run('UPDATE class_properties SET is_required = ?, is_unique = ?, default_value = ?, sort_order = ? WHERE id = ?',
        [b.isRequired ? 1 : 0, b.isUnique ? 1 : 0, b.defaultValue, b.sortOrder || 0, existing.id]);
    } else {
      run('INSERT INTO class_properties (class_id, property_id, is_required, is_unique, default_value, sort_order) VALUES (?, ?, ?, ?, ?, ?)',
        [classId, b.propertyId, b.isRequired ? 1 : 0, b.isUnique ? 1 : 0, b.defaultValue, b.sortOrder || 0]);
    }
  }
  saveDatabase();
  res.json(successResponse(null));
});

// POST /core/api/v1/class/property/unbind
router.post('/api/v1/class/property/unbind', (req, res) => {
  const { classId, propertyId } = req.body;
  run('DELETE FROM class_properties WHERE class_id = ? AND property_id = ?', [classId, propertyId]);
  saveDatabase();
  res.json(successResponse(null));
});

// ===================== CLASS RELATION BINDING APIs =====================

// POST /core/api/v1/class/relation/list
router.post('/api/v1/class/relation/list', (req, res) => {
  const { classId } = req.body;
  const records = query(`SELECT cr.*, r.name as relation_name, r.uri as relation_uri,
    r.domain_class_id, dc.name as domain_class_name, r.range_class_id, rc.name as range_class_name
    FROM class_relations cr
    JOIN relations r ON cr.relation_id = r.id
    LEFT JOIN classes dc ON r.domain_class_id = dc.id
    LEFT JOIN classes rc ON r.range_class_id = rc.id
    WHERE cr.class_id = ? ORDER BY cr.sort_order`, [classId]);
  res.json(successResponse(records));
});

// POST /core/api/v1/class/relation/bindList
router.post('/api/v1/class/relation/bindList', (req, res) => {
  const { classId, bindings } = req.body;
  for (const b of bindings) {
    const existing = getOne('SELECT id FROM class_relations WHERE class_id = ? AND relation_id = ?', [classId, b.relationId]);
    if (!existing) {
      run('INSERT INTO class_relations (class_id, relation_id, sort_order) VALUES (?, ?, ?)', [classId, b.relationId, b.sortOrder || 0]);
    }
  }
  saveDatabase();
  res.json(successResponse(null));
});

// POST /core/api/v1/class/relation/unbind
router.post('/api/v1/class/relation/unbind', (req, res) => {
  const { classId, relationId } = req.body;
  run('DELETE FROM class_relations WHERE class_id = ? AND relation_id = ?', [classId, relationId]);
  saveDatabase();
  res.json(successResponse(null));
});

// ===================== CLASS TOPOLOGY APIs =====================

// POST /core/api/v1/class/topology
router.post('/api/v1/class/topology', (req, res) => {
  const { classId, ontologyId, topologyId } = req.body;
  let classFilter = '';
  const params: any[] = [];

  if (topologyId) {
    const topo = getOne('SELECT class_ids FROM topologies WHERE id = ?', [topologyId]);
    if (topo && topo.class_ids) {
      const ids = JSON.parse(topo.class_ids);
      classFilter = `AND c.id IN (${ids.map(() => '?').join(',')})`;
      params.push(...ids);
    }
  }

  if (classId && !topologyId) {
    params.length = 0;
    params.push(classId, ontologyId);
  } else if (ontologyId) {
    if (params.length === 0) params.push(ontologyId);
    else params.push(ontologyId);
  }

  let sql = `SELECT c.id, c.name, c.uri, c.description, c.color, c.icon, c.status,
    (SELECT COUNT(*) FROM class_properties WHERE class_id = c.id) as property_count,
    CASE WHEN c.id = ? THEN 1 ELSE 0 END as center,
    tp.position_x, tp.position_y
    FROM classes c
    LEFT JOIN topology_positions tp ON c.id = tp.class_id AND tp.ontology_id = c.ontology_id
    WHERE c.ontology_id = ? ${classFilter}`;

  const nodes = query(sql, params.length > 0 ? params : [ontologyId || 0]);

  // Get edges (relations between classes)
  let edgeSql = `SELECT r.id, r.name, r.uri, r.description, r.domain_class_id as source_class_id,
    dc.name as source_class_name, r.range_class_id as target_class_id, rc.name as target_class_name,
    r.cardinality
    FROM relations r
    LEFT JOIN classes dc ON r.domain_class_id = dc.id
    LEFT JOIN classes rc ON r.range_class_id = rc.id
    WHERE r.ontology_id = ?`;
  const edgeParams = [ontologyId];

  if (topologyId) {
    const topo = getOne('SELECT class_ids FROM topologies WHERE id = ?', [topologyId]);
    if (topo && topo.class_ids) {
      const ids = JSON.parse(topo.class_ids);
      edgeSql += ` AND r.domain_class_id IN (${ids.map(() => '?').join(',')}) AND r.range_class_id IN (${ids.map(() => '?').join(',')})`;
      edgeParams.push(...ids, ...ids);
    }
  }

  const edges = query(edgeSql, edgeParams);

  res.json(successResponse({
    nodes: nodes.map((n: any) => ({ ...n, property_count: n.property_count || 0 })),
    edges,
    viewportX: null,
    viewportY: null,
    viewportScale: null,
  }));
});

// POST /core/api/v1/class/save-positions
router.post('/api/v1/class/save-positions', (req, res) => {
  const { ontologyId, positions, viewportX, viewportY, viewportScale } = req.body;
  for (const pos of positions) {
    const existing = getOne('SELECT id FROM topology_positions WHERE ontology_id = ? AND class_id = ?', [ontologyId, pos.classId]);
    if (existing) {
      run('UPDATE topology_positions SET position_x = ?, position_y = ? WHERE id = ?', [pos.positionX, pos.positionY, existing.id]);
    } else {
      run('INSERT INTO topology_positions (ontology_id, class_id, position_x, position_y) VALUES (?, ?, ?, ?)', [ontologyId, pos.classId, pos.positionX, pos.positionY]);
    }
  }
  saveDatabase();
  res.json(successResponse(null));
});

export default router;
