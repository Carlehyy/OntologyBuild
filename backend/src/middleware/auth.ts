import jwt from 'jsonwebtoken';
import { Request, Response, NextFunction } from 'express';

const JWT_SECRET = process.env.JWT_SECRET || 'ontology-web-demo-secret-key-2024';

export interface AuthRequest extends Request {
  user?: { userId: number; username: string; role: string };
}

export function generateToken(userId: number, username: string, role: string): string {
  return jwt.sign({ userId, username, role }, JWT_SECRET, { expiresIn: '7d' });
}

export function verifyToken(token: string): { userId: number; username: string; role: string } | null {
  try {
    return jwt.verify(token, JWT_SECRET) as any;
  } catch {
    return null;
  }
}

export function authMiddleware(req: AuthRequest, res: Response, next: NextFunction): void {
  const authHeader = req.headers.authorization;
  if (!authHeader || !authHeader.startsWith('Bearer ')) {
    res.status(401).json({ code: 'AUTH_001', message: 'Missing token', data: null });
    return;
  }

  const token = authHeader.substring(7);
  const decoded = verifyToken(token);
  if (!decoded) {
    res.status(401).json({ code: 'AUTH_002', message: 'Invalid or expired token', data: null });
    return;
  }

  req.user = decoded;
  next();
}

// Unified API response helper
export function successResponse<T>(data: T, message: string = 'Success') {
  return { code: 'SUCCESS', message, data };
}

export function errorResponse(message: string, code: string | number = 'ERROR') {
  return { code, message, data: null };
}
