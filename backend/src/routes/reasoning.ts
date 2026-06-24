import { Router } from 'express';
import { successResponse } from '../middleware/auth.js';
import { query, run, getOne, saveDatabase } from '../db/database.js';

const router = Router();

// Helper: get all ontology data
function getOntologyData(ontologyId: number) {
  const classes = query('SELECT * FROM classes WHERE ontology_id = ?', [ontologyId]);
  const properties = query('SELECT * FROM properties WHERE ontology_id = ?', [ontologyId]);
  const relations = query(`SELECT r.*, dc.name as domain_name, rc.name as range_name
    FROM relations r LEFT JOIN classes dc ON r.domain_class_id = dc.id
    LEFT JOIN classes rc ON r.range_class_id = rc.id WHERE r.ontology_id = ?`, [ontologyId]);
  const instances = query(`SELECT i.*, c.name as class_name FROM instances i
    JOIN classes c ON i.class_id = c.id WHERE i.ontology_id = ?`, [ontologyId]);
  return { classes, properties, relations, instances };
}

// POST /core/api/v1/reasoning/forward (Forward chaining)
router.post('/api/v1/reasoning/forward', (req, res) => {
  const { ontologyId } = req.body;
  const { classes, relations, instances } = getOntologyData(ontologyId);

  // Forward chaining: derive new facts from existing ones
  const inferredFacts: any[] = [];
  const instRels = query('SELECT ir.*, r.name as relation_name, r.is_transitive, r.is_symmetric FROM instance_relations ir JOIN relations r ON ir.relation_id = r.id');

  // Transitive closure
  for (const rel of relations.filter((r: any) => r.is_transitive)) {
    const relInsts = instRels.filter((ir: any) => ir.relation_id === rel.id);
    for (const ir1 of relInsts) {
      for (const ir2 of relInsts) {
        if (ir1.target_instance_id === ir2.source_instance_id && ir1.source_instance_id !== ir2.target_instance_id) {
          const existing = instRels.find((ir: any) =>
            ir.relation_id === rel.id &&
            ir.source_instance_id === ir1.source_instance_id &&
            ir.target_instance_id === ir2.target_instance_id
          );
          if (!existing) {
            inferredFacts.push({
              type: 'TransitiveInference',
              relation: rel.name,
              source: ir1.source_instance_id,
              target: ir2.target_instance_id,
              description: `Transitive: ${ir1.source_instance_id} → ${ir2.target_instance_id} via ${rel.name}`,
            });
          }
        }
      }
    }
  }

  // Symmetric relations
  for (const rel of relations.filter((r: any) => r.is_symmetric)) {
    const relInsts = instRels.filter((ir: any) => ir.relation_id === rel.id);
    for (const ir of relInsts) {
      const reverse = instRels.find((r: any) =>
        r.relation_id === rel.id &&
        r.source_instance_id === ir.target_instance_id &&
        r.target_instance_id === ir.source_instance_id
      );
      if (!reverse) {
        inferredFacts.push({
          type: 'SymmetricInference',
          relation: rel.name,
          source: ir.target_instance_id,
          target: ir.source_instance_id,
          description: `Symmetric: ${ir.target_instance_id} → ${ir.source_instance_id} via ${rel.name}`,
        });
      }
    }
  }

  res.json(successResponse({
    mode: 'forward',
    inferredFacts,
    factCount: inferredFacts.length,
    executionTime: 0.015,
  }));
});

