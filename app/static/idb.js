/* Tiny IndexedDB helper shared by the page and the service worker.
 * Stores: "kv" (key → value, for the in-progress draft) and "inbox" (photos shared in via the share sheet). */
(function (g) {
  const DB = "rxscan", VER = 1;
  let dbp = null;
  function open() {
    if (dbp) return dbp;
    dbp = new Promise((res, rej) => {
      const r = indexedDB.open(DB, VER);
      r.onupgradeneeded = () => {
        const db = r.result;
        if (!db.objectStoreNames.contains("kv")) db.createObjectStore("kv");
        if (!db.objectStoreNames.contains("inbox")) db.createObjectStore("inbox", { autoIncrement: true });
      };
      r.onsuccess = () => res(r.result);
      r.onerror = () => { dbp = null; rej(r.error); };
    });
    return dbp;
  }
  function tx(store, mode, fn) {
    return open().then((db) => new Promise((res, rej) => {
      const t = db.transaction(store, mode), s = t.objectStore(store);
      const out = fn(s);
      t.oncomplete = () => res(out && "result" in out ? out.result : out);
      t.onerror = t.onabort = () => rej(t.error);
    }));
  }
  g.RxDB = {
    get: (k) => tx("kv", "readonly", (s) => s.get(k)),
    set: (k, v) => tx("kv", "readwrite", (s) => s.put(v, k)),
    del: (k) => tx("kv", "readwrite", (s) => s.delete(k)),
    inboxAdd: (items) => tx("inbox", "readwrite", (s) => { items.forEach((i) => s.add(i)); }),
    inboxTake: () => open().then((db) => new Promise((res, rej) => {
      const t = db.transaction("inbox", "readwrite"), s = t.objectStore("inbox");
      const all = s.getAll(); s.clear();
      t.oncomplete = () => res(all.result || []);
      t.onerror = () => rej(t.error);
    })),
  };
})(self);
