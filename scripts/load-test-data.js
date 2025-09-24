#!/usr/bin/env node
/**
 * Test Data Loader for Graph AI
 * Loads sample data into the test database for consistent testing
 */

import { config } from 'dotenv';
import odbc from 'odbc';
import fs from 'fs';
import path from 'path';

// Load test environment
config({ path: '.env.test' });

// Static embeddings from test fixtures
const staticEmbeddings = {
  tp53: JSON.parse(fs.readFileSync('tests/fixtures/static-embeddings.ts', 'utf8')
    .match(/tp53:\s*(\[[\d\s,.,-]+\])/)[1]),
  cancer: JSON.parse(fs.readFileSync('tests/fixtures/static-embeddings.ts', 'utf8')
    .match(/cancer:\s*(\[[\d\s,.,-]+\])/)[1]),
  drug: JSON.parse(fs.readFileSync('tests/fixtures/static-embeddings.ts', 'utf8')
    .match(/drug:\s*(\[[\d\s,.,-]+\])/)[1])
};

// Test documents
const testDocuments = [
  {
    id: 'tp53_gene',
    text: 'TP53 tumor suppressor gene mutations cancer apoptosis DNA damage response cell cycle checkpoint',
    vector: JSON.stringify(staticEmbeddings.tp53),
    label: 'gene'
  },
  {
    id: 'cancer_review',
    text: 'comprehensive review of cancer mechanisms therapeutic targets oncology treatment drug resistance',
    vector: JSON.stringify(staticEmbeddings.cancer),
    label: 'document'
  },
  {
    id: 'drug_cisplatin',
    text: 'cisplatin chemotherapy DNA damage platinum-based anticancer drug cytotoxic treatment',
    vector: JSON.stringify(staticEmbeddings.drug),
    label: 'drug'
  },
  {
    id: 'brca1_gene',
    text: 'BRCA1 breast cancer gene DNA repair hereditary tumor suppressor mutation testing',
    vector: JSON.stringify(staticEmbeddings.tp53.map(v => v * 0.8 + Math.random() * 0.1)),
    label: 'gene'
  },
  {
    id: 'pathway_apoptosis',
    text: 'apoptosis programmed cell death pathway cancer biology molecular mechanisms caspase activation',
    vector: JSON.stringify(staticEmbeddings.cancer.map(v => v * 0.9 + Math.random() * 0.1)),
    label: 'pathway'
  }
];

// Test triples for graph data
const testTriples = [
  ['gene:TP53', 'associated_with', 'disease:cancer'],
  ['gene:TP53', 'regulates', 'pathway:apoptosis'],
  ['drug:cisplatin', 'targets', 'gene:TP53'],
  ['gene:BRCA1', 'associated_with', 'disease:breast_cancer'],
  ['pathway:apoptosis', 'involves', 'protein:p53'],
  ['drug:cisplatin', 'induces', 'pathway:apoptosis'],
  ['gene:TP53', 'function', 'tumor_suppressor'],
  ['gene:BRCA1', 'function', 'DNA_repair'],
  ['disease:cancer', 'characterized_by', 'mutation:TP53'],
  ['treatment:chemotherapy', 'includes', 'drug:cisplatin']
];

async function connectToDatabase() {
  const dsn = process.env.IRIS_DSN;
  const user = process.env.IRIS_USER;
  const pass = process.env.IRIS_PASS;

  if (!dsn || !user || !pass) {
    throw new Error('Missing database configuration in .env.test');
  }

  try {
    const connection = await odbc.connect(`DSN=${dsn};UID=${user};PWD=${pass}`);
    console.log('✅ Connected to test database');
    return connection;
  } catch (error) {
    console.error('❌ Database connection failed:', error);
    throw error;
  }
}

async function clearTestData(conn) {
  console.log('🧹 Clearing existing test data...');

  try {
    await conn.query('DELETE FROM docs WHERE id LIKE \'%test%\' OR id IN (\'tp53_gene\', \'cancer_review\', \'drug_cisplatin\', \'brca1_gene\', \'pathway_apoptosis\')');
    await conn.query('DELETE FROM triples WHERE s LIKE \'gene:%\' OR s LIKE \'drug:%\' OR s LIKE \'pathway:%\' OR s LIKE \'disease:%\' OR s LIKE \'treatment:%\'');
    console.log('✅ Test data cleared');
  } catch (error) {
    console.warn('⚠️ Warning: Could not clear existing data:', error.message);
  }
}

async function loadDocuments(conn) {
  console.log('📄 Loading test documents...');

  for (const doc of testDocuments) {
    try {
      await conn.query(`
        INSERT INTO docs (id, text, vector, label)
        VALUES ('${doc.id}', '${doc.text}', '${doc.vector}', '${doc.label}')
      `);
      console.log(`  ✅ Loaded document: ${doc.id}`);
    } catch (error) {
      console.error(`  ❌ Failed to load document ${doc.id}:`, error.message);
    }
  }
}

async function loadTriples(conn) {
  console.log('🔗 Loading test triples...');

  for (const [s, p, o] of testTriples) {
    try {
      await conn.query(`
        INSERT INTO triples (s, p, o)
        VALUES ('${s}', '${p}', '${o}')
      `);
      console.log(`  ✅ Loaded triple: ${s} --${p}--> ${o}`);
    } catch (error) {
      console.error(`  ❌ Failed to load triple ${s} --${p}--> ${o}:`, error.message);
    }
  }
}

async function buildGlobals(conn) {
  console.log('🌐 Building globals for graph traversal...');

  try {
    const stmt = await conn.createStatement();
    await stmt.prepare('CALL kg_BUILD_GLOBALS()');
    await stmt.call([]);
    await stmt.close();
    console.log('✅ Globals built successfully');
  } catch (error) {
    console.warn('⚠️ Warning: Could not build globals:', error.message);
    console.warn('   This is expected if the procedure is not yet loaded');
  }
}

async function verifyData(conn) {
  console.log('🔍 Verifying loaded data...');

  try {
    // Check document count
    const docCount = await conn.query('SELECT COUNT(*) as count FROM docs');
    console.log(`  📄 Documents: ${docCount[0].count || docCount[0].COUNT}`);

    // Check triples count
    const tripleCount = await conn.query('SELECT COUNT(*) as count FROM triples');
    console.log(`  🔗 Triples: ${tripleCount[0].count || tripleCount[0].COUNT}`);

    // Test vector search
    const vectorTest = await conn.query(`
      SELECT id, VECTOR_COSINE_SIMILARITY(vector, '${JSON.stringify(staticEmbeddings.tp53)}') as similarity
      FROM docs
      WHERE id = 'tp53_gene'
    `);

    if (vectorTest.length > 0) {
      const similarity = vectorTest[0].similarity || vectorTest[0].SIMILARITY;
      console.log(`  🧮 Vector similarity test: ${similarity} (should be ~1.0)`);
    }

    console.log('✅ Data verification completed');
  } catch (error) {
    console.error('❌ Data verification failed:', error.message);
  }
}

async function main() {
  console.log('🚀 Loading test data for Graph AI...');

  let conn;
  try {
    conn = await connectToDatabase();

    await clearTestData(conn);
    await loadDocuments(conn);
    await loadTriples(conn);
    await buildGlobals(conn);
    await verifyData(conn);

    console.log('🎉 Test data loading completed successfully!');
  } catch (error) {
    console.error('💥 Test data loading failed:', error);
    process.exit(1);
  } finally {
    if (conn) {
      await conn.close();
      console.log('👋 Database connection closed');
    }
  }
}

// Run if called directly
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch(console.error);
}

export { main as loadTestData };