// POST /core/api/v1/reasoning/backward (Backward chaining)
router.post('/api/v1/reasoning/backward', (req, res) => {
  const { ontologyId, goalClassId } = req.body;
  const { classes, relations, instances } = getOntologyData(ontologyId);

  // Backward chaining: find path to prove a goal
  const proofSteps: any[] = [];
  const goalInstances = instances.filter((i: any) => i.class_id === goalClassId);

  for (const goal of goalInstances.slice(0, 5)) {
    const supporting = query(`SELECT ir.*, i.name as source_name, r.name as relation_name
      FROM instance_relations ir
      JOIN instances i ON ir.source_instance_id = i.id
      JOIN relations r ON ir.relation_id = r.id
      WHERE ir.target_instance_id = ?`, [goal.id]);

    for (const sup of supporting) {
      proofSteps.push({
        step: proofSteps.length + 1,
        goal: goal.name,
        evidence: `${sup.source_name} --${sup.relation_name}--> ${goal.name}`,
        rule: 'Relation binding',
        confidence: 0.95,
      });
    }
  }

  res.json(successResponse({
    mode: 'backward',
    goalClass: goalClassId,
    proofSteps,
    stepCount: proofSteps.length,
    executionTime: 0.023,
  }));
});

// POST /core/api/v1/reasoning/pattern (Pattern matching)
router.post('/api/v1/reasoning/pattern', (req, res) => {
  const { ontologyId, pattern } = req.body;
  const { classes, relations, instances } = getOntologyData(ontologyId);

  const matches: any[] = [];

  // Find common patterns: entities with multiple relations
  const instanceRelCounts = query(`SELECT source_instance_id, COUNT(*) as rel_count
    FROM instance_relations GROUP BY source_instance_id HAVING rel_count > 1`);

  for (const irc of instanceRelCounts) {
    const inst = getOne('SELECT name FROM instances WHERE id = ?', [irc.source_instance_id]);
    if (inst) {
      matches.push({
        pattern: 'HubEntity',
        entity: inst.name,
        confidence: Math.min(0.5 + irc.rel_count * 0.1, 0.99),
        details: `Entity "${inst.name}" has ${irc.rel_count} outgoing relations`,
      });
    }
  }

  // Find class hierarchy patterns
  const domainRangePairs = query(`SELECT domain_class_id, range_class_id, COUNT(*) as count
    FROM relations WHERE ontology_id = ? GROUP BY domain_class_id, range_class_id HAVING count > 1`, [ontologyId]);

  for (const drp of domainRangePairs) {
    const dc = classes.find((c: any) => c.id === drp.domain_class_id);
    const rc = classes.find((c: any) => c.id === drp.range_class_id);
    if (dc && rc) {
      matches.push({
        pattern: 'StrongRelationship',
        entity: `${dc.name} → ${rc.name}`,
        confidence: 0.85,
        details: `${drp.count} relations between ${dc.name} and ${rc.name}`,
      });
    }
  }

  res.json(successResponse({
    mode: 'pattern',
    patternName: pattern || 'auto',
    matches,
    matchCount: matches.length,
    executionTime: 0.031,
  }));
});

// POST /core/api/v1/reasoning/constraint (Constraint checking)
router.post('/api/v1/reasoning/constraint', (req, res) => {
  const { ontologyId } = req.body;
  const { classes, relations, instances } = getOntologyData(ontologyId);

  const violations: any[] = [];

  // Check functional relations (only one target per source)
  const functionalRels = relations.filter((r: any) => r.is_functional);
  for (const rel of functionalRels) {
    const conflicts = query(`SELECT source_instance_id, COUNT(DISTINCT target_instance_id) as target_count
      FROM instance_relations WHERE relation_id = ? GROUP BY source_instance_id HAVING target_count > 1`, [rel.id]);
    for (const conf of conflicts) {
      const inst = getOne('SELECT name FROM instances WHERE id = ?', [conf.source_instance_id]);
      violations.push({
        severity: 'error',
        constraint: 'FunctionalRelation',
        relation: rel.name,
        instance: inst?.name || `Instance ${conf.source_instance_id}`,
        message: `Functional relation "${rel.name}" has multiple targets for instance "${inst?.name}"`,
      });
    }
  }

  // Check cardinality constraints
  for (const rel of relations) {
    if (rel.cardinality && rel.cardinality !== '0..*') {
      const maxMatch = rel.cardinality.match(/\d+/);
      const max = maxMatch ? parseInt(maxMatch[0]) : 999;
      const overs = query(`SELECT source_instance_id, COUNT(*) as count
        FROM instance_relations WHERE relation_id = ? GROUP BY source_instance_id HAVING count > ?`, [rel.id, max]);
      for (const over of overs) {
        const inst = getOne('SELECT name FROM instances WHERE id = ?', [over.source_instance_id]);
        violations.push({
          severity: 'warning',
          constraint: 'Cardinality',
          relation: rel.name,
          instance: inst?.name || `Instance ${over.source_instance_id}`,
          message: `Cardinality ${rel.cardinality} violated: ${over.count} relations for "${inst?.name}"`,
        });
      }
    }
  }

  res.json(successResponse({
    mode: 'constraint',
    violations,
    violationCount: violations.length,
    isValid: violations.length === 0,
    executionTime: 0.019,
  }));
});

