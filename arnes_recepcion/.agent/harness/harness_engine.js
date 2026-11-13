/**
 * Harness² Engine — Runtime Loader & CLI
 * Sistema de Agentes IA - Hostal Óvalo Mariátegui
 *
 * Zero external npm dependencies. Native Node.js v24+.
 */

const fs = require('node:fs');
const path = require('node:path');
const { execFile } = require('node:child_process');

// Path resolution
const HARNESS_DIR = __dirname;
const ROOT_DIR = path.resolve(HARNESS_DIR, '../../');
const MANIFEST_PATH = path.join(HARNESS_DIR, 'manifest.json');
const RUNS_PATH = path.join(HARNESS_DIR, 'harness_runs.json');
const EXPERIENCE_PATH = path.join(HARNESS_DIR, 'EXPERIENCE.md');

/**
 * Load and parse manifest.json
 * @param {string} [customPath]
 * @returns {object}
 */
function loadManifest(customPath) {
  const targetPath = customPath ? path.resolve(customPath) : MANIFEST_PATH;
  if (!fs.existsSync(targetPath)) {
    throw new Error(`Manifest file not found: ${targetPath}`);
  }
  const raw = fs.readFileSync(targetPath, 'utf8');
  return JSON.parse(raw);
}

/**
 * Resolve path relative to ROOT_DIR or as absolute
 * @param {string} targetPath
 * @returns {string}
 */
function resolvePath(targetPath) {
  if (!targetPath) return '';
  if (path.isAbsolute(targetPath)) {
    return targetPath;
  }
  return path.resolve(ROOT_DIR, targetPath);
}

/**
 * Validate manifest structure, components, and file references
 * @param {string} [customPath]
 * @returns {object}
 */
function validateManifest(customPath) {
  const result = {
    valid: true,
    timestamp: new Date().toISOString(),
    manifest_path: customPath || MANIFEST_PATH,
    component_count: 0,
    expected_components: 8,
    checks: [],
    warnings: [],
    errors: []
  };

  let manifest;
  try {
    manifest = loadManifest(customPath);
    result.checks.push({ check: 'manifest_json_parse', status: 'PASS', message: 'Valid JSON format' });
  } catch (err) {
    result.valid = false;
    result.errors.push(`Manifest parse error: ${err.message}`);
    return result;
  }

  // Guard against null / non-objects / arrays
  if (!manifest || typeof manifest !== 'object' || Array.isArray(manifest)) {
    result.valid = false;
    result.errors.push('Manifest root must be an object with $schema and version');
    return result;
  }

  // Schema & Version check
  if (!manifest.$schema || !manifest.version) {
    result.warnings.push('Manifest missing $schema or version header');
  } else {
    result.checks.push({ check: 'manifest_headers', status: 'PASS', version: manifest.version });
  }

  // 8 Required components check
  const requiredComponents = [
    'system_prompts',
    'guardrails',
    'memory',
    'skills',
    'tool_guidance',
    'probes',
    'subagents',
    'plugins_mcp'
  ];

  const components = manifest.components || {};
  result.component_count = Object.keys(components).length;

  for (const compId of requiredComponents) {
    if (!components[compId]) {
      result.valid = false;
      result.errors.push(`Missing required component: ${compId}`);
    } else {
      const comp = components[compId];
      result.checks.push({
        check: `component_${compId}`,
        status: 'PASS',
        name: comp.name,
        category: comp.category,
        component_status: comp.status
      });

      // Verify component directory if specified
      if (comp.directory) {
        const fullDir = resolvePath(comp.directory);
        if (!fs.existsSync(fullDir)) {
          result.warnings.push(`Component directory does not exist: ${comp.directory}`);
        }
      }

      // Verify files in component
      if (Array.isArray(comp.files)) {
        for (const f of comp.files) {
          const filePath = resolvePath(f.path);
          if (fs.existsSync(filePath)) {
            result.checks.push({ check: `file_${f.id || path.basename(f.path)}`, status: 'PASS', path: f.path });
          } else {
            result.warnings.push(`Component file missing: ${f.path}`);
          }
        }
      }

      // Verify rules_file or policy_file
      if (comp.rules_file) {
        const p = resolvePath(comp.rules_file);
        if (fs.existsSync(p)) {
          result.checks.push({ check: `rules_file_${compId}`, status: 'PASS', path: comp.rules_file });
        } else {
          result.warnings.push(`Rules file missing: ${comp.rules_file}`);
        }
      }

      // Verify index_file / catalog_file
      if (comp.index_file || comp.catalog_file) {
        const p = resolvePath(comp.index_file || comp.catalog_file);
        if (fs.existsSync(p)) {
          result.checks.push({ check: `index_file_${compId}`, status: 'PASS', path: comp.index_file || comp.catalog_file });
        } else {
          result.warnings.push(`Index file missing: ${comp.index_file || comp.catalog_file}`);
        }
      }

      // Verify guidelines
      if (Array.isArray(comp.guidelines)) {
        for (const g of comp.guidelines) {
          const p = resolvePath(g.path);
          if (fs.existsSync(p)) {
            result.checks.push({ check: `guideline_${g.tool}`, status: 'PASS', path: g.path });
          } else {
            result.warnings.push(`Guideline file missing: ${g.path}`);
          }
        }
      }

      // Verify probes runners
      if (Array.isArray(comp.runners)) {
        for (const r of comp.runners) {
          const p = resolvePath(r.file);
          const exists = fs.existsSync(p);
          result.checks.push({
            check: `probe_${r.id}`,
            status: exists ? 'PASS' : 'STANDBY',
            path: r.file,
            script_present: exists
          });
        }
      }
    }
  }

  if (result.errors.length > 0) {
    result.valid = false;
  }

  return result;
}

