-- Sample Data for Caregiver Route Optimization Demo
--
-- This creates a sample dataset of:
-- - 8 patients in a city (nodes)
-- - Travel times between patient locations (edges)
-- - Patient details (properties)
--
-- Use case: Home healthcare agency optimizing daily caregiver routes
--
-- To load:
--   docker exec -i iris /usr/irissys/bin/irissession IRIS -U USER < sql/caregiver_routing_demo.sql
--
-- Then test in Terminal:
--   Set patients = $ListBuild("patient:001", "patient:002", "patient:003", "patient:004", "patient:005")
--   Do ##class(Graph.CaregiverRouter).OptimizeRoute(patients, .route, .time)
--   Write "Total time: ", time, " minutes", !
--   For i=1:1:$ListLength(route) Write i, ". ", $ListGet(route, i), !

-- Clean existing patient data
DELETE FROM rdf_props WHERE s LIKE 'patient:%';
DELETE FROM rdf_edges WHERE s LIKE 'patient:%';
DELETE FROM rdf_labels WHERE s LIKE 'patient:%';

-- Insert patient nodes (labels)
INSERT INTO rdf_labels (s, label) VALUES ('patient:001', 'Patient');
INSERT INTO rdf_labels (s, label) VALUES ('patient:002', 'Patient');
INSERT INTO rdf_labels (s, label) VALUES ('patient:003', 'Patient');
INSERT INTO rdf_labels (s, label) VALUES ('patient:004', 'Patient');
INSERT INTO rdf_labels (s, label) VALUES ('patient:005', 'Patient');
INSERT INTO rdf_labels (s, label) VALUES ('patient:006', 'Patient');
INSERT INTO rdf_labels (s, label) VALUES ('patient:007', 'Patient');
INSERT INTO rdf_labels (s, label) VALUES ('patient:008', 'Patient');

-- Insert patient properties
-- Patient 001: Eleanor Rodriguez (Downtown)
INSERT INTO rdf_props (s, key, val) VALUES ('patient:001', 'name', 'Eleanor Rodriguez');
INSERT INTO rdf_props (s, key, val) VALUES ('patient:001', 'address', '123 Main St, Downtown');
INSERT INTO rdf_props (s, key, val) VALUES ('patient:001', 'service_minutes', '45');
INSERT INTO rdf_props (s, key, val) VALUES ('patient:001', 'care_type', 'Physical Therapy');

-- Patient 002: Marcus Johnson (Northside)
INSERT INTO rdf_props (s, key, val) VALUES ('patient:002', 'name', 'Marcus Johnson');
INSERT INTO rdf_props (s, key, val) VALUES ('patient:002', 'address', '456 Oak Ave, Northside');
INSERT INTO rdf_props (s, key, val) VALUES ('patient:002', 'service_minutes', '30');
INSERT INTO rdf_props (s, key, val) VALUES ('patient:002', 'care_type', 'Wound Care');

-- Patient 003: Sarah Chen (Eastside)
INSERT INTO rdf_props (s, key, val) VALUES ('patient:003', 'name', 'Sarah Chen');
INSERT INTO rdf_props (s, key, val) VALUES ('patient:003', 'address', '789 Elm St, Eastside');
INSERT INTO rdf_props (s, key, val) VALUES ('patient:003', 'service_minutes', '60');
INSERT INTO rdf_props (s, key, val) VALUES ('patient:003', 'care_type', 'Diabetes Management');

-- Patient 004: Robert Williams (Westside)
INSERT INTO rdf_props (s, key, val) VALUES ('patient:004', 'name', 'Robert Williams');
INSERT INTO rdf_props (s, key, val) VALUES ('patient:004', 'address', '321 Pine Rd, Westside');
INSERT INTO rdf_props (s, key, val) VALUES ('patient:004', 'service_minutes', '40');
INSERT INTO rdf_props (s, key, val) VALUES ('patient:004', 'care_type', 'Medication Management');

-- Patient 005: Maria Garcia (Southside)
INSERT INTO rdf_props (s, key, val) VALUES ('patient:005', 'name', 'Maria Garcia');
INSERT INTO rdf_props (s, key, val) VALUES ('patient:005', 'address', '654 Cedar Ln, Southside');
INSERT INTO rdf_props (s, key, val) VALUES ('patient:005', 'service_minutes', '50');
INSERT INTO rdf_props (s, key, val) VALUES ('patient:005', 'care_type', 'Post-Surgery Care');

