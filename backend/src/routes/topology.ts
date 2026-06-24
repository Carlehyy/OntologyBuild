import { Router } from 'express';
import { successResponse } from '../middleware/auth.js';
import { query, run, getOne, saveDatabase } from '../db/database.js';

const router = Router();

// POST /core/api/v1/topology/list
router.post('/api/v1/topology/list', (req, res) => {
  const { ontologyId, keyword } = req.body;
  let sql = 'SELECT * FROM topologies WHERE 1=1';
  const params: any[] = [];
  if (ontologyId) { sql += ' AND ontology_id = ?'; params.push(ontologyId); }
  if (keyword) { sql += ' AND name LIKE ?'; params.push(`%${keyword}%`); }
  sql += ' ORDER BY updated_at DESC';
  const records = query(sql, params);
  res.json(successResponse(records));
});

// POST /core/api/v1/topology/get
router.post('/api/v1/topology/get', (req, res) => {
  const { id } = req.body;
  const record = getOne('SELECT * FROM topologies WHERE id = ?', [id]);
  if (record && record.class_ids) {
    try { record.class_ids = JSON.parse(record.class_ids); } catch { /* keep as is */ }
  }
  res.json(successResponse(record));
});

// POST /core/api/v1/topology/create
router.post('/api/v1/topology/create', (req, res) => {
  const { ontologyId, name, description, classIds, centralClassId } = req.body;
  const result = run(
    'INSERT INTO topologies (ontology_id, name, description, class_ids, central_class_id, class_count) VALUES (?, ?, ?, ?, ?, ?)',
    [ontologyId, name, description, JSON.stringify(classIds || []), centralClassId, (classIds || []).length]
  );
  saveDatabase();
  const record = getOne('SELECT * FROM topologies WHERE id = ?', [result.lastID]);
  res.json(successResponse(record));
});

// POST /core/api/v1/topology/update
router.post('/api/v1/topology/update', (req, res) => {
  const { id, name, description, classIds, centralClassId } = req.body;
  const fields: string[] = [];
  const params: any[] = [];
  if (name !== undefined) { fields.push('name = ?'); params.push(name); }
  if (description !== undefined) { fields.push('description = ?'); params.push(description); }
  if (classIds !== undefined) { fields.push('class_ids = ?'); params.push(JSON.stringify(classIds)); fields.push('class_count = ?'); params.push(classIds.length); }
  if (centralClassId !== undefined) { fields.push('central_class_id = ?'); params.push(centralClassId); }
  fields.push("updated_at = datetime('now')");
  params.push(id);
  run(`UPDATE topologies SET ${fields.join(', ')} WHERE id = ?`, params);
  saveDatabase();
  const record = getOne('SELECT * FROM topologies WHERE id = ?', [id]);
  res.json(successResponse(record));
});

// POST /core/api/v1/topology/delete
router.post('/api/v1/topology/delete', (req, res) => {
  const { id } = req.body;
  run('DELETE FROM topologies WHERE id = ?', [id]);
  saveDatabase();
  res.json(successResponse(null));
});

// POST /core/api/v1/topology/save-positions
router.post('/api/v1/topology/save-positions', (req, res) => {
  const { topologyId, positions } = req.body;
  for (const pos of positions) {
    const existing = getOne('SELECT id FROM topology_positions WHERE topology_id = ? AND class_id = ?', [topologyId, pos.classId]);
    if (existing) {
      run('UPDATE topology_positions SET position_x = ?, position_y = ? WHERE id = ?', [pos.positionX, pos.positionY, existing.id]);
    } else {
      run('INSERT INTO topology_positions (topology_id, class_id, position_x, position_y) VALUES (?, ?, ?, ?)', [topologyId, pos.classId, pos.positionX, pos.positionY]);
    }
  }
  saveDatabase();
  res.json(successResponse(null));
});

// POST /core/api/v1/topology/reset-positions
router.post('/api/v1/topology/reset-positions', (req, res) => {
  const { id } = req.body;
  run('DELETE FROM topology_positions WHERE topology_id = ?', [id]);
  saveDatabase();
  res.json(successResponse(null));
});

export default router;
