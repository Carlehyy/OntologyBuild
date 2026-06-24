import { Router } from 'express';
import bcrypt from 'bcryptjs';
import jwt from 'jsonwebtoken';
import { generateToken, successResponse, errorResponse } from '../middleware/auth.js';
import { query, run, getOne, saveDatabase } from '../db/database.js';

const router = Router();
const JWT_SECRET = process.env.JWT_SECRET || 'ontology-web-demo-secret-key-2024';

// POST /auth/api/v1/public/auth/login
router.post('/api/v1/public/auth/login', (req, res) => {
  const { username, password } = req.body;
  if (!username || !password) {
    res.status(400).json(errorResponse('Username and password required'));
    return;
  }

  const user = getOne('SELECT * FROM users WHERE username = ?', [username]);
  if (!user) {
    res.status(401).json(errorResponse('Invalid credentials', 'AUTH_003'));
    return;
  }

  const valid = bcrypt.compareSync(password, user.password_hash);
  if (!valid) {
    res.status(401).json(errorResponse('Invalid credentials', 'AUTH_003'));
    return;
  }

  run("UPDATE users SET last_login_at = datetime('now') WHERE id = ?", [user.id]);
  saveDatabase();

  const token = generateToken(user.id, user.username, user.role);
  res.json(successResponse({
    token,
    userId: user.id,
    username: user.username,
    nickname: user.nickname || user.username,
    role: user.role,
  }));
});

// POST /auth/api/v1/public/auth/register
router.post('/api/v1/public/auth/register', (req, res) => {
  const { username, email, password, nickname } = req.body;
  if (!username || !password) {
    res.status(400).json(errorResponse('Username and password required'));
    return;
  }

  const existing = getOne('SELECT id FROM users WHERE username = ?', [username]);
  if (existing) {
    res.status(409).json(errorResponse('Username already exists'));
    return;
  }

  const hash = bcrypt.hashSync(password, 10);
  run(
    'INSERT INTO users (username, email, password_hash, nickname, role, status) VALUES (?, ?, ?, ?, ?, ?)',
    [username, email || '', hash, nickname || username, 'USER', 'ACTIVE']
  );
  saveDatabase();

  res.json(successResponse(null, 'Registration successful'));
});

// POST /auth/api/v1/public/auth/logout
router.post('/api/v1/public/auth/logout', (req, res) => {
  res.json(successResponse(null, 'Logout successful'));
});

// GET /auth/api/v1/me
router.get('/api/v1/me', (req, res) => {
  const authHeader = req.headers.authorization;
  if (!authHeader || !authHeader.startsWith('Bearer ')) {
    res.status(401).json(errorResponse('Missing token', 'AUTH_001'));
    return;
  }

  const token = authHeader.substring(7);
  try {
    const decoded = jwt.verify(token, JWT_SECRET) as any;
    const user = getOne('SELECT id, username, email, nickname, role, status, last_login_at as lastLoginAt, created_at as createdAt, updated_at as updatedAt FROM users WHERE id = ?', [decoded.userId]);
    if (!user) {
      res.status(401).json(errorResponse('User not found', 'AUTH_003'));
      return;
    }
    res.json(successResponse({
      id: user.id,
      username: user.username,
      email: user.email,
      nickname: user.nickname,
      role: user.role,
    }));
  } catch {
    res.status(401).json(errorResponse('Invalid token', 'AUTH_002'));
  }
});

// POST /auth/api/v1/public/auth/user/list
router.post('/api/v1/public/auth/user/list', (req, res) => {
  const { page = 1, size = 10, username, role, status } = req.body;
  let sql = 'SELECT id, username, nickname, role, status, last_login_at as lastLoginAt, created_at as createdAt, updated_at as updatedAt FROM users WHERE 1=1';
  const params: any[] = [];

  if (username) { sql += ' AND username LIKE ?'; params.push(`%${username}%`); }
  if (role) { sql += ' AND role = ?'; params.push(role); }
  if (status) { sql += ' AND status = ?'; params.push(status); }

  const countSql = `SELECT COUNT(*) as cnt FROM users WHERE 1=1${sql.split('WHERE 1=1')[1].split('ORDER BY')[0]}`;
  const countResult = query(countSql, [...params]);
  const total = countResult[0]?.cnt || 0;

  sql += ' ORDER BY id DESC LIMIT ? OFFSET ?';
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

// POST /auth/api/v1/public/auth/user/get
router.post('/api/v1/public/auth/user/get', (req, res) => {
  const { id } = req.body;
  const user = getOne('SELECT id, username, nickname, role, status, last_login_at as lastLoginAt, created_at as createdAt, updated_at as updatedAt FROM users WHERE id = ?', [id]);
  res.json(successResponse(user));
});

// POST /auth/api/v1/public/auth/user/update
router.post('/api/v1/public/auth/user/update', (req, res) => {
  const { id, nickname, role, status } = req.body;
  const fields: string[] = [];
  const params: any[] = [];
  if (nickname !== undefined) { fields.push('nickname = ?'); params.push(nickname); }
  if (role !== undefined) { fields.push('role = ?'); params.push(role); }
  if (status !== undefined) { fields.push('status = ?'); params.push(status); }
  fields.push("updated_at = datetime('now')");
  params.push(id);

  run(`UPDATE users SET ${fields.join(', ')} WHERE id = ?`, params);
  saveDatabase();

  const user = getOne('SELECT id, username, nickname, role, status, last_login_at as lastLoginAt, created_at as createdAt, updated_at as updatedAt FROM users WHERE id = ?', [id]);
  res.json(successResponse(user));
});

// POST /auth/api/v1/public/auth/user/delete
router.post('/api/v1/public/auth/user/delete', (req, res) => {
  const { id } = req.body;
  run('DELETE FROM users WHERE id = ?', [id]);
  saveDatabase();
  res.json(successResponse(null));
});

export default router;
