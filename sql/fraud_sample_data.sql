-- sql/fraud_sample_data.sql — Fraud Detection Sample Data
-- Contains 75 accounts, ~50 transactions, 25 alerts with detectable fraud patterns

-- ============================================================================
-- FRAUD PATTERNS INCLUDED:
-- 1. Ring Pattern (Money Laundering): 3 cycles of 5 accounts each
-- 2. Star Pattern (Mule Accounts): 2 hub accounts with 10+ connections each
-- 3. Velocity Violations: 8 accounts with rapid transaction bursts
-- ============================================================================

-- Clear existing fraud data (preserve biomedical data)
DELETE FROM kg_NodeEmbeddings WHERE id LIKE 'ACCOUNT:%' OR id LIKE 'TXN:%';
DELETE FROM rdf_edges WHERE s LIKE 'ACCOUNT:%' OR s LIKE 'TXN:%' OR s LIKE 'ALERT:%';
DELETE FROM rdf_props WHERE s LIKE 'ACCOUNT:%' OR s LIKE 'TXN:%' OR s LIKE 'ALERT:%';
DELETE FROM rdf_labels WHERE s LIKE 'ACCOUNT:%' OR s LIKE 'TXN:%' OR s LIKE 'ALERT:%';
DELETE FROM nodes WHERE node_id LIKE 'ACCOUNT:%' OR node_id LIKE 'TXN:%' OR node_id LIKE 'ALERT:%';

-- ============================================================================
-- NODES (must be created before FK-constrained tables)
-- ============================================================================

-- Total nodes: 150
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A001');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A002');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A003');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A004');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A005');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A006');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A007');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A008');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A009');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A010');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A011');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A012');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A013');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A014');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A015');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A016');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A017');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A018');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A019');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A020');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A021');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A022');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A023');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A024');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A025');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A026');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A027');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A028');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A029');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A030');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A031');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A032');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A033');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A034');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A035');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A036');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A037');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A038');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A039');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A040');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A041');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A042');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A043');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A044');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A045');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A046');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A047');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A048');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A049');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:A050');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:RING1_A');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:RING1_B');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:RING1_C');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:RING1_D');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:RING1_E');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:RING2_A');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:RING2_B');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:RING2_C');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:RING2_D');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:RING2_E');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:RING3_A');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:RING3_B');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:RING3_C');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:RING3_D');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:RING3_E');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:MULE1');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:MULE2');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:VELOCITY1');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:VELOCITY2');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:VELOCITY3');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:VELOCITY4');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:VELOCITY5');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:VELOCITY6');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:VELOCITY7');
INSERT INTO nodes(node_id) VALUES ('ACCOUNT:VELOCITY8');
INSERT INTO nodes(node_id) VALUES ('TXN:T001');
INSERT INTO nodes(node_id) VALUES ('TXN:T002');
INSERT INTO nodes(node_id) VALUES ('TXN:T003');
INSERT INTO nodes(node_id) VALUES ('TXN:T004');
INSERT INTO nodes(node_id) VALUES ('TXN:T005');
INSERT INTO nodes(node_id) VALUES ('TXN:T006');
INSERT INTO nodes(node_id) VALUES ('TXN:T007');
INSERT INTO nodes(node_id) VALUES ('TXN:T008');
INSERT INTO nodes(node_id) VALUES ('TXN:T009');
INSERT INTO nodes(node_id) VALUES ('TXN:T010');
INSERT INTO nodes(node_id) VALUES ('TXN:T011');
INSERT INTO nodes(node_id) VALUES ('TXN:T012');
INSERT INTO nodes(node_id) VALUES ('TXN:T013');
INSERT INTO nodes(node_id) VALUES ('TXN:T014');
INSERT INTO nodes(node_id) VALUES ('TXN:T015');
INSERT INTO nodes(node_id) VALUES ('TXN:T016');
INSERT INTO nodes(node_id) VALUES ('TXN:T017');
INSERT INTO nodes(node_id) VALUES ('TXN:T018');
INSERT INTO nodes(node_id) VALUES ('TXN:T019');
INSERT INTO nodes(node_id) VALUES ('TXN:T020');
INSERT INTO nodes(node_id) VALUES ('TXN:RING1_1');
INSERT INTO nodes(node_id) VALUES ('TXN:RING1_2');
INSERT INTO nodes(node_id) VALUES ('TXN:RING1_3');
INSERT INTO nodes(node_id) VALUES ('TXN:RING1_4');
INSERT INTO nodes(node_id) VALUES ('TXN:RING1_5');
INSERT INTO nodes(node_id) VALUES ('TXN:RING2_1');
INSERT INTO nodes(node_id) VALUES ('TXN:RING2_2');
INSERT INTO nodes(node_id) VALUES ('TXN:RING2_3');
INSERT INTO nodes(node_id) VALUES ('TXN:RING2_4');
INSERT INTO nodes(node_id) VALUES ('TXN:RING2_5');
INSERT INTO nodes(node_id) VALUES ('TXN:RING3_1');
INSERT INTO nodes(node_id) VALUES ('TXN:RING3_2');
INSERT INTO nodes(node_id) VALUES ('TXN:RING3_3');
INSERT INTO nodes(node_id) VALUES ('TXN:RING3_4');
INSERT INTO nodes(node_id) VALUES ('TXN:RING3_5');
INSERT INTO nodes(node_id) VALUES ('TXN:MULE1_IN1');
INSERT INTO nodes(node_id) VALUES ('TXN:MULE1_OUT1');
INSERT INTO nodes(node_id) VALUES ('TXN:MULE1_IN2');
INSERT INTO nodes(node_id) VALUES ('TXN:MULE1_OUT2');
INSERT INTO nodes(node_id) VALUES ('TXN:MULE1_IN3');
INSERT INTO nodes(node_id) VALUES ('TXN:MULE1_OUT3');
INSERT INTO nodes(node_id) VALUES ('TXN:MULE1_IN4');
INSERT INTO nodes(node_id) VALUES ('TXN:MULE1_OUT4');
INSERT INTO nodes(node_id) VALUES ('TXN:MULE1_IN5');
INSERT INTO nodes(node_id) VALUES ('TXN:MULE1_OUT5');
INSERT INTO nodes(node_id) VALUES ('TXN:MULE2_IN1');
INSERT INTO nodes(node_id) VALUES ('TXN:MULE2_IN2');
INSERT INTO nodes(node_id) VALUES ('TXN:MULE2_IN3');
INSERT INTO nodes(node_id) VALUES ('TXN:MULE2_OUT1');
INSERT INTO nodes(node_id) VALUES ('TXN:MULE2_OUT2');
INSERT INTO nodes(node_id) VALUES ('ALERT:AL001');
INSERT INTO nodes(node_id) VALUES ('ALERT:AL002');
INSERT INTO nodes(node_id) VALUES ('ALERT:AL003');
INSERT INTO nodes(node_id) VALUES ('ALERT:AL004');
INSERT INTO nodes(node_id) VALUES ('ALERT:AL005');
INSERT INTO nodes(node_id) VALUES ('ALERT:AL006');
INSERT INTO nodes(node_id) VALUES ('ALERT:AL007');
INSERT INTO nodes(node_id) VALUES ('ALERT:AL008');
INSERT INTO nodes(node_id) VALUES ('ALERT:AL009');
INSERT INTO nodes(node_id) VALUES ('ALERT:AL010');
INSERT INTO nodes(node_id) VALUES ('ALERT:AL011');
INSERT INTO nodes(node_id) VALUES ('ALERT:AL012');
INSERT INTO nodes(node_id) VALUES ('ALERT:AL013');
INSERT INTO nodes(node_id) VALUES ('ALERT:AL014');
INSERT INTO nodes(node_id) VALUES ('ALERT:AL015');
INSERT INTO nodes(node_id) VALUES ('ALERT:AL016');
INSERT INTO nodes(node_id) VALUES ('ALERT:AL017');
INSERT INTO nodes(node_id) VALUES ('ALERT:AL018');
INSERT INTO nodes(node_id) VALUES ('ALERT:AL019');
INSERT INTO nodes(node_id) VALUES ('ALERT:AL020');
INSERT INTO nodes(node_id) VALUES ('ALERT:AL021');
INSERT INTO nodes(node_id) VALUES ('ALERT:AL022');
INSERT INTO nodes(node_id) VALUES ('ALERT:AL023');
INSERT INTO nodes(node_id) VALUES ('ALERT:AL024');
INSERT INTO nodes(node_id) VALUES ('ALERT:AL025');