-- Patient 006: James Taylor (Suburbs)
INSERT INTO rdf_props (s, key, val) VALUES ('patient:006', 'name', 'James Taylor');
INSERT INTO rdf_props (s, key, val) VALUES ('patient:006', 'address', '987 Maple Dr, Suburbs');
INSERT INTO rdf_props (s, key, val) VALUES ('patient:006', 'service_minutes', '35');
INSERT INTO rdf_props (s, key, val) VALUES ('patient:006', 'care_type', 'Blood Pressure Monitoring');

-- Patient 007: Linda Anderson (University District)
INSERT INTO rdf_props (s, key, val) VALUES ('patient:007', 'name', 'Linda Anderson');
INSERT INTO rdf_props (s, key, val) VALUES ('patient:007', 'address', '147 College Blvd, University District');
INSERT INTO rdf_props (s, key, val) VALUES ('patient:007', 'service_minutes', '25');
INSERT INTO rdf_props (s, key, val) VALUES ('patient:007', 'care_type', 'Vitals Check');

-- Patient 008: David Lee (Industrial Area)
INSERT INTO rdf_props (s, key, val) VALUES ('patient:008', 'name', 'David Lee');
INSERT INTO rdf_props (s, key, val) VALUES ('patient:008', 'address', '258 Factory St, Industrial Area');
INSERT INTO rdf_props (s, key, val) VALUES ('patient:008', 'service_minutes', '55');
INSERT INTO rdf_props (s, key, val) VALUES ('patient:008', 'care_type', 'COPD Management');

-- Insert travel time edges (bidirectional)
-- Format: {"travel_time_minutes": X, "distance_miles": Y}

-- Downtown (001) connections
INSERT INTO rdf_edges (s, o_id, label, qualifiers)
VALUES ('patient:001', 'patient:002', 'travel_to', '{"travel_time_minutes": 12, "distance_miles": 3.2}');
INSERT INTO rdf_edges (s, o_id, label, qualifiers)
VALUES ('patient:002', 'patient:001', 'travel_to', '{"travel_time_minutes": 12, "distance_miles": 3.2}');

INSERT INTO rdf_edges (s, o_id, label, qualifiers)
VALUES ('patient:001', 'patient:003', 'travel_to', '{"travel_time_minutes": 8, "distance_miles": 2.1}');
INSERT INTO rdf_edges (s, o_id, label, qualifiers)
VALUES ('patient:003', 'patient:001', 'travel_to', '{"travel_time_minutes": 8, "distance_miles": 2.1}');

INSERT INTO rdf_edges (s, o_id, label, qualifiers)
VALUES ('patient:001', 'patient:004', 'travel_to', '{"travel_time_minutes": 15, "distance_miles": 4.5}');
INSERT INTO rdf_edges (s, o_id, label, qualifiers)
VALUES ('patient:004', 'patient:001', 'travel_to', '{"travel_time_minutes": 15, "distance_miles": 4.5}');

INSERT INTO rdf_edges (s, o_id, label, qualifiers)
VALUES ('patient:001', 'patient:005', 'travel_to', '{"travel_time_minutes": 10, "distance_miles": 2.8}');
INSERT INTO rdf_edges (s, o_id, label, qualifiers)
VALUES ('patient:005', 'patient:001', 'travel_to', '{"travel_time_minutes": 10, "distance_miles": 2.8}');

-- Northside (002) connections
INSERT INTO rdf_edges (s, o_id, label, qualifiers)
VALUES ('patient:002', 'patient:006', 'travel_to', '{"travel_time_minutes": 7, "distance_miles": 1.9}');
INSERT INTO rdf_edges (s, o_id, label, qualifiers)
VALUES ('patient:006', 'patient:002', 'travel_to', '{"travel_time_minutes": 7, "distance_miles": 1.9}');

INSERT INTO rdf_edges (s, o_id, label, qualifiers)
VALUES ('patient:002', 'patient:007', 'travel_to', '{"travel_time_minutes": 18, "distance_miles": 5.3}');
INSERT INTO rdf_edges (s, o_id, label, qualifiers)
VALUES ('patient:007', 'patient:002', 'travel_to', '{"travel_time_minutes": 18, "distance_miles": 5.3}');

