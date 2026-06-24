import { Router } from 'express';
import { successResponse } from '../middleware/auth.js';
import { query, run, getOne, saveDatabase } from '../db/database.js';

const router = Router();

// POST /core/api/v1/instance/list
router.post('/api/v1/instance/list', (req, res) => {
  const { ontologyId, classId, keyword, page = 1, size = 10 } = req.body;
  let sql = `SELECT i.*, c.name as class_name, c.color as class_color, c.icon as class_icon
    FROM instances i JOIN classes c ON i.class_id = c.id WHERE 1=1`;
  const params: any[] = [];
  if (ontologyId) { sql += ' AND i.ontology_id = ?'; params.push(ontologyId); }
  if (classId) { sql += ' AND i.class_id = ?'; params.push(classId); }
  if (keyword) { sql += ' AND i.name LIKE ?'; params.push(`%${keyword}%`); }

  const countResult = query(`SELECT COUNT(*) as cnt FROM instances i WHERE 1=1${sql.split('WHERE 1=1')[1].split('JOIN')[0] || ''}`, [...params]);
  // Fix count query - use simpler approach
  const countSql = sql.replace(/SELECT i\.\*, c\.name as class_name, c\.color as class_color, c\.icon as class_icon/, 'SELECT COUNT(*) as cnt');
  const countRes = query(countSql, [...params]);
  const total = countRes[0]?.cnt || 0;

  sql += ' ORDER BY i.updated_at DESC LIMIT ? OFFSET ?';
  params.push(size, (page - 1) * size);

  const records = query(sql, params);
  res.json(successResponse({
    current: page,
    size,
    total,
    pages: Math.ceil(total / size),
    records,
  }));
});

// POST /core/api/v1/instance/get
router.post('/api/v1/instance/get', (req, res) => {
  const { id } = req.body;
  const record = getOne(`SELECT i.*, c.name as class_name, c.color as class_color, c.icon as class_icon
    FROM instances i JOIN classes c ON i.class_id = c.id WHERE i.id = ?`, [id]);
  res.json(successResponse(record));
});

// POST /core/api/v1/instance/create
router.post('/api/v1/instance/create', (req, res) => {
  const { ontologyId, classId, name, description, domain } = req.body;
  const result = run(
    'INSERT INTO instances (ontology_id, class_id, name, description, domain) VALUES (?, ?, ?, ?, ?)',
    [ontologyId, classId, name, description, domain]
  );
  run('UPDATE ontologies SET instance_count = (SELECT COUNT(*) FROM instances WHERE ontology_id = ?), updated_at = datetime("now") WHERE id = ?', [ontologyId, ontologyId]);
  run('UPDATE classes SET instance_count = (SELECT COUNT(*) FROM instances WHERE class_id = ?) WHERE id = ?', [classId, classId]);
  saveDatabase();
  const record = getOne(`SELECT i.*, c.name as class_name FROM instances i JOIN classes c ON i.class_id = c.id WHERE i.id = ?`, [result.lastID]);
  res.json(successResponse(record));
});

// POST /core/api/v1/instance/update
router.post('/api/v1/instance/update', (req, res) => {
  const { id, name, description, classId, domain, status } = req.body;
  const fields: string[] = [];
  const params: any[] = [];
  if (name !== undefined) { fields.push('name = ?'); params.push(name); }
  if (description !== undefined) { fields.push('description = ?'); params.push(description); }
  if (classId !== undefined) { fields.push('class_id = ?'); params.push(classId); }
  if (domain !== undefined) { fields.push('domain = ?'); params.push(domain); }
  if (status !== undefined) { fields.push('status = ?'); params.push(status); }
  fields.push("updated_at = datetime('now')");
  params.push(id);
  run(`UPDATE instances SET ${fields.join(', ')} WHERE id = ?`, params);
  saveDatabase();
  const record = getOne('SELECT * FROM instances WHERE id = ?', [id]);
  res.json(successResponse(record));
});