-- ============================================================================
-- ACCOUNTS (75 total) - Labels
-- ============================================================================

INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A001', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A002', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A003', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A004', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A005', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A006', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A007', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A008', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A009', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A010', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A011', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A012', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A013', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A014', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A015', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A016', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A017', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A018', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A019', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A020', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A021', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A022', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A023', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A024', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A025', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A026', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A027', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A028', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A029', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A030', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A031', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A032', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A033', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A034', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A035', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A036', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A037', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A038', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A039', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A040', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A041', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A042', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A043', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A044', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A045', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A046', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A047', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A048', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A049', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:A050', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:RING1_A', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:RING1_B', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:RING1_C', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:RING1_D', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:RING1_E', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:RING2_A', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:RING2_B', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:RING2_C', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:RING2_D', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:RING2_E', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:RING3_A', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:RING3_B', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:RING3_C', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:RING3_D', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:RING3_E', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:MULE1', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:MULE2', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:VELOCITY1', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:VELOCITY2', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:VELOCITY3', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:VELOCITY4', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:VELOCITY5', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:VELOCITY6', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:VELOCITY7', 'Account');
INSERT INTO rdf_labels(s, label) VALUES ('ACCOUNT:VELOCITY8', 'Account');

-- Account properties (sample)
INSERT INTO rdf_props(s, key, val) VALUES ('ACCOUNT:A001', 'account_type', 'checking');
INSERT INTO rdf_props(s, key, val) VALUES ('ACCOUNT:A001', 'status', 'active');
INSERT INTO rdf_props(s, key, val) VALUES ('ACCOUNT:A001', 'risk_score', '0.1');
INSERT INTO rdf_props(s, key, val) VALUES ('ACCOUNT:A002', 'account_type', 'savings');
INSERT INTO rdf_props(s, key, val) VALUES ('ACCOUNT:A002', 'status', 'active');
INSERT INTO rdf_props(s, key, val) VALUES ('ACCOUNT:A002', 'risk_score', '0.05');
INSERT INTO rdf_props(s, key, val) VALUES ('ACCOUNT:A003', 'account_type', 'checking');
INSERT INTO rdf_props(s, key, val) VALUES ('ACCOUNT:A003', 'status', 'active');
INSERT INTO rdf_props(s, key, val) VALUES ('ACCOUNT:A003', 'risk_score', '0.15');
INSERT INTO rdf_props(s, key, val) VALUES ('ACCOUNT:A010', 'account_type', 'credit');
INSERT INTO rdf_props(s, key, val) VALUES ('ACCOUNT:A010', 'status', 'active');
INSERT INTO rdf_props(s, key, val) VALUES ('ACCOUNT:A010', 'risk_score', '0.2');
INSERT INTO rdf_props(s, key, val) VALUES ('ACCOUNT:A020', 'account_type', 'checking');
INSERT INTO rdf_props(s, key, val) VALUES ('ACCOUNT:A020', 'status', 'active');
INSERT INTO rdf_props(s, key, val) VALUES ('ACCOUNT:A020', 'risk_score', '0.1');
INSERT INTO rdf_props(s, key, val) VALUES ('ACCOUNT:RING1_A', 'account_type', 'checking');
INSERT INTO rdf_props(s, key, val) VALUES ('ACCOUNT:RING1_A', 'status', 'active');
INSERT INTO rdf_props(s, key, val) VALUES ('ACCOUNT:RING1_A', 'risk_score', '0.85');
INSERT INTO rdf_props(s, key, val) VALUES ('ACCOUNT:MULE1', 'account_type', 'checking');
INSERT INTO rdf_props(s, key, val) VALUES ('ACCOUNT:MULE1', 'status', 'active');
INSERT INTO rdf_props(s, key, val) VALUES ('ACCOUNT:MULE1', 'risk_score', '0.92');
INSERT INTO rdf_props(s, key, val) VALUES ('ACCOUNT:MULE2', 'account_type', 'checking');
INSERT INTO rdf_props(s, key, val) VALUES ('ACCOUNT:MULE2', 'status', 'suspended');
INSERT INTO rdf_props(s, key, val) VALUES ('ACCOUNT:MULE2', 'risk_score', '0.95');
INSERT INTO rdf_props(s, key, val) VALUES ('ACCOUNT:VELOCITY1', 'account_type', 'checking');
INSERT INTO rdf_props(s, key, val) VALUES ('ACCOUNT:VELOCITY1', 'status', 'active');
INSERT INTO rdf_props(s, key, val) VALUES ('ACCOUNT:VELOCITY1', 'risk_score', '0.7');

