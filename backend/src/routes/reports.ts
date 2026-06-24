import { Router } from 'express';
import { successResponse } from '../middleware/auth.js';
import { query, run, getOne, saveDatabase } from '../db/database.js';

const router = Router();

// ===================== REPORT APIs =====================

// POST /core/api/v1/report/list
router.post('/api/v1/report/list', (req, res) => {
  const { ontologyId, keyword, status, page = 1, pageSize = 10 } = req.body;
  let sql = 'SELECT * FROM reports WHERE 1=1';
  const params: any[] = [];
  if (ontologyId) { sql += ' AND ontology_id = ?'; params.push(ontologyId); }
  if (keyword) { sql += ' AND title LIKE ?'; params.push(`%${keyword}%`); }
  if (status) { sql += ' AND status = ?'; params.push(status); }

  const countRes = query(sql.replace('SELECT *', 'SELECT COUNT(*) as cnt'), [...params]);
  const total = countRes[0]?.cnt || 0;

  sql += ' ORDER BY updated_at DESC LIMIT ? OFFSET ?';
  params.push(pageSize, (page - 1) * pageSize);

  const records = query(sql, params);
  res.json(successResponse({
    current: page,
    size: pageSize,
    total,
    pages: Math.ceil(total / pageSize),
    records,
  }));
});

// POST /core/api/v1/report/detail
router.post('/api/v1/report/detail', (req, res) => {
  const { id } = req.body;
  const record = getOne('SELECT * FROM reports WHERE id = ?', [id]);
  res.json(successResponse(record));
});

// POST /core/api/v1/report/update
router.post('/api/v1/report/update', (req, res) => {
  const { id, title, description, icon, iconColor, status, content } = req.body;
  const fields: string[] = [];
  const params: any[] = [];
  if (title !== undefined) { fields.push('title = ?'); params.push(title); }
  if (description !== undefined) { fields.push('description = ?'); params.push(description); }
  if (icon !== undefined) { fields.push('icon = ?'); params.push(icon); }
  if (iconColor !== undefined) { fields.push('icon_color = ?'); params.push(iconColor); }
  if (status !== undefined) { fields.push('status = ?'); params.push(status); }
  if (content !== undefined) { fields.push('content = ?'); params.push(content); }
  fields.push("updated_at = datetime('now')");
  params.push(id);
  run(`UPDATE reports SET ${fields.join(', ')} WHERE id = ?`, params);
  saveDatabase();
  const record = getOne('SELECT * FROM reports WHERE id = ?', [id]);
  res.json(successResponse(record));
});

// POST /core/api/v1/report/delete
router.post('/api/v1/report/delete', (req, res) => {
  const { id } = req.body;
  run('DELETE FROM reports WHERE id = ?', [id]);
  saveDatabase();
  res.json(successResponse(null));
});

// ===================== REPORT TEMPLATE APIs =====================

// POST /core/api/v1/report-template/list
router.post('/api/v1/report-template/list', (req, res) => {
  const { ontologyId, keyword, status, page = 1, pageSize = 10 } = req.body;
  let sql = 'SELECT * FROM report_templates WHERE 1=1';
  const params: any[] = [];
  if (ontologyId) { sql += ' AND ontology_id = ?'; params.push(ontologyId); }
  if (keyword) { sql += ' AND title LIKE ?'; params.push(`%${keyword}%`); }
  if (status) { sql += ' AND status = ?'; params.push(status); }

  const countRes = query(sql.replace('SELECT *', 'SELECT COUNT(*) as cnt'), [...params]);
  const total = countRes[0]?.cnt || 0;

  sql += ' ORDER BY updated_at DESC LIMIT ? OFFSET ?';
  params.push(pageSize, (page - 1) * pageSize);

  const records = query(sql, params);
  res.json(successResponse({
    current: page,
    size: pageSize,
    total,
    pages: Math.ceil(total / pageSize),
    records,
  }));
});

// POST /core/api/v1/report-template/detail
router.post('/api/v1/report-template/detail', (req, res) => {
  const { id } = req.body;
  const record = getOne('SELECT * FROM report_templates WHERE id = ?', [id]);
  res.json(successResponse(record));
});

// POST /core/api/v1/report-template/create
router.post('/api/v1/report-template/create', (req, res) => {
  const { ontologyId, title, description, icon, iconColor, status, content } = req.body;
  const result = run(
    'INSERT INTO report_templates (ontology_id, title, description, icon, icon_color, status, content) VALUES (?, ?, ?, ?, ?, ?, ?)',
    [ontologyId, title, description, icon || 'FileText', iconColor || '#60a5fa', status || 'Draft', content]
  );
  saveDatabase();
  const record = getOne('SELECT * FROM report_templates WHERE id = ?', [result.lastID]);
  res.json(successResponse(record));
});

// POST /core/api/v1/report-template/update
router.post('/api/v1/report-template/update', (req, res) => {
  const { id, title, description, icon, iconColor, status, content } = req.body;
  const fields: string[] = [];
  const params: any[] = [];
  if (title !== undefined) { fields.push('title = ?'); params.push(title); }
  if (description !== undefined) { fields.push('description = ?'); params.push(description); }
  if (icon !== undefined) { fields.push('icon = ?'); params.push(icon); }
  if (iconColor !== undefined) { fields.push('icon_color = ?'); params.push(iconColor); }
  if (status !== undefined) { fields.push('status = ?'); params.push(status); }
  if (content !== undefined) { fields.push('content = ?'); params.push(content); }
  // Auto-increment minor version on update
  fields.push('minor_version = minor_version + 1');
  fields.push("updated_at = datetime('now')");
  params.push(id);
  run(`UPDATE report_templates SET ${fields.join(', ')} WHERE id = ?`, params);
  saveDatabase();
  const record = getOne('SELECT * FROM report_templates WHERE id = ?', [id]);
  res.json(successResponse(record));
});

// POST /core/api/v1/report-template/delete
router.post('/api/v1/report-template/delete', (req, res) => {
  const { id } = req.body;
  run('DELETE FROM report_templates WHERE id = ?', [id]);
  saveDatabase();
  res.json(successResponse(null));
});

// POST /core/api/v1/report-template/versions
router.post('/api/v1/report-template/versions', (req, res) => {
  const { templateId } = req.body;
  const template = getOne('SELECT * FROM report_templates WHERE id = ?', [templateId]);
  if (!template) {
    res.json(successResponse([]));
    return;
  }
  // Generate version history based on current template
  const versions = [];
  for (let v = 1; v <= template.major_version; v++) {
    for (let m = 0; m <= (v === template.major_version ? template.minor_version : 5); m++) {
      versions.push({
        id: v * 100 + m,
        templateId: template.id,
        title: `${template.title} v${v}.${m}`,
        majorVersion: v,
        minorVersion: m,
        createdAt: new Date(Date.now() - (template.major_version - v) * 86400000 * 30 - m * 86400000).toISOString(),
        createdBy: 1,
      });
    }
  }
  res.json(successResponse(versions));
});

// POST /core/api/v1/report-template/version-detail
router.post('/api/v1/report-template/version-detail', (req, res) => {
  const { id } = req.body;
  const major = Math.floor(id / 100);
  const minor = id % 100;
  // Return template snapshot for that version
  const template = getOne('SELECT * FROM report_templates WHERE id = ?', [id]); // Simplified
  res.json(successResponse({
    id,
    templateId: id,
    title: template?.title || 'Version',
    description: template?.description,
    content: template?.content,
    majorVersion: major,
    minorVersion: minor,
    createdAt: new Date().toISOString(),
    createdBy: 1,
  }));
});

export default router;