// POST /core/api/v1/instance/delete
router.post('/api/v1/instance/delete', (req, res) => {
  const { id } = req.body;
  const inst = getOne('SELECT ontology_id, class_id FROM instances WHERE id = ?', [id]);
  run('DELETE FROM instances WHERE id = ?', [id]);
  if (inst) {
    run('UPDATE ontologies SET instance_count = (SELECT COUNT(*) FROM instances WHERE ontology_id = ?), updated_at = datetime("now") WHERE id = ?', [inst.ontology_id, inst.ontology_id]);
    run('UPDATE classes SET instance_count = (SELECT COUNT(*) FROM instances WHERE class_id = ?) WHERE id = ?', [inst.class_id, inst.class_id]);
  }
  saveDatabase();
  res.json(successResponse(null));
});

// POST /core/api/v1/instance/property/get
router.post('/api/v1/instance/property/get', (req, res) => {
  const { instanceId } = req.body;
  const records = query(`SELECT ipv.*, p.name as property_name, p.data_type
    FROM instance_property_values ipv
    JOIN properties p ON ipv.property_id = p.id WHERE ipv.instance_id = ?`, [instanceId]);
  res.json(successResponse(records));
});

// POST /core/api/v1/instance/property/update
router.post('/api/v1/instance/property/update', (req, res) => {
  const { instanceId, values } = req.body;
  for (const v of values) {
    const existing = getOne('SELECT id FROM instance_property_values WHERE instance_id = ? AND property_id = ?', [instanceId, v.propertyId]);
    if (existing) {
      run('UPDATE instance_property_values SET value = ? WHERE id = ?', [v.value, existing.id]);
    } else {
      run('INSERT INTO instance_property_values (instance_id, property_id, value) VALUES (?, ?, ?)', [instanceId, v.propertyId, v.value]);
    }
  }
  saveDatabase();
  const records = query(`SELECT ipv.*, p.name as property_name, p.data_type
    FROM instance_property_values ipv
    JOIN properties p ON ipv.property_id = p.id WHERE ipv.instance_id = ?`, [instanceId]);
  res.json(successResponse(records));
});

// POST /core/api/v1/instance/relation/list
router.post('/api/v1/instance/relation/list', (req, res) => {
  const { id } = req.body;
  const records = query(`SELECT ir.*, r.name as relation_name, ti.name as target_instance_name, tc.name as target_class_name
    FROM instance_relations ir
    JOIN relations r ON ir.relation_id = r.id
    JOIN instances ti ON ir.target_instance_id = ti.id
    JOIN classes tc ON ti.class_id = tc.id
    WHERE ir.source_instance_id = ?`, [id]);
  res.json(successResponse(records));
});

// POST /core/api/v1/instance/relation/add
router.post('/api/v1/instance/relation/add', (req, res) => {
  const { sourceInstanceId, relationId, targetInstanceId } = req.body;
  run('INSERT INTO instance_relations (source_instance_id, relation_id, target_instance_id) VALUES (?, ?, ?)',
    [sourceInstanceId, relationId, targetInstanceId]);
  saveDatabase();
  res.json(successResponse(null));
});

// POST /core/api/v1/instance/relation/remove
router.post('/api/v1/instance/relation/remove', (req, res) => {
  const { id } = req.body;
  run('DELETE FROM instance_relations WHERE id = ?', [id]);
  saveDatabase();
  res.json(successResponse(null));
});