-- ============================================================================
-- TRANSACTIONS - Labels
-- ============================================================================

INSERT INTO rdf_labels(s, label) VALUES ('TXN:T001', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:T002', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:T003', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:T004', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:T005', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:T006', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:T007', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:T008', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:T009', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:T010', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:T011', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:T012', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:T013', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:T014', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:T015', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:T016', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:T017', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:T018', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:T019', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:T020', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:RING1_1', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:RING1_2', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:RING1_3', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:RING1_4', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:RING1_5', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:RING2_1', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:RING2_2', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:RING2_3', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:RING2_4', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:RING2_5', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:RING3_1', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:RING3_2', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:RING3_3', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:RING3_4', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:RING3_5', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:MULE1_IN1', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:MULE1_OUT1', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:MULE1_IN2', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:MULE1_OUT2', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:MULE1_IN3', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:MULE1_OUT3', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:MULE1_IN4', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:MULE1_OUT4', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:MULE1_IN5', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:MULE1_OUT5', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:MULE2_IN1', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:MULE2_IN2', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:MULE2_IN3', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:MULE2_OUT1', 'Transaction');
INSERT INTO rdf_labels(s, label) VALUES ('TXN:MULE2_OUT2', 'Transaction');

-- Transaction properties (sample)
INSERT INTO rdf_props(s, key, val) VALUES ('TXN:T001', 'amount', '150.00');
INSERT INTO rdf_props(s, key, val) VALUES ('TXN:T001', 'currency', 'USD');
INSERT INTO rdf_props(s, key, val) VALUES ('TXN:T001', 'status', 'completed');
INSERT INTO rdf_props(s, key, val) VALUES ('TXN:T002', 'amount', '500.00');
INSERT INTO rdf_props(s, key, val) VALUES ('TXN:T002', 'currency', 'USD');
INSERT INTO rdf_props(s, key, val) VALUES ('TXN:T002', 'status', 'completed');
INSERT INTO rdf_props(s, key, val) VALUES ('TXN:T003', 'amount', '75.50');
INSERT INTO rdf_props(s, key, val) VALUES ('TXN:T003', 'currency', 'USD');
INSERT INTO rdf_props(s, key, val) VALUES ('TXN:T003', 'status', 'completed');
INSERT INTO rdf_props(s, key, val) VALUES ('TXN:RING1_1', 'amount', '9999.00');
INSERT INTO rdf_props(s, key, val) VALUES ('TXN:RING1_1', 'currency', 'USD');
INSERT INTO rdf_props(s, key, val) VALUES ('TXN:RING1_1', 'status', 'completed');
INSERT INTO rdf_props(s, key, val) VALUES ('TXN:MULE1_IN1', 'amount', '5000.00');
INSERT INTO rdf_props(s, key, val) VALUES ('TXN:MULE1_IN1', 'currency', 'USD');
INSERT INTO rdf_props(s, key, val) VALUES ('TXN:MULE1_IN1', 'status', 'completed');

