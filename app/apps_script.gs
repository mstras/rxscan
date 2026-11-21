/**
 * RxScan bridge: lets RxScan list your Google Sheets and append rows to them.
 * It runs as YOU, so there's no service account and no need to share sheets.
 *
 * Receipt photos are saved to the Drive folder you choose in RxScan settings (private to you).
 *
 * Setup:
 *   1. script.google.com → New project → replace everything with this file → Save
 *   2. Deploy → New deployment → gear icon → Web app
 *        Execute as: Me    Who has access: Anyone
 *   3. Deploy → Authorize access (Advanced → Go to project if Google warns)
 *   4. Copy the Web app URL (ends in /exec) into RxScan → Settings → Google Sheets
 *
 * "Anyone" only means the URL is reachable without a Google login; every request
 * must carry the secret below, which only your RxScan server knows.
 */
const SECRET = '__RXSCAN_SECRET__';
const VERSION = 3;

function doGet() {
  return ContentService.createTextOutput('RxScan bridge is running. Paste this URL into RxScan settings.');
}

function doPost(e) {
  let res;
  try {
    const req = JSON.parse(e.postData.contents);
    if (!SECRET || SECRET.indexOf('__') === 0 || req.secret !== SECRET) throw new Error('Bad secret');
    const fn = ACTIONS[req.action];
    if (!fn) throw new Error('Unknown action: ' + req.action);
    res = fn(req);
  } catch (err) {
    res = { error: String(err && err.message ? err.message : err) };
  }
  return ContentService.createTextOutput(JSON.stringify(res)).setMimeType(ContentService.MimeType.JSON);
}

const ACTIONS = {
  ping: () => ({ ok: true, version: VERSION, user: Session.getEffectiveUser().getEmail() }),

  list: (r) => {
    const it = DriveApp.searchFiles('mimeType = "application/vnd.google-apps.spreadsheet" and trashed = false');
    const out = [];
    while (it.hasNext() && out.length < 300) {
      const f = it.next();
      out.push({ id: f.getId(), title: f.getName(), updated: f.getLastUpdated().getTime() });
    }
    out.sort((a, b) => b.updated - a.updated);
    return { files: out.slice(0, r.limit || 150) };
  },

  info: (r) => {
    const ss = SpreadsheetApp.openById(r.id);
    return { id: r.id, title: ss.getName(), tabs: ss.getSheets().map((s) => s.getName()), url: ss.getUrl() };
  },

  header: (r) => {
    const sh = tab_(r);
    const n = sh.getLastColumn();
    return { header: n ? sh.getRange(1, 1, 1, n).getDisplayValues()[0] : [] };
  },

  set_header: (r) => {
    const sh = tab_(r);
    const rng = sh.getRange(1, 1, 1, r.header.length);
    rng.setValues([r.header]).setFontWeight('bold');
    sh.setFrozenRows(1);
    return { ok: true };
  },

  append: (r) => {
    const lock = LockService.getScriptLock();
    lock.waitLock(20000);
    try {
      const sh = tab_(r);
      const start = sh.getLastRow() + 1;
      sh.getRange(start, 1, r.values.length, r.values[0].length).setValues(r.values);
      return { appended: r.values.length, range: sh.getName() + '!A' + start };
    } finally {
      lock.releaseLock();
    }
  },

  upload: (r) => {
    const root = r.folderId ? DriveApp.getFolderById(r.folderId) : folder_(r.folder || 'RxScan Receipts');
    const dest = r.subfolder ? child_(root, r.subfolder) : root;
    const blob = Utilities.newBlob(Utilities.base64Decode(r.data), r.mime || 'image/jpeg', r.name || 'receipt.jpg');
    const f = dest.createFile(blob);
    if (r.description) f.setDescription(r.description);
    return { id: f.getId(), url: f.getUrl(), name: f.getName() };
  },

  folders: (r) => {
    const take = (it) => { const out = []; while (it.hasNext() && out.length < 500) { const f = it.next(); out.push({ id: f.getId(), name: f.getName() }); } return out.sort((a, b) => a.name.localeCompare(b.name)); };
    if (r.parent === 'shared') {
      return { folder: { id: 'shared', name: 'Shared with me', path: 'Shared with me', parentId: null },
               children: take(DriveApp.searchFolders('sharedWithMe and trashed = false')) };
    }
    const f = folderById_(r.parent);
    return { folder: describe_(f), children: take(f.getFolders()) };
  },

  create_folder: (r) => {
    const name = String(r.name || '').trim();
    if (!name) throw new Error('Folder name is empty');
    return describe_(folderById_(r.parent).createFolder(name));
  },

  folder_info: (r) => describe_(DriveApp.getFolderById(r.id)),

  create: (r) => {
    const ss = SpreadsheetApp.create(r.title || 'Prescriptions');
    const sh = ss.getSheets()[0];
    sh.setName(r.tab || 'Prescriptions');
    return { id: ss.getId(), title: ss.getName(), tabs: [sh.getName()], url: ss.getUrl() };
  },
};

function folderById_(id) {
  return id && id !== 'root' ? DriveApp.getFolderById(id) : DriveApp.getRootFolder();
}

function describe_(f) {
  const rootId = DriveApp.getRootFolder().getId();
  const names = [];
  let cur = f, parentId = null, depth = 0;
  while (cur && depth < 10) {
    names.unshift(cur.getId() === rootId ? 'My Drive' : cur.getName());
    const ps = cur.getParents();
    const p = ps.hasNext() ? ps.next() : null;
    if (depth === 0) parentId = p ? p.getId() : (f.getId() === rootId ? null : 'shared');
    cur = p; depth++;
  }
  return { id: f.getId(), name: f.getId() === rootId ? 'My Drive' : f.getName(), path: names.join(' › '), parentId: parentId };
}

function folder_(name) {
  const props = PropertiesService.getScriptProperties();
  const key = 'folder:' + name;
  const id = props.getProperty(key);
  if (id) {
    try { const f = DriveApp.getFolderById(id); if (!f.isTrashed()) return f; } catch (e) {}
  }
  const it = DriveApp.getRootFolder().getFoldersByName(name);
  const f = it.hasNext() ? it.next() : DriveApp.createFolder(name);
  props.setProperty(key, f.getId());
  return f;
}

function child_(parent, name) {
  const it = parent.getFoldersByName(name);
  return it.hasNext() ? it.next() : parent.createFolder(name);
}

function tab_(r) {
  const sh = SpreadsheetApp.openById(r.id).getSheetByName(r.tab);
  if (!sh) throw new Error('Tab not found: ' + r.tab);
  return sh;
}
