// Rind <-> H-MEM bridge. A long-running process speaking line-delimited JSON on stdin/stdout,
// so the Python agents use the real H-MEM engine (src/index.ts) with on-disk persistence.
//
//   npx tsx hmem/bridge.ts <persistDir>
//   -> {"op":"add","content":"...","source":"rind:Reporter","chatId":"ACC-1020","tags":["kind:action"]}
//   <- {"ok":true,"result":{...}}
// ops: add, query, feedback, history, stats, sleep_preview
import readline from "node:readline";
import { HMEM } from "./src/index.ts";

const dir = process.argv[2] ?? "memory/hmem";
const hmem = new HMEM({ persistDir: dir, actor: "rind" });

function slim(m: any) {
  const { embedding, ...rest } = m;
  return rest;
}

function handle(req: any): unknown {
  switch (req.op) {
    case "add":
      return slim(hmem.add({
        content: req.content, type: req.type, layer: req.layer, tags: req.tags,
        confidence: req.confidence, source: req.source, chatId: req.chatId,
        provenance: req.provenance,
      }));
    case "query": {
      const r = hmem.query(req.query, { topN: req.topN, threshold: req.threshold, chatId: req.chatId });
      hmem.store.persist(); // keep access counts across sessions
      return { memories: r.memories.map((x: any) => ({ relevance: x.relevance, memory: slim(x.memory) })), context: r.context, trace: r.trace };
    }
    case "feedback":
      hmem.feedback(req.id, req.kind);
      return slim(hmem.store.get(req.id));
    case "history":
      return hmem.ledger.history(req.id);
    case "stats": {
      const all = hmem.store.all();
      const layers: Record<string, number> = {};
      for (const m of all) layers[m.layer] = (layers[m.layer] ?? 0) + 1;
      return { records: all.length, accounts: new Set(all.map((m) => m.chatId)).size, layers, dir };
    }
    case "sleep_preview":
      return hmem.sleep.preview();
    default:
      throw new Error(`unknown op ${req.op}`);
  }
}

const rl = readline.createInterface({ input: process.stdin });
rl.on("line", (line) => {
  if (!line.trim()) return;
  let out: unknown;
  try {
    out = { ok: true, result: handle(JSON.parse(line)) };
  } catch (e) {
    out = { ok: false, error: (e as Error).message };
  }
  process.stdout.write(JSON.stringify(out) + "\n");
});
process.stdout.write(JSON.stringify({ ok: true, ready: true, dir }) + "\n");