-- Transaction edges (FROM_ACCOUNT, TO_ACCOUNT relationships)
INSERT INTO rdf_edges(s, p, o_id) VALUES ('TXN:T001', 'FROM_ACCOUNT', 'ACCOUNT:A001');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('TXN:T001', 'TO_ACCOUNT', 'ACCOUNT:A002');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('TXN:T002', 'FROM_ACCOUNT', 'ACCOUNT:A002');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('TXN:T002', 'TO_ACCOUNT', 'ACCOUNT:A003');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('TXN:T003', 'FROM_ACCOUNT', 'ACCOUNT:A003');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('TXN:T003', 'TO_ACCOUNT', 'ACCOUNT:A004');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('TXN:RING1_1', 'FROM_ACCOUNT', 'ACCOUNT:RING1_A');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('TXN:RING1_1', 'TO_ACCOUNT', 'ACCOUNT:RING1_B');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('TXN:RING1_2', 'FROM_ACCOUNT', 'ACCOUNT:RING1_B');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('TXN:RING1_2', 'TO_ACCOUNT', 'ACCOUNT:RING1_C');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('TXN:RING1_3', 'FROM_ACCOUNT', 'ACCOUNT:RING1_C');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('TXN:RING1_3', 'TO_ACCOUNT', 'ACCOUNT:RING1_D');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('TXN:RING1_4', 'FROM_ACCOUNT', 'ACCOUNT:RING1_D');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('TXN:RING1_4', 'TO_ACCOUNT', 'ACCOUNT:RING1_E');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('TXN:RING1_5', 'FROM_ACCOUNT', 'ACCOUNT:RING1_E');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('TXN:RING1_5', 'TO_ACCOUNT', 'ACCOUNT:RING1_A');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('TXN:RING2_1', 'FROM_ACCOUNT', 'ACCOUNT:RING2_A');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('TXN:RING2_1', 'TO_ACCOUNT', 'ACCOUNT:RING2_B');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('TXN:RING2_2', 'FROM_ACCOUNT', 'ACCOUNT:RING2_B');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('TXN:RING2_2', 'TO_ACCOUNT', 'ACCOUNT:RING2_C');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('TXN:RING2_3', 'FROM_ACCOUNT', 'ACCOUNT:RING2_C');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('TXN:RING2_3', 'TO_ACCOUNT', 'ACCOUNT:RING2_D');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('TXN:RING2_4', 'FROM_ACCOUNT', 'ACCOUNT:RING2_D');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('TXN:RING2_4', 'TO_ACCOUNT', 'ACCOUNT:RING2_E');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('TXN:RING2_5', 'FROM_ACCOUNT', 'ACCOUNT:RING2_E');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('TXN:RING2_5', 'TO_ACCOUNT', 'ACCOUNT:RING2_A');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('TXN:MULE1_IN1', 'FROM_ACCOUNT', 'ACCOUNT:A010');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('TXN:MULE1_IN1', 'TO_ACCOUNT', 'ACCOUNT:MULE1');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('TXN:MULE1_IN2', 'FROM_ACCOUNT', 'ACCOUNT:A011');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('TXN:MULE1_IN2', 'TO_ACCOUNT', 'ACCOUNT:MULE1');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('TXN:MULE1_IN3', 'FROM_ACCOUNT', 'ACCOUNT:A012');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('TXN:MULE1_IN3', 'TO_ACCOUNT', 'ACCOUNT:MULE1');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('TXN:MULE1_OUT1', 'FROM_ACCOUNT', 'ACCOUNT:MULE1');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('TXN:MULE1_OUT1', 'TO_ACCOUNT', 'ACCOUNT:A020');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('TXN:MULE1_OUT2', 'FROM_ACCOUNT', 'ACCOUNT:MULE1');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('TXN:MULE1_OUT2', 'TO_ACCOUNT', 'ACCOUNT:A021');

-- ============================================================================
-- ALERTS (25 total)
-- ============================================================================