/**
 * Returns structured summary of all 8 components
 * @returns {object}
 */
function getComponentSummary() {
  const manifest = loadManifest();
  const summary = {
    name: manifest.name,
    version: manifest.version,
    updated_at: manifest.updated_at,
    runtime: manifest.runtime,
    total_components: Object.keys(manifest.components || {}).length,
    active_components: 0,
    components: {}
  };

  for (const [key, comp] of Object.entries(manifest.components || {})) {
    let itemCount = 0;
    if (comp.files) itemCount = comp.files.length;
    else if (comp.rules) itemCount = comp.rules.length;
    else if (comp.stores) itemCount = comp.stores.length;
    else if (comp.items) itemCount = comp.items.length;
    else if (comp.guidelines) itemCount = comp.guidelines.length;
    else if (comp.runners) itemCount = comp.runners.length;
    else if (comp.archetypes) itemCount = comp.archetypes.length;
    else if (comp.servers) itemCount = comp.servers.length;

    if (comp.status === 'ACTIVE' || comp.status === 'ENFORCED' || comp.status === 'CONNECTED' || comp.status === 'CONFIGURED') {
      summary.active_components++;
    }

    summary.components[key] = {
      id: comp.id,
      name: comp.name,
      category: comp.category,
      status: comp.status,
      item_count: itemCount,
      directory: comp.directory || null
    };
  }

  return summary;
}

/**
 * List all registered probes from manifest
 * @returns {Array<object>}
 */
function listProbes() {
  const manifest = loadManifest();
  const runners = (manifest.components && manifest.components.probes && manifest.components.probes.runners) || [];
  return runners.map(r => {
    const fullPath = resolvePath(r.file);
    return {
      id: r.id,
      name: r.name,
      file: r.file,
      command: r.command,
      criticality: r.criticality,
      description: r.description,
      exists: fs.existsSync(fullPath)
    };
  });
}

/**
 * Append an execution event to harness_runs.json
 * @param {object} event
 */
function recordRun(event) {
  try {
    let runs = [];
    if (fs.existsSync(RUNS_PATH)) {
      try {
        runs = JSON.parse(fs.readFileSync(RUNS_PATH, 'utf8'));
        if (!Array.isArray(runs)) runs = [];
      } catch {
        runs = [];
      }
    }

    const newRecord = {
      run_id: `run-${Date.now()}-${Math.random().toString(36).substring(2, 7)}`,
      timestamp: new Date().toISOString(),
      ...event
    };

    runs.unshift(newRecord);
    if (runs.length > 200) {
      runs = runs.slice(0, 200);
    }

    fs.writeFileSync(RUNS_PATH, JSON.stringify(runs, null, 2), 'utf8');
    return newRecord;
  } catch (err) {
    // Non-fatal logging error
    return null;
  }
}

/**
 * Get recent execution runs
 * @param {number} [limit=50]
 * @returns {Array<object>}
 */