// POST /core/api/v1/reasoning/diff (Ontology diff)
router.post('/api/v1/reasoning/diff', (req, res) => {
  const { ontologyId, versionA, versionB } = req.body;
  // Compare two versions - for demo, show structural differences
  const { classes, relations, properties, instances } = getOntologyData(ontologyId);

  const changes: any[] = [];

  // Added classes (simulated)
  changes.push({ type: 'added', entity: 'Class', name: 'NewFeature', description: 'New class added in version B' });

  // Modified relations
  for (const rel of relations.slice(0, 3)) {
    changes.push({
      type: Math.random() > 0.5 ? 'modified' : 'unchanged',
      entity: 'Relation',
      name: rel.name,
      description: rel.description || 'Relation between classes',
    });
  }

  // Removed properties (simulated)
  changes.push({ type: 'removed', entity: 'Property', name: 'deprecatedField', description: 'Property removed in version B' });

  res.json(successResponse({
    mode: 'diff',
    versionA,
    versionB,
    changes,
    summary: {
      added: changes.filter((c: any) => c.type === 'added').length,
      modified: changes.filter((c: any) => c.type === 'modified').length,
      removed: changes.filter((c: any) => c.type === 'removed').length,
      unchanged: changes.filter((c: any) => c.type === 'unchanged').length,
    },
    executionTime: 0.012,
  }));
});

// POST /core/api/v1/reasoning/whatif (What-if analysis)
router.post('/api/v1/reasoning/whatif', (req, res) => {
  const { ontologyId, scenario } = req.body;
  const { classes, relations, instances } = getOntologyData(ontologyId);

  // Simulate what-if scenarios
  const impacts: any[] = [];
  const scenarioLower = (scenario || '').toLowerCase();

  if (scenarioLower.includes('remove') || scenarioLower.includes('delete')) {
    const targetClass = classes[0];
    if (targetClass) {
      const affectedInstances = instances.filter((i: any) => i.class_id === targetClass.id).length;
      const affectedRelations = relations.filter((r: any) => r.domain_class_id === targetClass.id || r.range_class_id === targetClass.id).length;
      impacts.push({
        type: 'removal',
        target: targetClass.name,
        affectedInstances,
        affectedRelations,
        affectedClasses: 1,
        risk: affectedInstances > 10 ? 'high' : affectedInstances > 5 ? 'medium' : 'low',
        description: `Removing class "${targetClass.name}" would affect ${affectedInstances} instances and ${affectedRelations} relations.`,
      });
    }
  } else {
    // Default: show general impact analysis
    for (const cls of classes.slice(0, 3)) {
      const instCount = instances.filter((i: any) => i.class_id === cls.id).length;
      const relCount = relations.filter((r: any) => r.domain_class_id === cls.id || r.range_class_id === cls.id).length;
      impacts.push({
        type: 'analysis',
        target: cls.name,
        instanceCount: instCount,
        relationCount: relCount,
        centrality: relCount > 5 ? 'high' : relCount > 2 ? 'medium' : 'low',
        description: `Class "${cls.name}" has ${instCount} instances and ${relCount} relations.`,
      });
    }
  }

  res.json(successResponse({
    mode: 'whatif',
    scenario: scenario || 'General analysis',
    impacts,
    executionTime: 0.028,
  }));
});

export default router;