INSERT INTO rdf_labels(s, label) VALUES ('ALERT:AL001', 'Alert');
INSERT INTO rdf_labels(s, label) VALUES ('ALERT:AL002', 'Alert');
INSERT INTO rdf_labels(s, label) VALUES ('ALERT:AL003', 'Alert');
INSERT INTO rdf_labels(s, label) VALUES ('ALERT:AL004', 'Alert');
INSERT INTO rdf_labels(s, label) VALUES ('ALERT:AL005', 'Alert');
INSERT INTO rdf_labels(s, label) VALUES ('ALERT:AL006', 'Alert');
INSERT INTO rdf_labels(s, label) VALUES ('ALERT:AL007', 'Alert');
INSERT INTO rdf_labels(s, label) VALUES ('ALERT:AL008', 'Alert');
INSERT INTO rdf_labels(s, label) VALUES ('ALERT:AL009', 'Alert');
INSERT INTO rdf_labels(s, label) VALUES ('ALERT:AL010', 'Alert');
INSERT INTO rdf_labels(s, label) VALUES ('ALERT:AL011', 'Alert');
INSERT INTO rdf_labels(s, label) VALUES ('ALERT:AL012', 'Alert');
INSERT INTO rdf_labels(s, label) VALUES ('ALERT:AL013', 'Alert');
INSERT INTO rdf_labels(s, label) VALUES ('ALERT:AL014', 'Alert');
INSERT INTO rdf_labels(s, label) VALUES ('ALERT:AL015', 'Alert');
INSERT INTO rdf_labels(s, label) VALUES ('ALERT:AL016', 'Alert');
INSERT INTO rdf_labels(s, label) VALUES ('ALERT:AL017', 'Alert');
INSERT INTO rdf_labels(s, label) VALUES ('ALERT:AL018', 'Alert');
INSERT INTO rdf_labels(s, label) VALUES ('ALERT:AL019', 'Alert');
INSERT INTO rdf_labels(s, label) VALUES ('ALERT:AL020', 'Alert');
INSERT INTO rdf_labels(s, label) VALUES ('ALERT:AL021', 'Alert');
INSERT INTO rdf_labels(s, label) VALUES ('ALERT:AL022', 'Alert');
INSERT INTO rdf_labels(s, label) VALUES ('ALERT:AL023', 'Alert');
INSERT INTO rdf_labels(s, label) VALUES ('ALERT:AL024', 'Alert');
INSERT INTO rdf_labels(s, label) VALUES ('ALERT:AL025', 'Alert');

-- Alert properties
INSERT INTO rdf_props(s, key, val) VALUES ('ALERT:AL001', 'alert_type', 'ring_pattern');
INSERT INTO rdf_props(s, key, val) VALUES ('ALERT:AL001', 'severity', 'critical');
INSERT INTO rdf_props(s, key, val) VALUES ('ALERT:AL001', 'confidence', '0.95');
INSERT INTO rdf_props(s, key, val) VALUES ('ALERT:AL002', 'alert_type', 'ring_pattern');
INSERT INTO rdf_props(s, key, val) VALUES ('ALERT:AL002', 'severity', 'critical');
INSERT INTO rdf_props(s, key, val) VALUES ('ALERT:AL002', 'confidence', '0.92');
INSERT INTO rdf_props(s, key, val) VALUES ('ALERT:AL004', 'alert_type', 'mule_account');
INSERT INTO rdf_props(s, key, val) VALUES ('ALERT:AL004', 'severity', 'high');
INSERT INTO rdf_props(s, key, val) VALUES ('ALERT:AL004', 'confidence', '0.88');
INSERT INTO rdf_props(s, key, val) VALUES ('ALERT:AL005', 'alert_type', 'mule_account');
INSERT INTO rdf_props(s, key, val) VALUES ('ALERT:AL005', 'severity', 'critical');
INSERT INTO rdf_props(s, key, val) VALUES ('ALERT:AL005', 'confidence', '0.94');
INSERT INTO rdf_props(s, key, val) VALUES ('ALERT:AL006', 'alert_type', 'velocity');
INSERT INTO rdf_props(s, key, val) VALUES ('ALERT:AL006', 'severity', 'medium');
INSERT INTO rdf_props(s, key, val) VALUES ('ALERT:AL006', 'confidence', '0.72');

-- Alert edges
INSERT INTO rdf_edges(s, p, o_id) VALUES ('ALERT:AL001', 'INVOLVES', 'ACCOUNT:RING1_A');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('ALERT:AL001', 'RELATED_TO', 'TXN:RING1_1');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('ALERT:AL002', 'INVOLVES', 'ACCOUNT:RING2_A');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('ALERT:AL002', 'RELATED_TO', 'TXN:RING2_1');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('ALERT:AL004', 'INVOLVES', 'ACCOUNT:MULE1');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('ALERT:AL004', 'RELATED_TO', 'TXN:MULE1_IN1');
INSERT INTO rdf_edges(s, p, o_id) VALUES ('ALERT:AL006', 'INVOLVES', 'ACCOUNT:VELOCITY1');

-- ============================================================================
-- VECTOR EMBEDDINGS (768D for anomaly detection)
-- ============================================================================