function getRuns(limit = 50) {
  if (!fs.existsSync(RUNS_PATH)) {
    return [];
  }
  try {
    const runs = JSON.parse(fs.readFileSync(RUNS_PATH, 'utf8'));
    return Array.isArray(runs) ? runs.slice(0, limit) : [];
  } catch {
    return [];
  }
}

/**
 * Execute a specific probe script
 * @param {string} probeNameOrId
 * @returns {Promise<object>}
 */
function runProbe(probeNameOrId) {
  return new Promise((resolve, reject) => {
    if (!probeNameOrId || typeof probeNameOrId !== 'string') {
      return reject(new TypeError('Probe name required and must be a string'));
    }
    const sanitized = path.basename(probeNameOrId).replace(/[^a-zA-Z0-9_\-\.]/g, '');
    const probes = listProbes();
    const probeMeta = probes.find(p => p.id === sanitized || path.basename(p.file) === sanitized || p.id === sanitized.replace(/\.js$/, ''));

    const probeFile = probeMeta ? probeMeta.file : `.agent/harness/probes/${sanitized.endsWith('.js') ? sanitized : sanitized + '.js'}`;
    const fullScriptPath = resolvePath(probeFile);

    const startTime = Date.now();

    if (!fs.existsSync(fullScriptPath)) {
      const result = {
        name: probeMeta ? probeMeta.id : sanitized,
        status: 'STANDBY',
        exit_code: 0,
        latency_ms: Date.now() - startTime,
        timestamp: new Date().toISOString(),
        details: {
          summary: `Probe script not yet created: ${probeFile}`,
          script: probeFile
        }
      };
      recordRun({ event_type: 'PROBE_EXECUTION', target: result.name, status: result.status, latency_ms: result.latency_ms, details: result.details });
      return resolve(result);
    }

    execFile('node', [fullScriptPath], { timeout: 10000, cwd: ROOT_DIR }, (error, stdout, stderr) => {
      const latency = Date.now() - startTime;
      let parsedOutput = null;

      try {
        parsedOutput = JSON.parse(stdout.trim());
      } catch {
        // Output is not JSON
      }

      let result;
      if (parsedOutput && typeof parsedOutput === 'object' && parsedOutput.status) {
        const normStatus = (parsedOutput.status === 'OK' || parsedOutput.status === 'PASS') ? 'PASS' : parsedOutput.status;
        result = {
          name: parsedOutput.name || sanitized,
          status: normStatus,
          exit_code: typeof parsedOutput.exit_code === 'number' ? parsedOutput.exit_code : (error ? 1 : 0),
          latency_ms: parsedOutput.latency_ms || latency,
          timestamp: parsedOutput.timestamp || new Date().toISOString(),
          details: parsedOutput.details || { stdout: stdout.trim(), stderr: stderr.trim() }
        };
      } else {
        const isPass = !error && (!stderr || stderr.trim().length === 0);
        result = {
          name: probeMeta ? probeMeta.id : sanitized,
          status: isPass ? 'PASS' : 'FAIL',
          exit_code: error ? (error.code || 1) : 0,
          latency_ms: latency,
          timestamp: new Date().toISOString(),
          details: {
            stdout: stdout.trim(),
            stderr: stderr.trim(),
            error: error ? error.message : null
          }
        };
      }

      recordRun({ event_type: 'PROBE_EXECUTION', target: result.name, status: result.status, latency_ms: result.latency_ms, details: result.details });
      resolve(result);
    });
  });
}

/**
 * Execute all registered probes sequentially
 * @returns {Promise<Array<object>>}
 */
async function runAllProbes() {
  const probes = listProbes();
  const results = [];
  for (const p of probes) {
    const res = await runProbe(p.id);
    results.push(res);
  }
  return results;
}

/**
 * Parse EXPERIENCE.md and return structured cards
 * @param {string} [filterModule]
 * @param {string} [searchQuery]
 * @returns {object}
 */