// POST /core/api/v1/instance/topology
router.post('/api/v1/instance/topology', (req, res) => {
  const { instanceId } = req.body;
  const instance = getOne(`SELECT i.*, c.name as class_name, c.color as class_color FROM instances i JOIN classes c ON i.class_id = c.id WHERE i.id = ?`, [instanceId]);
  if (!instance) {
    res.json(successResponse({ nodes: [], edges: [] }));
    return;
  }

  // Get directly connected instances
  const relations = query(`SELECT ir.*, r.name as relation_name, ti.id as target_id, ti.name as target_name,
    tc.id as target_class_id, tc.name as target_class_name, tc.color as target_class_color
    FROM instance_relations ir
    JOIN relations r ON ir.relation_id = r.id
    JOIN instances ti ON ir.target_instance_id = ti.id
    JOIN classes tc ON ti.class_id = tc.id
    WHERE ir.source_instance_id = ?`, [instanceId]);

  const nodes = [{
    instanceId: instance.id,
    instanceName: instance.name,
    classId: instance.class_id,
    className: instance.class_name,
    classColor: instance.class_color,
    classIcon: instance.class_icon,
  }];

  const edges: any[] = [];

  for (const rel of relations) {
    nodes.push({
      instanceId: rel.target_id,
      instanceName: rel.target_name,
      classId: rel.target_class_id,
      className: rel.target_class_name,
      classColor: rel.target_class_color,
      classIcon: null,
    });
    edges.push({
      sourceInstanceId: instance.id,
      targetInstanceId: rel.target_id,
      relationId: rel.relation_id,
      relationName: rel.relation_name,
    });
  }

  res.json(successResponse({ nodes, edges }));
});

// POST /core/api/v1/instance/multi-topology
router.post('/api/v1/instance/multi-topology', (req, res) => {
  const { ontologyId, classIds } = req.body;
  const instances = query(`SELECT i.id as instanceId, i.name as instanceName, i.class_id as classId, c.name as className, c.color as classColor
    FROM instances i JOIN classes c ON i.class_id = c.id
    WHERE i.ontology_id = ? AND i.class_id IN (${classIds.map(() => '?').join(',')})`,
    [ontologyId, ...classIds]);

  const instanceIds = instances.map((i: any) => i.instanceId);
  let edges: any[] = [];
  if (instanceIds.length > 0) {
    edges = query(`SELECT ir.source_instance_id as sourceInstanceId, ir.target_instance_id as targetInstanceId,
      ir.relation_id as relationId, r.name as relationName
      FROM instance_relations ir JOIN relations r ON ir.relation_id = r.id
      WHERE ir.source_instance_id IN (${instanceIds.map(() => '?').join(',')})`,
      [...instanceIds]);
  }

  res.json(successResponse({ nodes: instances, edges }));
});

// POST /core/api/v1/instance/grouped-topology
router.post('/api/v1/instance/grouped-topology', (req, res) => {
  const { topologyId } = req.body;
  const topo = getOne('SELECT * FROM topologies WHERE id = ?', [topologyId]);
  if (!topo) {
    res.json(successResponse({ groups: [], unrelatedInstances: { nodes: [], edges: [] } }));
    return;
  }

  const classIds = topo.class_ids ? JSON.parse(topo.class_ids) : [];
  const centralClassId = topo.central_class_id;

  const groups: any[] = [];
  let unrelatedInstances: any = { nodes: [], edges: [] };

  if (centralClassId) {
    const centralInstances = query(`SELECT i.id as instanceId, i.name as instanceName, i.class_id as classId, c.name as className, c.color as classColor
      FROM instances i JOIN classes c ON i.class_id = c.id WHERE i.class_id = ?`, [centralClassId]);

    for (const ci of centralInstances) {
      const related = query(`SELECT i.id as instanceId, i.name as instanceName, i.class_id as classId, c.name as className, c.color as classColor
        FROM instances i JOIN classes c ON i.class_id = c.id
        JOIN instance_relations ir ON i.id = ir.target_instance_id
        WHERE ir.source_instance_id = ? AND i.class_id != ?`, [ci.instanceId, centralClassId]);

      const relEdges = query(`SELECT ir.source_instance_id as sourceInstanceId, ir.target_instance_id as targetInstanceId,
        ir.relation_id as relationId, r.name as relationName
        FROM instance_relations ir JOIN relations r ON ir.relation_id = r.id
        WHERE ir.source_instance_id = ?`, [ci.instanceId]);

      groups.push({
        centralInstance: ci,
        subgraph: {
          nodes: [ci, ...related],
          edges: relEdges,
        },
      });
    }
  }

  res.json(successResponse({ groups, unrelatedInstances }));
});

export default router;