-- Normal account embedding (baseline pattern)
INSERT INTO kg_NodeEmbeddings(id, emb) VALUES
 ('ACCOUNT:A001', TO_VECTOR('[0.15,0.1,0.07,0.15,0.09,0.13,0.05,0.07,0.15,0.13,0.05,0.1,0.09,0.07,0.06,0.08,0.06,0.07,0.14,0.09,0.08,0.09,0.06,0.14,0.1,0.12,0.15,0.06,0.11,0.1,0.13,0.11,0.09,0.06,0.1,0.13,0.14,0.1,0.1,0.13,0.09,0.09,0.12,0.14,0.13,0.14,0.07,0.1,0.06,0.14,0.07,0.1,0.12,0.1,0.1,0.1,0.11,0.11,0.12,0.1,0.09,0.07,0.05,0.08,0.15,0.1,0.07,0.1,0.12,0.13,0.09,0.11,0.06,0.13,0.09,0.06,0.07,0.13,0.14,0.07,0.15,0.07,0.06,0.05,0.07,0.12,0.11,0.14,0.07,0.07,0.14,0.1,0.1,0.13,0.05,0.06,0.06,0.09,0.06,0.1,0.09,0.09,0.08,0.09,0.13,0.15,0.15,0.05,0.11,0.12,0.14,0.1,0.07,0.09,0.14,0.06,0.1,0.09,0.08,0.06,0.1,0.11,0.13,0.15,0.08,0.08,0.06,0.1,0.13,0.12,0.09,0.15,0.15,0.14,0.08,0.12,0.05,0.11,0.08,0.1,0.15,0.08,0.08,0.12,0.06,0.08,0.13,0.06,0.13,0.09,0.09,0.14,0.09,0.06,0.11,0.09,0.13,0.1,0.13,0.12,0.12,0.13,0.12,0.06,0.07,0.06,0.1,0.14,0.07,0.06,0.07,0.11,0.14,0.14,0.06,0.07,0.1,0.09,0.14,0.09,0.13,0.14,0.14,0.05,0.12,0.09,0.14,0.14,0.06,0.06,0.1,0.1,0.06,0.05,0.06,0.08,0.06,0.09,0.09,0.09,0.11,0.09,0.06,0.1,0.13,0.13,0.11,0.13,0.15,0.07,0.11,0.1,0.12,0.15,0.06,0.15,0.09,0.05,0.09,0.11,0.12,0.15,0.11,0.13,0.07,0.06,0.09,0.08,0.11,0.11,0.09,0.12,0.13,0.11,0.07,0.08,0.1,0.12,0.08,0.11,0.06,0.12,0.13,0.1,0.06,0.08,0.15,0.11,0.11,0.14,0.11,0.12,0.14,0.11,0.12,0.06,0.14,0.15,0.06,0.05,0.11,0.08,0.1,0.1,0.05,0.08,0.14,0.1,0.1,0.14,0.07,0.09,0.09,0.13,0.1,0.14,0.13,0.14,0.08,0.1,0.05,0.11,0.06,0.09,0.11,0.14,0.07,0.06,0.11,0.05,0.06,0.05,0.14,0.11,0.1,0.08,0.08,0.11,0.11,0.09,0.07,0.09,0.09,0.1,0.09,0.07,0.12,0.1,0.06,0.1,0.08,0.13,0.07,0.14,0.14,0.07,0.14,0.1,0.15,0.06,0.06,0.06,0.12,0.12,0.08,0.06,0.14,0.1,0.07,0.09,0.14,0.1,0.06,0.11,0.12,0.08,0.12,0.05,0.13,0.12,0.11,0.09,0.1,0.08,0.07,0.09,0.13,0.08,0.06,0.05,0.11,0.09,0.06,0.09,0.09,0.11,0.14,0.07,0.12,0.1,0.11,0.08,0.14,0.14,0.14,0.09,0.09,0.13,0.08,0.12,0.13,0.1,0.09,0.07,0.13,0.13,0.1,0.09,0.11,0.06,0.07,0.07,0.09,0.13,0.06,0.11,0.12,0.15,0.06,0.13,0.08,0.06,0.06,0.11,0.14,0.08,0.08,0.14,0.08,0.09,0.06,0.13,0.11,0.1,0.11,0.1,0.1,0.1,0.08,0.09,0.05,0.14,0.09,0.06,0.06,0.07,0.05,0.11,0.14,0.05,0.06,0.1,0.14,0.12,0.12,0.14,0.11,0.11,0.13,0.05,0.1,0.08,0.08,0.14,0.06,0.08,0.11,0.06,0.08,0.05,0.07,0.12,0.14,0.15,0.07,0.09,0.07,0.05,0.07,0.07,0.08,0.08,0.11,0.11,0.15,0.11,0.08,0.1,0.12,0.13,0.05,0.13,0.06,0.13,0.08,0.05,0.13,0.1,0.11,0.12,0.14,0.08,0.14,0.14,0.08,0.09,0.12,0.08,0.14,0.09,0.13,0.12,0.09,0.1,0.13,0.08,0.11,0.09,0.09,0.12,0.06,0.14,0.07,0.06,0.13,0.09,0.1,0.07,0.07,0.12,0.1,0.08,0.06,0.09,0.1,0.11,0.13,0.11,0.15,0.11,0.11,0.08,0.13,0.12,0.12,0.07,0.12,0.08,0.06,0.1,0.07,0.06,0.13,0.11,0.09,0.06,0.11,0.08,0.09,0.14,0.13,0.09,0.1,0.1,0.06,0.1,0.15,0.08,0.07,0.1,0.1,0.06,0.11,0.13,0.11,0.12,0.1,0.08,0.06,0.1,0.1,0.06,0.13,0.12,0.07,0.08,0.06,0.06,0.08,0.06,0.08,0.08,0.06,0.13,0.1,0.07,0.09,0.1,0.13,0.07,0.08,0.11,0.07,0.08,0.06,0.05,0.1,0.1,0.14,0.08,0.06,0.06,0.1,0.07,0.07,0.11,0.08,0.09,0.05,0.15,0.12,0.08,0.1,0.06,0.05,0.11,0.12,0.08,0.08,0.13,0.14,0.09,0.06,0.14,0.14,0.11,0.15,0.09,0.13,0.14,0.13,0.07,0.1,0.05,0.08,0.1,0.08,0.14,0.15,0.08,0.06,0.06,0.13,0.13,0.11,0.13,0.06,0.12,0.1,0.11,0.12,0.06,0.1,0.05,0.09,0.13,0.08,0.15,0.06,0.09,0.11,0.11,0.09,0.06,0.11,0.06,0.13,0.1,0.07,0.06,0.14,0.05,0.09,0.05,0.06,0.15,0.12,0.1,0.09,0.06,0.05,0.09,0.06,0.07,0.05,0.08,0.09,0.11,0.1,0.14,0.11,0.08,0.08,0.15,0.09,0.12,0.11,0.1,0.07,0.07,0.13,0.14,0.12,0.1,0.08,0.08,0.13,0.06,0.14,0.06,0.06,0.13,0.15,0.13,0.12,0.09,0.13,0.08,0.12,0.08,0.11,0.12,0.13,0.14,0.09,0.15,0.08,0.05,0.12,0.12,0.09,0.14,0.08,0.09,0.12,0.12,0.06,0.13,0.14,0.06,0.07,0.07,0.13,0.1,0.14,0.13,0.12,0.14,0.08,0.15,0.12,0.13,0.07,0.14,0.13,0.14,0.14,0.09,0.11,0.14,0.13,0.07,0.06,0.08,0.11,0.06,0.09,0.13,0.14,0.13,0.12,0.11,0.05,0.14,0.1,0.1,0.09,0.05,0.12,0.06,0.09,0.09,0.1,0.06,0.06,0.06,0.12,0.13]'));