-- Eastside (003) connections
INSERT INTO rdf_edges (s, o_id, label, qualifiers)
VALUES ('patient:003', 'patient:007', 'travel_to', '{"travel_time_minutes": 6, "distance_miles": 1.5}');
INSERT INTO rdf_edges (s, o_id, label, qualifiers)
VALUES ('patient:007', 'patient:003', 'travel_to', '{"travel_time_minutes": 6, "distance_miles": 1.5}');

INSERT INTO rdf_edges (s, o_id, label, qualifiers)
VALUES ('patient:003', 'patient:008', 'travel_to', '{"travel_time_minutes": 14, "distance_miles": 4.1}');
INSERT INTO rdf_edges (s, o_id, label, qualifiers)
VALUES ('patient:008', 'patient:003', 'travel_to', '{"travel_time_minutes": 14, "distance_miles": 4.1}');

-- Westside (004) connections
INSERT INTO rdf_edges (s, o_id, label, qualifiers)
VALUES ('patient:004', 'patient:005', 'travel_to', '{"travel_time_minutes": 20, "distance_miles": 6.2}');
INSERT INTO rdf_edges (s, o_id, label, qualifiers)
VALUES ('patient:005', 'patient:004', 'travel_to', '{"travel_time_minutes": 20, "distance_miles": 6.2}');

INSERT INTO rdf_edges (s, o_id, label, qualifiers)
VALUES ('patient:004', 'patient:006', 'travel_to', '{"travel_time_minutes": 25, "distance_miles": 7.8}');
INSERT INTO rdf_edges (s, o_id, label, qualifiers)
VALUES ('patient:006', 'patient:004', 'travel_to', '{"travel_time_minutes": 25, "distance_miles": 7.8}');

-- Southside (005) connections
INSERT INTO rdf_edges (s, o_id, label, qualifiers)
VALUES ('patient:005', 'patient:008', 'travel_to', '{"travel_time_minutes": 11, "distance_miles": 3.4}');
INSERT INTO rdf_edges (s, o_id, label, qualifiers)
VALUES ('patient:008', 'patient:005', 'travel_to', '{"travel_time_minutes": 11, "distance_miles": 3.4}');

-- Suburbs (006) connections
INSERT INTO rdf_edges (s, o_id, label, qualifiers)
VALUES ('patient:006', 'patient:007', 'travel_to', '{"travel_time_minutes": 9, "distance_miles": 2.5}');
INSERT INTO rdf_edges (s, o_id, label, qualifiers)
VALUES ('patient:007', 'patient:006', 'travel_to', '{"travel_time_minutes": 9, "distance_miles": 2.5}');

-- University District (007) to Industrial Area (008)
INSERT INTO rdf_edges (s, o_id, label, qualifiers)
VALUES ('patient:007', 'patient:008', 'travel_to', '{"travel_time_minutes": 22, "distance_miles": 6.9}');
INSERT INTO rdf_edges (s, o_id, label, qualifiers)
VALUES ('patient:008', 'patient:007', 'travel_to', '{"travel_time_minutes": 22, "distance_miles": 6.9}');

-- Some cross-connections for graph completeness
INSERT INTO rdf_edges (s, o_id, label, qualifiers)
VALUES ('patient:002', 'patient:003', 'travel_to', '{"travel_time_minutes": 16, "distance_miles": 4.7}');
INSERT INTO rdf_edges (s, o_id, label, qualifiers)
VALUES ('patient:003', 'patient:002', 'travel_to', '{"travel_time_minutes": 16, "distance_miles": 4.7}');

INSERT INTO rdf_edges (s, o_id, label, qualifiers)
VALUES ('patient:004', 'patient:008', 'travel_to', '{"travel_time_minutes": 28, "distance_miles": 8.5}');
INSERT INTO rdf_edges (s, o_id, label, qualifiers)
VALUES ('patient:008', 'patient:004', 'travel_to', '{"travel_time_minutes": 28, "distance_miles": 8.5}');

-- Verify data loaded
SELECT COUNT(*) AS patient_count FROM rdf_labels WHERE label = 'Patient';
SELECT COUNT(*) AS patient_property_count FROM rdf_props WHERE s LIKE 'patient:%';
SELECT COUNT(*) AS travel_edge_count FROM rdf_edges WHERE label = 'travel_to';

-- Show sample patient
SELECT s, key, val FROM rdf_props WHERE s = 'patient:001' ORDER BY key;

-- Show sample travel times from patient 001
SELECT o_id AS destination, qualifiers
FROM rdf_edges
WHERE s = 'patient:001' AND label = 'travel_to';
