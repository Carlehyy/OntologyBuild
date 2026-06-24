/**
 * Seed realistic demo data for the ontology platform.
 * Creates a complete "Smart Manufacturing" domain ontology with:
 * - Classes, properties, relations
 * - Instances with realistic data
 * - Namespaces, versions
 * - Data sources, field mappings
 * - Reports, templates
 * - Demo user account
 */

import bcrypt from 'bcryptjs';
import { run, getOne, saveDatabase } from './database.js';

export function seedData(): void {
  console.log('Seeding demo data...');

  // Check if already seeded
  try {
    const existing = getOne('SELECT COUNT(*) as cnt FROM users');
    if (existing && existing.cnt > 0) {
      console.log('Data already seeded, skipping.');
      return;
    }
  } catch {
    // Table doesn't exist yet, continue with seeding
  }

  // ===================== DEMO USER =====================
  const adminHash = bcrypt.hashSync('admin123', 10);
  run(`INSERT INTO users (username, email, password_hash, nickname, role, status, last_login_at)
    VALUES ('admin', 'admin@ontology.io', ?, 'Administrator', 'ADMIN', 'ACTIVE', datetime('now'))`, [adminHash]);

  const demoHash = bcrypt.hashSync('demo123', 10);
  run(`INSERT INTO users (username, email, password_hash, nickname, role, status)
    VALUES ('demo', 'demo@ontology.io', ?, 'Demo User', 'USER', 'ACTIVE')`, [demoHash]);

  // ===================== NAMESPACES =====================
  run(`INSERT INTO namespaces (prefix, uri, type, description) VALUES
    ('rdf', 'http://www.w3.org/1999/02/22-rdf-syntax-ns#', 'Standard', 'RDF core vocabulary'),
    ('rdfs', 'http://www.w3.org/2000/01/rdf-schema#', 'Standard', 'RDF Schema vocabulary'),
    ('owl', 'http://www.w3.org/2002/07/owl#', 'Standard', 'Web Ontology Language'),
    ('xsd', 'http://www.w3.org/2001/XMLSchema#', 'Standard', 'XML Schema datatypes'),
    ('ont', 'http://ontology.io/schema#', 'Custom', 'Smart Manufacturing Ontology'),
    ('foaf', 'http://xmlns.com/foaf/0.1/', 'Standard', 'Friend of a Friend vocabulary'),
    ('dc', 'http://purl.org/dc/elements/1.1/', 'Standard', 'Dublin Core metadata'),
    ('skos', 'http://www.w3.org/2004/02/skos/core#', 'Standard', 'Simple Knowledge Organization System')`);

  // ===================== ONTOLOGY =====================
  run(`INSERT INTO ontologies (name, description, uri, status, version, class_count, relation_count, property_count, instance_count)
    VALUES ('Smart Manufacturing', 'A comprehensive ontology for smart manufacturing systems covering equipment, processes, quality control, and supply chain management.', 'http://ontology.io/smart-manufacturing', 'Published', '2.1.0', 12, 14, 18, 35)`);

  const ontId = 1;

  // ===================== CLASSES =====================
  run(`INSERT INTO classes (ontology_id, name, uri, description, color, icon, status, instance_count) VALUES
    (1, 'Equipment', 'http://ontology.io/smart-manufacturing/Equipment', 'Physical machinery and devices used in manufacturing processes', '#818cf8', 'factory', 'ACTIVE', 8),
    (1, 'Sensor', 'http://ontology.io/smart-manufacturing/Sensor', 'IoT sensors that collect data from equipment and environment', '#22d3ee', 'activity', 'ACTIVE', 6),
    (1, 'Product', 'http://ontology.io/smart-manufacturing/Product', 'Manufactured items or components produced by the factory', '#4ade80', 'box', 'ACTIVE', 5),
    (1, 'Process', 'http://ontology.io/smart-manufacturing/Process', 'Manufacturing processes and workflows', '#f59e0b', 'git-branch', 'ACTIVE', 4),
    (1, 'QualityCheckpoint', 'http://ontology.io/smart-manufacturing/QualityCheckpoint', 'Quality inspection points in the manufacturing line', '#f472b6', 'check-circle', 'ACTIVE', 3),
    (1, 'Supplier', 'http://ontology.io/smart-manufacturing/Supplier', 'External vendors and material suppliers', '#a78bfa', 'truck', 'ACTIVE', 4),
    (1, 'Facility', 'http://ontology.io/smart-manufacturing/Facility', 'Factory buildings and production facilities', '#60a5fa', 'building', 'ACTIVE', 2),
    (1, 'Material', 'http://ontology.io/smart-manufacturing/Material', 'Raw materials and components used in production', '#fbbf24', 'layers', 'ACTIVE', 3),
    (1, 'Operator', 'http://ontology.io/smart-manufacturing/Operator', 'Factory workers and machine operators', '#94a3b8', 'user', 'ACTIVE', 5),
    (1, 'Alert', 'http://ontology.io/smart-manufacturing/Alert', 'System alerts and notifications', '#ef4444', 'alert-triangle', 'ACTIVE', 3),
    (1, 'WorkOrder', 'http://ontology.io/smart-manufacturing/WorkOrder', 'Production orders and work assignments', '#10b981', 'file-text', 'ACTIVE', 4),
    (1, 'MaintenanceRecord', 'http://ontology.io/smart-manufacturing/MaintenanceRecord', 'Equipment maintenance history', '#6366f1', 'tool', 'ACTIVE', 2)`);

  // ===================== PROPERTIES =====================
  run(`INSERT INTO properties (ontology_id, name, uri, description, data_type, constraints, status) VALUES
    (1, 'name', 'http://ontology.io/schema/name', 'Display name of the entity', 'STRING', '{"maxLength":100}', 'ACTIVE'),
    (1, 'description', 'http://ontology.io/schema/description', 'Detailed description', 'TEXT', '{"maxLength":2000}', 'ACTIVE'),
    (1, 'createdAt', 'http://ontology.io/schema/createdAt', 'Creation timestamp', 'DATETIME', null, 'ACTIVE'),
    (1, 'updatedAt', 'http://ontology.io/schema/updatedAt', 'Last update timestamp', 'DATETIME', null, 'ACTIVE'),
    (1, 'status', 'http://ontology.io/schema/status', 'Current status', 'STRING', '{"enum":["ACTIVE","INACTIVE","MAINTENANCE","DECOMMISSIONED"]}', 'ACTIVE'),
    (1, 'serialNumber', 'http://ontology.io/schema/serialNumber', 'Unique serial number', 'STRING', '{"unique":true,"maxLength":50}', 'ACTIVE'),
    (1, 'temperature', 'http://ontology.io/schema/temperature', 'Temperature reading in Celsius', 'DECIMAL', '{"min":-50,"max":200}', 'ACTIVE'),
    (1, 'pressure', 'http://ontology.io/schema/pressure', 'Pressure reading in PSI', 'DECIMAL', '{"min":0,"max":10000}', 'ACTIVE'),
    (1, 'vibration', 'http://ontology.io/schema/vibration', 'Vibration level in mm/s', 'DECIMAL', '{"min":0,"max":50}', 'ACTIVE'),
    (1, 'efficiency', 'http://ontology.io/schema/efficiency', 'Operational efficiency percentage', 'DECIMAL', '{"min":0,"max":100}', 'ACTIVE'),
    (1, 'cost', 'http://ontology.io/schema/cost', 'Cost in USD', 'DECIMAL', '{"min":0}', 'ACTIVE'),
    (1, 'quantity', 'http://ontology.io/schema/quantity', 'Quantity or amount', 'INTEGER', '{"min":0}', 'ACTIVE'),
    (1, 'priority', 'http://ontology.io/schema/priority', 'Priority level', 'STRING', '{"enum":["LOW","MEDIUM","HIGH","URGENT"]}', 'ACTIVE'),
    (1, 'location', 'http://ontology.io/schema/location', 'Physical location', 'STRING', '{"maxLength":200}', 'ACTIVE'),
    (1, 'email', 'http://ontology.io/schema/email', 'Email address', 'STRING', '{"format":"email"}', 'ACTIVE'),
    (1, 'phone', 'http://ontology.io/schema/phone', 'Phone number', 'STRING', '{"maxLength":20}', 'ACTIVE'),
    (1, 'rating', 'http://ontology.io/schema/rating', 'Quality rating', 'DECIMAL', '{"min":1,"max":5}', 'ACTIVE'),
    (1, 'certified', 'http://ontology.io/schema/certified', 'Certification status', 'BOOLEAN', null, 'ACTIVE')`);

  // ===================== RELATIONS =====================
  run(`INSERT INTO relations (ontology_id, name, uri, description, domain_class_id, range_class_id, is_functional, is_inverse_functional, is_symmetric, is_transitive, cardinality, status) VALUES
    (1, 'hasSensor', 'http://ontology.io/schema/hasSensor', 'Equipment has installed sensors', 1, 2, 0, 0, 0, 0, '1..*', 'ACTIVE'),
    (1, 'produces', 'http://ontology.io/schema/produces', 'Process produces products', 4, 3, 0, 0, 0, 0, '1..*', 'ACTIVE'),
    (1, 'usesMaterial', 'http://ontology.io/schema/usesMaterial', 'Process uses raw materials', 4, 8, 0, 0, 0, 0, '1..*', 'ACTIVE'),
    (1, 'checks', 'http://ontology.io/schema/checks', 'Quality checkpoint checks products', 5, 3, 0, 0, 0, 0, '1..*', 'ACTIVE'),
    (1, 'supplies', 'http://ontology.io/schema/supplies', 'Supplier supplies materials', 6, 8, 0, 0, 0, 0, '1..*', 'ACTIVE'),
    (1, 'locatedIn', 'http://ontology.io/schema/locatedIn', 'Equipment is located in facility', 1, 7, 1, 0, 0, 0, '1..1', 'ACTIVE'),
    (1, 'operates', 'http://ontology.io/schema/operates', 'Operator operates equipment', 9, 1, 0, 0, 0, 0, '1..*', 'ACTIVE'),
    (1, 'triggers', 'http://ontology.io/schema/triggers', 'Alert triggers for equipment', 10, 1, 0, 0, 0, 0, '1..1', 'ACTIVE'),
    (1, 'forEquipment', 'http://ontology.io/schema/forEquipment', 'Work order is for equipment', 11, 1, 1, 0, 0, 0, '1..1', 'ACTIVE'),
    (1, 'records', 'http://ontology.io/schema/records', 'Maintenance record for equipment', 12, 1, 0, 0, 0, 0, '1..1', 'ACTIVE'),
    (1, 'partOf', 'http://ontology.io/schema/partOf', 'Product is part of another product', 3, 3, 0, 0, 0, 1, '0..*', 'ACTIVE'),
    (1, 'monitors', 'http://ontology.io/schema/monitors', 'Sensor monitors process', 2, 4, 0, 0, 0, 0, '1..1', 'ACTIVE'),
    (1, 'reportsTo', 'http://ontology.io/schema/reportsTo', 'Operator reports to another operator', 9, 9, 0, 0, 0, 0, '0..1', 'ACTIVE'),
    (1, 'assignedTo', 'http://ontology.io/schema/assignedTo', 'Work order assigned to operator', 11, 9, 1, 0, 0, 0, '1..1', 'ACTIVE')`);

  // ===================== CLASS-PROPERTY BINDINGS =====================
  run(`INSERT INTO class_properties (class_id, property_id, is_required, is_unique, default_value, sort_order) VALUES
    (1, 1, 1, 0, null, 1), (1, 2, 0, 0, null, 2), (1, 6, 1, 1, null, 3), (1, 10, 0, 0, '85.0', 4), (1, 14, 1, 0, null, 5), (1, 5, 1, 0, 'ACTIVE', 6),
    (2, 1, 1, 0, null, 1), (2, 7, 1, 0, null, 2), (2, 8, 0, 0, null, 3), (2, 9, 0, 0, null, 4),
    (3, 1, 1, 0, null, 1), (3, 2, 0, 0, null, 2), (3, 11, 0, 0, null, 3), (3, 17, 0, 0, null, 4),
    (4, 1, 1, 0, null, 1), (4, 2, 0, 0, null, 2), (4, 10, 0, 0, null, 3), (4, 12, 0, 0, null, 4),
    (5, 1, 1, 0, null, 1), (5, 2, 0, 0, null, 2), (5, 17, 1, 0, null, 3),
    (6, 1, 1, 0, null, 1), (6, 2, 0, 0, null, 2), (6, 15, 1, 0, null, 3), (6, 16, 0, 0, null, 4), (6, 18, 0, 0, '1', 5),
    (7, 1, 1, 0, null, 1), (7, 14, 1, 0, null, 2),
    (8, 1, 1, 0, null, 1), (8, 11, 0, 0, null, 2), (8, 12, 1, 0, null, 3),
    (9, 1, 1, 0, null, 1), (9, 15, 0, 0, null, 2), (9, 16, 0, 0, null, 3),
    (10, 1, 1, 0, null, 1), (10, 5, 1, 0, 'ACTIVE', 2), (10, 13, 1, 0, 'MEDIUM', 3),
    (11, 1, 1, 0, null, 1), (11, 12, 1, 0, null, 2), (11, 13, 0, 0, 'MEDIUM', 3), (11, 5, 1, 0, 'PENDING', 4),
    (12, 1, 1, 0, null, 1), (12, 2, 0, 0, null, 2), (12, 5, 1, 0, 'COMPLETED', 3)`);

  // ===================== INSTANCES =====================
  run(`INSERT INTO instances (ontology_id, class_id, name, description, domain, status) VALUES
    -- Equipment (class_id=1)
    (1, 1, 'CNC-Machine-01', 'High-precision CNC milling machine', 'Production Line A', 'ACTIVE'),
    (1, 1, 'Assembly-Robot-02', '6-axis robotic arm for assembly', 'Production Line A', 'ACTIVE'),
    (1, 1, 'Conveyor-Belt-03', 'Automated conveyor belt system', 'Production Line B', 'ACTIVE'),
    (1, 1, 'Injection-Molder-04', 'Plastic injection molding machine', 'Production Line C', 'ACTIVE'),
    (1, 1, 'Welding-Robot-05', 'Arc welding robotic system', 'Production Line B', 'ACTIVE'),
    (1, 1, '3D-Printer-06', 'Industrial SLA 3D printer', 'R&D Lab', 'ACTIVE'),
    (1, 1, 'Lathe-Machine-07', 'CNC turning lathe', 'Production Line A', 'MAINTENANCE'),
    (1, 1, 'Press-Machine-08', 'Hydraulic press for metal forming', 'Production Line C', 'ACTIVE'),
    -- Sensor (class_id=2)
    (1, 2, 'Temp-Sensor-A1', 'Temperature sensor on CNC machine', 'Production Line A', 'ACTIVE'),
    (1, 2, 'Vib-Sensor-B2', 'Vibration sensor on conveyor', 'Production Line B', 'ACTIVE'),
    (1, 2, 'Pressure-Sensor-C3', 'Pressure sensor on injection molder', 'Production Line C', 'ACTIVE'),
    (1, 2, 'Flow-Sensor-D4', 'Material flow sensor', 'Production Line A', 'ACTIVE'),
    (1, 2, 'Power-Sensor-E5', 'Power consumption monitor', 'Production Line B', 'ACTIVE'),
    (1, 2, 'Humidity-Sensor-F6', 'Ambient humidity sensor', 'R&D Lab', 'ACTIVE'),
    -- Product (class_id=3)
    (1, 3, 'Gear-Assembly-A', 'Precision gear assembly component', 'Automotive Division', 'ACTIVE'),
    (1, 3, 'Housing-Unit-B', 'Aluminum housing unit', 'Consumer Electronics', 'ACTIVE'),
    (1, 3, 'Connector-C', 'Electrical connector assembly', 'Automotive Division', 'ACTIVE'),
    (1, 3, 'Bracket-D', 'Mounting bracket assembly', 'Consumer Electronics', 'ACTIVE'),
    (1, 3, 'Valve-E', 'Pneumatic valve component', 'Industrial Division', 'ACTIVE'),
    -- Process (class_id=4)
    (1, 4, 'CNC-Milling-Process', 'CNC milling and drilling operation', 'Production Line A', 'ACTIVE'),
    (1, 4, 'Assembly-Process', 'Robotic assembly workflow', 'Production Line A', 'ACTIVE'),
    (1, 4, 'Quality-Inspection', 'Automated quality inspection', 'Production Line B', 'ACTIVE'),
    (1, 4, 'Packaging-Process', 'Final packaging and labeling', 'Production Line C', 'ACTIVE'),
    -- QualityCheckpoint (class_id=5)
    (1, 5, 'Dimensional-Check-1', 'Dimensional accuracy verification', 'Production Line A', 'ACTIVE'),
    (1, 5, 'Surface-Inspect-2', 'Surface finish quality check', 'Production Line B', 'ACTIVE'),
    (1, 5, 'Torque-Test-3', 'Torque specification testing', 'Production Line A', 'ACTIVE'),
    -- Supplier (class_id=6)
    (1, 6, 'SteelCorp-Inc', 'Steel and metal alloy supplier', 'Pittsburgh, PA', 'ACTIVE'),
    (1, 6, 'Polymer-Supply-Ltd', 'Engineering polymers supplier', 'Akron, OH', 'ACTIVE'),
    (1, 6, 'ElectroParts-Co', 'Electronic components supplier', 'Shenzhen, CN', 'ACTIVE'),
    (1, 6, 'Fastener-World', 'Industrial fasteners supplier', 'Detroit, MI', 'ACTIVE'),
    -- Facility (class_id=7)
    (1, 7, 'Main-Factory-A', 'Primary manufacturing facility', 'Building 1000', 'ACTIVE'),
    (1, 7, 'Assembly-Plant-B', 'Secondary assembly plant', 'Building 2000', 'ACTIVE'),
    -- Material (class_id=8)
    (1, 8, 'Aluminum-Alloy-6061', 'Aerospace grade aluminum', 'Warehouse A', 'ACTIVE'),
    (1, 8, 'Stainless-Steel-316', 'Marine grade stainless steel', 'Warehouse B', 'ACTIVE'),
    (1, 8, 'ABS-Resin', 'Acrylonitrile butadiene styrene', 'Warehouse C', 'ACTIVE'),
    -- Operator (class_id=9)
    (1, 9, 'John-Smith', 'Senior CNC operator', 'Shift A', 'ACTIVE'),
    (1, 9, 'Maria-Garcia', 'Assembly line supervisor', 'Shift A', 'ACTIVE'),
    (1, 9, 'Chen-Wei', 'Quality inspector', 'Shift B', 'ACTIVE'),
    (1, 9, 'Robert-Johnson', 'Maintenance technician', 'Shift B', 'ACTIVE'),
    (1, 9, 'Lisa-Wang', 'Process engineer', 'Day Shift', 'ACTIVE'),
    -- Alert (class_id=10)
    (1, 10, 'Overheat-Alert-CNC', 'CNC machine temperature threshold exceeded', 'Production Line A', 'ACTIVE'),
    (1, 10, 'Low-Pressure-Alert', 'Injection molder pressure below minimum', 'Production Line C', 'RESOLVED'),
    (1, 10, 'Vibration-Warning', 'Conveyor belt vibration anomaly detected', 'Production Line B', 'ACTIVE'),
    -- WorkOrder (class_id=11)
    (1, 11, 'WO-2024-001', 'Scheduled maintenance for CNC-Machine-01', 'Production Line A', 'IN_PROGRESS'),
    (1, 11, 'WO-2024-002', 'Assembly line calibration', 'Production Line A', 'PENDING'),
    (1, 11, 'WO-2024-003', 'Quality audit for Gear-Assembly-A', 'Production Line B', 'COMPLETED'),
    (1, 11, 'WO-2024-004', 'Sensor replacement on conveyor', 'Production Line B', 'IN_PROGRESS'),
    -- MaintenanceRecord (class_id=12)
    (1, 12, 'MR-2024-001', 'Quarterly preventive maintenance', 'Production Line A', 'COMPLETED'),
    (1, 12, 'MR-2024-002', 'Emergency repair - bearing replacement', 'Production Line A', 'COMPLETED')`);

  // ===================== INSTANCE PROPERTY VALUES =====================
  run(`INSERT INTO instance_property_values (instance_id, property_id, value) VALUES
    (1, 6, 'SN-CNC-2021-001'), (1, 10, '92.5'), (1, 14, 'Line A - Station 1'),
    (2, 6, 'SN-ROB-2022-015'), (2, 10, '88.0'), (2, 14, 'Line A - Station 3'),
    (3, 6, 'SN-CVB-2020-008'), (3, 10, '95.2'), (3, 14, 'Line B - Station 1'),
    (4, 6, 'SN-INJ-2023-003'), (4, 10, '78.5'), (4, 14, 'Line C - Station 2'),
    (9, 7, '185.5'), (9, 8, '45.2'), (9, 9, '2.1'),
    (10, 7, '22.0'), (10, 8, '30.1'), (10, 9, '4.5'),
    (15, 11, '24.50'), (15, 12, '1000'),
    (16, 11, '18.75'), (16, 12, '500'),
    (17, 11, '8.30'), (17, 12, '2000'),
    (21, 13, 'HIGH'), (22, 13, 'MEDIUM'), (23, 13, 'LOW'), (24, 13, 'MEDIUM'), (25, 13, 'HIGH'),
    (27, 15, 'contact@steelcorp.com'), (27, 16, '+1-412-555-0100'), (27, 18, '1'),
    (28, 15, 'orders@polymer.com'), (28, 16, '+1-330-555-0200'),
    (31, 11, '4.20'), (31, 12, '5000'),
    (32, 11, '3.85'), (32, 12, '3000'),
    (33, 11, '2.10'), (33, 12, '8000')`);

  // ===================== INSTANCE RELATIONS =====================
  run(`INSERT INTO instance_relations (source_instance_id, relation_id, target_instance_id) VALUES
    -- Equipment has sensors
    (1, 1, 9), (3, 1, 10), (4, 1, 11),
    -- Process produces products
    (13, 2, 15), (14, 2, 16), (15, 2, 17),
    -- Process uses materials
    (13, 3, 31), (13, 3, 32), (14, 3, 33),
    -- Quality checkpoints check products
    (16, 4, 15), (17, 4, 16), (18, 4, 17),
    -- Suppliers supply materials
    (19, 5, 31), (19, 5, 32), (20, 5, 33), (21, 5, 32),
    -- Equipment located in facilities
    (1, 6, 29), (2, 6, 29), (3, 6, 30), (4, 6, 29),
    -- Operators operate equipment
    (25, 7, 1), (26, 7, 2), (27, 7, 3), (28, 7, 4),
    -- Alerts trigger for equipment
    (34, 8, 1), (35, 8, 4), (36, 8, 3),
    -- Work orders for equipment
    (37, 9, 1), (38, 9, 2), (39, 9, 15), (40, 9, 3),
    -- Maintenance records
    (41, 10, 1), (42, 10, 2),
    -- Sensor monitors process
    (9, 12, 13), (10, 12, 14), (11, 12, 15),
    -- Work order assigned to operator
    (37, 14, 25), (38, 14, 26), (40, 14, 28)`);

  // ===================== TOPOLOGIES =====================
  run(`INSERT INTO topologies (ontology_id, name, description, central_class_id, class_ids, class_count, instance_count) VALUES
    (1, 'Production Line A Topology', 'Complete topology of Production Line A including equipment, sensors, and operators', 1, '[1,2,4,5,9,11]', 6, 15),
    (1, 'Supply Chain View', 'Supply chain topology showing suppliers, materials, and products', 3, '[3,6,8,11]', 4, 12),
    (1, 'Quality Control Network', 'Quality control flow from inspection to work orders', 5, '[1,3,5,10,11,12]', 6, 10),
    (1, 'Equipment Hierarchy', 'Equipment and facility relationships', 7, '[1,2,7,10,12]', 5, 18)`);

  // ===================== VERSIONS =====================
  run(`INSERT INTO versions (ontology_id, tag, description, author, additions, deletions, created_at) VALUES
    (1, 'v1.0.0', 'Initial release with core manufacturing classes', 'admin', 12, 0, '2024-01-15T08:00:00Z'),
    (1, 'v1.1.0', 'Added quality control and maintenance classes', 'admin', 4, 0, '2024-03-22T10:30:00Z'),
    (1, 'v1.2.0', 'Added supplier and material management', 'demo', 3, 0, '2024-05-10T14:15:00Z'),
    (1, 'v2.0.0', 'Major refactor: unified process model, added alerts', 'admin', 8, 3, '2024-08-01T09:00:00Z'),
    (1, 'v2.1.0', 'Added work orders and enhanced sensor properties', 'demo', 4, 0, '2024-10-18T11:45:00Z')`);

  // ===================== DATA SOURCES =====================
  run(`INSERT INTO data_sources (ontology_id, name, subtype, host, port, database_name, username, status, last_tested_at, brand_color) VALUES
    (1, 'MES-Production-DB', 'postgresql', 'mes-db.factory.local', 5432, 'manufacturing', 'mes_reader', 'CONNECTED', '2024-10-20T08:00:00Z', '#336791'),
    (1, 'ERP-System-DB', 'postgresql', 'erp-db.corp.local', 5432, 'enterprise', 'erp_user', 'CONNECTED', '2024-10-19T16:30:00Z', '#336791'),
    (1, 'IoT-Platform-DB', 'mysql', 'iot-db.cloud.local', 3306, 'sensor_data', 'iot_service', 'CONNECTED', '2024-10-20T12:00:00Z', '#4479A1'),
    (1, 'QMS-Quality-DB', 'postgresql', 'qms-db.factory.local', 5432, 'quality_mgmt', 'qms_reader', 'ERROR', '2024-10-18T09:15:00Z', '#336791'),
    (1, 'SCM-Supply-DB', 'mysql', 'scm-db.corp.local', 3306, 'supply_chain', 'scm_user', 'NOT_TESTED', null, '#4479A1')`);

  // ===================== FIELD MAPPINGS =====================
  run(`INSERT INTO field_mappings (data_source_id, source_table, source_column, source_column_type, target_class_id, target_property_id, transform_type) VALUES
    (1, 'equipment', 'equipment_id', 'VARCHAR', 1, 6, 'DIRECT'),
    (1, 'equipment', 'equipment_name', 'VARCHAR', 1, 1, 'DIRECT'),
    (1, 'equipment', 'location_code', 'VARCHAR', 1, 14, 'DIRECT'),
    (1, 'equipment', 'efficiency_pct', 'NUMERIC', 1, 10, 'DIRECT'),
    (1, 'sensors', 'sensor_name', 'VARCHAR', 2, 1, 'DIRECT'),
    (1, 'sensors', 'temperature', 'NUMERIC', 2, 7, 'DIRECT'),
    (1, 'sensors', 'vibration', 'NUMERIC', 2, 9, 'DIRECT'),
    (2, 'products', 'product_name', 'VARCHAR', 3, 1, 'DIRECT'),
    (2, 'products', 'unit_cost', 'NUMERIC', 3, 11, 'DIRECT'),
    (2, 'products', 'batch_qty', 'INTEGER', 3, 12, 'DIRECT'),
    (3, 'sensor_readings', 'reading_timestamp', 'TIMESTAMP', 2, 3, 'DIRECT'),
    (3, 'sensor_readings', 'temp_value', 'NUMERIC', 2, 7, 'DIRECT'),
    (3, 'sensor_readings', 'pressure_value', 'NUMERIC', 2, 8, 'DIRECT')`);

  // ===================== REPORTS =====================
  run(`INSERT INTO reports (ontology_id, title, description, icon, icon_color, status, content, created_by) VALUES
    (1, 'Manufacturing Efficiency Report', 'Monthly analysis of equipment efficiency and utilization rates across all production lines.', 'Activity', '#22d3ee', 'Published', '<h2>Manufacturing Efficiency Report</h2><p>Overall equipment efficiency (OEE) across all lines: <strong>87.3%</strong></p><ul><li>Line A: 92.5%</li><li>Line B: 85.1%</li><li>Line C: 78.5%</li></ul>', 1),
    (1, 'Quality Control Summary', 'Weekly quality inspection results and defect analysis.', 'CheckCircle', '#4ade80', 'Published', '<h2>Quality Control Summary</h2><p>Defect rate this week: <strong>0.42%</strong> (target: < 0.5%)</p><p>All quality checkpoints operating within specification.</p>', 1),
    (1, 'Maintenance Schedule Q4', 'Quarterly maintenance plan and historical maintenance records.', 'Tool', '#f59e0b', 'Draft', '<h2>Q4 Maintenance Schedule</h2><p>Upcoming maintenance tasks for October - December 2024.</p><ul><li>CNC-Machine-01: Nov 15</li><li>Assembly-Robot-02: Dec 1</li><li>Injection-Molder-04: Nov 30</li></ul>', 2),
    (1, 'Supply Chain Overview', 'Current supplier performance and material inventory levels.', 'Truck', '#a78bfa', 'Published', '<h2>Supply Chain Overview</h2><p>All suppliers meeting delivery schedules.</p><p>Material inventory: <strong>94% stocked</strong></p>', 1),
    (1, 'IoT Sensor Analytics', 'Sensor data analysis including temperature, vibration, and pressure trends.', 'Activity', '#22d3ee', 'Draft', '<h2>IoT Sensor Analytics</h2><p>Average temperature readings within normal range.</p><p>Vibration alert on Conveyor-Belt-03 requires attention.</p>', 2)`);

  // ===================== REPORT TEMPLATES =====================
  run(`INSERT INTO report_templates (ontology_id, title, description, icon, icon_color, status, content, major_version, minor_version, created_by) VALUES
    (1, 'Efficiency Report Template', 'Standard template for equipment efficiency reporting', 'Activity', '#22d3ee', 'Published', '<h2>{{line_name}} Efficiency Report</h2><p>OEE: {{oee_value}}%</p>', 2, 1, 1),
    (1, 'Quality Report Template', 'Template for quality control summaries', 'CheckCircle', '#4ade80', 'Published', '<h2>Quality Report - {{period}}</h2><p>Defect Rate: {{defect_rate}}%</p>', 1, 3, 1),
    (1, 'Maintenance Log Template', 'Template for maintenance record documentation', 'Tool', '#f59e0b', 'Draft', '<h2>Maintenance Log - {{equipment_name}}</h2><p>Date: {{maintenance_date}}</p>', 1, 0, 2)`);

  // ===================== VALIDATION RESULTS =====================
  run(`INSERT INTO validation_results (ontology_id, severity, check_name, description, target, status) VALUES
    (1, 'passed', 'Class Naming Convention', 'All class names use PascalCase format', 'All Classes', 'PASSED'),
    (1, 'passed', 'Property Types Defined', 'All properties have a data type specified', 'All Properties', 'PASSED'),
    (1, 'warning', 'Relation Cardinality', '3 relations use default cardinality (0..*)', 'Relations', 'WARNING'),
    (1, 'passed', 'Duplicate Names', 'No duplicate names found within the ontology', 'All Entities', 'PASSED'),
    (1, 'warning', 'Orphan Classes', 'MaintenanceRecord has no direct instance relations', 'MaintenanceRecord', 'WARNING'),
    (1, 'passed', 'Namespace Consistency', 'All URIs use registered namespace prefixes', 'Namespaces', 'PASSED'),
    (1, 'passed', 'Circular Dependencies', 'No circular inheritance detected', 'Class Hierarchy', 'PASSED'),
    (1, 'error', 'Missing Constraints', '5 properties lack constraint definitions', 'Properties', 'FAILED')`);

  // ===================== NOTIFICATIONS =====================
  run(`INSERT INTO notifications (user_id, type, title, message, is_read, created_at) VALUES
    (1, 'alert', 'CNC Machine Overheat', 'CNC-Machine-01 temperature exceeded 185C threshold', 0, '2024-10-20T08:30:00Z'),
    (1, 'info', 'Maintenance Completed', 'Scheduled maintenance on Assembly-Robot-02 completed successfully', 1, '2024-10-19T14:00:00Z'),
    (1, 'warning', 'Low Material Stock', 'ABS-Resin inventory below reorder point (2000 kg remaining)', 0, '2024-10-20T10:15:00Z'),
    (1, 'info', 'New Version Published', 'Ontology v2.1.0 has been published', 1, '2024-10-18T11:45:00Z'),
    (1, 'alert', 'Pressure Alert', 'Injection-Molder-04 pressure below minimum threshold', 0, '2024-10-19T16:20:00Z')`);

  // ===================== TASKS =====================
  run(`INSERT INTO tasks (name, type, status, progress, result, created_at, updated_at) VALUES
    ('Forward Reasoning Analysis', 'reasoning', 'COMPLETED', 100, 'Inferred 12 transitive relationships', '2024-10-20T08:00:00Z', '2024-10-20T08:05:00Z'),
    ('SPARQL Export - Full Ontology', 'export', 'COMPLETED', 100, 'Exported 1,247 triples to Turtle format', '2024-10-19T10:00:00Z', '2024-10-19T10:02:00Z'),
    ('Constraint Validation', 'validation', 'COMPLETED', 100, '8 violations found, 3 critical', '2024-10-18T14:00:00Z', '2024-10-18T14:03:00Z'),
    ('Pattern Mining - Hub Entities', 'pattern', 'RUNNING', 65, null, '2024-10-20T12:00:00Z', '2024-10-20T12:30:00Z'),
    ('Data Source Sync - MES', 'sync', 'PENDING', 0, null, '2024-10-20T14:00:00Z', '2024-10-20T14:00:00Z'),
    ('Backward Chaining - Quality', 'reasoning', 'COMPLETED', 100, 'Generated 8 proof steps', '2024-10-17T09:00:00Z', '2024-10-17T09:08:00Z')`);

  // ===================== CHANGE LOGS =====================
  run(`INSERT INTO change_logs (ontology_id, entity_type, entity_id, action, details, author, created_at) VALUES
    (1, 'Ontology', 1, 'CREATE', 'Created Smart Manufacturing ontology', 'admin', '2024-01-15T08:00:00Z'),
    (1, 'Class', 1, 'CREATE', 'Added Equipment class', 'admin', '2024-01-15T08:05:00Z'),
    (1, 'Class', 2, 'CREATE', 'Added Sensor class', 'admin', '2024-01-15T08:10:00Z'),
    (1, 'Relation', 1, 'CREATE', 'Added hasSensor relation', 'admin', '2024-01-15T08:15:00Z'),
    (1, 'Instance', 1, 'CREATE', 'Added CNC-Machine-01 instance', 'demo', '2024-02-01T09:00:00Z'),
    (1, 'Version', 5, 'PUBLISH', 'Published version v2.1.0', 'demo', '2024-10-18T11:45:00Z'),
    (1, 'Property', 7, 'UPDATE', 'Updated temperature constraints', 'admin', '2024-03-10T10:00:00Z'),
    (1, 'Class', 11, 'CREATE', 'Added WorkOrder class', 'demo', '2024-08-15T14:00:00Z')`);

  saveDatabase();
  console.log('Demo data seeded successfully!');
  console.log('  - 2 users (admin/admin123, demo/demo123)');
  console.log('  - 8 namespaces');
  console.log('  - 1 ontology (Smart Manufacturing)');
  console.log('  - 12 classes, 18 properties, 14 relations');
  console.log('  - 35 instances with property values and relations');
  console.log('  - 4 topologies, 5 versions');
  console.log('  - 5 data sources, 12 field mappings');
  console.log('  - 5 reports, 3 report templates');
  console.log('  - Validation results, notifications, tasks, change logs');
}