-- Suspicious account embedding (anomalous pattern)
INSERT INTO kg_NodeEmbeddings(id, emb) VALUES
 ('ACCOUNT:MULE1', TO_VECTOR('[0.87,-0.83,0.84,-0.81,0.82,-0.79,0.78,-0.72,0.6,-0.69,0.65,-0.75,0.85,-0.71,0.83,-0.81,0.75,-0.84,0.75,-0.86,0.88,-0.82,0.78,-0.74,0.73,-0.63,0.79,-0.67,0.65,-0.85,0.62,-0.66,0.78,-0.87,0.74,-0.74,0.74,-0.71,0.89,-0.79,0.79,-0.67,0.85,-0.77,0.76,-0.68,0.8,-0.67,0.77,-0.83,0.78,-0.75,0.81,-0.76,0.77,-0.68,0.69,-0.79,0.63,-0.77,0.66,-0.72,0.65,-0.8,0.73,-0.62,0.69,-0.75,0.78,-0.69,0.88,-0.72,0.61,-0.83,0.65,-0.68,0.89,-0.76,0.77,-0.82,0.81,-0.82,0.8,-0.8,0.78,-0.63,0.88,-0.68,0.66,-0.73,0.74,-0.74,0.84,-0.6,0.67,-0.74,0.89,-0.76,0.74,-0.85,0.62,-0.72,0.67,-0.67,0.84,-0.76,0.65,-0.63,0.75,-0.74,0.71,-0.77,0.76,-0.67,0.72,-0.73,0.87,-0.89,0.64,-0.61,0.7,-0.84,0.88,-0.62,0.62,-0.64,0.73,-0.84,0.78,-0.67,0.76,-0.76,0.61,-0.76,0.63,-0.81,0.88,-0.6,0.9,-0.78,0.74,-0.64,0.72,-0.84,0.75,-0.76,0.88,-0.78,0.62,-0.8,0.83,-0.67,0.75,-0.83,0.79,-0.86,0.61,-0.88,0.69,-0.65,0.85,-0.87,0.86,-0.73,0.73,-0.7,0.66,-0.85,0.8,-0.61,0.67,-0.73,0.74,-0.89,0.89,-0.61,0.84,-0.85,0.82,-0.83,0.67,-0.68,0.66,-0.84,0.7,-0.62,0.76,-0.75,0.84,-0.69,0.77,-0.71,0.75,-0.69,0.7,-0.76,0.6,-0.75,0.81,-0.9,0.62,-0.63,0.77,-0.87,0.83,-0.85,0.84,-0.8,0.71,-0.7,0.7,-0.65,0.8,-0.64,0.66,-0.89,0.84,-0.89,0.85,-0.8,0.72,-0.87,0.67,-0.61,0.83,-0.67,0.68,-0.84,0.69,-0.75,0.85,-0.77,0.79,-0.76,0.82,-0.83,0.86,-0.89,0.84,-0.61,0.87,-0.61,0.6,-0.86,0.63,-0.67,0.84,-0.61,0.63,-0.76,0.77,-0.69,0.63,-0.89,0.84,-0.79,0.63,-0.66,0.79,-0.77,0.8,-0.79,0.82,-0.68,0.78,-0.81,0.65,-0.62,0.62,-0.68,0.63,-0.72,0.81,-0.69,0.83,-0.69,0.63,-0.79,0.83,-0.66,0.63,-0.87,0.88,-0.77,0.67,-0.63,0.64,-0.76,0.69,-0.73,0.88,-0.82,0.87,-0.88,0.84,-0.77,0.62,-0.67,0.73,-0.72,0.86,-0.72,0.86,-0.69,0.76,-0.63,0.77,-0.73,0.89,-0.61,0.66,-0.64,0.86,-0.82,0.74,-0.86,0.84,-0.74,0.83,-0.63,0.88,-0.88,0.62,-0.76,0.79,-0.72,0.74,-0.85,0.83,-0.63,0.7,-0.73,0.76,-0.8,0.77,-0.86,0.74,-0.7,0.72,-0.61,0.89,-0.64,0.85,-0.7,0.64,-0.85,0.67,-0.63,0.7,-0.74,0.84,-0.81,0.84,-0.89,0.84,-0.61,0.68,-0.89,0.64,-0.64,0.79,-0.9,0.73,-0.63,0.63,-0.83,0.79,-0.85,0.65,-0.65,0.64,-0.69,0.81,-0.63,0.85,-0.73,0.87,-0.81,0.86,-0.7,0.72,-0.83,0.6,-0.65,0.89,-0.8,0.66,-0.63,0.88,-0.81,0.7,-0.78,0.67,-0.9,0.78,-0.78,0.72,-0.79,0.69,-0.69,0.72,-0.68,0.86,-0.61,0.69,-0.76,0.67,-0.66,0.75,-0.71,0.76,-0.65,0.77,-0.65,0.68,-0.61,0.68,-0.84,0.78,-0.87,0.88,-0.61,0.61,-0.89,0.89,-0.6,0.73,-0.77,0.86,-0.64,0.63,-0.65,0.64,-0.64,0.76,-0.68,0.86,-0.86,0.84,-0.62,0.71,-0.84,0.74,-0.81,0.72,-0.76,0.76,-0.71,0.71,-0.89,0.84,-0.71,0.65,-0.66,0.7,-0.67,0.72,-0.66,0.73,-0.65,0.66,-0.74,0.66,-0.83,0.83,-0.69,0.63,-0.66,0.79,-0.87,0.79,-0.65,0.68,-0.78,0.82,-0.88,0.71,-0.87,0.86,-0.75,0.78,-0.65,0.81,-0.7,0.7,-0.73,0.78,-0.7,0.8,-0.86,0.72,-0.7,0.86,-0.84,0.7,-0.86,0.6,-0.84,0.86,-0.88,0.78,-0.71,0.66,-0.84,0.68,-0.78,0.74,-0.68,0.75,-0.84,0.66,-0.83,0.9,-0.79,0.65,-0.83,0.7,-0.67,0.86,-0.79,0.64,-0.73,0.78,-0.74,0.73,-0.84,0.71,-0.63,0.65,-0.72,0.82,-0.68,0.69,-0.78,0.67,-0.61,0.87,-0.69,0.83,-0.67,0.79,-0.61,0.75,-0.75,0.67,-0.79,0.66,-0.68,0.66,-0.66,0.77,-0.81,0.61,-0.64,0.66,-0.85,0.6,-0.82,0.72,-0.6,0.83,-0.61,0.66,-0.67,0.72,-0.87,0.62,-0.69,0.89,-0.8,0.9,-0.65,0.9,-0.67,0.63,-0.75,0.67,-0.73,0.89,-0.88,0.86,-0.63,0.75,-0.71,0.63,-0.66,0.64,-0.65,0.73,-0.75,0.62,-0.9,0.61,-0.88,0.6,-0.82,0.69,-0.78,0.63,-0.64,0.83,-0.63,0.67,-0.63,0.79,-0.87,0.65,-0.83,0.65,-0.78,0.69,-0.62,0.68,-0.83,0.73,-0.76,0.7,-0.8,0.81,-0.75,0.89,-0.66,0.68,-0.74,0.87,-0.75,0.72,-0.76,0.81,-0.75,0.87,-0.68,0.66,-0.72,0.63,-0.84,0.81,-0.73,0.79,-0.89,0.66,-0.67,0.84,-0.61,0.67,-0.61,0.63,-0.64,0.73,-0.65,0.75,-0.73,0.6,-0.87,0.83,-0.66,0.69,-0.87,0.75,-0.75,0.74,-0.71,0.83,-0.88,0.84,-0.87,0.81,-0.83,0.71,-0.62,0.84,-0.64,0.69,-0.86,0.69,-0.81,0.7,-0.63,0.85,-0.66,0.89,-0.63,0.71,-0.9,0.62,-0.88,0.87,-0.79,0.79,-0.88,0.74,-0.82,0.64,-0.76,0.71,-0.64,0.82,-0.72,0.8,-0.66,0.87,-0.77,0.9,-0.86,0.84,-0.86,0.82,-0.6,0.83,-0.76,0.75,-0.82,0.85,-0.7,0.66,-0.71,0.6,-0.7,0.84,-0.73,0.69,-0.77,0.61,-0.72,0.6,-0.63,0.74,-0.71,0.69,-0.64,0.75,-0.75,0.63,-0.84,0.7,-0.83,0.7,-0.77,0.85,-0.86,0.74,-0.68,0.61,-0.88,0.85,-0.86,0.82,-0.88,0.71,-0.88,0.71,-0.69,0.68,-0.62,0.72,-0.81,0.6,-0.76,0.62,-0.61,0.73,-0.88,0.64,-0.85,0.69,-0.67,0.76,-0.62]'));