function getExperiences(filterModule, searchQuery) {
  if (!fs.existsSync(EXPERIENCE_PATH)) {
    return { count: 0, experiences: [] };
  }

  const content = fs.readFileSync(EXPERIENCE_PATH, 'utf8');
  const experiences = [];

  // Match ### [ID] [MODULE] Title
  const cardRegex = /###\s*\[(.*?)\]\s*\[(.*?)\]\s*(.*?)(?=\n###|\n##\s|$)/gs;
  let match;

  while ((match = cardRegex.exec(content)) !== null) {
    const id = match[1].trim();
    const moduleTag = match[2].trim();
    const title = match[3].split('\n')[0].trim();
    const body = match[3];

    // Extract Context, Contrast, Winning Rule
    const contextMatch = body.match(/- \*\*Contexto \/ Síntoma\*\*:\s*([^\n]+(?:\n(?!\s*- \*\*)[^\n]+)*)/);
    const contrastMatch = body.match(/- \*\*Contraste \/ Qué Falló\*\*:\s*([^\n]+(?:\n(?!\s*- \*\*)[^\n]+)*)/);
    const ruleMatch = body.match(/- \*\*Regla Ganadora \/ Script Probado\*\*:\s*([^\n]+(?:\n(?!\s*- \*\*)[^\n]+)*)/);

    const card = {
      id,
      module: moduleTag,
      title,
      context: contextMatch ? contextMatch[1].trim() : '',
      contrast: contrastMatch ? contrastMatch[1].trim() : '',
      winning_rule: ruleMatch ? ruleMatch[1].trim() : '',
      raw: body.trim()
    };

    if (filterModule && filterModule !== 'TODAS' && filterModule.toUpperCase() !== 'ALL' && card.module.toUpperCase() !== filterModule.toUpperCase()) {
      continue;
    }

    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      const inText = `${card.id} ${card.module} ${card.title} ${card.context} ${card.contrast} ${card.winning_rule}`.toLowerCase();
      if (!inText.includes(q)) {
        continue;
      }
    }

    experiences.push(card);
  }

  return {
    count: experiences.length,
    experiences
  };
}

// -----------------------------------------------------------------------------
// CLI Execution Handler
// -----------------------------------------------------------------------------
if (require.main === module) {
  const args = process.argv.slice(2);
  const command = args[0] || '--status';

  (async () => {
    switch (command) {
      case '--status':
      case 'status': {
        const summary = getComponentSummary();
        console.log(JSON.stringify(summary, null, 2));
        process.exit(0);
        break;
      }

      case '--validate-manifest':
      case 'validate':
      case '--validate': {
        const report = validateManifest();
        console.log(JSON.stringify(report, null, 2));
        process.exit(report.valid ? 0 : 1);
        break;
      }

      case '--list-probes':
      case 'probes':
      case '--probes': {
        const probes = listProbes();
        console.log(JSON.stringify({ total: probes.length, probes }, null, 2));
        process.exit(0);
        break;
      }

      case '--run-probe':
      case 'run-probe': {
        const probeName = args[1] || 'operational_health_probe';
        const res = await runProbe(probeName);
        console.log(JSON.stringify(res, null, 2));
        process.exit(res.status === 'FAIL' ? 1 : 0);
        break;
      }

      case '--run-all-probes': {
        const results = await runAllProbes();
        console.log(JSON.stringify({ total: results.length, results }, null, 2));
        const hasFail = results.some(r => r.status === 'FAIL');
        process.exit(hasFail ? 1 : 0);
        break;
      }

      case '--experiences':
      case 'experiences': {
        const mod = args[1];
        const exps = getExperiences(mod);
        console.log(JSON.stringify(exps, null, 2));
        process.exit(0);
        break;
      }

      case '--runs':
      case 'runs': {
        const runs = getRuns(20);
        console.log(JSON.stringify({ total: runs.length, runs }, null, 2));
        process.exit(0);
        break;
      }

      case '--help':
      case '-h':
      default: {
        console.log(`
Harness² Engine — CLI Usage:
  node harness_engine.js --status             Show summary of 8 components (JSON)
  node harness_engine.js --validate-manifest  Validate manifest schema & files (JSON)
  node harness_engine.js --list-probes        List configured telemetry probes
  node harness_engine.js --run-probe <name>   Execute a probe script
  node harness_engine.js --run-all-probes     Execute all probes
  node harness_engine.js --experiences [mod]  List experience catalog cards
  node harness_engine.js --runs               Show recent execution events
  node harness_engine.js --help               Show this message
`);
        process.exit(0);
      }
    }
  })().catch(err => {
    console.error(JSON.stringify({ error: err.message, stack: err.stack }));
    process.exit(1);
  });
}

const getStatus = getComponentSummary;

module.exports = {
  loadManifest,
  validateManifest,
  getComponentSummary,
  getStatus,
  listProbes,
  runProbe,
  runAllProbes,
  getExperiences,
  getRuns,
  recordRun,
  MANIFEST_PATH,
  HARNESS_DIR,
  ROOT_DIR
};
