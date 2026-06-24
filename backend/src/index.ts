import express from 'express';
import cors from 'cors';
import { initDatabase, saveDatabase } from './db/database.js';
import { seedData } from './db/seed.js';
import authRouter from './routes/auth.js';
import coreRouter from './routes/core.js';
import instanceRouter from './routes/instances.js';
import topologyRouter from './routes/topology.js';
import sparqlRouter from './routes/sparql.js';
import reasoningRouter from './routes/reasoning.js';
import datasourceRouter from './routes/datasource.js';
import reportsRouter from './routes/reports.js';
import adminRouter from './routes/admin.js';

const app = express();
const PORT = process.env.PORT || 3001;

app.use(cors());
app.use(express.json({ limit: '10mb' }));

// Request logging
app.use((req, res, next) => {
  console.log(`[${new Date().toISOString()}] ${req.method} ${req.path}`);
  next();
});

// Mount routers
app.use('/auth', authRouter);
app.use('/core', coreRouter);
app.use('/core', instanceRouter);
app.use('/core', topologyRouter);
app.use('/core', sparqlRouter);
app.use('/core', reasoningRouter);
app.use('/core', datasourceRouter);
app.use('/core', reportsRouter);
app.use('/core', adminRouter);

// Health check
app.get('/health', (req, res) => {
  res.json({ status: 'ok', timestamp: new Date().toISOString() });
});

// Save DB periodically
setInterval(() => {
  saveDatabase();
}, 30000);

// Graceful shutdown
process.on('SIGINT', () => {
  console.log('\nSaving database and shutting down...');
  saveDatabase();
  process.exit(0);
});

// Start server
async function start() {
  await initDatabase();
  seedData();

  app.listen(PORT, () => {
    console.log(`============================================`);
    console.log(`Ontology Web Backend`);
    console.log(`============================================`);
    console.log(`Server running on http://localhost:${PORT}`);
    console.log(`Health check: http://localhost:${PORT}/health`);
    console.log(``);
    console.log(`Demo credentials:`);
    console.log(`  Username: admin`);
    console.log(`  Password: admin123`);
    console.log(`  Username: demo`);
    console.log(`  Password: demo123`);
    console.log(`============================================`);
  });
}

start().catch(console.error);
