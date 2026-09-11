const dropzone   = document.getElementById('dropzone');
const fileInput  = document.getElementById('file-input');
const fileChosen = document.getElementById('file-chosen');
const runBtn     = document.getElementById('run-btn');
const statusBox  = document.getElementById('status-box');
const statusLog  = document.getElementById('status-log');
const dlArea     = document.getElementById('download-area');
const dlBtn      = document.getElementById('download-btn');

let selectedFile = null;
let pyodide      = null;
let pyodideReady = false;

function log(msg, cls) {
  statusBox.style.display = 'block';
  const line = document.createElement('div');
  if (cls) line.className = cls;
  line.textContent = msg;
  statusLog.appendChild(line);
  statusBox.scrollTop = statusBox.scrollHeight;
}

function clearLog() {
  statusLog.innerHTML = '';
}

async function initPyodide() {
  log('Step 1/3: Fetching convert_ses.py...');
  let pySource;
  try {
    const resp = await fetch('convert_ses.py');
    if (!resp.ok) throw new Error('HTTP ' + resp.status + ' ' + resp.statusText);
    pySource = await resp.text();
    log('Step 1/3: convert_ses.py fetched (' + pySource.length + ' chars).', 'log-ok');
  } catch (e) {
    log('Step 1/3 FAILED: ' + e.message, 'log-err');
    return;
  }

  log('Step 2/3: Loading Pyodide...');
  try {
    pyodide = await loadPyodide();
    log('Step 2/3: Pyodide loaded.', 'log-ok');
  } catch (e) {
    log('Step 2/3 FAILED: ' + e.message, 'log-err');
    return;
  }

  // No pandas needed: .ses files are parsed with the standard-library
  // xml.etree.ElementTree module, which ships with Pyodide already.
  log('Step 3/3: Defining conversion function...');
  try {
    await pyodide.runPythonAsync(pySource);
    log('Step 3/3: Done.', 'log-ok');
  } catch (e) {
    log('Step 3/3 FAILED: ' + e.message, 'log-err');
    return;
  }

  pyodideReady = true;
  log('Ready! Select a .ses file and click Generate.', 'log-ok');
  updateRunBtn();
}

function setFile(f) {
  selectedFile = f;
  fileChosen.textContent = f.name;
  dropzone.classList.add('has-file');
  updateRunBtn();
}

function updateRunBtn() {
  runBtn.disabled = !(pyodideReady && selectedFile);
}

fileInput.addEventListener('change', function() {
  if (fileInput.files.length) setFile(fileInput.files[0]);
});
dropzone.addEventListener('dragover', function(e) {
  e.preventDefault();
  dropzone.classList.add('drag-over');
});
dropzone.addEventListener('dragleave', function() {
  dropzone.classList.remove('drag-over');
});
dropzone.addEventListener('drop', function(e) {
  e.preventDefault();
  dropzone.classList.remove('drag-over');
  if (e.dataTransfer.files.length) setFile(e.dataTransfer.files[0]);
});

runBtn.addEventListener('click', async function() {
  clearLog();
  dlArea.style.display = 'none';
  runBtn.disabled = true;

  const author  = document.getElementById('f-author').value.trim();
  const title   = document.getElementById('f-title').value.trim();
  const session = parseInt(document.getElementById('f-session').value, 10) || 1;
  const version = parseInt(document.getElementById('f-version').value, 10) || 1;
  const outname = document.getElementById('f-outname').value.trim() || 'keystroke_output';

  log('Reading file...');
  const arrayBuffer = await selectedFile.arrayBuffer();
  const uint8 = new Uint8Array(arrayBuffer);

  log('Running conversion...');
  try {
    pyodide.globals.set('_ses_bytes', uint8);
    pyodide.globals.set('_author',   author);
    pyodide.globals.set('_title',    title);
    pyodide.globals.set('_session',  session);
    pyodide.globals.set('_version',  version);
    pyodide.globals.set('_outname',  outname);

    const result = await pyodide.runPythonAsync(
      'convert_ses_to_tei(bytes(_ses_bytes.to_py()), _author, _title, _session, _version, _outname)'
    );

    log('Conversion complete!', 'log-ok');

    const blob = new Blob([result], { type: 'application/xml' });
    const url  = URL.createObjectURL(blob);
    dlBtn.href     = url;
    dlBtn.download = outname + '.xml';
    dlArea.style.display = 'block';

  } catch (err) {
    const lines = (err.message || String(err)).split('\n');
    lines.forEach(function(l) { if (l.trim()) log(l, 'log-err'); });
  } finally {
    runBtn.disabled = false;
  }
});

initPyodide();
